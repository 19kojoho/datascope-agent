# Data Debugger: End-to-End Architecture

## The Question We're Solving

```
User: "Why do some customers have NULL churn_risk?"
```

This document walks through **every step** from user input to final answer, covering:
- Retrieval mechanisms
- Context engineering
- State & memory management
- Evaluation
- Tracing & observability

---

# PHASE 1: USER INPUT & QUERY UNDERSTANDING

## Step 1.1: User Submits Question

```
┌─────────────────────────────────────────────────────────────────┐
│  Databricks App (Streamlit / Gradio)                            │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  "Why do some customers have NULL churn_risk?"            │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Agent Endpoint (Model Serving)                           │  │
│  │  POST /serving-endpoints/datascope-agent/invocations      │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**What happens:**
1. User types question in Streamlit UI
2. App calls Model Serving endpoint
3. Request includes: `{"messages": [{"role": "user", "content": "Why do some customers have NULL churn_risk?"}]}`

## Step 1.2: Query Classification & Intent Detection

```python
# First LLM call - classify the question
CLASSIFICATION_PROMPT = """
Classify this data debugging question:

Question: {user_question}

Categories:
1. METRIC_DISCREPANCY - Numbers don't match between sources
2. CLASSIFICATION_ERROR - Entity has wrong status/category  
3. DATA_QUALITY - Missing, NULL, or invalid values
4. UNEXPECTED_CHANGE - Value changed without explanation
5. PIPELINE_FAILURE - Data didn't load or transform correctly

Also extract:
- Entities mentioned (tables, columns, customers)
- Symptoms described
- Timeframe if any

Return JSON.
"""
```

**Output for our question:**
```json
{
  "category": "DATA_QUALITY",
  "entities": {
    "columns": ["churn_risk"],
    "tables": ["churn_predictions"],  // inferred
    "customers": "some customers"      // ambiguous
  },
  "symptoms": ["NULL values", "missing data"],
  "timeframe": null,
  "confidence": 0.92
}
```

**MLflow Trace (Span 1):**
```
span_name: "query_classification"
inputs: {"question": "Why do some customers have NULL churn_risk?"}
outputs: {"category": "DATA_QUALITY", "entities": {...}}
latency_ms: 245
tokens_used: 156
```

---

# PHASE 2: RETRIEVAL

## Step 2.1: Parallel Retrieval Strategy

Based on classification, agent decides which retrievers to call **in parallel**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         RETRIEVAL ORCHESTRATOR                          │
│                                                                         │
│   Question: "Why do some customers have NULL churn_risk?"               │
│   Category: DATA_QUALITY                                                │
│                                                                         │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│   │   Vector    │  │   Unity     │  │    SQL      │  │   GitHub    │   │
│   │   Search    │  │   Catalog   │  │  Warehouse  │  │    MCP      │   │
│   │   (Async)   │  │    MCP      │  │   (Async)   │  │   (Async)   │   │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│          │                │                │                │          │
│          ▼                ▼                ▼                ▼          │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│   │  Similar    │  │   Table     │  │  Sample     │  │  Transform  │   │
│   │  Patterns   │  │   Schema    │  │   Data      │  │    Code     │   │
│   │  + Past     │  │  + Lineage  │  │  + Stats    │  │  + Comments │   │
│   │   Issues    │  │             │  │             │  │             │   │
│   └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Step 2.2: Vector Search Retrieval (Pattern Matching)

**Purpose:** Find similar past issues and known patterns

```python
# Vector Search query
from databricks.vector_search.client import VectorSearchClient

vsc = VectorSearchClient()
index = vsc.get_index(
    endpoint_name="datascope-vs-endpoint",
    index_name="novatech.ml.pattern_library_index"
)

# Semantic search for similar patterns
results = index.similarity_search(
    query_text="NULL values in churn_risk column missing data",
    columns=["pattern_id", "title", "symptoms", "root_cause", 
             "investigation_sql", "resolution"],
    num_results=3,
    filters={"category": ["Data Quality", "Business Logic"]}  # optional filter
)
```

**What gets retrieved:**
```json
{
  "results": [
    {
      "pattern_id": "PAT-005",
      "title": "NULL Values Not Handled in Conditional Logic",
      "score": 0.89,
      "symptoms": ["NULL values in fields that should have values", 
                   "CASE statements returning unexpected NULL"],
      "root_cause": "CASE statements don't handle NULL cases. Missing ELSE clause.",
      "investigation_sql": "SELECT COUNT(*) FROM gold.churn_predictions WHERE churn_risk IS NULL",
      "resolution": "Add ELSE clause to CASE statements. Use COALESCE()."
    },
    {
      "pattern_id": "PAT-008", 
      "title": "Incorrect Customer Classification",
      "score": 0.72,
      "symptoms": ["Risk level doesn't match behavior"],
      "root_cause": "Classification logic doesn't match current business rules."
    }
  ]
}
```

**MLflow Trace (Span 2a):**
```
span_name: "vector_search_retrieval"
inputs: {"query": "NULL values in churn_risk...", "num_results": 3}
outputs: {"patterns_found": 2, "top_score": 0.89}
latency_ms: 127
```

## Step 2.3: Unity Catalog MCP Retrieval (Schema + Lineage)

**Purpose:** Get table structure and data lineage

```python
# Unity Catalog MCP tool calls
# Tool 1: Get table schema
uc_schema = unity_catalog_mcp.get_table_schema(
    catalog="novatech",
    schema="gold", 
    table="churn_predictions"
)

# Tool 2: Get column lineage
uc_lineage = unity_catalog_mcp.get_column_lineage(
    catalog="novatech",
    schema="gold",
    table="churn_predictions",
    column="churn_risk"
)

