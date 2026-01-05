# DataScope System Design: Interview Deep Dive

This document traces how a question flows through the DataScope system, explaining each component, design decision, and the reasoning behind them. Use this for interview preparation.

---

## Architecture Evolution: Three Versions

I built **three different versions** of this application, each exploring different architectural approaches. This evolution demonstrates learning and trade-off analysis.

### Version 1: Databricks-Native (MLflow/Lakebase)

**Directory**: `datascope-ui-app/`

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  Databricks     │     │  Databricks          │     │  Lakebase       │
│  Apps (UI)      │────▶│  External Endpoints  │────▶│  (Monitoring)   │
│                 │     │  (Claude Proxy)      │     │                 │
└─────────────────┘     └──────────────────────┘     └─────────────────┘
```

**Stack**:
- Simple Python HTTP server (no framework)
- Databricks External Endpoints for LLM (proxies to Claude)
- Lakebase for monitoring/logging
- Vector Search for patterns

**Pros**:
- Everything in Databricks ecosystem
- Unity Catalog permissions built-in
- No external API keys to manage

**Cons**:
- Rate limiting on External Endpoints (429 errors)
- Network policies blocked some requests
- No streaming support
- Limited observability

**What I Learned**:
> "Databricks External Endpoints are great for batch inference but not for interactive agents. The rate limiting (designed for model serving) doesn't fit the agentic pattern of multiple rapid LLM calls."

---

### Version 2: LangGraph Agent

**Directory**: `datascope-langgraph-app/`

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  Databricks     │     │  LangGraph           │     │  MCP Server     │
│  Apps (UI)      │────▶│  ReAct Agent         │────▶│  (Tools)        │
│                 │     │  + MemorySaver       │     │                 │
└─────────────────┘     └──────────────────────┘     └─────────────────┘
                              │
                              ▼
                        ┌─────────────────┐
                        │  Galileo        │
                        │  (Observability)│
                        └─────────────────┘
```

**Stack**:
- LangGraph with `create_react_agent`
- MemorySaver for conversation checkpointing
- Custom ChatModel wrapping Databricks endpoints
- Galileo for tracing

**Pros**:
- Built-in ReAct loop (no manual implementation)
- Checkpointing for multi-turn conversations
- LangChain ecosystem compatibility

**Cons**:
- Abstraction overhead (wrapping endpoints as ChatModel)
- Debugging through layers of abstraction
- Still hit Databricks rate limits

**What I Learned**:
> "LangGraph provides excellent abstractions for agent patterns, but when you're debugging issues, the layers of abstraction can obscure what's actually happening. The `create_react_agent` is great until you need custom behavior."

---

### Version 3: Direct Anthropic + MCP (Current)

**Directory**: `datascope-vercel-app/`

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  Vercel         │     │  Anthropic API       │     │  MCP Server     │
│  (Next.js)      │────▶│  (Direct)            │────▶│  (Flask)        │
│                 │     │                      │     │                 │
└─────────────────┘     └──────────────────────┘     └─────────────────┘
       │                        │                           │
       │                        │                           │
       ▼                        ▼                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Galileo AI                                   │
