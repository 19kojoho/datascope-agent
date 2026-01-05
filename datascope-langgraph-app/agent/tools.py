"""DataScope Agent Tools.

Production-grade tools with error handling, logging, and timeouts.
These tools are used by the LangGraph ReAct agent to investigate data issues.

The search_code tool uses MCP (Model Context Protocol) to communicate with
the GitHub MCP Server for code search capabilities.
"""

import json
import logging
import requests
from typing import Annotated, Optional, Any
from langchain_core.tools import tool

from .config import get_config

logger = logging.getLogger(__name__)

# Timeouts
SQL_TIMEOUT = 30  # seconds
CODE_SEARCH_TIMEOUT = 30  # seconds
PATTERN_SEARCH_TIMEOUT = 15  # seconds
MCP_TIMEOUT = 30  # seconds

# Limits
MAX_ROWS = 15  # Maximum rows to return from SQL queries

# MCP request ID counter
_mcp_request_id = 0


def get_next_mcp_id() -> int:
    """Get next MCP request ID."""
    global _mcp_request_id
    _mcp_request_id += 1
    return _mcp_request_id


class MCPClient:
    """Simple MCP client for communicating with MCP servers.

    Implements the Model Context Protocol (JSON-RPC 2.0 over HTTP).
    For Databricks Apps, includes workspace authentication.
    """

    def __init__(self, server_url: str, auth_token: Optional[str] = None, timeout: int = MCP_TIMEOUT):
        self.server_url = server_url.rstrip('/')
        self.mcp_endpoint = f"{self.server_url}/mcp"
        self.auth_token = auth_token
        self.timeout = timeout
        self._initialized = False
        self._tools_cache: Optional[list] = None

    def _get_headers(self) -> dict:
        """Get HTTP headers including authentication if available."""
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _send_request(self, method: str, params: dict = None) -> dict:
        """Send a JSON-RPC request to the MCP server."""
        request = {
            "jsonrpc": "2.0",
            "id": get_next_mcp_id(),
            "method": method,
            "params": params or {}
        }

        logger.debug(f"MCP request: {method}")

        resp = requests.post(
            self.mcp_endpoint,
            json=request,
            headers=self._get_headers(),
            timeout=self.timeout
        )

        if resp.status_code != 200:
            raise Exception(f"MCP server error: {resp.status_code} - {resp.text[:200]}")

        result = resp.json()

        if "error" in result:
            error = result["error"]
            raise Exception(f"MCP error {error.get('code', '?')}: {error.get('message', 'Unknown')}")

        return result.get("result", {})

    def initialize(self) -> dict:
        """Initialize the MCP connection."""
        if self._initialized:
            return {}

        result = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {
                "name": "datascope-agent",
                "version": "1.0.0"
            },
            "capabilities": {}
        })

        self._initialized = True
        logger.info(f"MCP initialized with server: {result.get('serverInfo', {}).get('name', 'unknown')}")

        # Send initialized notification (no response expected)
        try:
            requests.post(
                self.mcp_endpoint,
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {}
                },
                headers=self._get_headers(),
                timeout=5
            )
        except:
            pass  # Notifications don't require response

        return result

    def list_tools(self) -> list:
        """List available tools from the MCP server."""
        if self._tools_cache is not None:
            return self._tools_cache

        if not self._initialized:
            self.initialize()

        result = self._send_request("tools/list")
        self._tools_cache = result.get("tools", [])

        logger.info(f"MCP tools available: {[t['name'] for t in self._tools_cache]}")
        return self._tools_cache

    def call_tool(self, tool_name: str, arguments: dict = None) -> Any:
        """Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Tool result (parsed from JSON if text content)
        """
        if not self._initialized:
            self.initialize()

        logger.info(f"MCP tool call: {tool_name}")

        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments or {}
        })

        # Extract content from MCP response
        content_list = result.get("content", [])
        if not content_list:
            return {}

        # Get first text content
        for content in content_list:
            if content.get("type") == "text":
                text = content.get("text", "{}")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}

        return {}


# Global MCP client instance (lazy initialization)
_mcp_client: Optional[MCPClient] = None