# Tool 3: Get upstream tables
uc_upstream = unity_catalog_mcp.get_table_lineage(
    catalog="novatech",
    schema="gold",
    table="churn_predictions",
    direction="upstream"
)
```

**Schema Retrieved:**
```sql
CREATE TABLE novatech.gold.churn_predictions (
    customer_id STRING NOT NULL,
    company_name STRING,
    segment STRING,
    region STRING,
    last_activity TIMESTAMP,
    avg_logins DOUBLE,           -- Can be NULL (from LEFT JOIN)
    total_api_calls BIGINT,
    churn_risk STRING,           -- THE PROBLEMATIC COLUMN
    is_predicted_churn BOOLEAN,
    days_since_activity INT,
    prediction_date TIMESTAMP,
    model_version STRING
)
COMMENT 'Customer churn risk predictions based on 30-day lookback'
```

**Lineage Retrieved:**
```
┌──────────────────────────────────────────────────────────────────┐
│  COLUMN LINEAGE: gold.churn_predictions.churn_risk               │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  silver.fct_product_usage.logins                                 │
│           │                                                      │
│           ▼                                                      │
│  [AVG aggregation in CTE "recent_activity"]                      │
│           │                                                      │
│           ▼                                                      │
│  avg_logins (intermediate)                                       │
│           │                                                      │
│           ▼                                                      │
│  [CASE statement - conditional logic]                            │
│           │                                                      │
│           ▼                                                      │
│  gold.churn_predictions.churn_risk                               │
│                                                                  │
│  UPSTREAM TABLES:                                                │
│  ├── silver.dim_customers                                        │
│  ├── silver.fct_product_usage                                    │
│  └── silver.fct_payments                                         │
│                                                                  │
│  JOIN TYPE: LEFT JOIN (can produce NULLs)                        │
└──────────────────────────────────────────────────────────────────┘
```

**MLflow Trace (Span 2b):**
```
span_name: "unity_catalog_retrieval"
inputs: {"table": "novatech.gold.churn_predictions", "column": "churn_risk"}
outputs: {"schema_columns": 12, "upstream_tables": 3, "join_type": "LEFT JOIN"}
latency_ms: 89
```

## Step 2.4: SQL Warehouse Retrieval (Data Profiling)

**Purpose:** Get actual data statistics and samples

```python
# SQL queries executed via Databricks SQL Warehouse
sql_tool = SQLWarehouseTool(warehouse_id="abc123")

# Query 1: Count NULLs
null_count_query = """
SELECT 
    COUNT(*) as total_customers,
    SUM(CASE WHEN churn_risk IS NULL THEN 1 ELSE 0 END) as null_count,
    ROUND(SUM(CASE WHEN churn_risk IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) as null_pct
FROM novatech.gold.churn_predictions
"""

# Query 2: Sample NULL records
sample_nulls_query = """
SELECT customer_id, company_name, segment, avg_logins, churn_risk, last_activity
FROM novatech.gold.churn_predictions
WHERE churn_risk IS NULL
LIMIT 10
"""

# Query 3: Compare with upstream
upstream_check_query = """
SELECT 
    c.customer_id,
    c.company_name,
    p.avg_logins,
    p.last_activity
FROM novatech.silver.dim_customers c
LEFT JOIN (
    SELECT customer_id, AVG(logins) as avg_logins, MAX(usage_timestamp) as last_activity
    FROM novatech.silver.fct_product_usage
    WHERE usage_timestamp > current_timestamp() - INTERVAL 30 DAYS
    GROUP BY customer_id
) p ON c.customer_id = p.customer_id
WHERE p.avg_logins IS NULL
LIMIT 10
"""
```

**Results Retrieved:**
```
Query 1 - NULL Count:
┌─────────────────┬────────────┬──────────┐
│ total_customers │ null_count │ null_pct │
├─────────────────┼────────────┼──────────┤
│ 500             │ 19         │ 3.80     │
└─────────────────┴────────────┴──────────┘

Query 2 - Sample NULLs:
┌─────────────┬─────────────────┬─────────┬───────────┬────────────┬───────────────┐
│ customer_id │ company_name    │ segment │ avg_logins│ churn_risk │ last_activity │
├─────────────┼─────────────────┼─────────┼───────────┼────────────┼───────────────┤
│ CUST-00023  │ Smith LLC       │ SMB     │ NULL      │ NULL       │ NULL          │
│ CUST-00089  │ Johnson Inc     │ Startup │ NULL      │ NULL       │ NULL          │
│ CUST-00142  │ Williams Corp   │ SMB     │ NULL      │ NULL       │ NULL          │
└─────────────┴─────────────────┴─────────┴───────────┴────────────┴───────────────┘

** KEY INSIGHT: avg_logins is also NULL for these customers! **
```

**MLflow Trace (Span 2c):**
```
span_name: "sql_data_profiling"
inputs: {"queries_executed": 3}
outputs: {"null_count": 19, "null_pct": 3.8, "pattern_found": "avg_logins also NULL"}
latency_ms: 342
sql_warehouse_id: "abc123"
```

## Step 2.5: GitHub MCP Retrieval (Transformation Code)

**Purpose:** Find the actual SQL that creates the churn_risk column

```python
# GitHub MCP search for transformation code
github_mcp = GitHubMCPTool(
    repo="novatech-org/data-transformations",
    branch="main"
)

# Search for files containing churn_risk
code_search = github_mcp.search_code(
    query="churn_risk CASE WHEN",
    file_extensions=[".sql", ".py"],
    max_results=5
)

# Get specific file content
file_content = github_mcp.get_file(
    path="sql/gold/churn_predictions.sql"
)
```

**Code Retrieved:**
```sql
-- From: sql/gold/churn_predictions.sql (lines 45-52)

    -- Churn risk classification
    -- Based on average daily logins in the lookback period
    CASE 
        WHEN ra.avg_logins > 20 THEN 'Low Risk'
        WHEN ra.avg_logins > 5 THEN 'Medium Risk'
        WHEN ra.avg_logins <= 5 THEN 'High Risk'
        -- BUG: Missing ELSE clause!
        -- Customers with NULL avg_logins get NULL churn_risk
    END as churn_risk,