│                    (Unified Observability)                          │
└─────────────────────────────────────────────────────────────────────┘
```

**Stack**:
- Next.js on Vercel (React + API routes)
- Anthropic SDK directly (no proxy)
- MCP server for Databricks tools
- Galileo for observability
- Streaming responses (SSE)

**Pros**:
- No rate limiting (direct API)
- Full streaming support
- Clear separation of concerns
- Easy debugging (fewer abstractions)

**Cons**:
- Need to manage Anthropic API key
- Custom agent loop (more code)
- Two deployments (Vercel + MCP server)

**What I Learned**:
> "Sometimes the simpler approach wins. Direct API access with a custom agent loop is more code but much easier to debug and extend. The MCP abstraction for tools is the right level - it hides Databricks complexity without hiding the agent logic."

---

### Evolution Summary

| Aspect | V1 (Databricks) | V2 (LangGraph) | V3 (Direct) |
|--------|-----------------|----------------|-------------|
| **LLM Access** | External Endpoints | External Endpoints | Direct Anthropic |
| **Agent Loop** | Manual | create_react_agent | Manual |
| **Streaming** | ❌ | ❌ | ✅ |
| **Rate Limits** | Hit frequently | Hit frequently | None |
| **Debugging** | Hard (black box) | Medium (abstractions) | Easy (direct) |
| **Observability** | Lakebase | Galileo | Galileo |
| **Deployment** | Databricks Apps | Databricks Apps | Vercel + MCP |

**Interview Answer**:
> "I built three versions of this system, each exploring different trade-offs. The first used Databricks External Endpoints - great for keeping everything in one ecosystem, but rate limiting killed the interactive experience. The second used LangGraph for its built-in agent patterns, but debugging through abstractions was painful. The final version uses direct Anthropic API calls with a custom agent loop - more code, but I can see exactly what's happening and extend it easily. This journey taught me that the 'right' architecture depends on your constraints - for an interactive debugging agent, low latency and observability matter more than ecosystem purity."

---

## System Overview (Current Architecture)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            USER QUESTION                                     │
│         "Why do some customers have NULL churn_risk?"                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         VERCEL FRONTEND (Next.js)                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  React UI   │  │  API Route  │  │  Agent      │  │  Galileo Tracer     │ │
│  │  (Chat)     │──▶│  /api/chat  │──▶│  Loop       │──▶│  (Observability)   │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
┌──────────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐
│   ANTHROPIC API      │  │   MCP SERVER    │  │      GALILEO CLOUD          │
│   (Claude Sonnet)    │  │   (Flask)       │  │                             │
│                      │  │                 │  │  ┌─────────────────────────┐│
│  - Reasoning         │  │  - execute_sql  │  │  │ Traces & Spans          ││
│  - Tool selection    │  │  - search_patt. │  │  │ - LLM calls             ││
│  - Response gen.     │  │  - get_schema   │  │  │ - Tool calls            ││
│                      │  │  - search_code  │  │  │ - Latency               ││
│                      │  │  - get_file     │  │  │ - Token usage           ││
│                      │  │  - list_files   │  │  └─────────────────────────┘│
└──────────────────────┘  └────────┬────────┘  └─────────────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
            ┌─────────────┐ ┌───────────┐ ┌─────────────┐
            │ DATABRICKS  │ │  VECTOR   │ │   GITHUB    │
            │ SQL API     │ │  SEARCH   │ │   API       │
            │             │ │           │ │             │
            │ Unity Cat.  │ │ Patterns  │ │ SQL Files   │
            │ Permissions │ │ Embeddings│ │ Code Search │
            └─────────────┘ └───────────┘ └─────────────┘
```

---

## Component Deep Dive

### 1. Frontend (Vercel/Next.js)

**Purpose**: User interface and request orchestration

**Key Files**:
- `app/page.tsx` - Chat UI component
- `app/api/chat/route.ts` - API endpoint
- `lib/agent/index.ts` - Agent loop logic
- `lib/observability/galileo.ts` - Tracing

**Why Next.js on Vercel?**
- Server-side API routes (no separate backend needed)
- Edge runtime support for low latency
- Easy deployment and scaling
- TypeScript for type safety with Anthropic SDK

**Interview Answer**:
> "I chose Next.js because it lets me colocate the UI and API routes. The agent logic runs server-side in API routes, which is important because I need to keep API keys secure and make authenticated calls to both Anthropic and my MCP server. Vercel provides automatic scaling and edge deployment."

---

### 2. Agent Loop (Core Intelligence)

**Purpose**: Orchestrate LLM reasoning and tool execution

**Location**: `lib/agent/index.ts`

```typescript
async *streamInvestigation(question: string): AsyncGenerator<string> {
  const tracer = createTracer(this.sessionId)

  while (iterations < MAX_ITERATIONS) {
    // 1. Call Claude with tools
    const stream = await this.client.messages.stream({
      model: 'claude-sonnet-4-20250514',
      system: SYSTEM_PROMPT,
      tools: tools,
      messages: this.conversationHistory
    })

    // 2. Stream text to UI
    for await (const event of stream) {
      if (event.delta.type === 'text_delta') {
        yield `event: text\ndata: ${JSON.stringify({text})}\n\n`
      }
    }

    // 3. Check if Claude wants to use tools
    const finalMessage = await stream.finalMessage()
    if (!hasToolUse) {
      await tracer.complete(question, fullText)
      return
    }

    // 4. Execute tools via MCP
    for (const block of finalMessage.content) {
      if (block.type === 'tool_use') {
        const result = await executeTool(block.name, block.input)
        await tracer.logToolCall({...})
        toolResults.push(result)
      }
    }

    // 5. Add results to history, loop continues
    this.conversationHistory.push({ role: 'user', content: toolResults })
  }
}
```

