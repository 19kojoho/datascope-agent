# MCP Development Journey: Building a Production MCP Server

This document captures my journey learning and implementing the Model Context Protocol (MCP) for the DataScope project. It's designed to demonstrate hands-on experience with MCP for AI engineering interviews.

---

## What is MCP?

**Model Context Protocol (MCP)** is an open standard created by Anthropic that enables AI models to securely connect to external data sources and tools. It's becoming the standard way for AI agents to interact with the world.

### Why MCP Matters for AI Engineers

1. **Industry Standard** - Created by Anthropic, adopted by the AI community
2. **Tool Ecosystems** - Build once, use with any MCP-compatible agent
3. **Security** - Standardized authentication and permission models
4. **Observability** - Built-in tracing and debugging capabilities

### MCP vs REST APIs

| Aspect | REST API | MCP |
|--------|----------|-----|
| Tool Discovery | Hardcoded | Dynamic via `tools/list` |
| Schema | OpenAPI (optional) | JSON Schema (required) |
| Protocol | HTTP verbs | JSON-RPC 2.0 |
| Transport | HTTP only | stdio, HTTP, SSE, WebSocket |
| Standardization | Per-API | Universal |

---

## Project Context

**Goal**: Build an MCP server that allows the DataScope AI agent to search SQL transformation code in GitHub.

**Use Case**: When investigating data quality issues, the agent needs to find the transformation logic that creates specific columns (e.g., find the CASE statement that computes `churn_risk`).

**Final Architecture**:
```
┌─────────────────────┐     MCP Protocol      ┌─────────────────────┐
│  DataScope Agent    │ <------------------> │  GitHub MCP Server  │
│  (LangGraph)        │     JSON-RPC 2.0      │  (Manual impl)      │
│  Databricks App     │     over HTTP         │  Databricks App     │
└─────────────────────┘                       └─────────────────────┘
                                                       │
                                                       │ GitHub API
                                                       ▼
                                              ┌─────────────────────┐
                                              │  GitHub Repository  │
                                              │  novatech-          │
                                              │  transformations    │
                                              └─────────────────────┘
```

---

## Development Timeline

### Attempt 1: REST API (Working but not MCP)

**Date**: December 31, 2024

**Approach**: Simple HTTP server with `/search` endpoint

**Code** (`simple_app.py`):
```python
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/search":
            result = search_code(body.get("query", ""))
            self.send_json(result)
```

**Problem**: GitHub's Search API doesn't index small repositories, returning 0 results.

**Fix**: Changed to use GitHub Contents API - scan files directly instead of relying on search index.

**Result**: ✅ Working, but not MCP - just a REST API.

**Learning**: Sometimes the simplest solution works, but it's not the standard approach.

---

### Attempt 2: FastMCP Server (Crashed on Databricks)

**Date**: December 31, 2024

**Approach**: Use FastMCP framework to create proper MCP server

**Code** (`mcp_server.py`):
```python
from fastmcp import FastMCP

mcp = FastMCP(
    name="github-code-search",
    version="1.0.0",
    description="MCP server for searching SQL transformation code"
)

@mcp.tool()
def search_code(query: str, file_extension: str = "sql") -> dict:
    """Search for code patterns in SQL transformation files."""
    # Implementation...

app = mcp.http_app()
```

**Deployment**:
```yaml
# app.yaml
command:
  - uvicorn
  - mcp_server:app
  - --host
  - "0.0.0.0"
  - --port
  - "8000"
```

**Result**: ❌ App crashed on Databricks Apps

**Error**: `Error: app crashed unexpectedly. Please check /logz for more details`

**Investigation**:
1. Couldn't access logs (PAT auth doesn't support `databricks apps logs`)
2. Tried official `mcp` SDK - same crash
3. Tried `fastmcp` standalone package - same crash

**Root Cause**: Databricks Apps environment couldn't install FastMCP dependencies (likely Python version or package conflicts). No way to see actual error without logs.