```

**MLflow Trace (Span 2d):**
```
span_name: "github_code_retrieval"
inputs: {"query": "churn_risk CASE WHEN", "repo": "novatech-org/data-transformations"}
outputs: {"files_found": 1, "file": "sql/gold/churn_predictions.sql", "lines": "45-52"}
latency_ms: 203
```

---

# PHASE 3: CONTEXT ENGINEERING

## Step 3.1: Context Assembly

Now we assemble all retrieved information into a structured context for the LLM:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CONTEXT WINDOW BUDGET                            │
│                        Total: 128K tokens                               │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ SYSTEM PROMPT (fixed)                               ~2,000 tokens│   │
│  │ - Agent role and capabilities                                    │   │
│  │ - Available tools                                                │   │
│  │ - Output format instructions                                     │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ RETRIEVED CONTEXT (dynamic)                        ~15,000 tokens│   │
│  │ ┌─────────────────────────────────────────────────────────────┐ │   │
│  │ │ Vector Search Results                         ~2,000 tokens │ │   │
│  │ │ - Pattern PAT-005: NULL handling                            │ │   │
│  │ │ - Pattern PAT-008: Classification errors                    │ │   │
│  │ └─────────────────────────────────────────────────────────────┘ │   │
│  │ ┌─────────────────────────────────────────────────────────────┐ │   │
│  │ │ Unity Catalog Schema                          ~1,500 tokens │ │   │
│  │ │ - Table: gold.churn_predictions (12 columns)                │ │   │
│  │ │ - Upstream: dim_customers, fct_product_usage, fct_payments  │ │   │
│  │ └─────────────────────────────────────────────────────────────┘ │   │
│  │ ┌─────────────────────────────────────────────────────────────┐ │   │
│  │ │ Column Lineage                                ~1,000 tokens │ │   │
│  │ │ - churn_risk ← CASE on avg_logins ← AVG(logins) ← LEFT JOIN │ │   │
│  │ └─────────────────────────────────────────────────────────────┘ │   │
│  │ ┌─────────────────────────────────────────────────────────────┐ │   │
│  │ │ SQL Query Results                             ~2,500 tokens │ │   │
│  │ │ - NULL count: 19 customers (3.8%)                           │ │   │
│  │ │ - Sample records with NULL avg_logins                       │ │   │
│  │ │ - Upstream comparison data                                  │ │   │
│  │ └─────────────────────────────────────────────────────────────┘ │   │
│  │ ┌─────────────────────────────────────────────────────────────┐ │   │
│  │ │ Transformation Code                           ~3,000 tokens │ │   │
│  │ │ - churn_predictions.sql (full file)                         │ │   │
│  │ │ - Highlighted: CASE statement without ELSE                  │ │   │
│  │ └─────────────────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ CONVERSATION HISTORY                               ~1,000 tokens│   │
│  │ - User question                                                  │   │
│  │ - (If multi-turn: previous exchanges)                           │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ RESERVED FOR RESPONSE                             ~10,000 tokens│   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  TOTAL USED: ~28,000 tokens (22% of context)                           │
└─────────────────────────────────────────────────────────────────────────┘
```

## Step 3.2: Context Prioritization & Truncation

```python
class ContextEngineer:
    """Manages context window for the agent."""
    
    MAX_CONTEXT_TOKENS = 128000
    RESERVED_FOR_RESPONSE = 10000
    SYSTEM_PROMPT_TOKENS = 2000
    
    def __init__(self):
        self.available_tokens = (
            self.MAX_CONTEXT_TOKENS - 
            self.RESERVED_FOR_RESPONSE - 
            self.SYSTEM_PROMPT_TOKENS
        )  # = 116,000 tokens available for retrieval
    
    def prioritize_context(self, retrieved_data: dict) -> str:
        """
        Prioritize what goes into context based on:
        1. Relevance score (from Vector Search)
        2. Recency (prefer recent patterns)
        3. Specificity (exact table match > general pattern)
        """
        
        priority_order = [
            ("sql_results", 0.95),      # Most specific to this question
            ("transformation_code", 0.90),  # Shows actual bug
            ("column_lineage", 0.85),    # Explains data flow
            ("table_schema", 0.80),      # Context for understanding
            ("vector_patterns", 0.75),   # Similar past issues
            ("conversation_history", 0.70)  # Previous context
        ]
        
        context_parts = []
        tokens_used = 0
        
        for source, priority in priority_order:
            if source in retrieved_data:
                source_tokens = count_tokens(retrieved_data[source])
                
                if tokens_used + source_tokens <= self.available_tokens:
                    context_parts.append(self.format_source(source, retrieved_data[source]))
                    tokens_used += source_tokens
                else:
                    # Truncate this source to fit
                    remaining = self.available_tokens - tokens_used
                    truncated = self.truncate_to_tokens(retrieved_data[source], remaining)
                    context_parts.append(self.format_source(source, truncated))
                    break
        
        return "\n\n".join(context_parts)
```

## Step 3.3: Assembled Context (What LLM Sees)

```xml
<system>
You are a Data Debugging Agent for NovaTech's data platform. Your job is to 
investigate data quality issues by analyzing table schemas, lineage, code, 
and data samples.

Available Tools:
- execute_sql: Run SQL queries against Databricks warehouse
- search_patterns: Find similar past issues in pattern library  
- get_lineage: Get column/table lineage from Unity Catalog
- search_code: Search transformation code in GitHub

When investigating, always:
1. Quantify the problem (how many records affected?)
2. Trace the data lineage to find where the issue originates
3. Provide the root cause with evidence
4. Suggest a fix

Output format: Structured markdown with SQL evidence.
</system>

<retrieved_context>

## Similar Past Patterns (from Vector Search)

### Pattern PAT-005: NULL Values Not Handled in Conditional Logic
- **Symptoms**: NULL values in fields that should have values, CASE statements returning unexpected NULL
- **Root Cause**: CASE statements don't handle NULL cases. Missing ELSE clause.
- **Resolution**: Add ELSE clause to CASE statements. Use COALESCE().
- **Relevance Score**: 0.89

---

## Table Schema (from Unity Catalog)

**Table**: novatech.gold.churn_predictions
| Column | Type | Nullable | Comment |
|--------|------|----------|---------|
| customer_id | STRING | NO | Primary key |
| company_name | STRING | YES | |
| avg_logins | DOUBLE | YES | Can be NULL from LEFT JOIN |
| churn_risk | STRING | YES | **THE PROBLEMATIC COLUMN** |
| ... | ... | ... | ... |

**Upstream Tables**: 
- silver.dim_customers (LEFT JOIN)
- silver.fct_product_usage (LEFT JOIN → aggregation)
- silver.fct_payments (LEFT JOIN)

---

## Column Lineage (from Unity Catalog)

```
silver.fct_product_usage.logins 
    → AVG() aggregation 
    → avg_logins (CTE) 
    → CASE statement 
    → churn_risk
    
JOIN TYPE: LEFT JOIN (produces NULLs when no matching records)
```

---

## Data Profiling Results (from SQL Warehouse)

**Query 1**: NULL count in churn_risk
```
total_customers: 500
null_count: 19
null_pct: 3.80%
```

**Query 2**: Sample records with NULL churn_risk
| customer_id | avg_logins | churn_risk | last_activity |
|-------------|------------|------------|---------------|
| CUST-00023 | NULL | NULL | NULL |
| CUST-00089 | NULL | NULL | NULL |
| CUST-00142 | NULL | NULL | NULL |

**Key Finding**: All records with NULL churn_risk also have NULL avg_logins!

---

## Transformation Code (from GitHub)

**File**: sql/gold/churn_predictions.sql
```sql
-- Lines 45-52
    CASE 
        WHEN ra.avg_logins > 20 THEN 'Low Risk'
        WHEN ra.avg_logins > 5 THEN 'Medium Risk'
        WHEN ra.avg_logins <= 5 THEN 'High Risk'
        -- ⚠️ NO ELSE CLAUSE - NULL avg_logins returns NULL
    END as churn_risk,
