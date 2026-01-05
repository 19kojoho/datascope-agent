# Plan: Create Production-Grade LangGraph Version of DataScope Agent

## Goal
Create a **production-ready, enterprise-grade** LangGraph version of the DataScope agent alongside the existing pure Python implementation. Must support multiple concurrent users and be deployable at scale.

## Key Decisions
- **LLM**: Databricks External Endpoint (same as current app)
- **State**: SQLite checkpointer for conversation state (persistent, works across restarts)
- **Analytics**: Lakebase for investigation tracking and monitoring
- **Current app**: Keep completely untouched
- **Location**: Same repo, new folder (for easy comparison)

## Current State
- **Working version**: `datascope-ui-app/app.py` - Pure Python, deployed, battle-tested (**DO NOT MODIFY**)
- **Partial LangGraph**: `src/datascope/agent/graph.py` - Reference only, won't use directly

## Directory Structure

```
datascope-project/
├── datascope-ui-app/          # UNTOUCHED (current working version)
│   ├── app.py                 # Pure Python agent
│   └── app.yaml               # Config with secrets
│
├── datascope-langgraph-app/   # NEW (Production LangGraph version)
│   ├── app.py                 # HTTP server (thread-safe, production-ready)
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── graph.py           # LangGraph ReAct workflow
│   │   ├── tools.py           # Tool implementations with error handling
│   │   ├── prompts.py         # System prompts
│   │   └── config.py          # Centralized configuration
│   ├── checkpoints/           # SQLite database for state (gitignored)
│   ├── requirements.txt
│   ├── app.yaml.example       # Template (no secrets)
│   └── README.md              # Setup and deployment guide
```

## Implementation Steps

### Step 1: Create Directory Structure
Create `datascope-langgraph-app/` with `agent/` and `checkpoints/` subdirectories.

### Step 2: Create Configuration (`agent/config.py`)
Centralized, environment-based configuration:
```python
@dataclass
class Config:
    databricks_host: str
    databricks_token: str
    llm_endpoint: str
    sql_warehouse_id: str
    github_mcp_url: str
    vs_endpoint: str
    vs_index: str
    lakebase_catalog: str
    lakebase_schema: str
    checkpoint_dir: str  # For SQLite persistence

    @classmethod
    def from_env(cls) -> "Config":
        # Load from environment with validation
```

### Step 3: Create Tools (`agent/tools.py`)
Production-grade tools with error handling, retries, and logging:
```python
@tool
def search_patterns(query: str) -> str:
    """Search for similar past data quality issues using Vector Search."""
    try:
        # Implementation with timeout and retry
    except Exception as e:
        logger.error(f"Pattern search failed: {e}")
        return f"Pattern search unavailable: {str(e)}"

@tool
def execute_sql(query: str) -> str:
    """Execute SQL query against Databricks SQL Warehouse."""
    # With query validation, row limits, timeout

@tool
def search_code(term: str) -> str:
    """Search SQL transformation code via GitHub MCP."""
    # With fallback handling
```

### Step 4: Create Prompts (`agent/prompts.py`)
Copy system prompt from current app.py - keep identical for fair comparison.

### Step 5: Create LangGraph Workflow (`agent/graph.py`)
Production-ready ReAct agent with persistent checkpointing:
```python
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.sqlite import SqliteSaver

def create_agent(config: Config):
    # LLM via Databricks External Endpoint
    llm = ChatOpenAI(
        model=config.llm_endpoint,
        base_url=f"{config.databricks_host}/serving-endpoints",
        api_key=config.databricks_token,
        temperature=0,
    )

    # Persistent checkpointer (survives restarts)
    checkpointer = SqliteSaver.from_conn_string(
        f"{config.checkpoint_dir}/conversations.db"
    )

    # Create agent with recursion limit
    return create_react_agent(
        model=llm,
        tools=[search_patterns, execute_sql, search_code],
        checkpointer=checkpointer,
    )
```

### Step 6: Create HTTP Server (`app.py`)
Production-grade server with:
- **Thread-safe request handling** - Each request gets isolated state
- **Proper error responses** - JSON error format with status codes
- **Request logging** - Track all requests for debugging
- **Graceful shutdown** - Handle SIGTERM properly
- **Health checks** - `/health` with dependency checks
- **Lakebase analytics** - Save investigations for monitoring

```python
# Endpoints
POST /chat          # Investigation with conversation_id
GET  /health        # Liveness + readiness
GET  /stats         # Lakebase analytics
GET  /conversations # List user's conversations
```

### Step 7: Create Config Files
- `requirements.txt` - Pinned versions for reproducibility
- `app.yaml.example` - Template with all env vars documented
- `README.md` - Setup, deployment, and troubleshooting guide

### Step 8: Update .gitignore
Add `datascope-langgraph-app/checkpoints/` to prevent committing SQLite state.

## Files to Create

| File | Purpose |
|------|---------|
| `datascope-langgraph-app/agent/__init__.py` | Package init, exports |
| `datascope-langgraph-app/agent/config.py` | Centralized configuration |
| `datascope-langgraph-app/agent/tools.py` | Three tools with error handling |
| `datascope-langgraph-app/agent/prompts.py` | System prompt |
| `datascope-langgraph-app/agent/graph.py` | LangGraph ReAct agent |
| `datascope-langgraph-app/app.py` | Production HTTP server + UI |
| `datascope-langgraph-app/requirements.txt` | Pinned dependencies |
| `datascope-langgraph-app/app.yaml.example` | Config template |
| `datascope-langgraph-app/README.md` | Setup and deployment guide |

## Dependencies (requirements.txt)

```
langgraph==0.2.59
langgraph-checkpoint-sqlite==2.0.6
langchain-core==0.3.28
langchain-openai==0.3.0
requests==2.32.3
```

## Production Features

| Feature | Implementation |
|---------|----------------|
| **Persistent State** | SQLite checkpointer (survives restarts) |
| **Multi-user** | Thread ID per conversation, isolated state |
| **Error Handling** | Try/catch in tools, graceful degradation |
| **Logging** | Python logging with request IDs |
| **Health Checks** | `/health` checks LLM + SQL connectivity |
| **Analytics** | Lakebase for investigation metrics |
| **Timeouts** | 30s for SQL, 120s for full investigation |
| **Graceful Shutdown** | SIGTERM handler |

## Comparison Points (for later analysis)

| Aspect | Pure Python (`datascope-ui-app`) | LangGraph (`datascope-langgraph-app`) |
|--------|----------------------------------|---------------------------------------|
| Lines of code | ~1000 | ~400 (estimate) |
| Multi-turn handling | Manual context summary injection | Automatic via SqliteSaver |
| Tool execution | Manual for-loop | LangGraph ReAct manages |
| State persistence | Lakebase (custom SQL) | SQLite (built-in) |
| Debugging | Direct, explicit | LangGraph tracing |
| Dependencies | Just `requests` | langgraph, langchain-* |
| Concurrent users | Works (stateless per request) | Works (thread_id isolation) |

## Success Criteria
- [ ] Can run locally with `python app.py`
- [ ] Same 3 tools work identically to current app
- [ ] Same system prompt for fair comparison
- [ ] Multi-turn conversations persist across restarts
- [ ] Multiple concurrent users supported
- [ ] Analytics saved to Lakebase
- [ ] Health checks verify all dependencies
- [ ] Can deploy to Databricks Apps as separate app
- [ ] README documents setup and troubleshooting
