# DataScope Agent: Development Journey

A comprehensive record of building a Data Debugging Agent for Databricks.

---

## Executive Summary

**What We Built**: DataScope - an AI-powered data debugging agent that investigates data quality issues through natural language conversation.

**Target Outcome**: Reduce investigation time from 4 hours to 20 minutes.

**Key Technologies**: Databricks Apps, Claude (via External Model Endpoints), Vector Search, Lakebase (Delta Tables), GitHub MCP Server.

**Repository**: https://github.com/19kojoho/datascope-agent

---

## 1. The Problem We Solved

Data teams spend hours investigating questions like:
- "Why is customer XYZ marked as churn when they logged in yesterday?"
- "Why does ARR show $125M but Finance reports $165M?"
- "Why do some customers have NULL churn_risk?"

These investigations require:
1. Understanding the business question
2. Querying multiple tables across bronze/silver/gold layers
3. Tracing data lineage
4. Finding the transformation code
5. Identifying the root cause
6. Explaining findings clearly

DataScope automates this entire workflow.

---

## 2. Architecture Evolution

### Original Plan (Oct-Nov 2024)
```
6-layer architecture:
├── Presentation (Databricks App)
├── Orchestration (Multi-agent vs Single agent)
├── Tools (UC MCP, GitHub MCP, SQL, Vector Search)
├── Data/State (Lakebase)
├── Inference (Claude via External Endpoints)
└── Monitoring (MLflow)
```

### What We Implemented
```
Simplified 4-layer architecture:
├── Presentation: Databricks App (Chat UI)
├── Agent: Single Claude Agent with 3 tools
├── State + Monitoring: Lakebase (Delta Tables)
└── Vector Search: Pattern matching
```

### Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Single vs Multi-Agent | Single Agent | Simpler, sufficient for use case, David Levy's recommendation |
| Lakebase vs MLflow | Lakebase only | Handles both state AND monitoring via SQL, less complexity |
| UC Lineage | Not implemented | Bugs found via SQL + code search, lineage adds latency without benefit |
| Multi-turn approach | Context Summary | Avoids API issues with tool call history reconstruction |

---

## 3. The Agent Architecture

### How It Works

```
User Question
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ 1. CONTEXT LOADING                                      │
│    - Load conversation summary from Lakebase            │
│    - Inject into system prompt                          │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ 2. REACT LOOP (max 5 iterations)                        │
│    For each iteration:                                  │
│    - LLM reasons about what to do                       │
│    - Optionally calls tools                             │
│    - Observes results                                   │
│    - Decides: more investigation or answer?             │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ 3. FORCED SUMMARY PHASE                                 │
│    - Remove tools from LLM call                         │
│    - Force final text response                          │
│    - Guarantees user gets an answer                     │
└─────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ 4. STATE PERSISTENCE                                    │
│    - Save to novatech.datascope.messages                │
│    - Save to novatech.datascope.investigations          │
└─────────────────────────────────────────────────────────┘
```

### The Three Tools

| Tool | Purpose | Implementation |
|------|---------|----------------|
| `search_patterns` | Find similar past issues | Vector Search with semantic similarity |
| `execute_sql` | Query data across layers | Databricks SQL Warehouse |
| `search_code` | Find transformation bugs | GitHub MCP Server |

---

## 4. Bugs Encountered and Fixed

### Bug 1: "Investigation complete but could not generate summary"

**Symptom**: Agent would call tools but never produce a final answer.

**Root Cause**: The LLM kept choosing to use tools instead of responding with text. With tools available, it always preferred more investigation.

**Fix**: Two-phase approach
```python
# Phase 1: Investigation with tools (max 5 iterations)
for iteration in range(5):
    resp = call_llm(messages, tools=tools)
    # ... process tool calls

# Phase 2: Force summary without tools
messages.append({"role": "user", "content": summary_prompt})
resp = call_llm(messages, tools=None)  # No tools = must respond with text
```

---

### Bug 2: "text content blocks must be non-empty"

**Symptom**: Follow-up questions failed with Anthropic API error.

**Root Cause**: When reconstructing conversation history, assistant messages with tool_calls had empty content fields. Anthropic's API requires content blocks to be non-empty if present.

**Fix**: Only include content field when non-empty
```python
assistant_msg = {"role": "assistant", "tool_calls": tool_calls}
if content and content.strip():
    assistant_msg["content"] = content  # Only add if non-empty
messages.append(assistant_msg)
```

---

### Bug 3: "tool_use ids were found without tool_result blocks"

**Symptom**: Multi-turn conversations failed even after the empty content fix.