```

</retrieved_context>

<user>
Why do some customers have NULL churn_risk?
</user>
```

---

# PHASE 4: STATE MANAGEMENT

## Step 4.1: Agent State Schema

```python
from typing import TypedDict, List, Optional, Literal
from langgraph.graph import StateGraph

class AgentState(TypedDict):
    """State maintained throughout the investigation."""
    
    # Input
    original_question: str
    question_category: str
    
    # Retrieval results
    vector_search_results: List[dict]
    schema_info: dict
    lineage_info: dict
    sql_results: List[dict]
    code_snippets: List[dict]
    
    # Investigation progress
    hypotheses: List[str]
    evidence: List[dict]
    queries_executed: List[str]
    tools_called: List[str]
    
    # Findings
    root_cause: Optional[str]
    affected_records: Optional[int]
    impact_assessment: Optional[str]
    recommended_fix: Optional[str]
    
    # Control flow
    current_step: Literal["retrieve", "analyze", "investigate", "synthesize", "complete"]
    iteration_count: int
    max_iterations: int
    should_continue: bool
    
    # Output
    final_response: Optional[str]
    confidence_score: Optional[float]
    
    # Tracing
    trace_id: str
    span_ids: List[str]
```

## Step 4.2: State Transitions

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          STATE MACHINE                                   │
│                                                                         │
│   ┌──────────┐    ┌──────────┐    ┌─────────────┐    ┌───────────┐     │
│   │  START   │───▶│ RETRIEVE │───▶│   ANALYZE   │───▶│INVESTIGATE│     │
│   └──────────┘    └──────────┘    └─────────────┘    └─────┬─────┘     │
│                                                            │            │
│                                          ┌─────────────────┘            │
│                                          │                              │
│                                          ▼                              │
│                                   ┌─────────────┐                       │
│                                   │ Need more   │                       │
│                              NO   │   data?     │  YES                  │
│                         ┌────────▶│             │◀────────┐             │
│                         │         └──────┬──────┘         │             │
│                         │                │                │             │
│                         │                ▼                │             │
│                   ┌─────┴─────┐    ┌──────────┐    ┌─────┴─────┐       │
│                   │SYNTHESIZE │◀───│ RUN MORE │───▶│ INVESTIGATE│       │
│                   │           │    │  QUERIES │    │  (loop)   │       │
│                   └─────┬─────┘    └──────────┘    └───────────┘       │
│                         │                                               │
│                         ▼                                               │
│                   ┌───────────┐                                         │
│                   │ COMPLETE  │                                         │
│                   └───────────┘                                         │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Step 4.3: State After Each Phase

```python
# AFTER RETRIEVAL PHASE
state = {
    "original_question": "Why do some customers have NULL churn_risk?",
    "question_category": "DATA_QUALITY",
    "current_step": "analyze",
    "iteration_count": 1,
    
    "vector_search_results": [
        {"pattern_id": "PAT-005", "score": 0.89, "title": "NULL Values Not Handled..."}
    ],
    "schema_info": {
        "table": "novatech.gold.churn_predictions",
        "columns": [...],
        "nullable_columns": ["avg_logins", "churn_risk", "last_activity"]
    },
    "lineage_info": {
        "churn_risk": {
            "upstream": ["fct_product_usage.logins"],
            "transformations": ["AVG", "CASE"],
            "join_type": "LEFT JOIN"
        }
    },
    "sql_results": [
        {"query": "NULL count", "null_count": 19, "null_pct": 3.8}
    ],
    "code_snippets": [
        {"file": "churn_predictions.sql", "lines": "45-52", "content": "CASE WHEN..."}
    ],
    
    "hypotheses": [],
    "evidence": [],
    "root_cause": None,
    "should_continue": True
}

# AFTER ANALYSIS PHASE
state["hypotheses"] = [
    "H1: CASE statement missing ELSE clause (high confidence - matches pattern PAT-005)",
    "H2: LEFT JOIN produces NULL avg_logins for customers with no recent activity",
    "H3: NULL propagation from upstream avg_logins to churn_risk"
]
state["current_step"] = "investigate"

# AFTER INVESTIGATION PHASE
state["evidence"] = [
    {
        "hypothesis": "H1",
        "query": "SELECT churn_risk, avg_logins FROM gold.churn_predictions WHERE churn_risk IS NULL",
        "result": "All 19 NULL churn_risk records also have NULL avg_logins",
        "confirms": True
    },
    {
        "hypothesis": "H2", 
        "query": "SELECT c.customer_id FROM dim_customers c LEFT JOIN (...) WHERE avg_logins IS NULL",
        "result": "19 customers have no activity in last 30 days",
        "confirms": True
    }
]
state["root_cause"] = "BUG-005: CASE statement missing ELSE clause"
state["affected_records"] = 19
state["current_step"] = "synthesize"

# AFTER SYNTHESIS PHASE
state["final_response"] = "..." # Full markdown response
state["confidence_score"] = 0.95
state["recommended_fix"] = "Add ELSE 'High Risk' to CASE statement"
state["current_step"] = "complete"
state["should_continue"] = False
```

---

# PHASE 5: MEMORY MANAGEMENT

## Step 5.1: Short-Term Memory (Conversation Context)

```python
class ShortTermMemory:
    """Manages current conversation context."""
    
    def __init__(self, max_turns: int = 10):
        self.messages: List[dict] = []
        self.max_turns = max_turns
    
    def add_message(self, role: str, content: str, metadata: dict = None):
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        })
        
        # Sliding window - keep last N turns
        if len(self.messages) > self.max_turns * 2:
            # Keep system message + last N turns
            self.messages = [self.messages[0]] + self.messages[-(self.max_turns * 2):]
    
    def get_context(self) -> List[dict]:
        return self.messages
    
    def summarize_if_needed(self, llm) -> str:
        """Compress old messages if context getting too long."""
        if len(self.messages) > self.max_turns:
            old_messages = self.messages[1:-self.max_turns]
            summary = llm.summarize(old_messages)
            
            # Replace old messages with summary
            self.messages = [
                self.messages[0],  # System prompt
                {"role": "system", "content": f"Previous conversation summary: {summary}"},
                *self.messages[-self.max_turns:]
            ]
```

## Step 5.2: Long-Term Memory (Pattern Storage)