**Key Learning**: When you can't access logs, you need to find alternative approaches.

---

### Attempt 3: Manual MCP Implementation (SUCCESS! ✅)

**Date**: December 31, 2024

**Approach**: Implement MCP protocol from scratch - no external MCP libraries

**Key Insight**: MCP is just JSON-RPC 2.0 over HTTP. We can implement it ourselves!

**Code** (`mcp_server.py` - 424 lines):
```python
"""Manual MCP implementation - no external libraries needed."""

# MCP Server Info
SERVER_INFO = {
    "name": "github-code-search",
    "version": "1.0.0",
    "protocolVersion": "2024-11-05"
}

# Tool Definitions (MCP format with JSON Schema)
TOOLS = [
    {
        "name": "search_code",
        "description": "Search for code patterns in SQL files",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term"},
                "file_extension": {"type": "string", "default": "sql"}
            },
            "required": ["query"]
        }
    },
    # ... more tools
]

def handle_mcp_request(request: dict) -> dict:
    """Handle MCP JSON-RPC requests."""
    method = request.get("method", "")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": SERVER_INFO,
                "capabilities": {"tools": {}}
            }
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"tools": TOOLS}
        }

    elif method == "tools/call":
        tool_name = request["params"]["name"]
        tool_args = request["params"]["arguments"]
        result = TOOL_HANDLERS[tool_name](**tool_args)
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "content": [{"type": "text", "text": json.dumps(result)}]
            }
        }
```

**Deployment**:
```yaml
# app.yaml - Simple, no uvicorn needed
command: ['python', 'mcp_server.py']

env:
  - name: GITHUB_PERSONAL_ACCESS_TOKEN
    value: "github_pat_..."
```

**Requirements** (minimal):
```
requests>=2.31.0
```

**Result**: ✅ **DEPLOYED SUCCESSFULLY!**

```json
{
  "status": {
    "message": "App started successfully",
    "state": "SUCCEEDED"
  }
}
```

**Key Learnings**:
1. **Understanding the protocol > using a library** - By implementing MCP manually, I deeply understood how it works
2. **Fewer dependencies = more reliable deployments** - Only needed `requests`
3. **JSON-RPC 2.0 is simple** - Just method, params, id, and result/error
4. **Backward compatibility matters** - Server also supports REST endpoints for existing code

---

## Key MCP Concepts Learned

### 1. Transport Layers

MCP supports multiple transports:

| Transport | Use Case | Databricks Compatible |
|-----------|----------|----------------------|
| **stdio** | Local tools, Claude Desktop | ❌ No (requires subprocess) |
| **HTTP** | Remote servers, web apps | ✅ Yes |
| **SSE** | Server-sent events | ✅ Yes |
| **WebSocket** | Bidirectional streaming | ⚠️ Maybe |

For Databricks Apps, **HTTP transport is required**.

### 2. JSON-RPC 2.0 Protocol

All MCP communication uses JSON-RPC:

```json
// Request
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "search_code",
    "arguments": {"query": "churn_risk"}
  }
}

// Response
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [{"type": "text", "text": "Found in file..."}]
  }
}
```

### 3. MCP Methods Implemented

| Method | Purpose | When Called |
|--------|---------|-------------|
| `initialize` | Exchange capabilities | First request |
| `tools/list` | Discover available tools | Before using tools |
| `tools/call` | Execute a tool | When agent needs data |
| `notifications/initialized` | Acknowledge init | After initialize |

### 4. Tool Definition Schema

Each tool must define its inputs using JSON Schema:

```python
{
    "name": "search_code",
    "description": "Search for code patterns in SQL files",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term (e.g., 'churn_risk')"
            },
            "file_extension": {
                "type": "string",
                "description": "File extension to filter",
                "default": "sql"
            }
        },
        "required": ["query"]
    }
}
```

---

## Tools Implemented

### 1. search_code

**Purpose**: Find transformation SQL containing specific patterns

