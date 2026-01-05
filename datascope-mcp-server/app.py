"""
DataScope MCP Server - Single Gateway for All Tools

This is a CUSTOM MCP server that acts as a single gateway for:
- Databricks SQL Warehouse (execute queries)
- Databricks Vector Search (find similar patterns)
- Databricks Unity Catalog (table schemas, lineage)
- GitHub API (code search, file retrieval)

HYBRID AUTHENTICATION MODEL (v3.1 - OAuth):
==========================================
This server uses a two-token authentication model:

1. SP OAuth Token (App-to-App Auth):
   - Header: Authorization: Bearer <databricks_oauth_token>
   - Purpose: Proves request is from legitimate Vercel app
   - Validation: Token verified against Databricks API
   - How Vercel gets it: M2M OAuth flow with client_id + client_secret

2. User Token (Data Access):
   - Header: X-User-Token: <user_databricks_oauth_token>
   - Purpose: User's Databricks OAuth token for data access
   - Effect: Unity Catalog enforces USER's permissions (RLS, column masks, etc.)

Why This Matters:
- Different users have different access levels in Databricks
- Using user's token ensures their permissions are enforced
- Audit logs show actual user identity, not service principal
- No static secrets - proper OAuth throughout

MCP Protocol (JSON-RPC 2.0):
- initialize: Handshake with client
- tools/list: Return available tools with schemas
- tools/call: Execute a tool and return results
- notifications/initialized: Client ready signal (no response)

Observability: Galileo AI
- All tool calls are logged as spans
- Traces are grouped by conversation/session
- Enables debugging and evaluation of agent performance

Deployed on: Databricks Apps
Called by: Vercel-hosted DataScope agent
"""

import os
import json
import logging
import base64
import hashlib
import hmac
import time
import uuid
from functools import wraps
from urllib.parse import quote
from contextlib import contextmanager
from pathlib import Path

import requests
from flask import Flask, request, jsonify, g