**Design Decision: Agentic Loop vs Single Call**

| Approach | Pros | Cons |
|----------|------|------|
| Single LLM call | Simple, predictable | Can't gather info iteratively |
| **Agentic loop** ✅ | Adaptive, can explore | Risk of infinite loops, higher cost |

**Why Agentic Loop?**
Data debugging requires iterative investigation:
1. First, understand what data exists (schema)
2. Then, quantify the issue (SQL count)
3. Then, trace the source (lineage/code)
4. Finally, explain root cause

A single call can't do this because the agent doesn't know what it will find until it looks.

**Interview Answer**:
> "I implemented an agentic loop because data debugging is inherently exploratory. The agent might query a table, discover an unexpected value, then need to check the transformation code. This back-and-forth requires multiple LLM calls with tool execution in between. I set MAX_ITERATIONS=10 as a safety bound to prevent runaway costs."

---

### 3. Context Engineering (System Prompt)

**Purpose**: Guide Claude's behavior and investigation approach

**Location**: `lib/agent/prompts.ts`

```typescript
export const SYSTEM_PROMPT = `You are DataScope, an expert data debugging agent...

## Investigation Framework
1. **Quantify First**: Always count affected records before diving deep
2. **Trace Lineage**: Find where problematic data comes from
3. **Compare Layers**: Check bronze vs silver vs gold
4. **Search Code**: Find the transformation that causes the bug
5. **Explain Clearly**: Root cause + evidence + fix

## Tool Usage Guidelines
- Use search_patterns FIRST to find similar past issues
- Use execute_sql to quantify and investigate
- Use get_table_schema to understand data structure
- Use list_sql_files + get_file to examine transformation code

## Response Format
Structure your findings as:
1. **Issue Summary**: One sentence describing the problem
2. **Affected Records**: Count and scope
3. **Root Cause**: Why this is happening
4. **Evidence**: SQL results that prove your finding
5. **Recommendation**: How to fix it
`
```

**Why This Prompt Structure?**

1. **Investigation Framework**: Encodes domain expertise (how data engineers debug issues)
2. **Tool Usage Guidelines**: Reduces trial-and-error by suggesting order
3. **Response Format**: Ensures consistent, useful output

**Context Window Management**:
- System prompt: ~500 tokens (fixed)
- Tool definitions: ~800 tokens (fixed)
- Conversation history: Variable (grows with each turn)
- Tool results: Can be large (SQL results)

**Interview Answer**:
> "The system prompt is critical for quality. I encoded a structured investigation framework based on how experienced data engineers work - quantify first, trace lineage, compare layers. This prevents the agent from jumping to conclusions without evidence. The tool usage guidelines help Claude choose efficiently rather than trying random tools."

---

### 4. Tool Definitions (How Claude Chooses Tools)

**Purpose**: Describe available capabilities to the LLM

**Location**: `lib/agent/tools.ts`

```typescript
export const tools = [
  {
    name: "execute_sql",
    description: `Execute a SQL query against Databricks.

    Use this to:
    - Count affected records (SELECT COUNT(*) WHERE condition)
    - Sample data to understand patterns (SELECT * LIMIT 10)
    - Compare values across tables (JOINs, UNIONs)
    - Aggregate metrics (GROUP BY, SUM, AVG)

    Always start with counts before selecting raw data.`,
    input_schema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "The SQL query to execute"
        }
      },
      required: ["query"]
    }
  },
  {
    name: "search_patterns",
    description: `Search for similar past data quality issues using semantic search.

    Use this FIRST when investigating a new issue to find:
    - Known patterns that match the symptoms
    - Investigation SQL from past issues
    - Root causes of similar problems

    Returns: Matching patterns with symptoms, root cause, and resolution.`,
    input_schema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Description of the data issue or symptoms"
        }
      },
      required: ["query"]
    }
  },
  // ... more tools
]
```

**How Claude Decides Which Tool to Use**:

1. **Semantic matching**: Claude matches the user's question to tool descriptions
2. **Context from prompt**: System prompt suggests "use search_patterns FIRST"
3. **Previous results**: If SQL returns unexpected data, Claude might check schema
4. **Goal-directed**: Claude reasons about what information is needed