**MCP Call**:
```json
{
  "method": "tools/call",
  "params": {
    "name": "search_code",
    "arguments": {"query": "churn_risk"}
  }
}
```

**Output**:
```json
{
  "query": "churn_risk",
  "repository": "19kojoho/novatech-transformations",
  "files_matched": 1,
  "results": [
    {
      "file": "sql/gold/churn_predictions.sql",
      "matches": [
        {"line_number": 47, "context": "...CASE WHEN...churn_risk..."}
      ]
    }
  ]
}
```

### 2. get_file

**Purpose**: Retrieve full file contents for detailed analysis

### 3. list_sql_files

**Purpose**: Discover available transformation files

---

## Challenges Encountered

### Challenge 1: GitHub Search API Doesn't Index Small Repos

**Problem**: GitHub's `/search/code` API returned 0 results even though files existed.

**Solution**: Use Contents API to scan files directly:
```python
def get_all_sql_files(path="sql"):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    resp = requests.get(url, headers=headers)
    for item in resp.json():
        if item["type"] == "dir":
            files.extend(get_all_sql_files(item["path"]))
        elif item["name"].endswith(".sql"):
            files.append(item)
    return files
```

**Trade-off**: More API calls, but actually works for any repository.

### Challenge 2: FastMCP/MCP SDK Crashes on Databricks

**Problem**: Both `fastmcp` and official `mcp` SDK crashed on Databricks Apps.

**Investigation**:
- No log access with PAT authentication
- Added extensive startup logging - never got to see it
- Tried multiple package versions

**Solution**: Implement MCP protocol manually!

**Why This Was Better**:
1. Zero external MCP dependencies
2. Full control over the protocol
3. Easy to debug
4. Works on any Python 3.x environment

### Challenge 3: Understanding MCP Protocol

**Problem**: Limited documentation on implementing MCP from scratch.