# =============================================================================
# Load .env file for local development
# =============================================================================
def load_dotenv():
    """Load environment variables from .env file if it exists."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value

load_dotenv()

# =============================================================================
# Galileo AI - Observability and Evaluation
# =============================================================================
try:
    from galileo import galileo_context
    from galileo.logger import GalileoLogger
    GALILEO_ENABLED = bool(os.environ.get("GALILEO_API_KEY"))
    if GALILEO_ENABLED:
        galileo_context.init(
            project=os.environ.get("GALILEO_PROJECT", "datascope-mcp"),
            log_stream=os.environ.get("GALILEO_LOG_STREAM", "mcp-tools")
        )
except ImportError:
    GALILEO_ENABLED = False
    GalileoLogger = None

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Databricks Configuration
DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "")
SQL_WAREHOUSE_ID = os.environ.get("SQL_WAREHOUSE_ID", "")
VS_INDEX = os.environ.get("VS_INDEX", "")

# Fallback Databricks token (used only if X-User-Token not provided)
# In production, user tokens should always be provided for proper access control
DATABRICKS_FALLBACK_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")

# GitHub Configuration
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "novatech")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "novatech-transformations")

# OAuth App Authentication (M2M)
# The service principal application ID that is allowed to call this MCP server
# This is the Vercel app's SP - tokens from other SPs will be rejected
ALLOWED_SP_APP_ID = os.environ.get("ALLOWED_SP_APP_ID", "")

# Static Auth Token (Simple Mode)
# If set, accepts this token in Authorization header instead of OAuth
# Use this when OAuth SP is not available in the Databricks account
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")

# Token validation cache (to avoid repeated API calls)
# Key: token hash, Value: (is_valid, expiry_time)
_token_cache: dict[str, tuple[bool, float]] = {}
TOKEN_CACHE_TTL = 300  # 5 minutes

# MCP Protocol Version
MCP_VERSION = "2024-11-05"

# Server Info
SERVER_INFO = {
    "name": "datascope-mcp-server",
    "version": "3.1.0"  # v3.1: OAuth for app auth, user token for data
}

# =============================================================================
# OAuth Token Validation
# =============================================================================

def decode_jwt_claims(token: str) -> dict:
    """Decode JWT token claims without verification (we verify via API call).

    Args:
        token: JWT token string

    Returns:
        dict: Decoded claims or empty dict if decoding fails
    """
    try:
        # JWT has 3 parts: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return {}

        # Decode the payload (middle part)
        payload = parts[1]
        # Add padding if needed
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding

        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        logger.debug(f"JWT decode failed: {e}")
        return {}


def validate_oauth_token(token: str) -> tuple[bool, str]:
    """Validate a Databricks OAuth token.

    This verifies:
    1. The token is a valid Databricks OAuth token (via API call)
    2. The token belongs to the allowed service principal (via JWT claims)

    Args:
        token: The OAuth Bearer token from Authorization header

    Returns:
        tuple: (is_valid, error_message_or_user_info)
    """
    if not token:
        return False, "No token provided"

    # Check cache first
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    if token_hash in _token_cache:
        is_valid, expiry = _token_cache[token_hash]
        if time.time() < expiry:
            logger.debug(f"Token validation cache hit: {is_valid}")
            return is_valid, "cached" if is_valid else "cached_invalid"

    # First, check JWT claims for the client_id (service principal check)
    if ALLOWED_SP_APP_ID:
        claims = decode_jwt_claims(token)
        # OAuth tokens have 'sub' (subject) which is the client_id for M2M tokens
        # They may also have 'azp' (authorized party) or 'client_id'
        token_client_id = claims.get("sub", "") or claims.get("azp", "") or claims.get("client_id", "")

        if token_client_id != ALLOWED_SP_APP_ID:
            logger.warning(f"Token from unauthorized client: {token_client_id[:20]}...")
            _token_cache[token_hash] = (False, time.time() + 60)
            return False, f"Unauthorized client ID"

    # Validate token by calling Databricks API
    try:
        resp = requests.get(
            f"{DATABRICKS_HOST}/api/2.0/preview/scim/v2/Me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )

        if resp.status_code == 401:
            _token_cache[token_hash] = (False, time.time() + 60)  # Cache failure for 1 min
            return False, "Invalid or expired token"

        if resp.status_code != 200:
            return False, f"Token validation failed: {resp.status_code}"

        user_data = resp.json()
        user_name = user_data.get("userName", "") or user_data.get("displayName", "service-principal")

        # Token is valid - cache it
        _token_cache[token_hash] = (True, time.time() + TOKEN_CACHE_TTL)
        logger.info(f"OAuth token validated for: {user_name}")
        return True, user_name

    except requests.exceptions.Timeout:
        return False, "Token validation timed out"
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        return False, str(e)


def require_auth(f):
    """Decorator to require authentication for MCP endpoints.

    Authentication modes (in order of priority):
    1. Static Token: If MCP_AUTH_TOKEN is set, validates against it
    2. OAuth: If ALLOWED_SP_APP_ID is set, validates OAuth token
    3. Dev Mode: If neither is set, authentication is disabled

    Clients must provide:
    - Authorization: Bearer <token>
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check Authorization header
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""

        # Mode 1: Static token authentication (simple mode)
        if MCP_AUTH_TOKEN:
            if not token:
                logger.warning(f"Missing Bearer token from {request.remote_addr}")
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32001,
                        "message": "Authorization header with Bearer token required"
                    }
                }), 401

            # Constant-time comparison to prevent timing attacks
            if hmac.compare_digest(token, MCP_AUTH_TOKEN):
                logger.debug(f"Static token auth successful from {request.remote_addr}")
                return f(*args, **kwargs)
            else:
                logger.warning(f"Invalid static token from {request.remote_addr}")
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32001,
                        "message": "Invalid authentication token"
                    }
                }), 401

        # Mode 2: OAuth authentication
        if ALLOWED_SP_APP_ID:
            if not token:
                logger.warning(f"Missing Bearer token from {request.remote_addr}")
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32001,
                        "message": "Authorization header with Bearer token required"
                    }
                }), 401

            is_valid, message = validate_oauth_token(token)

            if is_valid:
                g.oauth_user = message
                return f(*args, **kwargs)

            logger.warning(f"OAuth authentication failed from {request.remote_addr}: {message}")
            return jsonify({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32001,
                    "message": f"Authentication failed: {message}"
                }
            }), 401

        # Mode 3: No auth configured (development mode)
        logger.warning("No authentication configured - running in dev mode")
        return f(*args, **kwargs)

    return decorated

