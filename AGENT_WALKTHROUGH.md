# DataScope Agent: Step-by-Step Code Walkthrough

This document traces through the DataScope agent using a real question to show how all the concepts work together.

---

## Sample Question

**"Why do some customers have NULL churn_risk?"**

Let's follow this question through the entire system.

---

## Step 1: Request Entry Point

**File:** `datascope-ui-app/app.py` (lines 981-996)

```python
def do_POST(self):
    if self.path == "/chat":
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        question = body.get("question", "")
        conversation_id = body.get("conversation_id")  # Optional for follow-ups

        response, conv_id = chat_with_llm(question, conversation_id)
        self.send_json({
            "response": response,
            "conversation_id": conv_id
        })
```

**What happens:**
1. User types question in the chat UI
2. JavaScript sends POST to `/chat` with `{"question": "Why do some customers have NULL churn_risk?"}`
3. Handler extracts question and optional `conversation_id`
4. Calls `chat_with_llm()` - the main agent function

---

## Step 2: Conversation State Management

**File:** `datascope-ui-app/app.py` (lines 679-696)

```python
def chat_with_llm(question: str, conversation_id: str = None) -> tuple:
    import time
    start_time = time.time()

    # Create or load conversation
    if not conversation_id:
        conversation_id = generate_id()
        save_conversation(conversation_id, question[:100])
```

**What happens:**
1. If new conversation → generate UUID and save to Lakebase
2. If follow-up → use existing conversation_id for context

**Lakebase tables involved:**
- `novatech.datascope.conversations` - Stores session metadata
- `novatech.datascope.messages` - Stores each message
- `novatech.datascope.investigations` - Stores analytics

---

## Step 3: Context Engineering (Multi-Turn)

**File:** `datascope-ui-app/app.py` (lines 163-214, 736-748)

```python
# Build messages with context from previous turns
context_summary = get_conversation_summary(conversation_id)

if context_summary:
    system_content = SYSTEM_PROMPT + "\n\n" + context_summary + "\n\nNow answer the user's follow-up question using the context above."
else:
    system_content = SYSTEM_PROMPT

messages = [{"role": "system", "content": system_content}]
messages.append({"role": "user", "content": question})
```

**What `get_conversation_summary()` does:**
```python
def get_conversation_summary(conversation_id: str) -> str:
    # Query Lakebase for previous Q&A pairs
    query = f"""
    SELECT role, content
    FROM {table}
    WHERE conversation_id = '{conversation_id}'
      AND role IN ('user', 'assistant')
      AND content IS NOT NULL
    ORDER BY created_at ASC
    """
    # Returns text like:
    # "User asked: 'Why do customers have NULL churn_risk?'"
    # "You found: '16 customers affected due to missing ELSE clause...'"
```

**Why this approach:**
- Anthropic's API requires every `tool_call` to have a matching `tool_result`
- Instead of replaying full message history (complex), we inject a text summary
- This avoids API validation errors while providing context

---

## Step 4: System Prompt

**File:** `datascope-ui-app/app.py` (lines 268-303)

```python
SYSTEM_PROMPT = """You are DataScope, a Data Debugging Agent for NovaTech's Databricks data platform.

Your job is to investigate data quality issues and explain them in clear, simple English.

## Investigation Strategy

1. **FIRST**: Use search_patterns to find similar past issues - this gives you context and suggested SQL
2. **THEN**: Use execute_sql to verify the issue with actual data
3. **OPTIONALLY**: Use search_code to find the transformation that caused the bug

## Available Tables

**Gold Layer (Business Metrics):**
- novatech.gold.churn_predictions - Customer churn risk scores
- novatech.gold.arr_by_customer - Annual Recurring Revenue
- novatech.gold.customer_health_scores - Customer health metrics

**Silver Layer:** novatech.silver.dim_customers, fct_subscriptions, fct_payments, fct_product_usage
**Bronze Layer:** novatech.bronze.salesforce_accounts_raw, stripe_payments_raw, product_events_raw

## How to Respond

Structure your response like this:

**What I Found:** [One sentence summary]
**The Problem:** [Explain the issue simply]
**Why It Happened:** [The root cause, explained clearly]
**How Many Records:** [Quantify the impact]
**How to Fix It:** [Specific recommendation]
"""
```