def get_mcp_client() -> MCPClient:
    """Get or create the MCP client instance.

    Uses Databricks token for authentication when calling MCP servers
    deployed on Databricks Apps.
    """
    global _mcp_client

    if _mcp_client is None:
        config = get_config()
        if not config.github_mcp_url:
            raise Exception("GITHUB_MCP_APP_URL not configured")
        # Use Databricks token for authentication to Databricks Apps
        _mcp_client = MCPClient(
            server_url=config.github_mcp_url,
            auth_token=config.databricks_token
        )

    return _mcp_client


@tool
def search_patterns(
    query: Annotated[str, "Description of the data issue to find similar patterns for"]
) -> str:
    """Search for similar past data quality issues using Vector Search.

    Use this FIRST before investigating to get context on common patterns
    and suggested SQL queries for the investigation.

    Args:
        query: Description of the data issue (e.g., "NULL churn_risk values")

    Returns:
        Similar patterns with symptoms, root causes, and suggested SQL
    """
    config = get_config()
    logger.info(f"Searching patterns for: {query[:100]}")

    try:
        url = f"{config.databricks_host}/api/2.0/vector-search/indexes/{config.vs_index}/query"
        headers = config.get_auth_headers()

        resp = requests.post(
            url,
            headers=headers,
            json={
                "query_text": query,
                "columns": ["pattern_id", "title", "symptoms", "root_cause", "resolution", "investigation_sql"],
                "num_results": 3
            },
            timeout=PATTERN_SEARCH_TIMEOUT
        )

        if resp.status_code != 200:
            logger.warning(f"Vector search returned {resp.status_code}: {resp.text[:200]}")
            return f"Pattern search unavailable (status {resp.status_code}). Proceeding with direct investigation."

        data = resp.json()
        results = data.get("result", {}).get("data_array", [])

        if not results:
            return "No similar patterns found in history. This may be a new type of issue."

        # Format results
        output = ["**Similar Past Issues Found:**\n"]
        for row in results:
            if len(row) >= 6:
                pattern_id, title, symptoms, root_cause, resolution, investigation_sql = row[:6]
                output.append(f"### {pattern_id}: {title}")
                output.append(f"**Symptoms:** {symptoms[:300]}...")
                output.append(f"**Root Cause:** {root_cause}")
                output.append(f"**Resolution:** {resolution}")
                if investigation_sql:
                    output.append(f"**Suggested SQL:** `{investigation_sql[:150]}...`")
                output.append("")

        logger.info(f"Found {len(results)} matching patterns")
        return "\n".join(output)

    except requests.exceptions.Timeout:
        logger.error("Pattern search timed out")
        return "Pattern search timed out. Proceeding with direct investigation."
    except Exception as e:
        logger.error(f"Pattern search error: {e}")
        return f"Pattern search error: {str(e)}. Proceeding with direct investigation."


