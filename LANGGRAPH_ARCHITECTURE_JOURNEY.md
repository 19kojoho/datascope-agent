# DataScope LangGraph: Enterprise Agent Architecture Journey

A comprehensive documentation of building a production-grade AI agent for data debugging, covering architecture decisions, implementation choices, and lessons learned.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Starting Point: The Pure Python Agent](#2-starting-point-the-pure-python-agent)
3. [Why LangGraph?](#3-why-langgraph)
4. [Architecture Decisions](#4-architecture-decisions)
5. [The Nine Enterprise Requirements](#5-the-nine-enterprise-requirements)
6. [Implementation Journey](#6-implementation-journey)
7. [Comparison: Pure Python vs LangGraph](#7-comparison-pure-python-vs-langgraph)
8. [Lessons Learned](#8-lessons-learned)
9. [Future Considerations](#9-future-considerations)

---

## 1. Executive Summary

### What We Built
Two versions of DataScope - a data debugging agent that investigates data quality issues in Databricks:

| Version | Location | Architecture |
|---------|----------|--------------|
| **Pure Python** | `datascope-ui-app/` | Manual ReAct loop, ~1000 lines |
| **LangGraph** | `datascope-langgraph-app/` | Framework-based, ~400 lines |

### The Business Problem
Data teams spend 4+ hours investigating questions like:
- "Why is customer XYZ marked as churn when they logged in yesterday?"
- "Why does ARR show $125M but Finance reports $165M?"

**Target**: Reduce investigation time to 20 minutes through AI-assisted debugging.

### Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent Framework | LangGraph | Production-ready, built-in state management |
| LLM Integration | Databricks External Endpoint | Unified governance, audit trail |
| State Persistence | SQLite Checkpointer | Survives restarts, multi-user isolation |
| Analytics | Lakebase (Delta Tables) | Existing infrastructure, SQL queryable |
| Multi-turn Strategy | Automatic (LangGraph) | Avoids manual context engineering bugs |

---

## 2. Starting Point: The Pure Python Agent

### What We Had

The original agent (`datascope-ui-app/app.py`) was built with pure Python:

```python
# Manual ReAct loop
for iteration in range(5):
    resp = requests.post(llm_url, json={"messages": messages, "tools": tools})

    if not tool_calls:
        break

    # Execute tools manually
    for tc in tool_calls:
        result = execute_tool(tc)
        messages.append({"role": "tool", "content": result})
```

### Problems We Encountered

1. **"Investigation complete but could not generate summary"**
   - LLM kept calling tools without producing final answer
   - Fix: Two-phase approach (investigate with tools → force summary without tools)

2. **"text content blocks must be non-empty"**
   - Empty content in assistant messages broke Anthropic API
   - Fix: Only include content field when non-empty

3. **"tool_use ids were found without tool_result blocks"**
   - Incomplete tool sequences in conversation history
   - Fix: Abandoned message replay, switched to context summary injection

### The Context Engineering Workaround

```python
def get_conversation_summary(conversation_id: str) -> str:
    """Instead of replaying messages, inject a text summary."""
    # Query last 2 Q&A pairs from Lakebase
    # Return as: "User asked: '...' You found: '...'"

# Inject into system prompt
system_content = SYSTEM_PROMPT + "\n\n" + context_summary
```

This worked, but it was fragile and lost rich conversation context.

---

## 3. Why LangGraph?

### The Core Question

> "Would you call this application an agent or workflow?"

**Answer**: It's an **Agent** - the LLM decides which tools to use and when to stop.

### Agent vs Workflow

| Characteristic | Workflow | Agent |
|----------------|----------|-------|
| Control flow | Predefined, deterministic | Dynamic, LLM decides |
| Tool selection | Fixed sequence | LLM chooses |
| Termination | After fixed steps | When goal reached |

### Why Not Just Keep Pure Python?

| Aspect | Pure Python | LangGraph |
|--------|-------------|-----------|
| Multi-turn handling | Manual, bug-prone | Automatic, correct |
| State persistence | Custom SQL code | Built-in checkpointer |
| Tool execution | Manual loop | Framework manages |
| Streaming | Not implemented | Built-in support |
| Debugging | Print statements | LangSmith tracing |
| Code complexity | ~1000 lines | ~400 lines |

### The Deciding Factor

The multi-turn conversation bugs we hit (tool_call/tool_result pairing) are **exactly what LangGraph's checkpointer solves automatically**.

---

## 4. Architecture Decisions

### Decision 1: LLM Integration

**Options Considered:**

| Option | Pros | Cons |
|--------|------|------|
| Direct Anthropic API | Lower latency, latest features | Separate API key, no audit trail |
| Databricks External Endpoint | Unified auth, AI Gateway, monitoring | Slightly more latency |

**Choice**: Databricks External Endpoint

**Rationale**:
- Already set up and working
- Centralized credential management
- AI Gateway features (rate limiting, monitoring)
- Unified audit trail in Databricks system tables
- Enterprise governance compliance

```python
llm = ChatOpenAI(
    model=config.llm_endpoint,  # "claude-sonnet-endpoint"
    base_url=f"{config.databricks_host}/serving-endpoints",
    api_key=config.databricks_token,
    temperature=0,
)
```

### Decision 2: State Management

**Options Considered:**

| Option | Pros | Cons |
|--------|------|------|
| MemorySaver (in-memory) | Zero setup, fast | State lost on restart |
| Lakebase via SQL | Persists, works at scale | Complex, bug-prone (we proved this) |
| SQLite Checkpointer | Persists, LangGraph native | File-based, single instance |
| PostgreSQL Checkpointer | Scales, multi-instance | Requires Postgres setup |

**Choice**: SQLite Checkpointer + Lakebase for Analytics

**Rationale**:
- SQLite handles conversation state correctly (tool_call/tool_result pairing)
- Survives server restarts
- Lakebase handles analytics (investigations, metrics)
- Clean separation of concerns

```python
# Conversation state: SQLite
checkpointer = SqliteSaver.from_conn_string("./checkpoints/conversations.db")

# Analytics: Lakebase
save_investigation(conversation_id, question, response, duration)
```

### Decision 3: Agent Pattern

**Options Considered:**

| Option | Description | Complexity |
|--------|-------------|------------|
| Custom StateGraph | 5 nodes: classify → retrieve → investigate → analyze → synthesize | High |
| Prebuilt ReAct Agent | Single agent with tool loop | Low |

**Choice**: Prebuilt ReAct Agent

**Rationale**:
- The existing `src/datascope/agent/graph.py` had a 5-node graph
- But the pure Python version worked fine with a simple loop
- LangGraph's `create_react_agent` does the same thing with less code
- YAGNI (You Ain't Gonna Need It) - start simple

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(
    model=llm,
    tools=[search_patterns, execute_sql, search_code],
    checkpointer=checkpointer,
    state_modifier=SYSTEM_PROMPT,
)
```

### Decision 4: Deployment Strategy

**Choice**: Same repo, separate folder

**Rationale**:
- Easy side-by-side comparison
- Shared Databricks environment
- Single git history shows evolution
- Both deploy as separate Databricks Apps

```
datascope-project/
├── datascope-ui-app/          # Pure Python (untouched)
├── datascope-langgraph-app/   # LangGraph (new)
```

---

## 5. The Nine Enterprise Requirements

### 5.1 State Management

**Requirement**: Persist conversation state across requests and restarts.

**Implementation**:
```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("./checkpoints/conversations.db")
agent = create_react_agent(..., checkpointer=checkpointer)
```

**How it works**:
- Each conversation gets a `thread_id`
- Checkpointer saves state after each node execution
- State includes full message history with tool calls
- Survives server restarts

### 5.2 Context Engineering

**Requirement**: Provide agent with relevant context for investigation.

**Implementation**:
```python
SYSTEM_PROMPT = """You are DataScope, a Data Debugging Agent...

## Investigation Strategy
1. FIRST: Use search_patterns to find similar past issues
2. THEN: Use execute_sql to verify with actual data
3. OPTIONALLY: Use search_code to find the transformation bug

## Available Tables
- novatech.gold.churn_predictions
- novatech.gold.arr_by_customer
...
"""

agent = create_react_agent(..., state_modifier=SYSTEM_PROMPT)
```

**Key elements**:
- Agent identity and purpose
- Step-by-step investigation strategy
- Available tables and their purposes
- Expected output format

### 5.3 Retrieval (Pattern Matching)

**Requirement**: Find similar past issues to guide investigation.

**Implementation**:
```python
@tool
def search_patterns(query: str) -> str:
    """Search for similar past data quality issues using Vector Search."""
    resp = requests.post(
        f"{config.databricks_host}/api/2.0/vector-search/indexes/{VS_INDEX}/query",
        json={
            "query_text": query,
            "columns": ["pattern_id", "title", "symptoms", "root_cause", "resolution"],
            "num_results": 3
        }
    )
```

**Vector Search setup**:
- Endpoint: `datascope-vs-endpoint`
- Index: `novatech.gold.datascope_patterns_index`
- Embedding model: `databricks-gte-large-en`
- Patterns stored: Common data quality issues with symptoms, causes, fixes

### 5.4 Ranking

**Requirement**: Return most relevant patterns first.

**Implementation**: Handled by Databricks Vector Search
- Semantic similarity ranking using embeddings
- Top 3 results returned
- Each result includes relevance context

### 5.5 Tracing

**Requirement**: Track agent execution for debugging.

**Implementation**: LangGraph provides built-in tracing

```python
# Each invocation is traceable
result = agent.invoke(
    {"messages": [{"role": "user", "content": question}]},
    config={"configurable": {"thread_id": conversation_id}}
)
```

**Future enhancement**: LangSmith integration for visual tracing

### 5.6 Monitoring

**Requirement**: Track usage metrics and performance.

**Implementation**: Lakebase analytics

```python
def save_investigation(conversation_id, question, response, duration):
    """Save to novatech.datascope.investigations table."""
    query = f"""
    INSERT INTO {table} (investigation_id, conversation_id, question,
                         duration_seconds, summary, status)
    VALUES (...)
    """
```

**Metrics available via `/stats` endpoint**:
- Total investigations
- Average duration
- Investigations today
- Success rate

### 5.7 Observability

**Requirement**: Health checks and dependency verification.

**Implementation**:
```python
@app.get("/health")
def health_check():
    health = {"status": "healthy", "service": "datascope-langgraph"}

    # Check agent initialization
    try:
        create_agent()
        health["agent"] = "ok"
    except Exception as e:
        health["agent"] = f"error: {str(e)}"
        health["status"] = "degraded"

    return health
```

**Checks performed**:
- Agent can be created
- LLM endpoint is accessible
- SQL warehouse is available

### 5.8 Memory Management

**Requirement**: Handle conversation history without unbounded growth.

**Implementation**: LangGraph checkpointer with SQLite

**Key considerations**:
- SQLite handles message storage efficiently
- LangGraph manages token limits internally
- Old conversations can be pruned via SQL

```sql
-- Cleanup old conversations (if needed)
DELETE FROM checkpoints
WHERE created_at < CURRENT_DATE - INTERVAL 30 DAY;
```

### 5.9 Multi-turn Conversations

**Requirement**: Handle follow-up questions with full context.

**Implementation**: Automatic via LangGraph

```python
# First question
result1 = agent.invoke(
    {"messages": [{"role": "user", "content": "Why do customers have NULL churn_risk?"}]},
    config={"configurable": {"thread_id": "conv-123"}}
)

# Follow-up (same thread_id)
result2 = agent.invoke(
    {"messages": [{"role": "user", "content": "Which specific customers are affected?"}]},
    config={"configurable": {"thread_id": "conv-123"}}  # Same thread!
)
```

**Why this is better than pure Python**:
- No manual context injection
- No tool_call/tool_result bugs
- Full conversation history preserved
- LangGraph handles message format correctly

---

## 6. Implementation Journey

### Step 1: Planning

Created `LANGGRAPH_IMPLEMENTATION_PLAN.md` with:
- Directory structure
- File list
- Dependencies
- Success criteria

### Step 2: Directory Structure

```
datascope-langgraph-app/
├── agent/
│   ├── __init__.py
│   ├── config.py      # Centralized configuration
│   ├── tools.py       # Tool implementations
│   ├── prompts.py     # System prompt
│   └── graph.py       # LangGraph agent
├── checkpoints/       # SQLite state (gitignored)
├── app.py             # HTTP server
├── requirements.txt
├── app.yaml.example
└── README.md
```

### Step 3: Configuration (`agent/config.py`)

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
    checkpoint_dir: str
    port: int

    @classmethod
    def from_env(cls) -> "Config":
        # Load and validate all environment variables
```

**Design decisions**:
- All config from environment (12-factor app)
- Validation at startup (fail fast)
- OAuth fallback for Databricks Apps service principal

### Step 4: Tools (`agent/tools.py`)

Three tools with production-grade error handling:

```python
@tool
def search_patterns(query: str) -> str:
    """Search for similar past data quality issues."""
    try:
        # Vector Search API call
    except requests.exceptions.Timeout:
        return "Pattern search timed out. Proceeding with direct investigation."
    except Exception as e:
        return f"Pattern search error: {str(e)}"
```

**Features**:
- Type annotations for LangChain
- Timeouts (15s patterns, 30s SQL, 30s code)
- Query validation (SELECT only)
- Row limits (max 15 rows)
- Graceful degradation

### Step 5: LangGraph Agent (`agent/graph.py`)

```python
def create_agent(config: Config):
    # LLM via Databricks
    llm = ChatOpenAI(
        model=config.llm_endpoint,
        base_url=f"{config.databricks_host}/serving-endpoints",
        api_key=config.databricks_token,
    )

    # Persistent checkpointer
    checkpointer = SqliteSaver.from_conn_string(
        f"{config.checkpoint_dir}/conversations.db"
    )

    # ReAct agent
    return create_react_agent(
        model=llm,
        tools=get_tools(),
        checkpointer=checkpointer,
        state_modifier=SYSTEM_PROMPT,
    )
```

**Key design choices**:
- Use prebuilt ReAct (not custom StateGraph)
- Recursion limit = 15 (~5 tool iterations)
- Global agent instance (singleton pattern)

### Step 6: HTTP Server (`app.py`)

Production-ready server with:

```python
class DataScopeHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/chat":
            # Parse request
            question = body.get("question")
            conversation_id = body.get("conversation_id") or str(uuid.uuid4())

            # Invoke agent
            result = invoke_agent(question, conversation_id)

            # Save analytics
            save_investigation(conversation_id, question, result, duration)

            # Return response
            self.send_json({"response": result, "conversation_id": conversation_id})
```

**Features**:
- Same UI as pure Python version (with "LangGraph" badge)
- CORS support
- JSON error responses
- Request logging
- Graceful shutdown (SIGTERM handler)

---

## 7. Comparison: Pure Python vs LangGraph

### Code Complexity

| Metric | Pure Python | LangGraph |
|--------|-------------|-----------|
| Total lines | ~1000 | ~400 |
| Tool loop | 50 lines | 0 (framework) |
| State management | 100 lines | 0 (checkpointer) |
| Context injection | 50 lines | 1 line |

### Feature Comparison

| Feature | Pure Python | LangGraph |
|---------|-------------|-----------|
| Multi-turn | Manual context summary | Automatic |
| State persistence | Custom Lakebase SQL | SQLite checkpointer |
| Tool execution | Manual for-loop | ReAct agent |
| Error recovery | Manual try/catch | Built-in |
| Streaming | Not implemented | Available |
| Tracing | Print statements | LangSmith ready |

### What Pure Python Does Better

1. **No dependencies** - Just `requests`
2. **Explicit control** - Can see exactly what happens
3. **Easier debugging** - No framework abstractions
4. **Forced summary phase** - Guarantees final answer

### What LangGraph Does Better

1. **Correct multi-turn** - No API bugs
2. **Less code** - Focus on business logic
3. **State management** - Handled correctly
4. **Future features** - Streaming, human-in-loop, etc.

---

## 8. Lessons Learned

### 1. Start with Pure Python, Then Add Framework

Building the pure Python version first gave us:
- Deep understanding of the problem
- Clear requirements for framework selection
- Ability to compare approaches

### 2. Multi-turn is Hard

The tool_call/tool_result pairing requirement in Anthropic's API caused multiple bugs. A framework that handles this automatically is worth the dependency.

### 3. Separation of Concerns

```
State (SQLite)     → Conversation history
Analytics (Lakebase) → Business metrics
```

Don't try to use one system for both.

### 4. Fail Fast

Validate configuration at startup:
```python
if not databricks_host:
    raise ValueError("DATABRICKS_HOST required")
```

Don't wait until the first request to discover missing config.

### 5. Graceful Degradation

Tools should return helpful messages on failure:
```python
except Exception as e:
    return f"Pattern search unavailable. Proceeding with direct investigation."
```

Don't crash the whole investigation because one tool failed.

---

## 9. Future Considerations

### Scaling to Multiple Instances

Current SQLite checkpointer is file-based. For multi-instance deployment:

```python
# Option 1: PostgreSQL
from langgraph.checkpoint.postgres import PostgresSaver
checkpointer = PostgresSaver.from_conn_string(postgres_url)

# Option 2: Redis (if available)
# Option 3: Use Lakebase with proper SQL (we proved this is hard)
```

### Streaming Responses

LangGraph supports streaming:
```python
for chunk in agent.stream({"messages": [...]}):
    yield chunk
```

Would improve UX for long investigations.

### Human-in-the-Loop

LangGraph can pause for human approval:
```python
# Add interrupt_before for sensitive operations
workflow.add_node("execute_sql", run_sql, interrupt_before=True)
```

Useful for production queries that modify data.

### LangSmith Integration

For visual tracing and debugging:
```python
import langsmith
langsmith.init(project="datascope")
```

### Additional Tools

Could add:
- `get_table_schema` - Unity Catalog metadata
- `get_column_lineage` - Data lineage
- `explain_query` - Query plan analysis

---

## Appendix: File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `agent/__init__.py` | 15 | Package exports |
| `agent/config.py` | 120 | Configuration management |
| `agent/tools.py` | 180 | Tool implementations |
| `agent/prompts.py` | 35 | System prompt |
| `agent/graph.py` | 100 | LangGraph agent |
| `app.py` | 350 | HTTP server + UI |
| **Total** | **~400** | |

Compare to pure Python: **~1000 lines**

---

## Appendix: Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABRICKS_HOST` | Yes | Workspace URL |
| `DATABRICKS_TOKEN` | Yes | PAT or OAuth |
| `DATABRICKS_SQL_WAREHOUSE_ID` | Yes | SQL warehouse ID |
| `LLM_ENDPOINT_NAME` | No | Default: claude-sonnet-endpoint |
| `GITHUB_MCP_APP_URL` | No | Code search endpoint |
| `VS_ENDPOINT_NAME` | No | Vector Search endpoint |
| `VS_INDEX_NAME` | No | Pattern index name |
| `LAKEBASE_ENABLED` | No | Default: true |
| `LAKEBASE_CATALOG` | No | Default: novatech |
| `LAKEBASE_SCHEMA` | No | Default: datascope |
| `CHECKPOINT_DIR` | No | Default: ./checkpoints |
| `PORT` | No | Default: 8000 |

---

*Document created: December 2024*
*Repository: https://github.com/19kojoho/datascope-agent*