**Key elements:**
- **Identity**: "You are DataScope..."
- **Strategy**: Specific order of operations (patterns → SQL → code)
- **Knowledge**: Table names and their purposes
- **Output format**: Structured response template

---

## Step 5: Tool Definitions

**File:** `datascope-ui-app/app.py` (lines 697-734)

```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "search_patterns",
            "description": "Search for similar past data quality issues. Use this FIRST...",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Description of the data issue"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": "Execute SQL query to investigate data issues...",
            "parameters": {...}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search SQL transformation code to find the source of bugs",
            "parameters": {...}
        }
    }
]
```

**Three tools available:**
| Tool | Purpose | Backend |
|------|---------|---------|
| `search_patterns` | Find similar past issues | Vector Search (semantic similarity) |
| `execute_sql` | Query actual data | Databricks SQL Warehouse |
| `search_code` | Find transformation bugs | GitHub MCP Server |

---

## Step 6: ReAct Agent Loop

**File:** `datascope-ui-app/app.py` (lines 758-833)

```python
# Phase 1: Investigation with tools (max 5 iterations)
for iteration in range(5):
    resp = requests.post(url, headers=headers, json={
        "messages": messages,
        "tools": tools,
        "max_tokens": 4096,
        "temperature": 0
    })

    data = resp.json()
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content", "")
    tool_calls = msg.get("tool_calls", [])

    # If LLM returns content without tool calls, it's done
    if not tool_calls:
        if content:
            return (content, conversation_id)
        break

    # Execute tool calls
    assistant_msg = {"role": "assistant", "tool_calls": tool_calls}
    if content and content.strip():
        assistant_msg["content"] = content
    messages.append(assistant_msg)

    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        args = json.loads(fn.get("arguments", "{}"))

        if name == "search_patterns":
            result = search_patterns(args.get("query", ""))
        elif name == "execute_sql":
            result = execute_sql(args.get("query", ""))
        elif name == "search_code":
            result = search_code(args.get("term", ""))

        messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id"),
            "content": result
        })
```

**The ReAct Pattern:**
```
┌─────────────────────────────────────────────────────────────┐
│ Iteration 1:                                                │
│   LLM Thinks: "I should search for similar patterns first"  │
│   LLM Acts: tool_call → search_patterns("NULL churn_risk")  │
│   System Observes: Returns PAT-005 (NULL Values pattern)    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Iteration 2:                                                │
│   LLM Thinks: "Pattern suggests CASE statement issue"       │
│   LLM Acts: tool_call → execute_sql("SELECT COUNT(*)...")   │
│   System Observes: "16 customers have NULL churn_risk"      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Iteration 3:                                                │
│   LLM Thinks: "Need to find the code that calculates this"  │
│   LLM Acts: tool_call → search_code("churn_risk")           │
│   System Observes: Found CASE statement missing ELSE        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Iteration 4:                                                │
│   LLM Thinks: "I have enough evidence to answer"            │
│   LLM Acts: Returns final text response (no tool calls)     │
│   Loop exits                                                │
└─────────────────────────────────────────────────────────────┘
```

---

## Step 7: Vector Search (search_patterns)

**File:** `datascope-ui-app/app.py` (lines 641-676)

```python
def search_patterns(query: str) -> str:
    """Search for similar data quality patterns using Vector Search."""
    url = f"{DATABRICKS_HOST}/api/2.0/vector-search/indexes/{VS_INDEX}/query"
    headers = get_auth_headers()

    resp = requests.post(url, headers=headers, json={
        "query_text": query,  # "NULL churn_risk"
        "columns": ["pattern_id", "title", "symptoms", "root_cause", "resolution", "investigation_sql"],
        "num_results": 3
    })

    # Format results
    for row in results:
        pattern_id, title, symptoms, root_cause, resolution, investigation_sql = row[:6]
        out.append(f"### {pattern_id}: {title}")
        out.append(f"**Root Cause:** {root_cause}")
        out.append(f"**Resolution:** {resolution}")
```

**How Vector Search works:**
1. User query "NULL churn_risk" is converted to an embedding vector
2. Vector Search finds patterns with similar embeddings
3. Returns PAT-005: "NULL Values Not Handled in Conditional Logic"