**Example Decision Flow**:
```
User: "Why do some customers have NULL churn_risk?"

Claude's reasoning (internal):
- "NULL values" → matches search_patterns description ("known patterns")
- System prompt says "use search_patterns FIRST"
- → Calls search_patterns("NULL values churn_risk")

Pattern found: PAT-005 "NULL Values Not Handled in Conditional Logic"
- Pattern suggests: "CASE statements don't handle NULL cases"
- Pattern includes investigation SQL

Claude's next reasoning:
- "I should verify this with actual data"
- → Calls execute_sql("SELECT COUNT(*) FROM gold.churn_predictions WHERE churn_risk IS NULL")

Results: 847 records with NULL

Claude's next reasoning:
- "I should check the transformation code"
- → Calls list_sql_files("sql/gold") then get_file("sql/gold/churn_predictions.sql")

Finds: CASE statement without ELSE clause
```

**Interview Answer**:
> "Tool selection is driven by semantic similarity between the user's question and tool descriptions. I optimized descriptions to be specific about when to use each tool. The system prompt also provides ordering hints - 'use search_patterns FIRST' - which guides Claude to check for known issues before doing ad-hoc exploration. This makes investigations more efficient."

---

### 5. MCP Server (Tool Execution Layer)

**Purpose**: Bridge between agent and data sources

**Location**: `datascope-mcp-server/app.py`

**Why MCP (Model Context Protocol)?**

| Alternative | Pros | Cons |
|-------------|------|------|
| Direct API calls from Vercel | Simpler | Credentials in frontend, no abstraction |
| REST API | Standard | Need to define custom protocol |
| **MCP** ✅ | Standard protocol, tool discovery | Newer, less tooling |

**MCP provides**:
1. **Standard protocol**: JSON-RPC 2.0 for tool calls
2. **Tool discovery**: `tools/list` returns available tools
3. **Consistent interface**: Same format for all tools

**Request Flow**:
```
Vercel Agent                    MCP Server                     Databricks
     │                              │                              │
     │  POST /mcp                   │                              │
     │  {method: "tools/call",      │                              │
     │   params: {name: "execute_sql", │                           │
     │            arguments: {query}}} │                           │
     │─────────────────────────────▶│                              │
     │                              │  POST /api/2.0/sql/statements│
     │                              │  Authorization: Bearer token │
     │                              │─────────────────────────────▶│
     │                              │                              │
     │                              │◀─────────────────────────────│
     │                              │  {data_array: [...]}         │
     │◀─────────────────────────────│                              │
     │  {result: {content: [...]}}  │                              │
```

**Authentication Design**:

```python
def get_user_token() -> tuple[str, bool]:
    """Get authentication token with fallback logic.

    Priority:
    1. User's OAuth token (X-User-Token header) - per-user permissions
    2. Fallback PAT token (env var) - dev mode only
    """
    user_token = request.headers.get("X-User-Token")
    if user_token:
        return user_token, True  # User's own permissions

    if DATABRICKS_TOKEN:
        logger.warning("Using fallback token (dev mode)")
        return DATABRICKS_TOKEN, False

    return None, False
```

**Interview Answer**:
> "I chose MCP because it provides a standard protocol for tool execution. The MCP server acts as a security boundary - it validates requests, manages authentication, and abstracts the underlying APIs. This separation means I can change how I connect to Databricks without changing the agent code. The two-token model (app auth + user token) enables multi-user deployments with per-user permissions."

---

### 6. Vector Search (Pattern Matching)

**Purpose**: Find similar past issues using semantic similarity

**Why Vector Search over Keyword Search?**

| User says | Keyword match | Semantic match |
|-----------|---------------|----------------|
| "NULL churn_risk" | ✓ Exact match | ✓ |
| "missing risk scores" | ✗ No match | ✓ Similar meaning |
| "customers without risk" | ✗ No match | ✓ Similar meaning |

**Architecture**:
```
User Question                     Vector Search Index
     │                                   │
     │ "NULL values in churn"            │
     ▼                                   │
┌─────────────────┐                      │
│ Embedding Model │                      │
│ (BGE-large-en)  │                      │
└────────┬────────┘                      │
         │ [0.23, -0.45, 0.12, ...]     │
         ▼                               │
┌─────────────────────────────────────────────────┐
│              Similarity Search                   │
│  Compare query embedding to pattern embeddings   │
│  Return top-k by cosine similarity              │
└─────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│ PAT-005: "NULL Values Not Handled..."           │
│   Similarity: 0.89                              │
│   Root Cause: CASE statements missing ELSE     │
│   Investigation SQL: SELECT COUNT(*) WHERE...  │
└─────────────────────────────────────────────────┘
```