# =============================================================================
# Tool Definitions (JSON Schema format for MCP)
# =============================================================================

TOOLS = [
    # Databricks SQL Tool
    {
        "name": "execute_sql",
        "description": """Execute a SQL query against Databricks SQL Warehouse.

Use this to:
- Count affected records: SELECT COUNT(*) FROM table WHERE condition
- Sample data: SELECT * FROM table WHERE condition LIMIT 10
- Compare values between tables or layers (bronze/silver/gold)
- Check for NULL values or duplicates
- Get table schema: DESCRIBE novatech.gold.table_name
- List tables: SHOW TABLES IN novatech.gold

Only SELECT, DESCRIBE, and SHOW queries are allowed (read-only).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "SQL query to execute"
                }
            },
            "required": ["query"]
        }
    },

    # Databricks Vector Search Tool
    {
        "name": "search_patterns",
        "description": """Search for similar past data quality issues using Vector Search.

Use this FIRST before investigating to get context on common patterns
and suggested SQL queries for the investigation.

Returns similar patterns with symptoms, root causes, and suggested SQL.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Description of the data issue (e.g., 'NULL churn_risk values')"
                }
            },
            "required": ["query"]
        }
    },

    # Databricks Unity Catalog Tool
    {
        "name": "get_table_schema",
        "description": """Get the schema of a table from Unity Catalog.

Returns column names, types, and comments for the specified table.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Fully qualified table name (e.g., novatech.gold.churn_predictions)"
                }
            },
            "required": ["table_name"]
        }
    },

    # GitHub Code Search Tool
    {
        "name": "search_code",
        "description": """Search SQL transformation code in the GitHub repository.

Use this to find the transformation logic that creates a specific column
or table. This helps identify WHERE the bug is in the code.

Searches .sql files in the repository for the given term.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term (e.g., column name like 'churn_risk')"
                }
            },
            "required": ["query"]
        }
    },

    # GitHub File Retrieval Tool
    {
        "name": "get_file",
        "description": """Get the full contents of a file from the GitHub repository.

Use this after search_code to see the full transformation logic.
Returns file contents with line numbers.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to file (e.g., 'sql/gold/churn_predictions.sql')"
                }
            },
            "required": ["file_path"]
        }
    },

    # GitHub Directory Listing Tool
    {
        "name": "list_sql_files",
        "description": """List SQL transformation files in the repository.

Use this to discover what transformation files exist before searching.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory to list (default: 'sql')"
                }
            },
            "required": []
        }
    }
]

# =============================================================================
# Helper Functions
# =============================================================================

def get_user_token() -> tuple[str, bool]:
    """Extract user's Databricks token from request headers.

    Returns:
        tuple: (token, is_user_token)
            - token: The Databricks token to use
            - is_user_token: True if using user's token, False if using fallback

    Token Priority:
        1. X-User-Token header (user's OAuth token) - PREFERRED
        2. DATABRICKS_FALLBACK_TOKEN env var - for dev/testing only
    """
    # Try to get user token from header
    user_token = request.headers.get("X-User-Token", "").strip()

    if user_token:
        logger.debug("Using user token from X-User-Token header")
        return user_token, True

    # Fall back to configured token (dev mode)
    if DATABRICKS_FALLBACK_TOKEN:
        logger.warning("No X-User-Token provided - using fallback token (dev mode)")
        return DATABRICKS_FALLBACK_TOKEN, False

    return "", False


def get_databricks_headers(user_token: str = None) -> dict:
    """Get Databricks API authentication headers.

    Args:
        user_token: User's Databricks OAuth token. If None, extracts from request.

    Returns:
        dict: Headers for Databricks API calls
    """
    if user_token is None:
        user_token, _ = get_user_token()

    return {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json"
    }