**Solution**:
1. Read the [MCP specification](https://modelcontextprotocol.io/)
2. Studied JSON-RPC 2.0 standard
3. Tested with simple requests locally
4. Built up functionality incrementally

---

## Interview Talking Points

### 1. "Tell me about your experience with MCP"

> "I built a production MCP server for code search that's deployed on Databricks Apps. The interesting part was that the standard FastMCP library crashed in that environment, so I implemented the MCP protocol from scratch. This gave me deep understanding of how MCP works - it's JSON-RPC 2.0 over HTTP with specific methods like `initialize`, `tools/list`, and `tools/call`. The server enables an AI agent to search SQL transformation code in GitHub to find root causes of data quality issues."

### 2. "What challenges did you face?"

> "Three main challenges:
> 1. **GitHub's Search API limitation** - It doesn't index small repos, so I had to use the Contents API to scan files directly
> 2. **Deployment environment constraints** - FastMCP crashed on Databricks with no accessible logs, forcing me to implement MCP manually
> 3. **Protocol understanding** - I had to deeply understand JSON-RPC 2.0 and MCP's specific methods to implement them correctly"

### 3. "Why implement MCP manually instead of using a library?"

> "Necessity drove the decision - the libraries crashed. But it turned out to be valuable:
> - **Fewer dependencies** - Just `requests` instead of 10+ packages
> - **Deep understanding** - I now know exactly how MCP works
> - **Portability** - Works on any Python environment
> - **Debugging** - Full control when something goes wrong"

### 4. "How does MCP work?"

> "MCP uses JSON-RPC 2.0 protocol. Key methods:
> - `initialize` - Client and server exchange capabilities
> - `tools/list` - Client discovers available tools with their schemas
> - `tools/call` - Client invokes a tool with arguments
>
> Each tool has a JSON Schema defining its inputs. Responses include structured content (text, images, etc.). The protocol supports multiple transports - I used HTTP for Databricks deployment."

### 5. "What would you do differently?"

> "I'd start with the manual implementation from the beginning. Using libraries is great when they work, but understanding the underlying protocol is invaluable. I'd also set up better logging and health checks earlier in the process."

---

## Final Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      DataScope System                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────┐         ┌──────────────────┐              │
│  │  DataScope Agent │         │ GitHub MCP Server │              │
│  │  (LangGraph)     │◄──MCP──►│ (Manual impl)     │              │
│  │                  │         │                   │              │
│  │  Tools:          │         │  MCP Endpoint:    │              │
│  │  - execute_sql   │         │  POST /mcp        │              │
│  │  - search_code ──┼─────────┼──► tools/call     │              │
│  │  - search_pattern│         │                   │              │
│  └────────┬─────────┘         └─────────┬─────────┘              │
│           │                             │                        │
│           ▼                             ▼                        │
│  ┌──────────────────┐         ┌──────────────────┐              │
│  │ Databricks SQL   │         │ GitHub API       │              │
│  │ Warehouse        │         │ (Contents API)   │              │
│  └──────────────────┘         └──────────────────┘              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Deployment**:
- Both apps run on Databricks Apps
- DataScope Agent: https://datascope-langgraph-xxx.databricksapps.com
- GitHub MCP Server: https://github-mcp-server-xxx.databricksapps.com

---

## Code Statistics

| Component | Lines of Code | Dependencies |
|-----------|---------------|--------------|
| MCP Server (manual) | 424 | requests only |
| MCP Server (FastMCP) | 250 | fastmcp, uvicorn, + transitive |
| REST Server | 180 | requests only |

**Lesson**: Manual implementation is not significantly more code, but much more reliable.

---

## Resources

- [MCP Official Documentation](https://modelcontextprotocol.io/)
- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
- [FastMCP Framework](https://gofastmcp.com/)
- [Databricks MCP Integration](https://docs.databricks.com/generative-ai/mcp/)

---

## Checklist

- [x] Understand MCP protocol and JSON-RPC 2.0
- [x] Implement tool definitions with JSON Schema
- [x] Handle `initialize`, `tools/list`, `tools/call` methods
- [x] Fix GitHub API search limitation
- [x] Deploy MCP server to Databricks Apps
- [x] Update DataScope agent to use MCP client
- [x] Add Databricks authentication to MCP client
- [ ] Add more tools (lineage, schema inspection)

---

## Attempt 4: MCP Client Integration (January 1, 2025)

### What Was Done

Created an MCP client in the DataScope agent that communicates with the GitHub MCP Server:

**MCP Client Implementation** (`datascope-langgraph-app/agent/tools.py`):
```python
class MCPClient:
    """Simple MCP client for communicating with MCP servers.
    Implements the Model Context Protocol (JSON-RPC 2.0 over HTTP).
    """

    def __init__(self, server_url: str, auth_token: str = None, timeout: int = 30):
        self.mcp_endpoint = f"{server_url}/mcp"
        self.auth_token = auth_token

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Call a tool on the MCP server."""
        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        # Extract content from MCP response format
        ...
```

**Tools Updated to Use MCP**:
1. `search_code` - Now calls MCP server with fallback to REST
2. `get_transformation_file` - New tool to get full file contents via MCP
3. `list_transformation_files` - New tool to list SQL files via MCP

**Key Learning**: Databricks Apps authentication requires OAuth for external access but uses service principals for app-to-app communication. The PAT token doesn't work for external access to Databricks Apps.

### Authentication Challenge

Databricks Apps are protected by OAuth authentication:
- **External access** (from local machine) → Redirects to OAuth login
- **Internal access** (app-to-app) → Uses service principal tokens

Solution: When calling MCP server from within Databricks, the apps share the same workspace and can communicate. The agent includes the Databricks token for authorization.

---

*Last Updated: January 1, 2025*
*MCP Server URL: https://github-mcp-server-1262935113136277.gcp.databricksapps.com*
*DataScope Agent URL: https://datascope-langgraph-1262935113136277.gcp.databricksapps.com*
