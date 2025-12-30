# DataScope Architecture Decisions

## Overview

This document analyzes the differences between our original architecture plan and our implementation, justifying the decisions made.

---

## Original Plan vs Implementation

### 1. LLM Access: External Endpoint vs Direct API

| Aspect | Original Plan | Current Implementation | Recommended |
|--------|---------------|------------------------|-------------|
| **How** | Claude via Databricks External Endpoint | `langchain_anthropic.ChatAnthropic` (direct API) | **External Endpoint** |
| **Auth** | Workspace identity, centralized credentials | ANTHROPIC_API_KEY env var per user | Databricks secrets |
| **Governance** | AI Gateway, rate limits, audit logs | None | AI Gateway |

#### Why External Endpoint is Better

1. **Centralized Credential Management**: API keys stored in Databricks secrets, not scattered across environments
2. **AI Gateway Features**: Rate limiting, usage tracking, cost monitoring, audit logs
3. **Unified API**: OpenAI-compatible interface works with any LLM provider
4. **Production Ready**: Same endpoint used in dev and prod, no credential rotation needed
5. **Governance**: System tables capture all LLM calls for compliance

#### Implementation Change
```python
# FROM (current - direct API)
from langchain_anthropic import ChatAnthropic
llm = ChatAnthropic(model="claude-sonnet-4-20250514")

# TO (recommended - External Endpoint via OpenAI-compatible API)
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(
    model="databricks-claude-sonnet",  # Your endpoint name
    base_url=f"{DATABRICKS_HOST}/serving-endpoints",
    api_key=DATABRICKS_TOKEN,  # PAT or OAuth token
)
```

---

### 2. Tools: Managed MCP vs Custom SDK Wrappers

| Aspect | Original Plan | Current Implementation | Recommended |
|--------|---------------|------------------------|-------------|
| **SQL** | Databricks SQL MCP (`/api/2.0/mcp/sql`) | Custom `SQLTool` class wrapping SDK | **Managed MCP** |
| **Schema/Lineage** | Unity Catalog MCP | Custom `SchemaTool`, `LineageTool` | **Managed MCP** |
| **Code Search** | GitHub External MCP (Databricks App) | Local file search fallback | **Custom MCP App** |
| **Vector Search** | Databricks VS MCP | Not implemented | **Managed MCP** |

#### Why Managed MCP is Better

1. **Authentication**: Automatic on-behalf-of-user auth, respects UC permissions
2. **Maintenance**: Databricks maintains the tools, no custom code to update
3. **Discoverability**: `mcp_client.list_tools()` returns available tools dynamically
4. **Consistency**: Same tool definitions work across all agents in organization
5. **Deployment**: `mcp_client.get_databricks_resources()` auto-captures required permissions

#### Why Custom Wrappers May Be Acceptable (Trade-offs)

| Custom Wrappers | Managed MCP |
|-----------------|-------------|
| ✅ Works without MCP setup | ❌ Requires MCP servers configured |
| ✅ Full control over logic | ❌ Limited to MCP capabilities |
| ✅ Easier local testing | ❌ Needs Databricks connection |
| ❌ Must maintain SDK code | ✅ Auto-updated by Databricks |
| ❌ Manual auth handling | ✅ Built-in auth |

#### Recommendation
Use **Managed MCPs** for production, keep custom wrappers as **fallback for local dev**.

```python
# Production: Use MCP tools
from databricks_mcp import DatabricksMCPClient

sql_mcp = DatabricksMCPClient(
    server_url=f"{host}/api/2.0/mcp/sql",
    workspace_client=workspace_client
)
tools = sql_mcp.list_tools()

# Local dev fallback: Use custom tools
if not DATABRICKS_HOST:
    tools = create_local_tools()
```

---

### 3. State Management: Lakebase vs In-Memory

| Aspect | Original Plan | Current Implementation | Recommended |
|--------|---------------|------------------------|-------------|
| **Storage** | Lakebase (managed Postgres) | LangGraph in-memory state | **Lakebase** |
| **Multi-turn** | Thread IDs, checkpointing | Single invocation only | Checkpointer |
| **Persistence** | 90-day retention | Lost on restart | Persistent |
| **Long-term Memory** | PostgresStore for patterns | Not implemented | Optional |

#### Why Lakebase is Essential for Production

1. **Multi-Turn Conversations**: User asks follow-up questions without losing context
   ```
   User: "Why do some customers have NULL churn_risk?"
   Agent: [investigates, finds BUG-005]
   User: "What about the revenue impact?"  # Needs previous context!
   ```

2. **Distributed Deployment**: Model Serving doesn't guarantee same replica handles all requests

3. **Investigation Resume**: User can pause and resume investigation later

4. **Audit Trail**: All conversation history persisted for compliance

#### Implementation with LangGraph Checkpointer