def get_github_headers():
    """Get GitHub API authentication headers."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def mcp_response(request_id, result=None, error=None):
    """Format MCP JSON-RPC response."""
    response = {"jsonrpc": "2.0", "id": request_id}
    if error:
        response["error"] = error
    else:
        response["result"] = result or {}
    return jsonify(response)


def mcp_error(request_id, code, message, data=None):
    """Format MCP error response."""
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return mcp_response(request_id, error=error)


def tool_result(data):
    """Format tool result for MCP response."""
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data)
            }
        ]
    }


# =============================================================================
# Galileo Observability Helpers
# =============================================================================

def log_tool_span(tool_name: str, input_args: dict, output: dict, duration_ms: float,
                  session_id: str = None, error: str = None):
    """Log a tool execution as a span in Galileo.

    This enables:
    - Tracing all MCP tool calls
    - Measuring latency per tool
    - Debugging failed tool calls
    - Analyzing agent behavior patterns
    """
    if not GALILEO_ENABLED:
        return

    try:
        galileo_logger = GalileoLogger(
            project=os.environ.get("GALILEO_PROJECT", "datascope-mcp"),
            log_stream=os.environ.get("GALILEO_LOG_STREAM", "mcp-tools")
        )

        # Log as a tool span (MCP tool call)
        galileo_logger.add_tool_span(
            input=json.dumps(input_args),
            output=json.dumps(output),
            tool_call_id=str(uuid.uuid4()),
            name=tool_name,
            duration_ns=int(duration_ms * 1_000_000),  # Convert ms to ns
            status_code="ERROR" if error else "OK",
            tags={
                "mcp.tool": tool_name,
                "mcp.session_id": session_id or "unknown",
                "mcp.server": "datascope-mcp",
            }
        )

        galileo_logger.flush()
        logger.debug(f"Logged tool span to Galileo: {tool_name}")

    except Exception as e:
        logger.warning(f"Failed to log to Galileo: {e}")


# =============================================================================
# Tool Implementations - Databricks
# =============================================================================

def execute_sql(query: str, user_token: str = None) -> dict:
    """Execute SQL query against Databricks SQL Warehouse.

    Uses the user's token to ensure Unity Catalog enforces their permissions.
    This means row-level security, column masks, and table access are all
    enforced based on the user's identity, not the service principal.

    Args:
        query: SQL query to execute (must be read-only)
        user_token: User's Databricks OAuth token. If None, extracted from request.

    Returns:
        dict: Query results or error message
    """
    if not DATABRICKS_HOST:
        return {"error": "Databricks host not configured"}

    if not SQL_WAREHOUSE_ID:
        return {"error": "SQL Warehouse ID not configured"}

    # Get user token if not provided
    if user_token is None:
        user_token, is_user_token = get_user_token()
        if not user_token:
            return {"error": "No Databricks token available. User must authenticate."}
    else:
        is_user_token = True

    # Validate query is read-only
    query_upper = query.upper().strip()
    allowed_prefixes = ["SELECT", "DESCRIBE", "DESC", "SHOW"]
    if not any(query_upper.startswith(p) for p in allowed_prefixes):
        return {"error": "Only SELECT, DESCRIBE, and SHOW queries are allowed"}

    dangerous_keywords = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "CREATE"]
    if any(kw in query_upper for kw in dangerous_keywords):
        return {"error": "Only read-only queries are allowed"}

    try:
        url = f"{DATABRICKS_HOST}/api/2.0/sql/statements"
        resp = requests.post(
            url,
            headers=get_databricks_headers(user_token),
            json={
                "warehouse_id": SQL_WAREHOUSE_ID,
                "statement": query,
                "wait_timeout": "30s"
            },
            timeout=35
        )

        if resp.status_code == 401:
            return {"error": "Authentication failed. User token may be expired."}
        elif resp.status_code == 403:
            return {"error": "Access denied. User may not have permission for this query."}
        elif resp.status_code != 200:
            return {"error": f"SQL error ({resp.status_code}): {resp.text[:200]}"}

        data = resp.json()
        status = data.get("status", {}).get("state", "")

        if status == "SUCCEEDED":
            columns = [c["name"] for c in data.get("manifest", {}).get("schema", {}).get("columns", [])]
            rows = data.get("result", {}).get("data_array", [])[:15]  # Limit rows
            row_count = data.get("result", {}).get("row_count", len(rows))

            logger.info(f"SQL query returned {row_count} rows (user_token={is_user_token})")
            return {
                "columns": columns,
                "rows": rows,
                "row_count": row_count,
                "truncated": row_count > 15,
                "using_user_token": is_user_token
            }
        elif status == "FAILED":
            error_msg = data.get("status", {}).get("error", {}).get("message", "Query failed")
            return {"error": error_msg}
        else:
            return {"error": f"Query status: {status}. Warehouse may be starting."}

    except requests.exceptions.Timeout:
        return {"error": "Query timed out after 30 seconds"}
    except Exception as e:
        logger.error(f"SQL error: {e}")
        return {"error": str(e)}


def search_patterns(query: str, user_token: str = None) -> dict:
    """Search Vector Search index for similar patterns.

    Uses the user's token to ensure they have access to the vector search index.

    Args:
        query: Description of the data issue to search for
        user_token: User's Databricks OAuth token. If None, extracted from request.

    Returns:
        dict: Matching patterns or error message
    """
    if not VS_INDEX:
        return {"error": "Vector Search not configured", "patterns": []}

    # Get user token if not provided
    if user_token is None:
        user_token, is_user_token = get_user_token()
        if not user_token:
            return {"error": "No Databricks token available. User must authenticate.", "patterns": []}
    else:
        is_user_token = True

    try:
        url = f"{DATABRICKS_HOST}/api/2.0/vector-search/indexes/{VS_INDEX}/query"
        resp = requests.post(
            url,
            headers=get_databricks_headers(user_token),
            json={
                "query_text": query,
                "columns": ["pattern_id", "title", "symptoms", "root_cause", "resolution", "investigation_sql"],
                "num_results": 3
            },
            timeout=15
        )

        if resp.status_code == 401:
            return {"error": "Authentication failed. User token may be expired.", "patterns": []}
        elif resp.status_code == 403:
            return {"error": "Access denied to Vector Search index.", "patterns": []}
        elif resp.status_code != 200:
            return {"error": f"Vector Search error: {resp.text[:200]}", "patterns": []}

        data = resp.json()
        results = data.get("result", {}).get("data_array", [])

        patterns = []
        for row in results:
            if len(row) >= 6:
                patterns.append({
                    "pattern_id": row[0],
                    "title": row[1],
                    "symptoms": row[2],
                    "root_cause": row[3],
                    "resolution": row[4],
                    "investigation_sql": row[5]
                })

        logger.info(f"Pattern search found {len(patterns)} matches (user_token={is_user_token})")
        return {"patterns": patterns, "count": len(patterns), "using_user_token": is_user_token}

    except Exception as e:
        logger.error(f"Pattern search error: {e}")
        return {"error": str(e), "patterns": []}


def get_table_schema(table_name: str, user_token: str = None) -> dict:
    """Get table schema from Unity Catalog via DESCRIBE.

    Uses the user's token to ensure they have access to view the table schema.

    Args:
        table_name: Fully qualified table name (catalog.schema.table)
        user_token: User's Databricks OAuth token. If None, extracted from request.

    Returns:
        dict: Table schema or error message
    """
    # Validate table name format
    if table_name.count(".") != 2:
        return {"error": "Table name must be fully qualified: catalog.schema.table"}

    return execute_sql(f"DESCRIBE {table_name}", user_token=user_token)

# =============================================================================
# Tool Implementations - GitHub
# =============================================================================

def search_code(query: str) -> dict:
    """Search code in GitHub repository."""
    if not GITHUB_TOKEN:
        logger.warning("GitHub token not configured - using unauthenticated API (rate limited)")

    try:
        # GitHub code search API
        # Note: Code search requires authentication and has specific requirements
        search_query = f"{query} repo:{GITHUB_OWNER}/{GITHUB_REPO} extension:sql"

        url = f"https://api.github.com/search/code?q={quote(search_query)}"

        headers = get_github_headers()
        # Request text-match for highlighted snippets
        headers["Accept"] = "application/vnd.github.v3.text-match+json"

        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code == 401:
            return {"error": "GitHub authentication failed", "files_matched": 0, "results": []}
        elif resp.status_code == 403:
            return {"error": "GitHub rate limit exceeded or repo access denied", "files_matched": 0, "results": []}
        elif resp.status_code != 200:
            return {"error": f"GitHub API error ({resp.status_code}): {resp.text[:200]}", "files_matched": 0, "results": []}

        data = resp.json()
        items = data.get("items", [])

        results = []
        for item in items[:5]:  # Limit to 5 files
            file_result = {
                "file": item.get("path", ""),
                "html_url": item.get("html_url", ""),
                "matches": []
            }

            # Extract text matches if available
            for match in item.get("text_matches", [])[:3]:  # Limit matches per file
                file_result["matches"].append({
                    "fragment": match.get("fragment", ""),
                    "property": match.get("property", "")
                })

            results.append(file_result)

        logger.info(f"GitHub search found {len(items)} files matching '{query}'")
        return {
            "files_searched": data.get("total_count", 0),
            "files_matched": len(items),
            "results": results
        }

    except Exception as e:
        logger.error(f"GitHub search error: {e}")
        return {"error": str(e), "files_matched": 0, "results": []}


def get_file(file_path: str) -> dict:
    """Get file contents from GitHub repository."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}"
        resp = requests.get(url, headers=get_github_headers(), timeout=10)

        if resp.status_code == 404:
            return {"error": f"File not found: {file_path}"}
        elif resp.status_code != 200:
            return {"error": f"GitHub API error ({resp.status_code})"}

        data = resp.json()

        # Decode base64 content
        content_b64 = data.get("content", "")
        try:
            content = base64.b64decode(content_b64).decode("utf-8")
        except Exception:
            return {"error": "Failed to decode file content"}

        line_count = len(content.split("\n"))

        logger.info(f"Retrieved file: {file_path} ({line_count} lines)")
        return {
            "file_path": file_path,
            "content": content,
            "line_count": line_count,
            "html_url": data.get("html_url", ""),
            "sha": data.get("sha", "")
        }

    except Exception as e:
        logger.error(f"GitHub file error: {e}")
        return {"error": str(e)}