**Embedding model:** `databricks-gte-large-en`

**Pattern data stored:**
```sql
-- novatech.gold.datascope_patterns
| pattern_id | title                                    | symptoms                        | root_cause                        |
|------------|------------------------------------------|--------------------------------|-----------------------------------|
| PAT-005    | NULL Values Not Handled in Conditional  | CASE statements returning NULL | CASE statement missing ELSE clause|
```

---

## Step 8: SQL Execution (execute_sql)

**File:** `datascope-ui-app/app.py` (lines 580-612)

```python
def execute_sql(query: str) -> str:
    """Execute SQL via Databricks Statement Execution API."""
    url = f"{DATABRICKS_HOST}/api/2.0/sql/statements"
    headers = get_auth_headers()

    resp = requests.post(url, headers=headers, json={
        "warehouse_id": SQL_WAREHOUSE_ID,
        "statement": query,
        "wait_timeout": "30s"
    })

    # Format as markdown table
    if data.get("status", {}).get("state") == "SUCCEEDED":
        columns = [c["name"] for c in data.get("manifest", {}).get("schema", {}).get("columns", [])]
        rows = result["data_array"][:15]
        # Return markdown table
```

**Example query the LLM might generate:**
```sql
SELECT COUNT(*) as null_count
FROM novatech.gold.churn_predictions
WHERE churn_risk IS NULL
```

**Result:**
```
| null_count |
|------------|
| 16         |
```

---

## Step 9: Code Search (search_code)

**File:** `datascope-ui-app/app.py` (lines 615-638)

```python
def search_code(term: str) -> str:
    """Search code via GitHub MCP app."""
    resp = requests.post(
        f"{GITHUB_MCP_APP_URL.rstrip('/')}/search",
        json={"query": term, "file_extension": "sql"},
        headers=get_auth_headers(),
        timeout=30
    )

    if resp.status_code == 200:
        data = resp.json()
        if data.get("results"):
            for r in data["results"][:2]:
                out.append(f"**File: {r['file']}**")
                for m in r.get("matches", [])[:1]:
                    out.append(f"```sql\n{m.get('context', '')}\n```")
```

**Example search:** `search_code("churn_risk")`

**Returns:**
```sql
-- File: gold/churn_predictions.sql
CASE
    WHEN last_login_days > 90 THEN 'high'
    WHEN last_login_days > 30 THEN 'medium'
    WHEN last_login_days <= 30 THEN 'low'
    -- BUG: No ELSE clause! Customers with NULL last_login_days get NULL churn_risk
END as churn_risk
```

---

## Step 10: Guardrails and Forced Summary

**File:** `datascope-ui-app/app.py` (lines 834-880)

```python
# Phase 2: Force summary generation (no tools)
summary_prompt = """Based on your investigation above, provide your final answer...

You MUST respond with a clear explanation, NOT with more tool calls.

Format your response as:
**What I Found:** [One sentence summary]
**The Problem:** [Explain what's wrong]
**Why It Happened:** [Root cause]
**How Many Records:** [Quantify impact]
**How to Fix It:** [Recommendation]
"""

messages.append({"role": "user", "content": summary_prompt})

# Make request WITHOUT tools to force text response
resp = requests.post(url, headers=headers, json={
    "messages": messages,
    "max_tokens": 4096,
    "temperature": 0
    # No "tools" parameter!
})
```

**Guardrails:**
| Guardrail | Purpose |
|-----------|---------|
| Max 5 iterations | Prevent infinite loops |
| Forced summary phase | Guarantee final answer (remove tools) |
| Row limit (15) | Prevent memory issues |
| Timeout (30s) | Prevent hanging |

---

## Step 11: State Persistence

**File:** `datascope-ui-app/app.py` (lines 780-783, 870-873)

```python
# Save after successful response
duration = time.time() - start_time
save_message(conversation_id, "assistant", content)
save_investigation(conversation_id, question, tool_results_collected, content, duration)
return (content, conversation_id)
```

