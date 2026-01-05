"""GitHub Code Search MCP Server - Manual Implementation.

Implements MCP (Model Context Protocol) from scratch without external MCP libraries.
This gives us full control and avoids dependency issues on Databricks Apps.

MCP Protocol: JSON-RPC 2.0 over HTTP
- POST /mcp - Main MCP endpoint for all requests
- GET /mcp - SSE endpoint for server-to-client messages (optional)
- GET /health - Health check
"""

import os
import sys
import json
import base64
import logging
from typing import Any
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

logger.info("=" * 60)
logger.info("MCP SERVER - Manual Implementation")
logger.info("=" * 60)
logger.info(f"Python version: {sys.version}")

import requests

# Configuration
GITHUB_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
REPO = os.environ.get("GITHUB_REPO", "19kojoho/novatech-transformations")
GITHUB_API = "https://api.github.com"
PORT = int(os.environ.get("PORT", 8000))

logger.info(f"Repository: {REPO}")
logger.info(f"Token configured: {bool(GITHUB_TOKEN)}")
logger.info(f"Port: {PORT}")

# MCP Server Info
SERVER_INFO = {
    "name": "github-code-search",
    "version": "1.0.0",
    "protocolVersion": "2024-11-05"
}

# Tool Definitions (MCP format)
TOOLS = [
    {
        "name": "search_code",
        "description": "Search for code patterns in SQL transformation files. Scans all SQL files and returns matching lines with context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term (e.g., 'churn_risk', 'CASE WHEN')"
                },
                "file_extension": {
                    "type": "string",
                    "description": "File extension to filter",
                    "default": "sql"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_file",
        "description": "Get the full contents of a SQL transformation file.",
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
    {
        "name": "list_sql_files",
        "description": "List all SQL transformation files in the repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Starting directory",
                    "default": "sql"
                }
            }
        }
    }
]


# GitHub API helpers
def github_headers() -> dict:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def get_all_sql_files(path: str = "sql") -> list:
    files = []
    url = f"{GITHUB_API}/repos/{REPO}/contents/{path}"

    try:
        resp = requests.get(url, headers=github_headers(), timeout=10)
        if resp.status_code != 200:
            return files

        for item in resp.json():
            if item["type"] == "dir":
                files.extend(get_all_sql_files(item["path"]))
            elif item["type"] == "file" and item["name"].endswith(".sql"):
                files.append({
                    "path": item["path"],
                    "name": item["name"],
                    "url": item["url"],
                    "size": item.get("size", 0)
                })
    except Exception as e:
        logger.error(f"Error listing files: {e}")

    return files


def fetch_file_content(file_url: str) -> str:
    try:
        resp = requests.get(file_url, headers=github_headers(), timeout=10)
        if resp.status_code == 200:
            content_b64 = resp.json().get("content", "")
            return base64.b64decode(content_b64).decode("utf-8")
    except Exception as e:
        logger.error(f"Error fetching file: {e}")
    return ""


# Tool implementations
def tool_search_code(query: str, file_extension: str = "sql") -> dict:
    logger.info(f"search_code: query='{query}'")

    results = []
    all_files = get_all_sql_files("sql")

    for file_info in all_files:
        if not file_info["name"].endswith(f".{file_extension.lstrip('.')}"):
            continue

        content = fetch_file_content(file_info["url"])
        if not content:
            continue

        lines = content.split("\n")
        matches = []

        for i, line in enumerate(lines):
            if query.lower() in line.lower():
                start = max(0, i - 3)
                end = min(len(lines), i + 3)
                context = "\n".join(
                    f"{j+1:4d} {'>>>' if j == i else '   '} {lines[j]}"
                    for j in range(start, end)
                )
                matches.append({
                    "line_number": i + 1,
                    "context": context
                })

        if matches:
            results.append({
                "file": file_info["path"],
                "matches": matches[:3]
            })

    return {
        "query": query,
        "repository": REPO,
        "files_searched": len(all_files),
        "files_matched": len(results),
        "results": results[:5]
    }