```python
class LongTermMemory:
    """
    Stores successful investigations for future retrieval.
    Backed by Databricks Vector Search.
    """
    
    def __init__(self, vector_index):
        self.index = vector_index
        self.table = "novatech.ml.investigation_history"
    
    def store_investigation(self, investigation: dict):
        """Store completed investigation for future reference."""
        record = {
            "investigation_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "question": investigation["original_question"],
            "category": investigation["question_category"],
            "root_cause": investigation["root_cause"],
            "tables_involved": investigation["tables_involved"],
            "fix_applied": investigation["recommended_fix"],
            "resolution_time_seconds": investigation["resolution_time"],
            "confidence_score": investigation["confidence_score"],
            
            # For embedding
            "combined_text": f"""
                Question: {investigation["original_question"]}
                Root Cause: {investigation["root_cause"]}
                Fix: {investigation["recommended_fix"]}
            """
        }
        
        # Write to Delta table (triggers Vector Search sync)
        spark.createDataFrame([record]).write.mode("append").saveAsTable(self.table)
    
    def recall_similar(self, question: str, n: int = 3) -> List[dict]:
        """Retrieve similar past investigations."""
        results = self.index.similarity_search(
            query_text=question,
            columns=["question", "root_cause", "fix_applied", "tables_involved"],
            num_results=n
        )
        return results
```

## Step 5.3: Working Memory (Investigation Scratchpad)

```python
class WorkingMemory:
    """
    Scratchpad for current investigation.
    Tracks what we've tried and learned.
    """
    
    def __init__(self):
        self.queries_executed: List[dict] = []
        self.hypotheses_tested: List[dict] = []
        self.dead_ends: List[str] = []
        self.key_findings: List[str] = []
    
    def log_query(self, query: str, result: any, useful: bool):
        self.queries_executed.append({
            "query": query,
            "result_summary": self._summarize_result(result),
            "useful": useful,
            "timestamp": datetime.now().isoformat()
        })
    
    def log_hypothesis(self, hypothesis: str, evidence: str, confirmed: bool):
        self.hypotheses_tested.append({
            "hypothesis": hypothesis,
            "evidence": evidence,
            "confirmed": confirmed
        })
        
        if not confirmed:
            self.dead_ends.append(hypothesis)
    
    def add_finding(self, finding: str):
        self.key_findings.append(finding)
    
    def get_summary(self) -> str:
        """Get summary for including in context."""
        return f"""
        Queries executed: {len(self.queries_executed)}
        Hypotheses tested: {len(self.hypotheses_tested)}
        Confirmed: {sum(1 for h in self.hypotheses_tested if h['confirmed'])}
        Dead ends: {len(self.dead_ends)}
        Key findings: {self.key_findings}
        """
```

---

# PHASE 6: THE AGENTIC LOOP

## Step 6.1: LangGraph Agent Definition

```python
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

def create_datascope_agent():
    """Create the Data Debugger agent graph."""
    
    workflow = StateGraph(AgentState)
    
    # Define nodes
    workflow.add_node("classify", classify_question)
    workflow.add_node("retrieve", parallel_retrieval)
    workflow.add_node("analyze", analyze_evidence)
    workflow.add_node("investigate", run_investigation)
    workflow.add_node("tool_executor", ToolNode(tools))
    workflow.add_node("synthesize", synthesize_response)
    
    # Define edges
    workflow.set_entry_point("classify")
    workflow.add_edge("classify", "retrieve")
    workflow.add_edge("retrieve", "analyze")
    
    # Conditional edge: need more investigation?
    workflow.add_conditional_edges(
        "analyze",
        should_investigate_more,
        {
            "investigate": "investigate",
            "synthesize": "synthesize"
        }
    )
    
    # Investigation loop
    workflow.add_edge("investigate", "tool_executor")
    workflow.add_conditional_edges(
        "tool_executor",
        check_investigation_complete,
        {
            "continue": "analyze",
            "complete": "synthesize"
        }
    )
    
    workflow.add_edge("synthesize", END)
    
    return workflow.compile()
```

## Step 6.2: Full Loop Execution for Our Question

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ITERATION 1                                                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│ INPUT: "Why do some customers have NULL churn_risk?"                    │
│                                                                         │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ STEP 1: CLASSIFY                                                    │ │
│ │ Category: DATA_QUALITY                                              │ │
│ │ Entities: churn_risk column, churn_predictions table                │ │
│ │ Trace: span_id=abc123, latency=245ms                                │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                              │                                          │
│                              ▼                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ STEP 2: RETRIEVE (parallel)                                         │ │
│ │ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐     │ │
│ │ │Vector Search│ │Unity Catalog│ │SQL Warehouse│ │ GitHub MCP  │     │ │
│ │ │ 127ms       │ │ 89ms        │ │ 342ms       │ │ 203ms       │     │ │
│ │ │ 2 patterns  │ │ 12 columns  │ │ 3 queries   │ │ 1 file      │     │ │
│ │ └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘     │ │
│ │ Total parallel time: 342ms (max of all)                             │ │
│ │ Trace: span_id=def456, latency=342ms                                │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                              │                                          │
│                              ▼                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ STEP 3: ANALYZE                                                     │ │
│ │ Context assembled: ~15,000 tokens                                   │ │
│ │ LLM reasoning:                                                      │ │
│ │   "Based on the evidence:                                           │ │
│ │    1. Pattern PAT-005 matches with 89% similarity                   │ │
│ │    2. SQL shows all NULL churn_risk have NULL avg_logins            │ │
│ │    3. Code shows CASE statement without ELSE clause                 │ │
│ │    4. Lineage shows LEFT JOIN can produce NULLs                     │ │
│ │                                                                     │ │
│ │    Hypothesis: Missing ELSE clause in CASE statement"               │ │
│ │                                                                     │ │
│ │ Decision: Confidence HIGH (0.92), proceed to synthesis              │ │
│ │ Trace: span_id=ghi789, latency=1,245ms, tokens=2,340                │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                              │                                          │
│                              ▼                                          │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ STEP 4: SYNTHESIZE                                                  │ │
│ │ Generate final response with:                                       │ │
│ │ - Root cause explanation                                            │ │
│ │ - Evidence (SQL + code)                                             │ │
│ │ - Impact quantification (19 customers, 3.8%)                        │ │
│ │ - Recommended fix                                                   │ │
│ │ Trace: span_id=jkl012, latency=1,890ms, tokens=1,456                │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│ TOTAL TIME: 3,722ms                                                     │
│ TOTAL TOKENS: 4,196                                                     │
│ ITERATIONS: 1 (no additional investigation needed)                      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Step 6.3: Generated Response

