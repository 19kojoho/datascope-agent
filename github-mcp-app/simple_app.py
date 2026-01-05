"""GitHub Code Search Server.

A REST API server for searching SQL transformation code in GitHub.
Uses GitHub Contents API (not Search API) to scan files directly,
which works for any repository regardless of indexing status.
"""

import http.server
import socketserver
import json
import os
import requests
import base64
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8000))
GITHUB_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
REPO = os.environ.get("GITHUB_REPO", "19kojoho/novatech-transformations")
GITHUB_API = "https://api.github.com"


def github_headers():
    """Get GitHub API headers with authentication."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def get_all_sql_files(path="sql"):
    """Recursively get all SQL files from a directory in the repo."""
    files = []
    url = f"{GITHUB_API}/repos/{REPO}/contents/{path}"

    try:
        resp = requests.get(url, headers=github_headers(), timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Failed to list {path}: {resp.status_code}")
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
        logger.error(f"Error listing files in {path}: {e}")

    return files


def search_code(query, ext="sql"):
    """Search for code by scanning files directly (GitHub Search API doesn't index small repos)."""
    logger.info(f"Searching for '{query}' in {REPO}")

    results = []
    all_files = get_all_sql_files("sql")
    logger.info(f"Found {len(all_files)} SQL files to search")

    for file_info in all_files:
        try:
            # Get file content
            file_url = file_info.get("url", "")
            if not file_url:
                continue

            file_resp = requests.get(file_url, headers=github_headers(), timeout=10)
            if file_resp.status_code != 200:
                continue

            content = base64.b64decode(file_resp.json().get("content", "")).decode("utf-8")
            lines = content.split("\n")

            # Search for query in file
            matches = []
            for i, line in enumerate(lines):
                if query.lower() in line.lower():
                    # Get context (3 lines before, 2 after)
                    start = max(0, i - 3)
                    end = min(len(lines), i + 3)
                    context_lines = []
                    for j in range(start, end):
                        prefix = ">>> " if j == i else "    "
                        context_lines.append(f"{j+1:4d} {prefix}{lines[j]}")

                    matches.append({
                        "line": i + 1,
                        "context": "\n".join(context_lines)
                    })

            if matches:
                results.append({
                    "file": file_info["path"],
                    "matches": matches[:3]  # Limit to 3 matches per file
                })
                logger.info(f"Found {len(matches)} matches in {file_info['path']}")

        except Exception as e:
            logger.error(f"Error processing {file_info.get('path', 'unknown')}: {e}")
            continue

    return {
        "query": query,
        "repository": REPO,
        "total_files_searched": len(all_files),
        "files_with_matches": len(results),
        "results": results[:5]  # Limit to 5 files
    }


def get_file(path):
    """Get full file contents from GitHub."""
    url = f"{GITHUB_API}/repos/{REPO}/contents/{path}"
    resp = requests.get(url, headers=github_headers(), timeout=10)

    if resp.status_code != 200:
        return {"error": resp.text, "status_code": resp.status_code}

    data = resp.json()
    content = base64.b64decode(data.get("content", "")).decode("utf-8")

    return {
        "path": path,
        "content": content,
        "size_bytes": data.get("size", 0),
        "html_url": data.get("html_url", "")
    }


def list_files(directory="sql"):
    """List all SQL files in a directory."""
    all_files = get_all_sql_files(directory)

    # Organize by subdirectory
    files_by_dir = {}
    for f in all_files:
        dir_name = os.path.dirname(f["path"])
        if dir_name not in files_by_dir:
            files_by_dir[dir_name] = []
        files_by_dir[dir_name].append({
            "name": f["name"],
            "path": f["path"],
            "size": f["size"]
        })

    return {
        "repository": REPO,
        "directory": directory,
        "files_by_directory": files_by_dir,
        "total_files": len(all_files)
    }


class Handler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for the code search server."""

    def log_message(self, format, *args):
        """Override to use Python logging."""
        logger.info("%s - %s", self.address_string(), format % args)

    def send_json(self, data, status=200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/" or self.path == "/health":
            self.send_json({
                "status": "healthy",
                "service": "github-code-search",
                "repository": REPO,
                "token_configured": bool(GITHUB_TOKEN)
            })
        elif self.path == "/list":
            result = list_files()
            self.send_json(result)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        """Handle POST requests."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            if self.path == "/search":
                query = body.get("query", "")
                ext = body.get("file_extension", "sql").lstrip(".")
                if not query:
                    self.send_json({"error": "Missing 'query' parameter"}, 400)
                    return
                result = search_code(query, ext)
                self.send_json(result)

            elif self.path == "/file":
                file_path = body.get("file_path", "")
                if not file_path:
                    self.send_json({"error": "Missing 'file_path' parameter"}, 400)
                    return
                result = get_file(file_path)
                self.send_json(result)

            elif self.path == "/list":
                directory = body.get("directory", "sql")
                result = list_files(directory)
                self.send_json(result)

            else:
                self.send_json({"error": "Not found"}, 404)

        except json.JSONDecodeError as e:
            self.send_json({"error": f"Invalid JSON: {e}"}, 400)
        except Exception as e:
            logger.error(f"Error handling request: {e}")
            self.send_json({"error": str(e)}, 500)


if __name__ == "__main__":
    logger.info(f"Starting GitHub Code Search Server on port {PORT}")
    logger.info(f"Repository: {REPO}")
    logger.info(f"Token configured: {'Yes' if GITHUB_TOKEN else 'No'}")

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        logger.info(f"Server running at http://0.0.0.0:{PORT}")
        httpd.serve_forever()
