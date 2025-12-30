# DataScope TODO

## Phase 1: Tools ← START HERE

### SQL Tool
- [ ] Implement `execute_query()` using Databricks SDK
- [ ] Add result formatting (rows → dict)
- [ ] Add query timeout handling
- [ ] Add error handling for invalid SQL
- [ ] Test with simple query: `SELECT COUNT(*) FROM novatech.gold.churn_predictions`

### Schema Tool  
- [ ] Implement `get_table_schema()` - columns, types, comments
- [ ] Implement `list_tables()` - tables in a schema
- [ ] Use Unity Catalog API via Databricks SDK
- [ ] Test: get schema for `novatech.gold.churn_predictions`

### Lineage Tool
- [ ] Implement `get_column_lineage()` - upstream sources for a column
- [ ] Implement `get_table_lineage()` - upstream/downstream tables
- [ ] Use Unity Catalog lineage API
- [ ] Test: get lineage for `churn_risk` column

### Pattern Tool (Vector Search)
- [ ] Create Vector Search index from `pattern_library.json`
- [ ] Implement `search_patterns()` - semantic search
- [ ] Test: search for "NULL values CASE statement"

---

## Phase 2: Agent Core

### State Definition
- [ ] Define `AgentState` TypedDict
- [ ] Include: question, retrieved_context, hypotheses, evidence, findings

### Prompts
- [ ] Write classification prompt (categorize question)
- [ ] Write analysis prompt (generate hypotheses from evidence)
- [ ] Write synthesis prompt (generate final report)

### LangGraph Workflow
- [ ] Define nodes: classify, retrieve, analyze, investigate, synthesize
- [ ] Define edges and conditional routing
- [ ] Add tool node for SQL/schema/lineage calls
- [ ] Test with BUG-005 question

---

## Phase 3: Evaluation

### Test Runner
- [ ] Load test cases from JSON
- [ ] Run agent on each question
- [ ] Collect responses and timing

### Agent-as-Judge
- [ ] Write evaluation prompt
- [ ] Score: root_cause, evidence, tables, fix, efficiency
- [ ] Generate pass/fail and feedback

### Metrics Dashboard
- [ ] Track: latency, token usage, pass rate, avg score
- [ ] Log to MLflow

---

## Phase 4: Deploy

### Model Registration
- [ ] Wrap agent as MLflow pyfunc model
- [ ] Log to MLflow Model Registry

### Model Serving
- [ ] Deploy to Databricks Model Serving
- [ ] Test endpoint with curl

### UI
- [ ] Streamlit chat interface
- [ ] Show investigation steps
- [ ] Display SQL queries executed
- [ ] Link to MLflow traces

---

## Current Focus

**TODAY: Get SQL Tool working**

1. Copy `.env.example` to `.env` and fill in credentials
2. Implement `sql_tool.py`
3. Test with: `python -c "from datascope.tools.sql_tool import SQLTool; ..."`

Once SQL works, the rest builds on top of it.
