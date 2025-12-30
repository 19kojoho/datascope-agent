import http.server
import socketserver
import json
import os
import requests
import base64

PORT = 8000
GITHUB_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
REPO = os.environ.get("GITHUB_REPO", "19kojoho/novatech-transformations")
GITHUB_API = "https://api.github.com"

def github_headers():
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers

def search_code(query, ext="sql"):
    url = f"{GITHUB_API}/search/code?q={query}+repo:{REPO}+extension:{ext}"
    resp = requests.get(url, headers=github_headers())
    if resp.status_code != 200:
        return {"error": resp.text}

    results = []
    for item in resp.json().get("items", [])[:5]:
        file_url = item.get("url", "")
        file_resp = requests.get(file_url, headers=github_headers())
        if file_resp.status_code == 200:
            content = base64.b64decode(file_resp.json().get("content", "")).decode("utf-8")
            lines = content.split("\n")
            matches = []
            for i, line in enumerate(lines):
                if query.lower() in line.lower():
                    start = max(0, i-2)
                    end = min(len(lines), i+3)
                    context = "\n".join(f"{j+1}: {lines[j]}" for j in range(start, end))
                    matches.append({"line": i+1, "context": context})
            if matches:
                results.append({"file": item.get("path"), "matches": matches[:3]})
    return {"query": query, "results": results}

def get_file(path):
    url = f"{GITHUB_API}/repos/{REPO}/contents/{path}"
    resp = requests.get(url, headers=github_headers())
    if resp.status_code != 200:
        return {"error": resp.text}
    content = base64.b64decode(resp.json().get("content", "")).decode("utf-8")
    return {"path": path, "content": content}

class Handler(http.server.BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self.send_json({"status": "healthy", "service": "github-code-search"})
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/search":
            result = search_code(body.get("query", ""), body.get("file_extension", "sql").lstrip("."))
            self.send_json(result)
        elif self.path == "/file":
            result = get_file(body.get("file_path", ""))
            self.send_json(result)
        else:
            self.send_json({"error": "Not found"}, 404)

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving on port {PORT}")
        httpd.serve_forever()
