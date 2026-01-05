# DataScope LangGraph Agent

A production-ready data debugging agent built with LangGraph. This is the LangGraph-based version of DataScope, running alongside the pure Python implementation for comparison.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HTTP Server (app.py)                      │
│  - Chat UI                                                   │
│  - /chat, /health, /stats endpoints                         │
│  - Lakebase analytics                                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              LangGraph ReAct Agent (graph.py)                │
│  - Persistent state via SQLite checkpointer                 │
│  - Automatic multi-turn conversation handling               │
│  - Recursion limit for iteration control                    │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ search_patterns │ │   execute_sql   │ │   search_code   │
│ (Vector Search) │ │ (SQL Warehouse) │ │  (GitHub MCP)   │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Quick Start

### 1. Install Dependencies

```bash
cd datascope-langgraph-app
pip install -r requirements.txt
```

### 2. Configure Environment

Copy the example config and fill in your values:

```bash
cp app.yaml.example app.yaml
# Edit app.yaml with your Databricks credentials
```

Or set environment variables directly:

```bash
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"
export DATABRICKS_TOKEN="dapi..."
export DATABRICKS_SQL_WAREHOUSE_ID="your-warehouse-id"
export LLM_ENDPOINT_NAME="claude-sonnet-endpoint"
```

### 3. Run Locally

```bash
python app.py
```

Open http://localhost:8000 in your browser.

## Deployment to Databricks Apps

```bash
databricks apps create datascope-langgraph --source-code-path ./datascope-langgraph-app
databricks apps deploy datascope-langgraph
```

## Key Features

### Persistent Conversations
Conversations are stored in a SQLite database and survive server restarts:
```
checkpoints/conversations.db
```

### Multi-User Support
Each conversation gets a unique `thread_id`, providing isolation between users.

### Galileo AI Observability
Built-in observability with Galileo AI for:
- **Tracing** - Every LLM call and tool execution is traced
- **Debugging** - Identify slow tools, failed calls, and reasoning paths
- **Evaluation** - Measure investigation quality and accuracy
- **Cost Tracking** - Token counting for cost attribution

Enable by setting `GALILEO_API_KEY` environment variable. Get your key from [Galileo Console](https://console.galileo.ai/).

### Production-Ready
- Error handling with graceful degradation
- Request logging
- Health checks with dependency verification
- Graceful shutdown on SIGTERM
- Galileo observability for debugging

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Chat UI |
| `/chat` | POST | Send investigation request |
| `/health` | GET | Health check |
| `/stats` | GET | Analytics from Lakebase |

### Chat Request

```json
POST /chat
{
  "question": "Why do some customers have NULL churn_risk?",
  "conversation_id": "optional-uuid-for-follow-ups"
}
```

### Chat Response

```json
{
  "response": "**What I Found:** 16 customers have NULL churn_risk...",
  "conversation_id": "uuid",
  "duration_seconds": 12.5
}
```

## Comparison with Pure Python Version

| Aspect | Pure Python | LangGraph |
|--------|-------------|-----------|
| Location | `datascope-ui-app/` | `datascope-langgraph-app/` |
| Multi-turn | Manual context injection | Automatic via checkpointer |
| State | Lakebase (custom) | SQLite (built-in) |
| Tool loop | Manual for-loop | ReAct agent |
| Lines of code | ~1000 | ~400 |

## Troubleshooting

### Agent fails to initialize
Check that all required environment variables are set:
- `DATABRICKS_HOST`
- `DATABRICKS_TOKEN`
- `DATABRICKS_SQL_WAREHOUSE_ID`

### Tools not working
Verify the endpoints are accessible:
- LLM endpoint: Check Model Serving in Databricks
- SQL Warehouse: Check if warehouse is running
- Vector Search: Check if index exists
- GitHub MCP: Check if app is deployed

### Conversation not persisting
Check that `checkpoints/` directory is writable and not gitignored in deployment.

## File Structure

```
datascope-langgraph-app/
├── app.py              # HTTP server and main entry point
├── agent/
│   ├── __init__.py     # Package exports
│   ├── config.py       # Configuration management
│   ├── graph.py        # LangGraph agent definition
│   ├── tools.py        # Tool implementations
│   ├── prompts.py      # System prompts
│   └── observability.py # Galileo AI tracing
├── checkpoints/        # SQLite database (gitignored)
├── requirements.txt    # Python dependencies
├── app.yaml.example    # Config template
└── README.md           # This file
```

## Observability with Galileo AI

### What Gets Traced

Every investigation creates a **trace** containing:
- User question (input)
- LLM calls (model, messages, latency, tokens)
- Tool calls (name, args, result, latency)
- Final answer (output)

### Viewing Traces

After enabling Galileo:
1. Log into [Galileo Console](https://console.galileo.ai/)
2. Navigate to your project (`datascope-langgraph`)
3. View traces in the **Investigations** log stream
4. Click any trace to see the full execution flow

### Evaluation Metrics

Use the `log_evaluation` function to track investigation quality:

```python
from agent import log_evaluation

await log_evaluation(
    trace_id="...",
    metrics={
        "accuracy": 0.9,      # Did agent find correct root cause?
        "completeness": 0.8,  # Did it answer all aspects?
        "efficiency": 0.7,    # Did it use minimal tools?
    }
)
```

### Interview Talking Points

If asked about Galileo in an interview:
- **Why observability?** LLM apps are non-deterministic; you need to see what's happening
- **What we trace:** Every LLM call and tool execution with timing and tokens
- **Debugging example:** "The investigation was slow because SQL took 15 seconds"
- **Evaluation:** Track accuracy over time to measure improvement