```python
from langgraph.checkpoint.postgres import PostgresSaver

# Connect to Lakebase
checkpointer = PostgresSaver.from_conn_string(
    conn_string=LAKEBASE_CONNECTION_STRING
)

# Create graph with checkpointing
graph = create_datascope_agent()
graph_with_memory = graph.compile(checkpointer=checkpointer)

# Each conversation has a thread_id
result = graph_with_memory.invoke(
    state,
    config={"configurable": {"thread_id": "investigation-123"}}
)
```

---

### 4. UC Functions: Custom Investigation Logic

| Aspect | Original Plan | Current Implementation | Recommended |
|--------|---------------|------------------------|-------------|
| **Pattern** | Register Python as UC Functions | Python code in agent | **UC Functions** |
| **Discovery** | Via Managed MCP | Hardcoded in agent | MCP discovery |

#### Why UC Functions are Valuable

1. **Reusability**: Functions available to any agent, not just DataScope
2. **Governance**: UC permissions control who can use which functions
3. **Versioning**: Functions are versioned and auditable
4. **Examples**:
   - `investigate_null_values(table, column)` - Standard NULL analysis
   - `compare_aggregations(query1, query2)` - Metric discrepancy check
   - `trace_data_flow(table)` - Automated lineage summary

#### Implementation

```sql
-- Register function in Unity Catalog
CREATE OR REPLACE FUNCTION novatech.ai.investigate_null_values(
    table_name STRING,
    column_name STRING
)
RETURNS STRING
LANGUAGE PYTHON
AS $$
    # Standardized NULL investigation logic
    return f"""
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN {column_name} IS NULL THEN 1 ELSE 0 END) as null_count
    FROM {table_name}
    """
$$;
```

Then access via UC Functions MCP:
```python
uc_mcp = DatabricksMCPClient(
    server_url=f"{host}/api/2.0/mcp/functions/novatech/ai",
    workspace_client=workspace_client
)
# investigate_null_values now available as a tool!
```

---

## Recommended Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     DATABRICKS APP (UI)                          │
│                        Streamlit Chat                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MODEL SERVING ENDPOINT                        │
│              DataScope Agent (LangGraph + MLflow)                │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                   LANGGRAPH WORKFLOW                       │  │
│  │   classify → retrieve → analyze → investigate → synthestic │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│  ┌───────────────────────────┴───────────────────────────────┐  │
│  │                    STATE (Lakebase)                        │  │
│  │          PostgresCheckpointer for multi-turn               │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────────────┐
│  LLM Endpoint │   │ Managed MCPs  │   │   Custom MCP (App)    │
│   (Claude)    │   │               │   │                       │
│               │   │ • SQL         │   │  • GitHub Code Search │
│ External      │   │ • UC Schema   │   │  • novatech-transforms│
│ Endpoint via  │   │ • UC Lineage  │   │                       │
│ AI Gateway    │   │ • Vector Srch │   │  Databricks App       │
│               │   │ • UC Functions│   │  hosting MCP server   │
└───────────────┘   └───────────────┘   └───────────────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DATABRICKS SERVICES                           │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │    Unity    │  │     SQL     │  │   Vector    │              │
│  │   Catalog   │  │  Warehouse  │  │   Search    │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                 novatech (bronze/silver/gold)                ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       OBSERVABILITY                              │
│                                                                  │
│   MLflow Tracing  │  System Tables  │  Lakehouse Monitoring     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Migration Path

### Phase 1: Core Refactoring (Now)
1. ✅ Keep LangGraph workflow structure
2. 🔄 Switch LLM to External Endpoint (OpenAI-compatible)
3. 🔄 Replace custom tools with MCP client calls
4. 🔄 Add Lakebase checkpointer for state

### Phase 2: Enhanced Tools
1. Deploy GitHub MCP as Databricks App
2. Create UC Functions for common investigation patterns
3. Set up Vector Search index for pattern library

### Phase 3: Production Deployment
1. Package as MLflow model
2. Deploy to Model Serving
3. Build Streamlit UI
4. Enable AI Gateway monitoring

---

## Summary of Decisions

| Decision | Choice | Justification |
|----------|--------|---------------|
| LLM Access | External Endpoint | Governance, central credentials, AI Gateway |
| Tools | Managed MCP (primary) + Custom (fallback) | Auto-auth, maintenance-free, permissions |
| State | Lakebase PostgresCheckpointer | Multi-turn, distributed deployment, audit |
| Code Search | Custom MCP on Databricks App | GitHub integration, same auth model |
| UC Functions | Yes, for reusable patterns | Governance, discoverability, versioning |

---

## References

- [Databricks External Models](https://docs.databricks.com/aws/en/generative-ai/external-models/)
- [Databricks Managed MCP](https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp)
- [Custom MCP on Databricks Apps](https://docs.databricks.com/aws/en/generative-ai/mcp/custom-mcp)
- [AI Agent Memory with Lakebase](https://docs.databricks.com/aws/en/generative-ai/agent-framework/stateful-agents)
- [Databricks + Anthropic Partnership](https://www.databricks.com/company/newsroom/press-releases/databricks-and-anthropic-sign-landmark-deal-bring-claude-models)