**Root Cause**: Anthropic's API requires every tool_call to have a matching tool_result immediately after. When loading history from Lakebase, we had incomplete tool sequences.

**Attempted Fix 1**: Filter to only user/assistant messages with content.

**Problem**: Still failed because the API validates the entire message sequence.

**Final Fix**: Context Summary Approach - don't replay messages at all
```python
def get_conversation_summary(conversation_id: str) -> str:
    """Instead of replaying messages, inject a text summary."""
    # Query last 2 Q&A pairs from Lakebase
    # Return as text: "User asked: '...' You found: '...'"

# Then inject into system prompt
system_content = SYSTEM_PROMPT + "\n\n" + context_summary
messages = [{"role": "system", "content": system_content}]
messages.append({"role": "user", "content": current_question})
```

This approach:
- Avoids API validation issues entirely
- Provides sufficient context for follow-up questions
- Is simpler and more reliable

---

### Bug 4: Vector Search embedding model not found

**Symptom**: `databricks-bge-large-en` not found when creating index.

**Fix**: Used `databricks-gte-large-en` which was available in the workspace.

---

### Bug 5: 401 Credential Error

**Symptom**: Databricks Apps service principal couldn't access external model endpoints.

**Root Cause**: Service principal didn't have permissions for the Claude endpoint.

**Fix**: Added PAT token directly to app.yaml as `DATABRICKS_TOKEN` environment variable.

---

## 5. Key Implementation Details

### System Prompt (Context Engineering)

The system prompt defines agent behavior:
```
You are DataScope, a data debugging agent...

Investigation approach:
1. QUANTIFY: How many records affected?
2. TRACE: Where does the data come from?
3. COMPARE: Check bronze vs silver vs gold
4. SEARCH: Find the transformation code
5. EXPLAIN: Root cause + evidence + fix
```

### Guardrails

| Guardrail | Purpose |
|-----------|---------|
| Max 5 iterations | Prevent infinite loops |
| Forced summary phase | Guarantee final answer |
| Row limit (1000) | Prevent memory issues |
| Timeout (120s) | Prevent hanging |

### State Management (Lakebase)

Three Delta tables:
```sql
-- Conversation sessions
novatech.datascope.conversations (
  conversation_id, user_id, created_at, updated_at, title, status
)

-- Individual messages
novatech.datascope.messages (
  message_id, conversation_id, role, content,
  tool_calls, tool_call_id, created_at, tokens_used
)

-- Investigation metadata & analytics
novatech.datascope.investigations (
  investigation_id, conversation_id, question, status,
  started_at, completed_at, duration_seconds,
  tools_used, patterns_matched, tables_queried,
  root_cause_found, bug_id, summary
)
```

### Vector Search (Pattern Matching)

```python
def search_patterns(query: str) -> str:
    """Semantic search for similar data quality patterns."""
    response = requests.post(
        f"{DATABRICKS_HOST}/api/2.0/vector-search/indexes/{VS_INDEX}/query",
        json={
            "query_text": query,  # Natural language question
            "columns": ["pattern_id", "title", "symptoms",
                       "root_cause", "resolution", "investigation_sql"],
            "num_results": 3
        }
    )
    # Returns semantically similar patterns ranked by relevance
```

Patterns stored:
- PAT-001: Timezone Mismatch Between Source Systems
- PAT-002: Late-Arriving Data Not Reflected in Status
- PAT-003: Aggregation Excludes Relevant Records
- PAT-004: Duplicate Records Inflating Metrics
- PAT-005: NULL Values Not Handled in Conditional Logic

---

## 6. Files Reference

| File | Purpose |
|------|---------|
| `datascope-ui-app/app.py` | Main application - agent implementation |
| `datascope-ui-app/app.yaml` | Databricks App configuration (secrets) |
| `datascope-ui-app/app.yaml.example` | Template without secrets |
| `notebooks/setup_vector_search.py` | Creates Vector Search endpoint & index |
| `notebooks/setup_lakebase.py` | Creates state management tables |
| `github-mcp-app/simple_app.py` | GitHub code search server |
| `CLAUDE.md` | Project context for AI assistants |

---

## 7. Deployment

### Components Deployed

| Component | URL/Endpoint |
|-----------|--------------|
| DataScope UI | datascope-ui-1262935113136277.gcp.databricksapps.com |
| GitHub MCP Server | github-mcp-server-1262935113136277.gcp.databricksapps.com |
| Claude Endpoint | claude-sonnet-endpoint |
| Vector Search | datascope-vs-endpoint |
| SQL Warehouse | 7f129e1d1f34f3e2 |