```markdown
# Investigation Report: NULL churn_risk Values

## Summary
**19 customers (3.8%)** have NULL values in the `churn_risk` column due to a 
missing ELSE clause in the classification logic.

## Root Cause
**BUG-005: NULL Handling in Conditional Logic**

The CASE statement in `gold/churn_predictions.sql` (lines 45-52) does not 
include an ELSE clause:

```sql
CASE 
    WHEN ra.avg_logins > 20 THEN 'Low Risk'
    WHEN ra.avg_logins > 5 THEN 'Medium Risk'
    WHEN ra.avg_logins <= 5 THEN 'High Risk'
    -- ⚠️ Missing ELSE clause!
END as churn_risk
```

When `avg_logins` is NULL (which happens for customers with no activity in 
the 30-day lookback window), none of the WHEN conditions match, and the 
CASE returns NULL.

## Evidence

### 1. NULL Count
```sql
SELECT COUNT(*) as null_count 
FROM novatech.gold.churn_predictions 
WHERE churn_risk IS NULL;
-- Result: 19 customers
```

### 2. Correlation with avg_logins
```sql
SELECT customer_id, avg_logins, churn_risk 
FROM novatech.gold.churn_predictions 
WHERE churn_risk IS NULL;
```
| customer_id | avg_logins | churn_risk |
|-------------|------------|------------|
| CUST-00023  | NULL       | NULL       |
| CUST-00089  | NULL       | NULL       |
| ...         | NULL       | NULL       |

**All 19 records with NULL churn_risk also have NULL avg_logins.**

### 3. Root Cause in Code
The LEFT JOIN in the transformation produces NULL `avg_logins` for customers 
with no recent activity, which then propagates to NULL `churn_risk`.

## Impact
- **Affected records**: 19 customers (3.8% of total)
- **Business impact**: High-risk customers (no recent activity) are not 
  being flagged for intervention
- **Severity**: Medium

## Recommended Fix

Update the CASE statement to include an ELSE clause:

```sql
CASE 
    WHEN ra.avg_logins > 20 THEN 'Low Risk'
    WHEN ra.avg_logins > 5 THEN 'Medium Risk'
    WHEN ra.avg_logins <= 5 THEN 'High Risk'
    ELSE 'High Risk'  -- ✅ Customers with no activity = High Risk
END as churn_risk
```

**Confidence Score: 95%**
```

---

# PHASE 7: EVALUATION

## Step 7.1: Ground Truth Comparison

```python
# Test case from test_cases.json
GROUND_TRUTH = {
    "id": "TC-006",
    "question": "Why do some customers have NULL churn_risk?",
    "expected_bug": "BUG-005",
    "expected_root_cause": "null_handling",
    "expected_tables": ["gold.churn_predictions", "silver.fct_product_usage"],
    "success_criteria": [
        "Agent identifies missing ELSE clause",
        "Agent explains NULL propagation",
        "Agent suggests adding ELSE 'High Risk'"
    ]
}
```

## Step 7.2: Agent-as-Judge Evaluation

```python
EVALUATION_PROMPT = """
You are evaluating a Data Debugging Agent's investigation.

## Original Question
{question}

## Agent's Response
{agent_response}

## Ground Truth
- Expected Bug: {expected_bug}
- Expected Root Cause: {expected_root_cause}
- Expected Tables: {expected_tables}
- Success Criteria:
{success_criteria}

## Evaluation Rubric

Score each dimension 0-100:

1. **Root Cause Identification (40% weight)**
   - Did the agent correctly identify the root cause?
   - Score 100 if exact match, 50 if partially correct, 0 if wrong
   
2. **Evidence Quality (25% weight)**
   - Did the agent provide SQL queries as evidence?
   - Did the agent show actual data/numbers?
   - Score based on completeness and relevance
   
3. **Tables Investigated (15% weight)**
   - Did the agent query the right tables?
   - Score based on coverage of expected tables
   
4. **Fix Recommendation (10% weight)**
   - Did the agent suggest an appropriate fix?
   - Is the fix actionable and correct?
   
5. **Efficiency (10% weight)**
   - Did the agent solve it in reasonable steps?
   - Were there unnecessary tangents?

Return JSON:
{
  "root_cause_score": <0-100>,
  "evidence_score": <0-100>,
  "tables_score": <0-100>,
  "fix_score": <0-100>,
  "efficiency_score": <0-100>,
  "weighted_total": <0-100>,
  "pass": <true/false>,
  "feedback": "<specific feedback>"
}
"""
```

## Step 7.3: Evaluation Execution

```python
def evaluate_investigation(question: str, agent_response: str, test_case: dict) -> dict:
    """Run agent-as-judge evaluation."""
    
    # Call judge LLM (Claude)
    evaluation = llm.invoke(
        EVALUATION_PROMPT.format(
            question=question,
            agent_response=agent_response,
            expected_bug=test_case["expected_bug"],
            expected_root_cause=test_case["expected_root_cause"],
            expected_tables=test_case["expected_tables"],
            success_criteria="\n".join(f"- {c}" for c in test_case["success_criteria"])
        )
    )
    
    return json.loads(evaluation)
```

## Step 7.4: Evaluation Result for Our Question

```json
{
  "test_case_id": "TC-006",
  "question": "Why do some customers have NULL churn_risk?",
  "evaluation": {
    "root_cause_score": 100,
    "root_cause_feedback": "Correctly identified BUG-005: missing ELSE clause in CASE statement",
    
    "evidence_score": 95,
    "evidence_feedback": "Provided SQL queries, showed data samples, quantified impact (19 customers, 3.8%)",
    
    "tables_score": 100,
    "tables_feedback": "Investigated gold.churn_predictions and traced to silver.fct_product_usage",
    
    "fix_score": 100,
    "fix_feedback": "Recommended adding ELSE 'High Risk' - exactly correct fix",
    
    "efficiency_score": 90,
    "efficiency_feedback": "Solved in 1 iteration, 4 tool calls. Could have skipped vector search.",
    
    "weighted_total": 97.5,
    "pass": true,
    "grade": "A"
  },
  "execution_metrics": {
    "total_time_ms": 3722,
    "llm_calls": 3,
    "tool_calls": 4,
    "tokens_used": 4196,
    "iterations": 1
  }
}
```

---

# PHASE 8: TRACING & OBSERVABILITY

## Step 8.1: MLflow Tracing Setup

