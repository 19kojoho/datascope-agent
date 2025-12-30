# DataScope: Data Debugging Agent

## What This Project Is

A data debugging agent that investigates data quality issues in Databricks, inspired by Asana's Data Debugger. It answers questions like:

- "Why is customer XYZ marked as churn when they logged in yesterday?"
- "Why does ARR show $125M but Finance reports $165M?"
- "Why do some customers have NULL churn_risk?"

**Target: 4 hours → 20 minutes per investigation**

---

## Project Structure

```
datascope-project/
├── src/datascope/
│   ├── tools/           # Databricks tool implementations
│   │   ├── sql_tool.py       # Execute SQL queries
│   │   ├── schema_tool.py    # Get table schemas
│   │   ├── lineage_tool.py   # Get column/table lineage
│   │   └── pattern_tool.py   # Search similar patterns (Vector Search)
│   ├── agent/
│   │   ├── state.py          # Agent state definition
│   │   ├── graph.py          # LangGraph workflow
│   │   └── prompts.py        # System prompts
│   └── evaluation/
│       ├── judge.py          # Agent-as-judge evaluator
│       └── test_runner.py    # Run test cases
├── sql/gold/            # Transformation SQL (for code search)
├── config/
│   ├── test_cases.json       # 10 test cases with ground truth
│   └── pattern_library.json  # Common bug patterns
├── notebooks/           # Databricks notebooks
└── tests/               # Unit tests
```

---

## Environment

### Databricks Setup
- **Catalog**: `novatech`
- **Schemas**: `bronze`, `silver`, `gold`
- Tables have intentional bugs planted (see Bug Reference below)

### Required Environment Variables
```
DATABRICKS_HOST=https://xxx.cloud.databricks.com
DATABRICKS_TOKEN=dapi...
DATABRICKS_SQL_WAREHOUSE_ID=...
```

---

## Bug Reference

These bugs are planted in the `novatech` catalog for testing:

| Bug ID | Table | Issue | Question It Produces |
|--------|-------|-------|---------------------|
| BUG-001 | `gold.churn_predictions` | Timezone mismatch (PST vs UTC) | "Why is customer marked as churn when they logged in yesterday?" |
| BUG-002 | `gold.payment_status_summary` | Uses payment_date instead of processed_at | "Why does payment show as cleared when it hasn't?" |
| BUG-003 | `gold.arr_by_customer` | WHERE clause excludes addon products | "Why does ARR show $125M but Finance says $165M?" |
| BUG-004 | `gold.revenue_recognition` | No deduplication on payment_id | "Why is revenue 3% higher than Stripe?" |
| BUG-005 | `gold.churn_predictions` | CASE statement missing ELSE clause | "Why do some customers have NULL churn_risk?" |
| BUG-006 | `gold.customer_health_scores` | 1:N join creates row multiplication | "Why did health score change overnight?" |
| BUG-007 | `bronze.salesforce_accounts_raw` | New column with NULLs (schema drift) | "Why did records fail to load?" |

---

## Development Approach

### Phase 1: Tools (Current)
Build and test individual tools:
1. `sql_tool.py` - Execute queries, return results
2. `schema_tool.py` - Get table/column metadata from Unity Catalog
3. `lineage_tool.py` - Get upstream/downstream lineage
4. `pattern_tool.py` - Vector search for similar past issues

### Phase 2: Agent Core
Wire tools into LangGraph agent:
1. Define state schema
2. Build investigation workflow
3. Add context assembly logic

### Phase 3: Evaluation
Test against ground truth:
1. Run all 10 test cases
2. Score with agent-as-judge
3. Iterate on prompts/tools

### Phase 4: Deploy
1. Register as MLflow model
2. Deploy to Model Serving
3. Add Streamlit UI

---

## Key Files to Know

### `config/test_cases.json`
Contains 10 test cases, each with:
- Question to ask
- Expected bug ID
- Expected tables to investigate
- Success criteria

### `sql/gold/*.sql`
Transformation SQL files - the agent should be able to search these to find bugs in the code.

### `src/datascope/agent/prompts.py`
System prompts that define agent behavior. Critical for investigation quality.

---

## Commands

```bash
# Install dependencies
pip install -e .

# Run a single investigation
python -m datascope.cli "Why do some customers have NULL churn_risk?"

# Run all test cases
python -m datascope.evaluation.test_runner

# Start local UI
streamlit run src/datascope/ui/app.py
```

---

## Code Style

- Python 3.10+
- Type hints everywhere
- Async for tool calls
- Pydantic for data models
- Keep functions small and testable

---

## When Building Tools

Each tool should:
1. Have clear input/output types
2. Handle errors gracefully (return error message, don't crash)
3. Log what it's doing (for tracing)
4. Return structured data (not just strings)

Example:
```python
class SQLToolResult(BaseModel):
    query: str
    columns: list[str]
    rows: list[dict]
    row_count: int
    execution_time_ms: int
    error: str | None = None
```

---

## When Building the Agent

The agent should:
1. **Quantify first** - Always count affected records
2. **Trace lineage** - Find where data comes from
3. **Compare layers** - Check bronze vs silver vs gold
4. **Search code** - Find the transformation that causes the bug
5. **Explain clearly** - Root cause + evidence + fix

---

## Current Task

Check `TODO.md` for the current task list.