def list_sql_files(directory: str = "sql") -> dict:
    """List SQL files in repository directory."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{directory}"
        resp = requests.get(url, headers=get_github_headers(), timeout=10)

        if resp.status_code == 404:
            return {"error": f"Directory not found: {directory}", "files_by_directory": {}}
        elif resp.status_code != 200:
            return {"error": f"GitHub API error ({resp.status_code})", "files_by_directory": {}}

        items = resp.json()
        if not isinstance(items, list):
            return {"error": "Unexpected response format", "files_by_directory": {}}

        files_by_dir = {}
        total_files = 0

        for item in items:
            item_type = item.get("type", "")
            item_name = item.get("name", "")
            item_path = item.get("path", "")

            if item_type == "file" and item_name.endswith(".sql"):
                dir_name = directory
                if dir_name not in files_by_dir:
                    files_by_dir[dir_name] = []
                files_by_dir[dir_name].append(item_name)
                total_files += 1

            elif item_type == "dir":
                # Recursively list subdirectories
                sub_result = list_sql_files(item_path)
                if "files_by_directory" in sub_result:
                    for sub_dir, sub_files in sub_result["files_by_directory"].items():
                        files_by_dir[sub_dir] = sub_files
                        total_files += len(sub_files)

        logger.info(f"Listed {total_files} SQL files in {directory}")
        return {
            "total_files": total_files,
            "files_by_directory": files_by_dir
        }

    except Exception as e:
        logger.error(f"GitHub list error: {e}")
        return {"error": str(e), "files_by_directory": {}}

# =============================================================================
# Tool Dispatcher
# =============================================================================

def dispatch_tool(tool_name: str, args: dict) -> dict:
    """Dispatch a tool call with proper token handling.

    Databricks tools automatically extract the user token from the request.
    GitHub tools use the server's configured token.

    Args:
        tool_name: Name of the tool to execute
        args: Tool arguments from MCP request

    Returns:
        dict: Tool result
    """
    # Databricks tools - use user token from request headers
    if tool_name == "execute_sql":
        return execute_sql(args.get("query", ""))
    elif tool_name == "search_patterns":
        return search_patterns(args.get("query", ""))
    elif tool_name == "get_table_schema":
        return get_table_schema(args.get("table_name", ""))

    # GitHub tools - use server's configured token
    elif tool_name == "search_code":
        return search_code(args.get("query", ""))
    elif tool_name == "get_file":
        return get_file(args.get("file_path", ""))
    elif tool_name == "list_sql_files":
        return list_sql_files(args.get("directory", "sql"))

    else:
        return {"error": f"Unknown tool: {tool_name}"}

# =============================================================================
# MCP Endpoints
# =============================================================================

@app.route("/mcp", methods=["POST"])
@require_auth
def mcp_endpoint():
    """Main MCP endpoint - handles all JSON-RPC requests.

    MCP Protocol Methods:
    - initialize: Exchange capabilities
    - notifications/initialized: Client ready
    - tools/list: Return available tools
    - tools/call: Execute a tool
    """
    try:
        data = request.get_json()
        if not data:
            return mcp_error(None, -32700, "Parse error: Invalid JSON")

        request_id = data.get("id")
        method = data.get("method", "")
        params = data.get("params", {})

        logger.info(f"MCP request: {method} (id={request_id})")

        # =====================================================================
        # MCP: initialize
        # =====================================================================
        if method == "initialize":
            client_info = params.get("clientInfo", {})
            logger.info(f"Client connecting: {client_info.get('name', 'unknown')}")

            return mcp_response(request_id, {
                "protocolVersion": MCP_VERSION,
                "serverInfo": SERVER_INFO,
                "capabilities": {
                    "tools": {"listChanged": False}
                }
            })

        # =====================================================================
        # MCP: notifications/initialized
        # =====================================================================
        elif method == "notifications/initialized":
            logger.info("Client initialization complete")
            return mcp_response(request_id, {})

        # =====================================================================
        # MCP: tools/list
        # =====================================================================
        elif method == "tools/list":
            logger.info(f"Returning {len(TOOLS)} tools")
            return mcp_response(request_id, {"tools": TOOLS})

        # =====================================================================
        # MCP: tools/call
        # =====================================================================
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            # Check if tool exists
            valid_tools = ["execute_sql", "search_patterns", "get_table_schema",
                          "search_code", "get_file", "list_sql_files"]
            if tool_name not in valid_tools:
                return mcp_error(request_id, -32601, f"Unknown tool: {tool_name}")

            # Log token status for debugging
            user_token_header = request.headers.get("X-User-Token", "")
            has_user_token = bool(user_token_header)
            logger.info(f"Executing tool: {tool_name} (user_token={has_user_token})")

            start_time = time.time()

            # Execute the tool (Databricks tools will extract user token from request)
            result = dispatch_tool(tool_name, tool_args)

            duration_ms = (time.time() - start_time) * 1000
            logger.info(f"Tool {tool_name} completed in {duration_ms:.0f}ms")

            # Log to Galileo for observability
            # Session ID can be passed via X-Session-ID header for trace grouping
            session_id = request.headers.get("X-Session-ID")
            error_msg = result.get("error") if isinstance(result, dict) else None

            log_tool_span(
                tool_name=tool_name,
                input_args=tool_args,
                output=result,
                duration_ms=duration_ms,
                session_id=session_id,
                error=error_msg
            )

            return mcp_response(request_id, tool_result(result))

        # =====================================================================
        # Unknown method
        # =====================================================================
        else:
            return mcp_error(request_id, -32601, f"Unknown method: {method}")

    except Exception as e:
        logger.exception(f"MCP endpoint error: {e}")
        return mcp_error(None, -32603, f"Internal error: {str(e)}")

# =============================================================================
# Health & Info Endpoints
# =============================================================================

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint for monitoring."""
    checks = {
        "databricks_host_configured": bool(DATABRICKS_HOST),
        "sql_warehouse_configured": bool(SQL_WAREHOUSE_ID),
        "vector_search_configured": bool(VS_INDEX),
        "github_configured": bool(GITHUB_TOKEN),
        "oauth_enabled": bool(ALLOWED_SP_APP_ID),
        "fallback_token_configured": bool(DATABRICKS_FALLBACK_TOKEN),
        "galileo_enabled": GALILEO_ENABLED
    }

    # Overall status
    required_checks = ["databricks_host_configured", "sql_warehouse_configured"]
    status = "healthy" if all(checks[c] for c in required_checks) else "degraded"

    return jsonify({
        "status": status,
        "checks": checks,
        "server": SERVER_INFO,
        "auth_model": {
            "type": "OAuth M2M + User Token Pass-through",
            "app_auth": {
                "method": "OAuth M2M (Service Principal)",
                "header": "Authorization: Bearer <sp_oauth_token>",
                "validation": "Token verified against Databricks API",
                "allowed_sp": ALLOWED_SP_APP_ID[:8] + "..." if ALLOWED_SP_APP_ID else "NOT CONFIGURED (dev mode)"
            },
            "data_auth": {
                "method": "User Token Pass-through",
                "header": "X-User-Token: <user_databricks_oauth_token>",
                "effect": "Unity Catalog enforces per-user permissions"
            }
        }
    })