**Pattern Library Design**:
```sql
CREATE TABLE datascope_patterns (
    pattern_id STRING,
    title STRING,
    symptoms STRING,        -- JSON array
    root_cause STRING,
    resolution STRING,
    investigation_sql STRING,

    -- Computed column for embedding
    embedding_text STRING GENERATED ALWAYS AS (
        CONCAT('Issue: ', title, '. Symptoms: ', symptoms, '. Cause: ', root_cause)
    )
)
```

**Why Computed Column?**
- Combines multiple fields for better semantic matching
- Auto-updates when data changes
- Single embedding per pattern (efficient)

**Interview Answer**:
> "Vector Search enables the agent to learn from past investigations. When a user asks about NULL values, even if they phrase it differently, semantic search finds the relevant pattern. The pattern includes not just the diagnosis but also the investigation SQL - so the agent knows exactly what query to run. This dramatically reduces investigation time from exploring randomly to following proven paths."

---

### 7. Observability (Galileo)

**Purpose**: Debug, measure, and improve agent behavior

**Location**: `lib/observability/galileo.ts`

**What We Trace**:

```typescript
// Trace structure for one investigation
{
  trace_id: "abc-123",
  input: "Why do some customers have NULL churn_risk?",
  output: "The NULL values are caused by...",
  duration_ns: 45000000000,  // 45 seconds

  spans: [
    {
      type: "llm",
      model: "claude-sonnet-4-20250514",
      input: [{role: "user", content: "..."}],
      output: {role: "assistant", content: "..."},
      duration_ns: 3200000000,
      input_tokens: 1500,
      output_tokens: 450
    },
    {
      type: "tool",
      name: "search_patterns",
      input: {query: "NULL churn_risk"},
      output: {patterns: [...]},
      duration_ns: 2100000000
    },
    {
      type: "llm",
      // Second LLM call after getting pattern
    },
    {
      type: "tool",
      name: "execute_sql",
      input: {query: "SELECT COUNT(*)..."},
      output: {rows: [[847]]},
      duration_ns: 1800000000
    },
    // ... more spans
  ]
}
```

**Why Galileo Matters**:

1. **Debugging**: When the agent gives wrong answers, traces show exactly what it saw
2. **Performance**: Identify slow tools (is SQL slow? Vector search slow?)
3. **Cost**: Track token usage per investigation
4. **Quality**: Build evaluation datasets from real traces

**Example Debug Scenario**:
```
Problem: Agent said "no NULL values found" but there are 847

Check Galileo trace:
1. LLM Call #1: Asked to investigate NULL values ✓
2. Tool: execute_sql with query "SELECT * FROM table LIMIT 10" ✗
   → Agent sampled data instead of counting!

Fix: Update system prompt to say "Always COUNT first, don't sample"
```

**Interview Answer**:
> "Galileo is essential for understanding agent behavior. Without it, the agent is a black box - you see input and output but not the reasoning. Traces show me exactly which tools were called, what results came back, and how Claude interpreted them. When something goes wrong, I can pinpoint whether it was a bad tool choice, unexpected data, or flawed reasoning. This is crucial for iterating on prompt engineering."

---

### 8. End-to-End Flow Example

Let's trace a complete investigation:

**User Question**: "Why do some customers have NULL churn_risk?"

**Step 1: Request Arrives**
```
Browser → POST /api/chat
  Body: { message: "Why do some customers have NULL churn_risk?" }
```

**Step 2: Agent Initialization**
```typescript
// API route creates agent and tracer
const agent = createAgent({ sessionId })
const tracer = createTracer(sessionId)

// Add user message to history
conversationHistory.push({
  role: 'user',
  content: "Why do some customers have NULL churn_risk?"
})
```