def tool_get_file(file_path: str) -> dict:
    logger.info(f"get_file: path='{file_path}'")

    url = f"{GITHUB_API}/repos/{REPO}/contents/{file_path}"
    resp = requests.get(url, headers=github_headers(), timeout=10)

    if resp.status_code != 200:
        return {"error": f"File not found: {file_path}"}

    data = resp.json()
    content = base64.b64decode(data.get("content", "")).decode("utf-8")

    return {
        "path": file_path,
        "content": content,
        "line_count": len(content.split("\n")),
        "html_url": data.get("html_url", "")
    }


def tool_list_sql_files(directory: str = "sql") -> dict:
    logger.info(f"list_sql_files: directory='{directory}'")

    all_files = get_all_sql_files(directory)

    files_by_dir = {}
    for f in all_files:
        dir_name = os.path.dirname(f["path"])
        if dir_name not in files_by_dir:
            files_by_dir[dir_name] = []
        files_by_dir[dir_name].append(f["name"])

    return {
        "repository": REPO,
        "total_files": len(all_files),
        "files_by_directory": files_by_dir
    }


# Tool dispatcher
TOOL_HANDLERS = {
    "search_code": tool_search_code,
    "get_file": tool_get_file,
    "list_sql_files": tool_list_sql_files
}


def handle_mcp_request(request: dict) -> dict:
    """Handle an MCP JSON-RPC request and return a response."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    logger.info(f"MCP request: method={method}, id={req_id}")

    try:
        # Initialize
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": SERVER_INFO["protocolVersion"],
                    "serverInfo": {
                        "name": SERVER_INFO["name"],
                        "version": SERVER_INFO["version"]
                    },
                    "capabilities": {
                        "tools": {}
                    }
                }
            }

        # List tools
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": TOOLS
                }
            }

        # Call tool
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            if tool_name not in TOOL_HANDLERS:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unknown tool: {tool_name}"
                    }
                }

            handler = TOOL_HANDLERS[tool_name]
            result = handler(**tool_args)

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, indent=2)
                        }
                    ]
                }
            }

        # Notifications (no response needed)
        elif method == "notifications/initialized":
            return None

        # Unknown method
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }

    except Exception as e:
        logger.error(f"Error handling request: {e}")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32603,
                "message": str(e)
            }
        }


class MCPHandler(BaseHTTPRequestHandler):
    """HTTP handler for MCP requests."""

    def log_message(self, format, *args):
        logger.info("%s - %s", self.address_string(), format % args)

    def send_json(self, data: Any, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health" or self.path == "/":
            self.send_json({
                "status": "healthy",
                "server": SERVER_INFO,
                "mcp_endpoint": "/mcp",
                "tools": [t["name"] for t in TOOLS]
            })

        # Also support REST endpoints for backward compatibility
        elif self.path == "/list":
            result = tool_list_sql_files()
            self.send_json(result)

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()
            request = json.loads(body) if body else {}

            # MCP endpoint
            if self.path == "/mcp":
                response = handle_mcp_request(request)
                if response:
                    self.send_json(response)
                else:
                    self.send_response(202)
                    self.end_headers()

            # REST endpoint for backward compatibility with DataScope agent
            elif self.path == "/search":
                query = request.get("query", "")
                ext = request.get("file_extension", "sql")
                result = tool_search_code(query, ext)
                self.send_json(result)

            elif self.path == "/file":
                file_path = request.get("file_path", "")
                result = tool_get_file(file_path)
                self.send_json(result)

            else:
                self.send_json({"error": "Not found"}, 404)

        except json.JSONDecodeError as e:
            self.send_json({"error": f"Invalid JSON: {e}"}, 400)
        except Exception as e:
            logger.error(f"Error: {e}")
            self.send_json({"error": str(e)}, 500)


logger.info("✓ MCP Handler configured")
logger.info(f"Tools: {[t['name'] for t in TOOLS]}")
logger.info("=" * 60)
logger.info("MCP SERVER READY")
logger.info("=" * 60)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), MCPHandler)
    logger.info(f"Starting MCP server on http://0.0.0.0:{PORT}")
    logger.info(f"MCP endpoint: http://0.0.0.0:{PORT}/mcp")
    logger.info(f"Health check: http://0.0.0.0:{PORT}/health")
    server.serve_forever()