```python
import mlflow
from mlflow.tracking import MlflowClient

# Enable autologging for Databricks
mlflow.databricks.autolog()

# Set experiment
mlflow.set_experiment("/Users/festus/datascope-agent")

# Create custom trace context
class DataScopeTracer:
    def __init__(self):
        self.client = MlflowClient()
        self.run_id = None
        
    def start_investigation(self, question: str):
        """Start a new traced investigation."""
        self.run = mlflow.start_run(run_name=f"investigation_{datetime.now().isoformat()}")
        self.run_id = self.run.info.run_id
        
        mlflow.log_param("question", question)
        mlflow.log_param("start_time", datetime.now().isoformat())
        
    def trace_retrieval(self, source: str, results: dict, latency_ms: int):
        """Trace a retrieval operation."""
        with mlflow.start_span(name=f"retrieval_{source}") as span:
            span.set_inputs({"source": source})
            span.set_outputs(results)
            span.set_attribute("latency_ms", latency_ms)
            
    def trace_llm_call(self, prompt: str, response: str, tokens: int, latency_ms: int):
        """Trace an LLM call."""
        with mlflow.start_span(name="llm_call") as span:
            span.set_inputs({"prompt_preview": prompt[:500]})
            span.set_outputs({"response_preview": response[:500]})
            span.set_attribute("tokens", tokens)
            span.set_attribute("latency_ms", latency_ms)
            
    def trace_tool_call(self, tool_name: str, inputs: dict, outputs: dict, latency_ms: int):
        """Trace a tool execution."""
        with mlflow.start_span(name=f"tool_{tool_name}") as span:
            span.set_inputs(inputs)
            span.set_outputs(outputs)
            span.set_attribute("latency_ms", latency_ms)
            
    def end_investigation(self, result: dict, evaluation: dict):
        """End the traced investigation."""
        mlflow.log_metric("total_time_ms", result["total_time_ms"])
        mlflow.log_metric("tokens_used", result["tokens_used"])
        mlflow.log_metric("iterations", result["iterations"])
        mlflow.log_metric("confidence_score", result["confidence_score"])
        
        # Evaluation metrics
        mlflow.log_metric("eval_score", evaluation["weighted_total"])
        mlflow.log_metric("eval_pass", 1 if evaluation["pass"] else 0)
        
        mlflow.end_run()
```

## Step 8.2: Full Trace for Our Question