### Environment Variables

```yaml
DATABRICKS_HOST: "https://1262935113136277.7.gcp.databricks.com"
LLM_ENDPOINT_NAME: "claude-sonnet-endpoint"
DATABRICKS_SQL_WAREHOUSE_ID: "7f129e1d1f34f3e2"
GITHUB_MCP_APP_URL: "https://github-mcp-server-..."
DATABRICKS_TOKEN: "<PAT token>"
VS_ENDPOINT_NAME: "datascope-vs-endpoint"
VS_INDEX_NAME: "novatech.gold.datascope_patterns_index"
LAKEBASE_ENABLED: "true"
LAKEBASE_CATALOG: "novatech"
LAKEBASE_SCHEMA: "datascope"
```

---

## 8. Sample Conversations

### Single-Turn Investigation

**User**: "Why do some customers have NULL churn_risk?"

**Agent Investigation**:
1. Searches patterns → Finds PAT-005 (NULL Values Not Handled)
2. Executes SQL → Counts 16 customers with NULL churn_risk
3. Searches code → Finds CASE statement missing ELSE clause

**Agent Response**:
> Found 16 customers with NULL churn_risk in gold.churn_predictions.
>
> **Root Cause**: The CASE statement in the churn calculation doesn't have an ELSE clause. Customers who don't match any WHEN condition get NULL.
>
> **Fix**: Add `ELSE 'unknown'` to the CASE statement in gold/churn_predictions.sql

### Multi-Turn Follow-Up

**User**: "Which specific customers are affected?"

**Agent** (with context from previous turn):
> Based on my earlier analysis, here are the affected customers:
> [Executes SQL to list customer IDs]
> CUST-017, CUST-023, CUST-045...

---

## 9. Monitoring

The `/stats` endpoint returns analytics from Lakebase:

```json
{
  "total_conversations": 45,
  "total_investigations": 127,
  "avg_duration_seconds": 23.5,
  "success_rate": 0.92,
  "most_common_tools": ["execute_sql", "search_patterns", "search_code"],
  "patterns_matched": ["PAT-005", "PAT-001", "PAT-003"]
}
```

---

## 10. Lessons Learned

### Technical Lessons

1. **LLM API constraints matter**: Anthropic's requirements around tool_call/tool_result pairing forced our multi-turn architecture.

2. **Force outputs when needed**: The two-phase approach (investigate with tools, then summarize without) ensures reliable responses.

3. **Context engineering > message replay**: Injecting summaries into system prompts is more reliable than reconstructing full conversation history.

4. **Start simple, add complexity as needed**: We removed MLflow monitoring when we realized Lakebase could handle both state and monitoring.

### Architectural Lessons

1. **Single agent is often sufficient**: Multi-agent systems add coordination complexity. Start with one agent and split only if needed.

2. **Not every feature adds value**: We skipped UC Lineage because SQL + code search already finds bugs effectively.

3. **Guardrails are essential**: Max iterations and forced summaries prevent the agent from getting stuck.

---

## 11. Future Enhancements

Potential improvements (not implemented):
- Add more patterns to Vector Search (currently 5)
- User authentication and multi-tenant support
- Streaming responses for better UX
- Integration with Slack/Teams
- Automated bug fix suggestions with PRs

---

## Final Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATABRICKS APPS                               │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              DataScope UI (app.py)                       │    │
│  │  - Chat interface                                        │    │
│  │  - Context summary injection                             │    │
│  │  - ReAct agent loop                                      │    │
│  │  - Two-phase response (investigate → summarize)          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│              ┌───────────────┼───────────────┐                  │
│              ▼               ▼               ▼                  │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │
│  │ search_patterns│  │  execute_sql  │  │  search_code  │       │
│  │ (Vector Search)│  │(SQL Warehouse)│  │ (GitHub MCP)  │       │
│  └───────────────┘  └───────────────┘  └───────────────┘       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         LAKEBASE                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  conversations  │  │    messages     │  │ investigations  │  │
│  │   (sessions)    │  │  (chat history) │  │  (analytics)    │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      VECTOR SEARCH                               │
│  Endpoint: datascope-vs-endpoint                                │
│  Index: novatech.gold.datascope_patterns_index                  │
│  Embeddings: databricks-gte-large-en                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CLAUDE (External Endpoint)                    │
│  Model: claude-sonnet-endpoint                                  │
│  Provider: Anthropic via Databricks External Models             │
└─────────────────────────────────────────────────────────────────┘
```

---

*Document created: December 2024*
*Repository: https://github.com/19kojoho/datascope-agent*