**What gets saved:**
```sql
-- novatech.datascope.messages
| message_id | conversation_id | role      | content                          |
|------------|-----------------|-----------|----------------------------------|
| uuid-1     | conv-123        | user      | Why do some customers have NULL? |
| uuid-2     | conv-123        | assistant | **What I Found:** 16 customers...|

-- novatech.datascope.investigations
| investigation_id | question                    | duration_seconds | tools_used                        |
|------------------|-----------------------------|------------------|-----------------------------------|
| inv-456          | Why do some customers...    | 12.5             | ["Pattern search", "SQL", "Code"] |
```

---

## Step 12: Monitoring (/stats endpoint)

**File:** `datascope-ui-app/app.py` (lines 940-977)

```python
elif self.path == "/stats":
    stats = {"lakebase_enabled": LAKEBASE_ENABLED}

    if LAKEBASE_ENABLED:
        # Total investigations
        result = execute_sql_internal(
            f"SELECT COUNT(*) FROM {LAKEBASE_CATALOG}.{LAKEBASE_SCHEMA}.investigations",
            return_data=True
        )
        stats["total_investigations"] = result[0][0] if result else 0

        # Average duration
        result = execute_sql_internal(
            f"SELECT AVG(duration_seconds) FROM ... WHERE duration_seconds IS NOT NULL",
            return_data=True
        )
        stats["avg_duration_seconds"] = round(float(result[0][0]), 2)
```

**Sample /stats response:**
```json
{
  "lakebase_enabled": true,
  "total_investigations": 127,
  "avg_duration_seconds": 23.5,
  "investigations_today": 8,
  "total_conversations": 45
}
```

---

## Complete Flow Diagram

```
User Question: "Why do some customers have NULL churn_risk?"
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. REQUEST ENTRY (do_POST /chat)                            │
│    Extract question and conversation_id                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. STATE MANAGEMENT                                         │
│    - Create/load conversation from Lakebase                 │
│    - Get context summary from previous turns                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. CONTEXT ENGINEERING                                      │
│    - Inject context summary into system prompt              │
│    - Build messages array                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. REACT LOOP (max 5 iterations)                            │
│    ┌─────────────────────────────────────────────────────┐  │
│    │ Iteration 1: search_patterns("NULL churn_risk")     │  │
│    │ → Returns PAT-005: NULL Values pattern              │  │
│    └─────────────────────────────────────────────────────┘  │
│    ┌─────────────────────────────────────────────────────┐  │
│    │ Iteration 2: execute_sql("SELECT COUNT(*)...")      │  │
│    │ → Returns: 16 customers affected                    │  │
│    └─────────────────────────────────────────────────────┘  │
│    ┌─────────────────────────────────────────────────────┐  │
│    │ Iteration 3: search_code("churn_risk")              │  │
│    │ → Returns: CASE statement missing ELSE              │  │
│    └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. FORCED SUMMARY (no tools)                                │
│    LLM generates structured response                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. PERSIST & RETURN                                         │
│    - Save message to Lakebase                               │
│    - Save investigation metadata                            │
│    - Return response to UI                                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ FINAL RESPONSE:                                             │
│                                                             │
│ **What I Found:** 16 customers have NULL churn_risk due to  │
│ a missing ELSE clause in the calculation.                   │
│                                                             │
│ **The Problem:** The CASE statement that calculates         │
│ churn_risk doesn't handle all scenarios...                  │
│                                                             │
│ **Why It Happened:** Customers with NULL last_login_days    │
│ don't match any WHEN condition...                           │
│                                                             │
│ **How Many Records:** 16 customers (0.8% of total)          │
│                                                             │
│ **How to Fix It:** Add ELSE 'unknown' to the CASE statement │
│ in gold/churn_predictions.sql                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Concepts Summary

| Concept | What It Does | Where in Code |
|---------|--------------|---------------|
| **ReAct Pattern** | Reason-Act-Observe loop | Lines 758-833 |
| **Tool Calling** | LLM decides which tools to use | Lines 697-734 |
| **Vector Search** | Semantic similarity for patterns | Lines 641-676 |
| **Embeddings** | Text → vectors for similarity | Databricks GTE model |
| **Context Engineering** | Inject history into prompt | Lines 736-748 |
| **Lakebase** | Delta tables for state | Lines 123-235 |
| **Guardrails** | Max iterations, forced summary | Lines 760, 834-860 |

---

*Document created: December 2024*
*This traces through `datascope-ui-app/app.py` - the deployed production code.*