```
┌─────────────────────────────────────────────────────────────────────────┐
│ MLflow Trace: investigation_2024-12-01T14:32:15                         │
│ Run ID: abc123def456                                                    │
│ Status: COMPLETED                                                       │
│ Duration: 3,722ms                                                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│ SPANS:                                                                  │
│                                                                         │
│ ├── query_classification (245ms)                                        │
│ │   ├── Input: "Why do some customers have NULL churn_risk?"            │
│ │   ├── Output: {category: "DATA_QUALITY", entities: [...]}             │
│ │   └── Tokens: 156                                                     │
│ │                                                                       │
│ ├── parallel_retrieval (342ms)                                          │
│ │   │                                                                   │
│ │   ├── retrieval_vector_search (127ms)                                 │
│ │   │   ├── Input: {query: "NULL values churn_risk..."}                 │
│ │   │   ├── Output: {patterns: [{id: "PAT-005", score: 0.89}]}          │
│ │   │   └── Index: novatech.ml.pattern_library_index                    │
│ │   │                                                                   │
│ │   ├── retrieval_unity_catalog (89ms)                                  │
│ │   │   ├── Input: {table: "gold.churn_predictions"}                    │
│ │   │   ├── Output: {columns: 12, upstream_tables: 3}                   │
│ │   │   └── MCP: unity-catalog-mcp                                      │
│ │   │                                                                   │
│ │   ├── retrieval_sql_warehouse (342ms)                                 │
│ │   │   ├── Input: {queries: 3}                                         │
│ │   │   ├── Output: {null_count: 19, null_pct: 3.8}                     │
│ │   │   └── Warehouse: abc123                                           │
│ │   │                                                                   │
│ │   └── retrieval_github (203ms)                                        │
│ │       ├── Input: {query: "churn_risk CASE WHEN"}                      │
│ │       ├── Output: {file: "churn_predictions.sql", lines: "45-52"}     │
│ │       └── Repo: novatech-org/data-transformations                     │
│ │                                                                       │
│ ├── context_assembly (12ms)                                             │
│ │   ├── Tokens assembled: 15,234                                        │
│ │   └── Sources: 4 (vector, schema, sql, code)                          │
│ │                                                                       │
│ ├── llm_analyze (1,245ms)                                               │
│ │   ├── Model: claude-3-5-sonnet                                        │
│ │   ├── Input tokens: 15,234                                            │
│ │   ├── Output tokens: 856                                              │
│ │   ├── Hypothesis: "Missing ELSE clause in CASE statement"             │
│ │   └── Confidence: 0.92                                                │
│ │                                                                       │
│ ├── llm_synthesize (1,890ms)                                            │
│ │   ├── Model: claude-3-5-sonnet                                        │
│ │   ├── Input tokens: 16,090                                            │
│ │   ├── Output tokens: 1,456                                            │
│ │   └── Output: [markdown response]                                     │
│ │                                                                       │
│ └── evaluation (523ms)                                                  │
│     ├── Judge model: claude-3-5-sonnet                                  │
│     ├── Score: 97.5/100                                                 │
│     └── Pass: TRUE                                                      │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│ METRICS:                                                                │
│ ├── total_time_ms: 3,722                                                │
│ ├── llm_calls: 3                                                        │
│ ├── tool_calls: 4                                                       │
│ ├── tokens_input: 31,324                                                │
│ ├── tokens_output: 2,468                                                │
│ ├── tokens_total: 33,792                                                │
│ ├── iterations: 1                                                       │
│ ├── confidence_score: 0.95                                              │
│ ├── eval_score: 97.5                                                    │
│ └── eval_pass: 1                                                        │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│ PARAMETERS:                                                             │
│ ├── question: "Why do some customers have NULL churn_risk?"             │
│ ├── category: DATA_QUALITY                                              │
│ ├── root_cause_found: BUG-005                                           │
│ ├── affected_records: 19                                                │
│ └── fix_recommended: "Add ELSE 'High Risk' to CASE statement"           │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Step 8.3: Lakehouse Monitoring Dashboard

```sql
-- Query for monitoring dashboard
SELECT 
    date_trunc('hour', start_time) as hour,
    COUNT(*) as investigations,
    AVG(total_time_ms) as avg_latency_ms,
    AVG(eval_score) as avg_quality_score,
    SUM(CASE WHEN eval_pass = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as pass_rate,
    SUM(tokens_total) as total_tokens,
    AVG(iterations) as avg_iterations
FROM mlflow.experiments.datascope_traces
WHERE start_time > current_timestamp() - INTERVAL 24 HOURS
GROUP BY date_trunc('hour', start_time)
ORDER BY hour DESC;
```

---

# COMPLETE ARCHITECTURE DIAGRAM

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                    DATA DEBUGGER ARCHITECTURE                            │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  ┌─────────────────┐                                                                    │
│  │   USER INPUT    │  "Why do some customers have NULL churn_risk?"                     │
│  └────────┬────────┘                                                                    │
│           │                                                                             │
│           ▼                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                           DATABRICKS APP (Streamlit)                             │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │   │
│  │  │    Chat     │  │  History    │  │   Traces    │  │   Metrics   │              │   │
│  │  │  Interface  │  │   Panel     │  │   Viewer    │  │  Dashboard  │              │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘              │   │
│  └───────────────────────────────────────┬─────────────────────────────────────────┘   │
│                                          │                                              │
│                                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                         MODEL SERVING ENDPOINT                                   │   │
│  │                     POST /serving-endpoints/datascope-agent                      │   │
│  └───────────────────────────────────────┬─────────────────────────────────────────┘   │
│                                          │                                              │
│                                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              AGENT FRAMEWORK                                     │   │
│  │  ┌─────────────────────────────────────────────────────────────────────────┐    │   │
│  │  │                           STATE MANAGER                                  │    │   │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │    │   │
│  │  │  │  Short-Term  │  │   Working    │  │  Long-Term   │                   │    │   │
│  │  │  │   Memory     │  │   Memory     │  │   Memory     │                   │    │   │
│  │  │  │ (Conversation)│  │ (Scratchpad) │  │(Vector Store)│                   │    │   │
│  │  │  └──────────────┘  └──────────────┘  └──────────────┘                   │    │   │
│  │  └─────────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                                  │   │
│  │  ┌─────────────────────────────────────────────────────────────────────────┐    │   │
│  │  │                         AGENTIC LOOP (LangGraph)                         │    │   │
│  │  │                                                                          │    │   │
│  │  │   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │    │   │
│  │  │   │ CLASSIFY │───▶│ RETRIEVE │───▶│ ANALYZE  │───▶│SYNTHESTIC│          │    │   │
│  │  │   └──────────┘    └────┬─────┘    └────┬─────┘    └──────────┘          │    │   │
│  │  │                        │               │                                 │    │   │
│  │  │                        │          ┌────┴────┐                            │    │   │
│  │  │                        │          │ INVEST- │◀──────┐                    │    │   │
│  │  │                        │          │  IGATE  │───────┤                    │    │   │
│  │  │                        │          └─────────┘  loop │                    │    │   │
│  │  │                        │                            │                    │    │   │
│  │  │                        ▼                            │                    │    │   │
│  │  │  ┌─────────────────────────────────────────────────────────────────┐    │    │   │
│  │  │  │                      TOOL ORCHESTRATOR                          │    │    │   │
│  │  │  │                                                                 │    │    │   │
│  │  │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────┐ │    │    │   │
│  │  │  │  │   Vector    │  │    Unity    │  │     SQL     │  │ GitHub │ │    │    │   │
│  │  │  │  │   Search    │  │   Catalog   │  │  Warehouse  │  │  MCP   │ │    │    │   │
│  │  │  │  │    Tool     │  │    MCP      │  │    Tool     │  │  Tool  │ │    │    │   │
│  │  │  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └───┬────┘ │    │    │   │
│  │  │  │         │                │                │             │      │    │    │   │
│  │  │  └─────────┼────────────────┼────────────────┼─────────────┼──────┘    │    │   │
│  │  │            │                │                │             │           │    │   │
│  │  └────────────┼────────────────┼────────────────┼─────────────┼───────────┘    │   │
│  │               │                │                │             │                │   │
│  └───────────────┼────────────────┼────────────────┼─────────────┼────────────────┘   │
│                  │                │                │             │                    │
│                  ▼                ▼                ▼             ▼                    │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              DATABRICKS SERVICES                                 │   │
│  │                                                                                  │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │   │
│  │  │   Vector    │  │    Unity    │  │     SQL     │  │        GitHub           │ │   │
│  │  │   Search    │  │   Catalog   │  │  Warehouse  │  │     (External MCP)      │ │   │
│  │  │   Index     │  │             │  │             │  │                         │ │   │
│  │  │             │  │  ┌───────┐  │  │  ┌───────┐  │  │  ┌───────────────────┐  │ │   │
│  │  │ ┌─────────┐ │  │  │Schemas│  │  │  │Queries│  │  │  │ Transformation    │  │ │   │
│  │  │ │Patterns │ │  │  │Lineage│  │  │  │ Data  │  │  │  │ Code Repository   │  │ │   │
│  │  │ │History  │ │  │  │Permiss│  │  │  │Samples│  │  │  │                   │  │ │   │
│  │  │ └─────────┘ │  │  └───────┘  │  │  └───────┘  │  │  └───────────────────┘  │ │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────────┘ │   │
│  │                                                                                  │   │
│  │  ┌─────────────────────────────────────────────────────────────────────────────┐ │   │
│  │  │                           UNITY CATALOG TABLES                               │ │   │
│  │  │                                                                              │ │   │
│  │  │  novatech.bronze.*  ──▶  novatech.silver.*  ──▶  novatech.gold.*            │ │   │
│  │  │                                                                              │ │   │
│  │  └─────────────────────────────────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              OBSERVABILITY                                       │   │
│  │                                                                                  │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │   │
│  │  │   MLflow    │  │  Lakehouse  │  │    Agent    │  │   Alerts    │             │   │
│  │  │   Tracing   │  │  Monitoring │  │ Evaluation  │  │  & Metrics  │             │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘             │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

# SUMMARY: END-TO-END FLOW

| Phase | What Happens | Time | Key Components |
|-------|--------------|------|----------------|
| 1. Input | User asks question, classify intent | 245ms | Streamlit, Model Serving |
| 2. Retrieval | Parallel fetch from 4 sources | 342ms | Vector Search, UC MCP, SQL, GitHub |
| 3. Context | Assemble & prioritize retrieved data | 12ms | Context Engineer, Token Budget |
| 4. State | Track hypotheses, evidence, progress | - | LangGraph State, Working Memory |
| 5. Reasoning | LLM analyzes evidence, forms hypothesis | 1,245ms | Claude, Prompt Engineering |
| 6. Synthesis | Generate structured response | 1,890ms | Claude, Markdown Formatting |
| 7. Evaluation | Judge scores against ground truth | 523ms | Agent-as-Judge, Test Cases |
| 8. Tracing | Log all spans, metrics, artifacts | - | MLflow, Lakehouse Monitoring |

**Total Time: 3.7 seconds**
**Confidence: 95%**
**Evaluation Score: 97.5/100**

---

This document provides everything you need to draw your architecture diagram!