@app.route("/", methods=["GET"])
def root():
    """Root endpoint - server info."""
    return jsonify({
        "name": "DataScope MCP Server",
        "version": SERVER_INFO["version"],
        "description": "Single gateway MCP server for DataScope agent tools",
        "protocol": "MCP (Model Context Protocol)",
        "protocol_version": MCP_VERSION,
        "endpoints": {
            "/": "Server info (this page)",
            "/health": "Health check",
            "/mcp": "MCP JSON-RPC endpoint (POST)"
        },
        "tools": [t["name"] for t in TOOLS],
        "authentication": {
            "model": "OAuth M2M + User Token Pass-through",
            "headers": {
                "Authorization": "Bearer <sp_oauth_token> (M2M OAuth from Vercel SP)",
                "X-User-Token": "<user_databricks_oauth_token> (for per-user data access)"
            },
            "note": "Get SP OAuth token via POST to Databricks /oidc/oauth2/token"
        }
    })


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))

    # Log configuration on startup
    logger.info("=" * 60)
    logger.info("DataScope MCP Server v3.1 - OAuth Auth")
    logger.info("=" * 60)
    logger.info(f"Databricks Host: {DATABRICKS_HOST[:30]}..." if DATABRICKS_HOST else "Databricks: NOT CONFIGURED")
    logger.info(f"SQL Warehouse: {SQL_WAREHOUSE_ID}" if SQL_WAREHOUSE_ID else "SQL Warehouse: NOT CONFIGURED")
    logger.info(f"Vector Search: {VS_INDEX}" if VS_INDEX else "Vector Search: NOT CONFIGURED")
    logger.info(f"GitHub: {GITHUB_OWNER}/{GITHUB_REPO}" if GITHUB_TOKEN else "GitHub: NOT CONFIGURED")
    logger.info("-" * 60)
    logger.info("Authentication Model: OAuth M2M + User Token")
    logger.info(f"  App Auth: {'OAuth (SP: ' + ALLOWED_SP_APP_ID[:8] + '...)' if ALLOWED_SP_APP_ID else 'DISABLED (dev mode)'}")
    logger.info(f"  Data Auth: User token via X-User-Token header")
    logger.info(f"  Fallback Token: {'CONFIGURED' if DATABRICKS_FALLBACK_TOKEN else 'NOT SET'}")
    logger.info("-" * 60)
    logger.info(f"Tools available: {[t['name'] for t in TOOLS]}")
    logger.info("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=False)