**Step 3: First LLM Call**
```typescript
// Claude receives:
// - System prompt (investigation framework)
// - Tool definitions (6 tools)
// - User question

const response = await client.messages.create({
  model: 'claude-sonnet-4-20250514',
  system: SYSTEM_PROMPT,
  tools: tools,
  messages: conversationHistory
})

// Claude's response:
{
  content: [
    { type: 'text', text: "I'll investigate the NULL churn_risk values. Let me first check for similar past issues." },
    { type: 'tool_use', name: 'search_patterns', input: { query: 'NULL values in churn risk score' } }
  ],
  stop_reason: 'tool_use'
}
```

**Why did Claude choose search_patterns?**
1. Tool description says "use FIRST when investigating"
2. System prompt says "Use search_patterns FIRST"
3. "NULL values" semantically matches pattern description

**Step 4: Tool Execution (search_patterns)**
```typescript
// Agent executes tool via MCP
const result = await executeTool('search_patterns', { query: '...' })

// MCP Server:
// 1. Receives request
// 2. Calls Databricks Vector Search API
// 3. Returns top-3 matching patterns

// Result:
{
  patterns: [
    {
      pattern_id: "PAT-005",
      title: "NULL Values Not Handled in Conditional Logic",
      symptoms: "NULL values in fields, CASE returning NULL",
      root_cause: "CASE statements missing ELSE clause",
      investigation_sql: "SELECT COUNT(*) FROM gold.churn_predictions WHERE churn_risk IS NULL"
    }
  ]
}

// Logged to Galileo:
tracer.logToolCall({
  name: 'search_patterns',
  input: { query: '...' },
  output: { patterns: [...] },
  durationMs: 2100
})
```

**Step 5: Second LLM Call**
```typescript
// Conversation history now includes:
// 1. User question
// 2. Claude's first response (text + tool_use)
// 3. Tool result from search_patterns

// Claude reasons:
// "Pattern PAT-005 matches. It suggests checking for NULL values.
//  The pattern includes investigation SQL. Let me run it."

// Claude's response:
{
  content: [
    { type: 'text', text: "I found a matching pattern. Let me verify by counting affected records." },
    { type: 'tool_use', name: 'execute_sql', input: {
      query: 'SELECT COUNT(*) FROM novatech.gold.churn_predictions WHERE churn_risk IS NULL'
    }}
  ]
}
```

**Step 6: Tool Execution (execute_sql)**
```typescript
// MCP Server executes SQL via Databricks API
// Returns: { rows: [[847]], columns: ['count'] }

// Logged to Galileo with timing
tracer.logToolCall({
  name: 'execute_sql',
  input: { query: '...' },
  output: { rows: [[847]] },
  durationMs: 1800
})
```

**Step 7: Third LLM Call (Code Investigation)**
```typescript
// Claude sees: 847 NULL records
// Claude reasons: "Pattern says CASE statement issue. Let me check the code."

// Claude's response:
{
  content: [
    { type: 'tool_use', name: 'list_sql_files', input: { directory: 'sql/gold' } }
  ]
}

// Result: ['churn_predictions.sql', 'arr_by_customer.sql', ...]

// Claude then calls:
{
  content: [
    { type: 'tool_use', name: 'get_file', input: { file_path: 'sql/gold/churn_predictions.sql' } }
  ]
}

// Result: Full SQL file content showing:
// CASE
//     WHEN avg_logins > 20 THEN 'Low Risk'
//     WHEN avg_logins > 5 THEN 'Medium Risk'
//     WHEN avg_logins <= 5 THEN 'High Risk'
//     -- Missing ELSE clause!
// END as churn_risk
```

**Step 8: Final Response**
```typescript
// Claude now has all the evidence:
// - Pattern match (PAT-005)
// - Record count (847 NULL)
// - Code showing missing ELSE

// Final response:
{
  content: [
    {
      type: 'text',
      text: `## Issue Summary
847 customers have NULL churn_risk values.

## Root Cause
The CASE statement in gold/churn_predictions.sql is missing an ELSE clause.
When avg_logins is NULL (no activity data), no condition matches, resulting in NULL.

## Evidence
- Pattern match: PAT-005 "NULL Values Not Handled in Conditional Logic"
- Affected records: 847 (verified via SQL)
- Code location: sql/gold/churn_predictions.sql, line 52

## Recommendation
Add ELSE clause to handle NULL cases:

\`\`\`sql
CASE
    WHEN avg_logins > 20 THEN 'Low Risk'
    WHEN avg_logins > 5 THEN 'Medium Risk'
    WHEN avg_logins <= 5 THEN 'High Risk'
    ELSE 'High Risk'  -- Handle NULL avg_logins
END as churn_risk
\`\`\`
`
    }
  ],
  stop_reason: 'end_turn'
}
```

**Step 9: Trace Completion**
```typescript
// Agent loop exits (no more tool_use)
await tracer.complete(question, fullText)

// Trace sent to Galileo:
// - Total duration: 45 seconds
// - 4 LLM calls
// - 4 tool calls
// - Token usage: ~8000 input, ~2000 output
```

---

## Key Interview Talking Points

### 1. "How does the agent decide which tool to use?"

> "Tool selection is driven by semantic matching between the user's question and tool descriptions, combined with guidance from the system prompt. I optimized tool descriptions to be specific about use cases - for example, search_patterns says 'use FIRST when investigating new issues.' The system prompt reinforces this ordering. Claude then reasons about what information it needs to answer the question and selects tools accordingly. After each tool result, Claude re-evaluates what's needed next."

### 2. "How do you handle hallucinations?"

> "Three mechanisms: First, the system prompt requires evidence - Claude must show SQL results, not just claim things. Second, the tool architecture grounds Claude in real data - it can't make up table names because get_table_schema returns actual schemas. Third, Galileo tracing lets me audit every investigation. If Claude says 'no NULL values' but there are 847, I can see it ran the wrong query and fix the prompt."

### 3. "Why separate the MCP server from the agent?"

> "Separation of concerns and security. The MCP server handles authentication, rate limiting, and API abstraction. The agent handles reasoning. This means I can change how I connect to Databricks (OAuth vs PAT, different warehouses) without changing agent code. It also keeps credentials server-side - the frontend never sees Databricks tokens."

### 4. "How does Vector Search improve the agent?"

> "Vector Search enables learning from past investigations. Without it, every investigation starts from scratch - the agent explores randomly until it finds the issue. With patterns, the agent recognizes 'this looks like PAT-005' and immediately knows what SQL to run and what code to check. It's like giving the agent institutional knowledge."

### 5. "What would you add for production?"

> "Several things: (1) Caching - SQL results and patterns don't change frequently, cache them. (2) Rate limiting - prevent abuse and control costs. (3) Evaluation pipeline - run test cases nightly to catch regressions. (4) User feedback loop - let users rate investigations, use that to improve patterns. (5) Guardrails - prevent SQL injection, limit query complexity."

### 6. "How do you measure success?"

> "Three metrics: (1) Investigation accuracy - did we find the right root cause? Measured by test cases with known bugs. (2) Time to resolution - how long does investigation take? Target is <2 minutes for known patterns. (3) Tool efficiency - how many tool calls per investigation? Fewer is better if accuracy stays high. Galileo tracks all of these."

---

## Architecture Decisions Summary

| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Frontend | Next.js on Vercel | Colocate UI and API, easy deployment |
| LLM | Claude Sonnet | Best tool use, fast enough for streaming |
| Agent Pattern | Agentic loop | Exploration requires multiple turns |
| Tool Protocol | MCP (JSON-RPC) | Standard, discoverable, abstracts APIs |
| Pattern Matching | Databricks Vector Search | Semantic similarity for natural language |
| Embedding Model | BGE-large-en | Native to Databricks, high quality |
| Observability | Galileo | LLM-specific tracing, evaluation hooks |
| Authentication | Two-token model | App auth + user permissions |

---

## Common Failure Modes & Debugging

| Symptom | Likely Cause | Debug With |
|---------|--------------|------------|
| Agent loops forever | Not finding answer, keeps trying | Check MAX_ITERATIONS, review Galileo trace |
| Wrong tool chosen | Tool descriptions unclear | Review tool descriptions, add examples |
| Slow investigation | SQL queries too broad | Check Galileo latency, optimize queries |
| Missing data in response | Tool result truncated | Check MCP server logs, increase limits |
| Hallucinated table names | No grounding | Add get_table_schema to list available tables |
| Same pattern always returned | Embedding mismatch | Check Vector Search index, re-embed |

---

## Questions to Ask Interviewer

1. "How do you currently handle multi-turn agent conversations - do you persist state?"
2. "What's your approach to evaluating agent quality at scale?"
3. "How do you balance token costs with investigation thoroughness?"
4. "Do you use guardrails or content filtering on agent outputs?"
5. "How do you handle cases where the agent should ask clarifying questions?"