@tool
def execute_sql(
    query: Annotated[str, "SQL query to execute against Databricks SQL Warehouse"]
) -> str:
    """Execute a SQL query to investigate data issues.

    Use this to:
    - Count affected records: SELECT COUNT(*) FROM table WHERE condition
    - Sample data: SELECT * FROM table WHERE condition LIMIT 10
    - Compare values between tables or layers (bronze/silver/gold)
    - Check for NULL values or duplicates
    - Get table schema: DESCRIBE novatech.gold.table_name
    - List tables: SHOW TABLES IN novatech.gold

    Args:
        query: The SQL query to execute (SELECT, DESCRIBE, or SHOW)

    Returns:
        Query results as a markdown table, or error message
    """
    config = get_config()
    logger.info(f"Executing SQL: {query[:200]}")

    # Basic query validation
    query_upper = query.upper().strip()

    # Allow safe read-only commands
    allowed_prefixes = ["SELECT", "DESCRIBE", "DESC", "SHOW"]
    if not any(query_upper.startswith(prefix) for prefix in allowed_prefixes):
        return "Error: Only SELECT, DESCRIBE, and SHOW queries are allowed for safety."

    if any(kw in query_upper for kw in ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE"]):
        return "Error: Only read-only SELECT queries are allowed."

    try:
        url = f"{config.databricks_host}/api/2.0/sql/statements"
        headers = config.get_auth_headers()

        resp = requests.post(
            url,
            headers=headers,
            json={
                "warehouse_id": config.sql_warehouse_id,
                "statement": query,
                "wait_timeout": f"{SQL_TIMEOUT}s"
            },
            timeout=SQL_TIMEOUT + 5  # Add buffer for HTTP overhead
        )

        if resp.status_code != 200:
            logger.error(f"SQL execution failed: {resp.status_code} - {resp.text[:200]}")
            return f"SQL Error (status {resp.status_code}): {resp.text[:200]}"

        data = resp.json()
        status = data.get("status", {}).get("state", "")

        if status == "SUCCEEDED":
            result = data.get("result", {})
            columns = [c["name"] for c in data.get("manifest", {}).get("schema", {}).get("columns", [])]
            rows = result.get("data_array", [])[:MAX_ROWS]

            if not rows:
                return "Query executed successfully but returned no results."

            # Format as markdown table
            header = "| " + " | ".join(columns) + " |"
            separator = "| " + " | ".join(["---"] * len(columns)) + " |"
            body_lines = []
            for row in rows:
                formatted_row = " | ".join(str(v) if v is not None else "NULL" for v in row)
                body_lines.append(f"| {formatted_row} |")

            total_count = result.get("row_count", len(rows))
            footer = f"\n*Showing {len(rows)} of {total_count} rows*" if total_count > MAX_ROWS else ""

            logger.info(f"SQL returned {len(rows)} rows")
            return f"```\n{header}\n{separator}\n" + "\n".join(body_lines) + f"\n```{footer}"

        elif status == "FAILED":
            error_msg = data.get("status", {}).get("error", {}).get("message", "Unknown error")
            logger.error(f"SQL query failed: {error_msg}")
            return f"Query error: {error_msg}"

        else:
            return f"Query status: {status}. May need more time or warehouse is starting."

    except requests.exceptions.Timeout:
        logger.error("SQL query timed out")
        return f"Query timed out after {SQL_TIMEOUT} seconds. Try a simpler query or check warehouse status."
    except Exception as e:
        logger.error(f"SQL execution error: {e}")
        return f"SQL execution error: {str(e)}"


@tool
def search_code(
    term: Annotated[str, "Search term to look for in SQL transformation files"]
) -> str:
    """Search SQL transformation code to find the source of bugs.

    Uses MCP (Model Context Protocol) to communicate with the GitHub code search server.
    This tool searches the novatech-transformations repository for SQL files
    containing the specified term.

    Use this to find the transformation logic that creates a specific column
    or table. This helps identify WHERE the bug is in the code.

    Args:
        term: Term to search for (e.g., column name like 'churn_risk', or SQL keyword)

    Returns:
        Matching code snippets with file paths and line context
    """
    logger.info(f"Searching code via MCP for: {term}")

    try:
        # Get MCP client
        mcp_client = get_mcp_client()

        # Call search_code tool via MCP
        result = mcp_client.call_tool("search_code", {"query": term})

        # Check for errors
        if "error" in result:
            return f"Code search error: {result['error']}"

        # Format results
        files_matched = result.get("files_matched", 0)
        if files_matched == 0:
            return f"No code found matching '{term}'. Try different search terms."

        output = [f"**Code Search Results for '{term}':**"]
        output.append(f"*Searched {result.get('files_searched', '?')} files, found matches in {files_matched} files*\n")

        for r in result.get("results", [])[:3]:  # Limit to 3 files
            file_path = r.get("file", "unknown")
            output.append(f"### File: `{file_path}`")

            for m in r.get("matches", [])[:2]:  # Limit to 2 matches per file
                line_num = m.get("line_number", "?")
                context = m.get("context", "")
                output.append(f"**Line {line_num}:**")
                output.append(f"```sql\n{context}\n```")
            output.append("")

        logger.info(f"MCP search found code in {files_matched} files")
        return "\n".join(output)

    except Exception as e:
        logger.error(f"MCP code search error: {e}")

        # Fall back to REST API if MCP fails
        logger.info("Falling back to REST API for code search")
        return _search_code_rest_fallback(term)


def _search_code_rest_fallback(term: str) -> str:
    """Fallback to REST API if MCP fails."""
    config = get_config()

    if not config.github_mcp_url:
        return "Code search not configured (GITHUB_MCP_APP_URL not set)."

    try:
        url = f"{config.github_mcp_url.rstrip('/')}/search"

        resp = requests.post(
            url,
            json={"query": term, "file_extension": "sql"},
            headers={"Content-Type": "application/json"},
            timeout=CODE_SEARCH_TIMEOUT
        )

        if resp.status_code != 200:
            return f"Code search unavailable (status {resp.status_code})."

        data = resp.json()
        results = data.get("results", [])

        if not results:
            return f"No code found matching '{term}'."

        output = [f"**Code Search Results for '{term}':** (via REST fallback)\n"]
        for r in results[:3]:
            output.append(f"### File: `{r.get('file', 'unknown')}`")
            for m in r.get("matches", [])[:2]:
                output.append(f"**Line {m.get('line', '?')}:**")
                output.append(f"```sql\n{m.get('context', '')}\n```")
            output.append("")

        return "\n".join(output)

    except Exception as e:
        return f"Code search error: {str(e)}"


@tool
def get_transformation_file(
    file_path: Annotated[str, "Path to the SQL transformation file (e.g., 'sql/gold/churn_predictions.sql')"]
) -> str:
    """Get the full contents of a SQL transformation file from GitHub.

    Uses MCP (Model Context Protocol) to retrieve the complete file.
    Use this after search_code to see the full transformation logic.

    Args:
        file_path: Path to file in the repository (e.g., 'sql/gold/churn_predictions.sql')

    Returns:
        Complete file contents with line numbers
    """
    logger.info(f"Getting transformation file via MCP: {file_path}")

    try:
        mcp_client = get_mcp_client()
        result = mcp_client.call_tool("get_file", {"file_path": file_path})

        if "error" in result:
            return f"Error: {result['error']}"

        content = result.get("content", "")
        line_count = result.get("line_count", 0)
        html_url = result.get("html_url", "")

        # Add line numbers
        lines = content.split("\n")
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))

        output = [f"**File: `{file_path}`** ({line_count} lines)"]
        if html_url:
            output.append(f"*GitHub: {html_url}*")
        output.append("")
        output.append(f"```sql\n{numbered}\n```")

        return "\n".join(output)

    except Exception as e:
        logger.error(f"Error getting file: {e}")
        return f"Error retrieving file: {str(e)}"


@tool
def list_transformation_files(
    directory: Annotated[str, "Directory to list (default: 'sql')"] = "sql"
) -> str:
    """List all SQL transformation files in the repository.

    Uses MCP (Model Context Protocol) to discover available files.
    Use this to see what transformation files exist before searching.

    Args:
        directory: Starting directory to list (default: 'sql')

    Returns:
        List of available SQL files organized by directory
    """
    logger.info(f"Listing transformation files via MCP: {directory}")

    try:
        mcp_client = get_mcp_client()
        result = mcp_client.call_tool("list_sql_files", {"directory": directory})

        if "error" in result:
            return f"Error: {result['error']}"

        total = result.get("total_files", 0)
        files_by_dir = result.get("files_by_directory", {})

        output = [f"**SQL Transformation Files** ({total} files)\n"]

        for dir_name, files in sorted(files_by_dir.items()):
            output.append(f"### `{dir_name}/`")
            for f in files:
                output.append(f"- {f}")
            output.append("")

        return "\n".join(output)

    except Exception as e:
        logger.error(f"Error listing files: {e}")
        return f"Error listing files: {str(e)}"


# Export all tools for use in agent
def get_tools():
    """Get all available tools for the agent."""
    return [
        search_patterns,
        execute_sql,
        search_code,
        get_transformation_file,
        list_transformation_files
    ]
