# AI Engineering Lessons: Building Production AI Agents

This document captures real-world lessons from building DataScope, a production AI agent for data debugging. These lessons are organized into two parts:

1. **Part 1: Core AI Agent Concepts** - Universal patterns for building AI agents
2. **Part 2: Databricks-Specific Lessons** - Platform-specific challenges and solutions

---

## Part 1: Core AI Agent Concepts

### 1. Context Engineering (Prompt Design)

**What It Is**: Context engineering is the art of providing the right information to an LLM at the right time. It's arguably the most important skill in AI engineering.

**Key Insight**: A well-engineered prompt can make the difference between a useless agent and one that solves real problems.

**What We Learned**:

The DataScope system prompt demonstrates several context engineering patterns:

```python
SYSTEM_PROMPT = """You are DataScope, a Data Debugging Agent...

## Step 1: Understand the Question

First, classify what type of question this is:

| Category | Example | Best Approach |
|----------|---------|---------------|
| METRIC_DISCREPANCY | "Why does ARR show $125M but Finance reports $165M?" | Compare data across layers |
| CLASSIFICATION_ERROR | "Why is customer marked as churn when they logged in?" | Check classification logic |
| DATA_QUALITY | "Why do some customers have NULL churn_risk?" | Find NULLs, trace lineage |

## Step 2: Plan Your Investigation
...
"""
```

**Context Engineering Patterns Used**:

1. **Role Definition**: "You are DataScope, a Data Debugging Agent" - gives the model a clear identity
2. **Decision Framework**: The classification table helps the model categorize problems
3. **Tool Selection Guidance**: "For METRIC_DISCREPANCY → Start with SQL" - explicit decision trees
4. **Anti-Patterns**: "Don't follow a fixed pattern" - prevents rigid behavior
5. **Output Structure**: "Structure your response: What I Found, The Problem, Why It Happened..."

**Interview Talking Point**:
> "Context engineering isn't just writing prompts - it's designing a decision framework. In DataScope, I created classification tables and tool selection guidance that helps the model reason about which approach to use. This reduced unnecessary tool calls by teaching the agent WHEN to use each tool."

---

### 2. State Management

**What It Is**: How you track and persist conversation history, tool results, and agent decisions across turns.

**The Challenge**: HTTP is stateless, but conversations are stateful. Users expect agents to remember context.

**What We Learned**:

```python
# LangGraph uses thread_id for conversation isolation
thread_config = {"configurable": {"thread_id": conversation_id}}

result = agent.invoke(
    {"messages": [{"role": "user", "content": question}]},
    config={
        **thread_config,
        "recursion_limit": 50,  # Max tool call iterations
    }
)
```

**State Management Options**:

| Approach | Pros | Cons | Use When |
|----------|------|------|----------|
| **MemorySaver** | Simple, no dependencies | Lost on restart | Development, stateless apps |
| **SqliteSaver** | Persists across restarts | Single-server only | Single-instance production |
| **PostgresSaver** | Distributed, scalable | More infrastructure | Multi-instance production |

**Our Implementation**:
```python
def get_checkpointer(config: Config):
    """Create checkpointer for conversation state."""
    # Use MemorySaver - state preserved within session but lost on restart
    # For production persistence, upgrade to SqliteSaver
    return MemorySaver()
```

**Interview Talking Point**:
> "State management in AI agents is about balancing simplicity with durability. We started with MemorySaver for development speed, but the architecture allows easy upgrade to SqliteSaver or PostgresSaver for production persistence. The key is isolating state by conversation_id (thread_id in LangGraph) so multiple users don't share context."

---

### 3. Memory Management

**What It Is**: How agents handle growing context windows and long conversations.

**The Challenge**: LLMs have token limits. Long conversations can exceed these limits or become expensive.

**Strategies We Considered**:

1. **Summarization**: Periodically summarize conversation history
2. **Windowing**: Keep only the last N messages
3. **RAG on History**: Store messages in vector DB, retrieve relevant ones
4. **Checkpointing**: LangGraph's automatic state management

**Our Approach** (from the pure Python version):
```python
# When conversation gets long, summarize for efficiency
if len(messages) > 10:
    # Create a brief summary of context for LLM
    context_summary = f"""
Previous investigation context:
- Question: {original_question}
- Tools used: {tool_names_used}
- Key findings: {key_findings_summary}
"""
    messages = [{"role": "system", "content": context_summary}] + messages[-4:]
```

**Interview Talking Point**:
> "Memory management is the hidden cost of agentic applications. We implemented a sliding window with summarization - keeping full context for recent messages while summarizing older ones. This keeps token costs predictable while preserving investigation context."

---

### 4. Tool Design

**What It Is**: Creating functions that LLMs can reliably call to interact with external systems.

**What We Learned**:

Good tools have:
1. **Clear descriptions** - The LLM uses these to decide when to call the tool
2. **Typed parameters** - Prevents hallucinated inputs
3. **Error handling** - Graceful degradation, not crashes
4. **Bounded outputs** - Don't return 10,000 rows

```python
@tool
def execute_sql(
    query: Annotated[str, "SQL query to execute against Databricks SQL Warehouse"]
) -> str:
    """Execute a SQL query to investigate data issues.

    Use this to:
    - Count affected records: SELECT COUNT(*) FROM table WHERE condition
    - Sample data: SELECT * FROM table WHERE condition LIMIT 10
    - Compare values between tables or layers (bronze/silver/gold)

    Args:
        query: The SQL query to execute (SELECT, DESCRIBE, or SHOW)

    Returns:
        Query results as a markdown table, or error message
    """
    # Safety: Only allow read operations
    if not any(query.upper().startswith(p) for p in ["SELECT", "DESCRIBE", "SHOW"]):
        return "Error: Only SELECT, DESCRIBE, and SHOW queries are allowed."

    # Bounded output: Limit rows
    rows = result.get("data_array", [])[:MAX_ROWS]  # MAX_ROWS = 15

    # Graceful errors
    try:
        # ... execution logic
    except requests.exceptions.Timeout:
        return f"Query timed out after {SQL_TIMEOUT} seconds."
    except Exception as e:
        return f"SQL execution error: {str(e)}"
```

**Tool Design Principles**:

| Principle | Why It Matters | Example |
|-----------|---------------|---------|
| **Descriptive names** | LLM picks tools by name | `search_code` not `sc` |
| **Clear docstrings** | LLM reads these | Include examples of when to use |
| **Type annotations** | Prevents bad inputs | `query: Annotated[str, "..."]` |
| **Output limits** | Prevents context explosion | `rows[:15]` |
| **Graceful errors** | Agent can recover | Return error message, don't crash |

**Interview Talking Point**:
> "Tool design is API design for LLMs. The descriptions are critical because that's how the model decides which tool to use. I include usage examples in docstrings and always return error messages as strings rather than raising exceptions - this lets the agent recover and try a different approach."

---

### 5. Retrieval and Ranking

**What It Is**: Finding relevant information from large datasets to include in context.

**DataScope's Retrieval Pattern**:

```python
@tool
def search_patterns(query: str) -> str:
    """Search for similar past data quality issues using Vector Search."""

    resp = requests.post(
        f"{config.databricks_host}/api/2.0/vector-search/indexes/{config.vs_index}/query",
        json={
            "query_text": query,
            "columns": ["pattern_id", "title", "symptoms", "root_cause",
                       "resolution", "investigation_sql"],
            "num_results": 3  # Limit to top 3 for context efficiency
        }
    )
```

**Retrieval Considerations**:

1. **What to retrieve**: Past issues, documentation, code snippets
2. **How much**: Too little = missing context, too much = token waste
3. **Ranking**: Vector similarity isn't always semantic relevance
4. **Freshness**: Old information may be outdated

**Interview Talking Point**:
> "We use Vector Search to find similar past investigations. The key insight is limiting results - we return only 3 matches because more would consume tokens without adding value. The agent can always search again with different terms if needed."

---

### 6. Observability and Tracing

**What It Is**: Understanding what your agent is doing, why it made decisions, and how long things take.

**Why It Matters**: Agents are non-deterministic. Without observability, debugging is impossible.

**Our Implementation**:

```yaml
# app.yaml - LangSmith integration
env:
  - name: LANGSMITH_TRACING
    value: "true"
  - name: LANGSMITH_API_KEY
    value: "lsv2_pt_..."
  - name: LANGSMITH_PROJECT
    value: "datascope-langgraph"
```

**What LangSmith Shows**:
- Every LLM call with full prompt/response
- Tool calls and their results
- Latency breakdown per step
- Token usage and costs
- Error traces

**Custom Analytics (Lakebase)**:

```python
def save_investigation(conversation_id, question, response, duration):
    """Save investigation metadata to Lakebase for analytics."""
    query = f"""
    INSERT INTO {table} (investigation_id, conversation_id, question,
                         status, started_at, duration_seconds, summary)
    VALUES ('{investigation_id}', '{conversation_id}', '{question}',
            'completed', CURRENT_TIMESTAMP(), {duration}, '{summary}')
    """
```

**Observability Stack**:

| Layer | Tool | Purpose |
|-------|------|---------|
| Traces | LangSmith | LLM calls, tool usage, latency |
| Logs | Python logging | Application events, errors |
| Metrics | Custom SQL | Investigation counts, durations |
| Alerts | Databricks | Error rate spikes |

**Interview Talking Point**:
> "Observability is essential for agentic apps because they're non-deterministic. We use LangSmith for detailed traces of every LLM call and tool invocation. But we also save business metrics to Lakebase - investigation counts, durations, success rates. This lets us answer both 'why did this fail?' and 'how is the system performing overall?'"

---

### 7. Evaluation

**What It Is**: Measuring whether your agent produces correct, useful outputs.

**The Challenge**: Agent outputs are freeform text. How do you score them?

**Approaches**:

1. **Ground Truth Comparison**: Have known-correct answers, compare
2. **LLM-as-Judge**: Use another LLM to evaluate quality
3. **Human Evaluation**: Manual review of outputs
4. **Automated Metrics**: Response time, tool call count, error rate

**DataScope's Evaluation Plan** (from test_cases.json):

```json
{
  "test_cases": [
    {
      "question": "Why do some customers have NULL churn_risk?",
      "expected_bug_id": "BUG-005",
      "expected_tables": ["gold.churn_predictions"],
      "success_criteria": [
        "Identifies CASE statement missing ELSE clause",
        "Provides affected record count",
        "Suggests adding ELSE clause"
      ]
    }
  ]
}
```

**Interview Talking Point**:
> "Agent evaluation is hard because outputs are non-deterministic. We use a three-part approach: (1) ground truth test cases with known bugs, (2) LLM-as-judge scoring for quality, and (3) automated metrics like response time and error rate. The key is having success criteria that can be checked programmatically."

---

### 8. MCP (Model Context Protocol)

**What It Is**: A standardized protocol for AI agents to communicate with external tools and services.

**Why It Matters**: MCP enables tool interoperability. Build once, use with any MCP-compatible agent.

**What We Learned Building an MCP Server**:

```python
# MCP is JSON-RPC 2.0 over HTTP
def handle_mcp_request(request: dict) -> dict:
    method = request.get("method", "")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "github-code-search", "version": "1.0.0"},
                "capabilities": {"tools": {}}
            }
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"tools": TOOLS}  # Tool definitions with JSON Schema
        }

    elif method == "tools/call":
        tool_name = request["params"]["name"]
        tool_args = request["params"]["arguments"]
        result = TOOL_HANDLERS[tool_name](**tool_args)
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "content": [{"type": "text", "text": json.dumps(result)}]
            }
        }
```

**MCP Key Concepts**:

| Concept | Description |
|---------|-------------|
| **JSON-RPC 2.0** | The underlying protocol - method, params, id, result/error |
| **initialize** | Handshake to exchange capabilities |
| **tools/list** | Dynamic tool discovery with JSON Schema |
| **tools/call** | Execute a tool with arguments |
| **Content Types** | Results can be text, images, or other types |

**MCP Client Implementation**:

```python
class MCPClient:
    """Simple MCP client for communicating with MCP servers."""

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Call a tool on the MCP server."""
        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

        # Extract content from MCP response format
        content_list = result.get("content", [])
        for content in content_list:
            if content.get("type") == "text":
                return json.loads(content.get("text", "{}"))
        return {}
```

**Interview Talking Point**:
> "MCP is JSON-RPC 2.0 designed for AI tool communication. I implemented both a server and client from scratch when the FastMCP library crashed on Databricks. The protocol is simple: `initialize` for handshake, `tools/list` for discovery, `tools/call` for execution. The key insight is that tools must have JSON Schema definitions so clients know how to call them."

---

## Part 2: Databricks-Specific Lessons

### 1. External Endpoints vs Direct API Calls (Critical Concept!)

**The Problem**: Databricks serverless environments block outbound calls to external APIs like `api.anthropic.com`.

**The Error**:
```
CUSTOMER_UNAUTHORIZED: Access to api.anthropic.com is denied
because of serverless network policy.
```

#### Why Are We Using OpenAI SDK to Call Claude/Anthropic?

This is one of the most important patterns to understand in modern AI engineering.

**The Architecture**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│                         YOUR APPLICATION                                 │
│                                                                          │
│   LangGraph Agent                                                        │
│        │                                                                 │
│        ▼                                                                 │
│   ChatOpenAI (langchain-openai)  ◄── Uses OpenAI SDK format             │
│        │                                                                 │
│        │  HTTP POST to base_url + /chat/completions                     │
│        ▼                                                                 │
└────────┼────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              DATABRICKS EXTERNAL ENDPOINT (Proxy)                        │
│                                                                          │
│   URL: /serving-endpoints/claude-sonnet-endpoint-v2/invocations         │
│                                                                          │
│   • Accepts OpenAI-compatible request format                            │
│   • Translates to Anthropic API format internally                       │
│   • Handles authentication with Anthropic                               │
│   • Returns OpenAI-compatible response format                           │
│        │                                                                 │
└────────┼────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    ANTHROPIC API (Claude)                                │
│                                                                          │
│   URL: api.anthropic.com                                                │
│   The actual LLM that processes your request                            │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Why This Pattern Exists

**1. OpenAI Set the Industry Standard**

OpenAI was first to market with a widely-adopted chat API format:
```json
{
  "model": "gpt-4",
  "messages": [
    {"role": "system", "content": "You are helpful"},
    {"role": "user", "content": "Hello"}
  ],
  "tools": [...],
  "max_tokens": 1000
}
```

This format became the de facto standard. Most AI frameworks (LangChain, LlamaIndex, LangGraph) were built around it.

**2. Anthropic Has a Different Native Format**

Anthropic's native API is different:
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "system": "You are helpful",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "max_tokens": 1000
}
```

Key differences:
- `system` is a separate field, not a message with role "system"
- Tool/function calling format differs
- Response structure differs

**3. Databricks External Endpoints Bridge the Gap**

Databricks created "External Endpoints" that:
- Accept the **OpenAI format** (what your code sends)
- Translate to the **provider's native format** (Anthropic, Cohere, Google, etc.)
- Handle authentication with the provider (API keys stored securely)
- Return responses in **OpenAI format**

This means you can use `ChatOpenAI` from LangChain to call Claude!

#### The URL Format Mismatch Problem

**OpenAI's API URL pattern:**
```
POST https://api.openai.com/v1/chat/completions
```

**How ChatOpenAI constructs URLs:**
```python
# Inside ChatOpenAI
final_url = f"{base_url}/chat/completions"
```

**Databricks endpoint URL pattern:**
```
POST https://{host}/serving-endpoints/{endpoint_name}/invocations
```

**The mismatch we encountered:**
```
ChatOpenAI calls:  /serving-endpoints/claude-sonnet-endpoint-v2/chat/completions
Databricks wants:  /serving-endpoints/claude-sonnet-endpoint-v2/invocations
                                                                 ▲
                                                     Different path!
```

**The 404 error we got:**
```
ENDPOINT_NOT_FOUND: Path must be of form
/serving-endpoints/<name>/invocations
```

#### Why We Can't Just Use ChatAnthropic

```python
# This seems like the "obvious" choice for calling Claude
from langchain_anthropic import ChatAnthropic
llm = ChatAnthropic(model="claude-3-5-sonnet-20241022")
```

But this fails because:
1. `ChatAnthropic` calls `api.anthropic.com` directly
2. Databricks serverless **blocks** outbound calls to external APIs (security policy)
3. Error: `CUSTOMER_UNAUTHORIZED: Access to api.anthropic.com is denied`

#### Solution Options

**Option A: Custom LLM Wrapper (Most Reliable) - THE SOLUTION WE USED**

This is the approach that worked. Create a LangChain-compatible wrapper around direct HTTP calls:

```python
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration

class DatabricksExternalLLM(BaseChatModel):
    """Custom LLM wrapper for Databricks External Endpoints.

    This solves the URL mismatch problem by giving us full control
    over the HTTP request construction.
    """

    endpoint_url: str  # Full URL: {host}/serving-endpoints/{name}/invocations
    api_key: str
    temperature: float = 0.0
    max_tokens: int = 4096

    @property
    def _llm_type(self) -> str:
        return "databricks-external"

    def _convert_messages(self, messages: List[BaseMessage]) -> List[dict]:
        """Convert LangChain messages to OpenAI format."""
        result = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                msg_dict = {"role": "assistant", "content": msg.content or ""}
                # Handle tool calls
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"])
                            }
                        }
                        for tc in msg.tool_calls
                    ]
                result.append(msg_dict)
            elif isinstance(msg, ToolMessage):
                result.append({
                    "role": "tool",
                    "content": msg.content,
                    "tool_call_id": msg.tool_call_id
                })
        return result

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """Generate a response from the Databricks endpoint."""
        payload = {
            "messages": self._convert_messages(messages),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        # Add tools if provided (for function calling)
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]

        response = requests.post(
            self.endpoint_url,  # The CORRECT URL with /invocations
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=120
        )

        # Parse response and return as ChatResult
        data = response.json()
        message = data["choices"][0]["message"]
        # ... handle tool_calls if present
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    def bind_tools(self, tools: list, **kwargs):
        """Bind tools to the LLM for function calling."""
        # Convert LangChain tools to OpenAI format and return bound instance
        # ... implementation
```

**Usage:**
```python
def get_llm(config):
    endpoint_url = f"{config.databricks_host}/serving-endpoints/{config.llm_endpoint}/invocations"
    return DatabricksExternalLLM(
        endpoint_url=endpoint_url,
        api_key=config.databricks_token,
    )
```

**Why This Works:**
1. We control the exact URL - no library assumptions
2. We implement LangChain's `BaseChatModel` interface - works with LangGraph
3. We handle message conversion ourselves - full control
4. We implement `bind_tools()` - enables function calling with agents

**Option B: Use langchain-databricks Package**
```python
from langchain_databricks import ChatDatabricks
llm = ChatDatabricks(endpoint="claude-sonnet-endpoint-v2")
```
Note: This crashed on Databricks Apps due to dependency conflicts.

**Option C: Fix ChatOpenAI URL Construction**
Theoretically possible but fragile - requires understanding library internals.

#### Key Interview Talking Points

> "This is a great example of API abstraction layers in the AI ecosystem. OpenAI's chat completions format became the industry standard, so platforms like Databricks created 'External Endpoints' that accept OpenAI format but can call any provider - Anthropic, Cohere, Google, etc. This means your application code uses one interface (OpenAI SDK) but can switch between providers by changing endpoint configuration, not code."

> "The challenge we hit was URL format mismatch. ChatOpenAI appends `/chat/completions` to the base URL, but Databricks expects `/invocations`. This is the kind of integration detail that only surfaces in production when you're actually deploying to constrained environments."

> "The key insight is that 'using OpenAI SDK' doesn't mean 'using OpenAI the company'. It means using the OpenAI-compatible API format, which has become a lingua franca for LLM APIs. Databricks, Azure OpenAI, AWS Bedrock, and many other platforms support this format as a compatibility layer."

> "This pattern - OpenAI format as universal interface - is similar to how SQL became the universal database language. You write SQL, but the actual database could be PostgreSQL, MySQL, or Snowflake. Similarly, you write OpenAI-format requests, but the actual LLM could be GPT-4, Claude, Llama, or Mistral."

---

### 2. External Endpoints vs Foundation Model APIs (Critical!)

**The Problem We Hit**: Even with our custom LLM wrapper calling the correct Databricks URL, we got:
```
CUSTOMER_UNAUTHORIZED: Access to api.anthropic.com is denied
because of serverless network policy.
```

**Wait, What?** We're calling the Databricks endpoint, not Anthropic directly!

**The Root Cause**: There are TWO types of Databricks serving endpoints:

#### Type 1: External Model Endpoints (DON'T WORK in serverless)
```python
# These endpoints proxy to external APIs
# The ENDPOINT itself needs to call api.anthropic.com
{
  "external_model": {
    "name": "claude-sonnet-4-20250514",
    "provider": "anthropic",  # ← Needs to call Anthropic
    "anthropic_api_key": "{{secrets/...}}"
  }
}
```

The network policy blocks the **endpoint's** outbound call to Anthropic, not your app's call to the endpoint.

```
Your App ──▶ Databricks Endpoint ──✗──▶ api.anthropic.com
                                   ▲
                           BLOCKED BY NETWORK POLICY
```

#### Type 2: Foundation Model APIs (WORK in serverless)
```python
# These models are HOSTED BY Databricks - no external calls needed
{
  "foundation_model": {
    "name": "GPT-5.1",
    "entity_name": "system.ai.databricks-gpt-5-1"
  }
}
```

```
Your App ──▶ Databricks Foundation Model
                    ▲
            HOSTED INTERNALLY - NO EXTERNAL CALL
```

**Available Databricks-Hosted Models** (as of Jan 2025):
- `databricks-gpt-5-2` - GPT-5.2 (latest)
- `databricks-gpt-5-1` - GPT-5.1
- `databricks-gpt-oss-120b` - Open-source GPT 120B
- `databricks-llama-4-maverick` - Llama 4
- `databricks-meta-llama-3-3-70b-instruct` - Llama 3.3 70B (verified working)
- Various other Llama, Gemma, Mistral models

**Watch Out for Rate Limits!** Some Foundation Model endpoints have rate limits set to 0:
```
PERMISSION_DENIED: The endpoint is temporarily disabled due to
a Databricks-set rate limit of 0.
```

**Always test the endpoint before deploying:**
```bash
curl -X POST "$DATABRICKS_HOST/serving-endpoints/$ENDPOINT/invocations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello"}],"max_tokens":10}'
```

**The Fix**:
```yaml
# app.yaml
env:
  # WRONG - External endpoint can't reach Anthropic
  # - name: LLM_ENDPOINT_NAME
  #   value: "claude-sonnet-endpoint-v2"

  # RIGHT - Databricks-hosted model works
  - name: LLM_ENDPOINT_NAME
    value: "databricks-gpt-5-1"
```

**Interview Talking Point**:
> "This was a subtle but critical issue. We created a custom LLM wrapper, called the correct Databricks URL, but still got 'access to Anthropic denied'. The insight is that Databricks has TWO types of endpoints: External Model endpoints that proxy to external APIs (blocked by network policy), and Foundation Model APIs that are hosted by Databricks (work fine). The network policy blocks the endpoint's outbound call, not your app's call to the endpoint. You need Databricks-hosted models for serverless environments."

---

### 3. Databricks Apps Authentication

**The Problem**: Databricks Apps are protected by OAuth. External HTTP calls get redirected to login.

**What We Saw**:
```bash
$ curl https://my-app.databricksapps.com/health
<a href="https://...databricks.com/oidc/oauth2/authorize?...">Found</a>
```

**Key Insight**: Authentication works differently for:
- **External access** (from your laptop): Requires OAuth login in browser
- **Internal access** (app-to-app within Databricks): Uses service principals automatically

**Our MCP Client with Auth**:
```python
class MCPClient:
    def __init__(self, server_url: str, auth_token: str = None):
        self.server_url = server_url
        self.auth_token = auth_token

    def _get_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers
```

**Interview Talking Point**:
> "Databricks Apps authentication is OAuth-based. You can't just curl an endpoint - you'll get redirected. But for app-to-app communication within Databricks, service principals handle auth automatically. We include the Databricks token in our MCP client headers, which works for internal communication."

---

### 3. SQL Warehouse API Patterns

**The Pattern**: All SQL goes through the Statement Execution API, not direct connections.

```python
def execute_sql(query: str) -> str:
    url = f"{config.databricks_host}/api/2.0/sql/statements"

    resp = requests.post(url, headers=config.get_auth_headers(), json={
        "warehouse_id": config.sql_warehouse_id,
        "statement": query,
        "wait_timeout": "30s"  # Wait for result
    })

    data = resp.json()
    status = data.get("status", {}).get("state", "")

    if status == "SUCCEEDED":
        columns = [c["name"] for c in data["manifest"]["schema"]["columns"]]
        rows = data["result"]["data_array"]
        return format_as_table(columns, rows)
    elif status == "FAILED":
        return f"Error: {data['status']['error']['message']}"
    else:
        return f"Query status: {status}"
```

**Key Considerations**:
- **Warehouse startup**: Cold warehouses take 30-60 seconds to start
- **Timeout handling**: Set appropriate wait_timeout
- **Result pagination**: Large results need cursor handling
- **Cost awareness**: Each query has compute cost

---

### 4. Databricks Apps Deployment Patterns

**What We Learned About app.yaml**:

```yaml
command: ['python', 'app.py']

env:
  - name: DATABRICKS_HOST
    value: "https://xxx.gcp.databricks.com"
  - name: DATABRICKS_TOKEN
    value: "dapi..."  # Or use secrets
  - name: LLM_ENDPOINT_NAME
    value: "claude-sonnet-endpoint-v2"
```

**Deployment Commands**:
```bash
# Upload code to workspace
databricks workspace import-dir . /Workspace/Users/me/my-app --overwrite

# Deploy
databricks apps deploy my-app --source-code-path /Workspace/Users/me/my-app

# Check status
databricks apps get my-app

# Stop/Start (for troubleshooting)
databricks apps stop my-app
databricks apps start my-app
```

**Common Issues**:

| Issue | Symptom | Solution |
|-------|---------|----------|
| Dependency crash | "App crashed unexpectedly" | Reduce dependencies, check versions |
| Import error | Crash on startup | Test imports locally first |
| Auth failure | 401/403 errors | Check token, use service principal |
| Network blocked | Connection timeout | Use External Endpoints for APIs |

---

### 5. Minimal Dependencies Win

**The Lesson**: Fewer dependencies = more reliable deployments.

**FastMCP Attempt** (crashed):
```
fastmcp>=0.1.0
uvicorn
starlette
pydantic
httpx
# ... many transitive dependencies
```

**Manual MCP Implementation** (worked):
```
requests>=2.31.0
# That's it
```

**Interview Talking Point**:
> "When FastMCP crashed on Databricks with no accessible logs, I implemented MCP manually. The protocol is simple - just JSON-RPC 2.0. The manual implementation has one dependency (requests) versus dozens for FastMCP. Fewer dependencies means fewer things that can break in constrained environments."

---

### 6. LangGraph vs Pure Python Trade-offs

We built both versions - here's what we learned:

**Pure Python** (`datascope-ui-app/app.py`):
```python
# Manual tool loop
for iteration in range(5):
    resp = requests.post(url, json={"messages": messages, "tools": tools})
    tool_calls = resp.json()["choices"][0]["message"].get("tool_calls", [])

    if not tool_calls:
        break  # Done investigating

    for tc in tool_calls:
        result = execute_tool(tc["function"]["name"], tc["function"]["arguments"])
        messages.append({"role": "tool", "content": result, "tool_call_id": tc["id"]})
```

**LangGraph** (`datascope-langgraph-app/`):
```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(
    model=llm,
    tools=[search_patterns, execute_sql, search_code],
    checkpointer=MemorySaver(),
    prompt=SYSTEM_PROMPT,
)

result = agent.invoke({"messages": [{"role": "user", "content": question}]})
```

**Comparison**:

| Aspect | Pure Python | LangGraph |
|--------|-------------|-----------|
| Lines of code | ~1000 | ~400 |
| Dependencies | Just `requests` | langgraph, langchain-* |
| Debugging | Direct, explicit | Requires tracing tools |
| State management | Manual | Built-in checkpointers |
| Multi-turn | Manual context injection | Automatic |
| Flexibility | Full control | Framework patterns |

**Interview Talking Point**:
> "We built both versions to compare. Pure Python gives you complete control and minimal dependencies - great when you need to debug every step. LangGraph abstracts the tool loop and state management, reducing code by 60%. The trade-off is debuggability versus convenience. For production, I'd use LangGraph with good observability (LangSmith) to compensate for the abstraction."

---

---

## Part 3: Architecture Decision - Where Should AI Agents Live?

One of the most important decisions in AI engineering is **where to deploy your agent**. This section analyzes the trade-offs we discovered.

### The Fundamental Question

> Should the AI agent run inside the data platform (Databricks) or outside (Vercel/AWS/GCP)?

### Architecture Option 1: Everything in Databricks (What We Built)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATABRICKS WORKSPACE                               │
│                                                                              │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐          │
│  │  DataScope UI   │    │  GitHub MCP     │    │ Claude Endpoint │          │
│  │  (Databricks    │───▶│  Server         │    │ (External       │          │
│  │   App)          │    │  (Databricks    │    │  Endpoint)      │          │
│  │                 │    │   App)          │    │                 │          │
│  └────────┬────────┘    └─────────────────┘    └────────▲────────┘          │
│           │                                             │                    │
│           │  ┌──────────────────────────────────────────┘                    │
│           ▼  ▼                                                               │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐          │
│  │  SQL Warehouse  │    │  Vector Search  │    │  Unity Catalog  │          │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Challenges We Encountered:**

| Challenge | Impact | Workaround |
|-----------|--------|------------|
| Network policy blocks external APIs | Can't call Anthropic directly | External Endpoints (proxy) |
| URL format mismatch | ChatOpenAI doesn't work | Custom LLM wrapper |
| OAuth for app-to-app calls | MCP client needs auth | Service principal tokens |
| Dependency conflicts | Apps crash on startup | Minimize dependencies |
| No log access | Can't debug crashes | Add extensive startup logging |
| Constrained Python environment | Some packages don't work | Manual implementations |

### Architecture Option 2: Vercel Frontend + Databricks MCP

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              VERCEL                                          │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │                      DataScope UI + Agent                        │        │
│  │                                                                  │        │
│  │  • Next.js/React frontend                                       │        │
│  │  • LangGraph agent (serverless functions)                       │        │
│  │  • Direct Anthropic API calls ✓                                 │        │
│  │  • No network restrictions ✓                                    │        │
│  │  • Easy debugging (Vercel logs) ✓                               │        │
│  │                                                                  │        │
│  └──────────────────────────┬──────────────────────────────────────┘        │
│                             │                                                │
└─────────────────────────────┼────────────────────────────────────────────────┘
                              │ MCP Protocol (HTTPS)
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATABRICKS WORKSPACE                               │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │                  Databricks MCP Server                           │        │
│  │                                                                  │        │
│  │  Tools exposed via MCP:                                         │        │
│  │  • execute_sql(query) → SQL Warehouse                           │        │
│  │  • search_patterns(query) → Vector Search                       │        │
│  │  • get_lineage(table) → Unity Catalog                           │        │
│  │                                                                  │        │
│  └──────────────────────────┬──────────────────────────────────────┘        │
│                             │                                                │
│           ┌─────────────────┼─────────────────┐                              │
│           ▼                 ▼                 ▼                              │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐                │
│  │  SQL Warehouse  │ │  Vector Search  │ │  Unity Catalog  │                │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Advantages:**
- Direct LLM API calls (no External Endpoint needed)
- Standard development environment
- Easy debugging with Vercel logs
- Modern frontend tooling (Next.js, React)
- Simple deployment (git push)

**Disadvantages:**
- Data crosses network boundary
- Additional latency (network hops)
- Need to secure MCP endpoints
- Two platforms to manage

### Architecture Option 3: Hybrid (Recommended for Production)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              VERCEL                                          │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │                      DataScope Frontend                          │        │
│  │  • Next.js UI                                                   │        │
│  │  • Streaming responses                                           │        │
│  │  • Auth via Databricks OAuth                                    │        │
│  └──────────────────────────┬──────────────────────────────────────┘        │
│                             │ REST API                                       │
└─────────────────────────────┼────────────────────────────────────────────────┘
                              │
┌─────────────────────────────┼────────────────────────────────────────────────┐
│                             ▼            DATABRICKS WORKSPACE                │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │              DataScope Agent (Databricks App)                    │        │
│  │                                                                  │        │
│  │  • LangGraph agent with tools                                   │        │
│  │  • Claude via External Endpoint                                  │        │
│  │  • Direct SQL, Vector Search, Unity Catalog access              │        │
│  │  • All data stays in Databricks ✓                               │        │
│  │                                                                  │        │
│  └─────────────────────────────────────────────────────────────────┘        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Why This is the Best of Both Worlds:**

1. **Frontend in Vercel**: Modern tooling, easy iteration, great DX
2. **Agent in Databricks**: Data never leaves workspace, direct access to services
3. **Clear separation**: UI concerns (Vercel) vs data concerns (Databricks)
4. **Security**: Only results cross the boundary, not raw data

### Decision Matrix

| Factor | All Databricks | All Vercel + MCP | Hybrid |
|--------|---------------|------------------|--------|
| **LLM Integration** | Complex | Simple | Medium |
| **Data Security** | ✅ Best | ⚠️ Data crosses boundary | ✅ Good |
| **Development Speed** | ❌ Slow | ✅ Fast | ✅ Fast |
| **Debugging** | ❌ Hard | ✅ Easy | ✅ Easy (UI) / Medium (Agent) |
| **Latency** | ✅ Lowest | ⚠️ Higher | ⚠️ Medium |
| **Cost** | Single platform | Two platforms | Two platforms |
| **Deployment** | ❌ Complex | ✅ Simple | Medium |
| **Scalability** | ✅ Databricks handles | ✅ Vercel handles | ✅ Both handle their part |

### The Principle: Compute Near Data, UI Near Developers

> **Put compute where the data lives, but put UI where developers are productive.**

- **Data-intensive operations** (SQL, vector search, lineage) → Run in Databricks
- **User interface** → Run in modern frontend platform (Vercel, Netlify, etc.)
- **LLM calls** → Can run either place, but simpler outside Databricks

### Why We Chose All-Databricks (And What We Learned)

We intentionally built everything in Databricks to learn:

1. **Constrained environment patterns** - How to work within limitations
2. **Custom LLM wrappers** - When libraries don't fit, build your own
3. **MCP from scratch** - Understanding the protocol deeply
4. **Databricks-specific skills** - External Endpoints, Apps, Unity Catalog

These challenges made us better engineers. In production, we'd likely use the hybrid approach.

### Interview Talking Points

> "Architecture decisions are about trade-offs. We built everything in Databricks for data locality and security, but hit environment constraints - network policies, dependency conflicts, debugging challenges. If redesigning for production, I'd separate the UI (Vercel) from the agent (Databricks). The UI benefits from modern frontend tooling, while the agent needs direct data access. The key principle: put compute where the data is, but put UI where developers are productive."

> "The All-Databricks approach taught us valuable lessons about working in constrained environments - custom LLM wrappers, manual MCP implementation, minimal dependencies. These are exactly the skills that differentiate experienced AI engineers who've deployed to production versus those who've only used notebooks."

> "MCP (Model Context Protocol) is the bridge that enables flexible architectures. With MCP, your agent can live anywhere and still access Databricks data securely. This is where the industry is heading - standardized protocols for AI tool access."

---

## Summary: Key Takeaways for AI Engineers

### Universal Principles

1. **Context Engineering is the Killer Skill**: A well-structured prompt with decision frameworks beats clever code.

2. **Tools are APIs for LLMs**: Descriptive names, clear docstrings, typed parameters, bounded outputs.

3. **State Management Matters**: Choose the right persistence layer for your use case.

4. **Observability is Non-Negotiable**: You cannot debug what you cannot see. Invest in tracing early.

5. **Evaluation is Hard but Essential**: Combine ground truth, LLM-as-judge, and automated metrics.

### Databricks-Specific Lessons

1. **Use External Endpoints**: Never call external APIs directly from serverless.

2. **Mind the Model Name**: Some libraries route based on model name - use generic names.

3. **Minimize Dependencies**: Complex dependency trees break in constrained environments.

4. **OAuth Everywhere**: Apps are protected - plan for service principal auth.

5. **SQL API, Not Connections**: All queries go through Statement Execution API.

### MCP Lessons

1. **It's Just JSON-RPC 2.0**: Don't be intimidated - the protocol is simple.

2. **Manual Implementation Works**: When libraries fail, implement the protocol yourself.

3. **Tool Schemas are Critical**: JSON Schema definitions enable dynamic discovery.

---

## Part 4: Vercel + Databricks MCP Implementation

After hitting rate limits and network policy issues with the pure Databricks approach, we implemented the "Vercel + Databricks MCP" architecture. Here's what we built and learned.

### Why We Switched

```
Previous Approach (All Databricks):
├── Problem 1: External API blocked (api.anthropic.com denied)
├── Problem 2: External Endpoints ALSO blocked (same network policy)
├── Problem 3: Foundation Model rate limits (QPS = 0 or exceeded)
└── Problem 4: Shared rate limits across workspace

New Approach (Vercel + MCP):
├── Solution: Direct Anthropic API access (your own rate limits)
├── Solution: MCP for Databricks data access
└── Solution: Independent scaling
```

### Architecture We Built

```
┌─────────────────────────────────────────────────────────────────────┐
│                           VERCEL                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Next.js Application                       │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │   │
│  │  │   React UI   │  │  API Routes  │  │  Anthropic SDK   │  │   │
│  │  │  (Streaming) │  │  /api/chat   │  │  Direct Access   │  │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              │ MCP (JSON-RPC 2.0)                   │
│                              ▼                                       │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               │ HTTPS
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       DATABRICKS                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    MCP Server (Databricks App)               │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │   │
│  │  │ SQL Warehouse│  │Vector Search │  │  Unity Catalog   │  │   │
│  │  │   (Async)    │  │   (Async)    │  │    Metadata      │  │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Files We Created

**Vercel App (`datascope-vercel-app/`):**

| File | Purpose |
|------|---------|
| `lib/agent/index.ts` | DataScope agent using Anthropic SDK directly |
| `lib/agent/tools.ts` | Tool definitions and execution handlers |
| `lib/mcp/client.ts` | MCP client for Databricks tools |
| `lib/mcp/databricks.ts` | Direct Databricks API client |
| `app/api/chat/route.ts` | Streaming chat API endpoint |
| `components/Chat.tsx` | React UI with SSE handling |

**Databricks MCP Server (`datascope-mcp-server/`):**

| File | Purpose |
|------|---------|
| `app.py` | Flask-based MCP server |
| `app.yaml` | Databricks Apps config |

### Direct Anthropic SDK Usage

The key insight: Using Anthropic SDK directly removes all the complexity:

```typescript
// lib/agent/index.ts
import Anthropic from '@anthropic-ai/sdk'

export class DataScopeAgent {
  private client: Anthropic

  constructor() {
    // Direct API access - your rate limits, no proxy
    this.client = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY
    })
  }

  async investigate(question: string): Promise<AgentMessage> {
    const response = await this.client.messages.create({
      model: 'claude-sonnet-4-20250514',
      max_tokens: 4096,
      system: SYSTEM_PROMPT,
      tools: tools,  // Our tool definitions
      messages: this.conversationHistory
    })
    // ... handle tool use blocks
  }
}
```

Compare to the Databricks approach:
```python
# Previous: Custom LLM wrapper, URL mismatch fixes, network policy workarounds
class DatabricksExternalLLM(BaseChatModel):
    # 100+ lines of code to make it work
```

### Streaming with Server-Sent Events

```typescript
// app/api/chat/route.ts
export async function POST(request: NextRequest) {
  const { message } = await request.json()
  const agent = createAgent()

  const stream = new ReadableStream({
    async start(controller) {
      const encoder = new TextEncoder()
      for await (const chunk of agent.streamInvestigation(message)) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    }
  })

  return new Response(stream, {
    headers: { 'Content-Type': 'text/event-stream' }
  })
}
```

### MCP Client in TypeScript

```typescript
// lib/mcp/client.ts
export class MCPClient {
  async callTool(toolName: string, args?: Record<string, unknown>): Promise<unknown> {
    const response = await fetch(`${this.serverUrl}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.authToken}`
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: getNextId(),
        method: 'tools/call',
        params: { name: toolName, arguments: args }
      })
    })

    const result = await response.json()
    // Extract text content from MCP response
    return JSON.parse(result.result.content[0].text)
  }
}
```

### Interview Talking Points

> "When we hit Databricks rate limits, the question was: fight the platform or change the architecture? We chose to separate concerns - LLM access in Vercel where it's straightforward, data access in Databricks where it's native. MCP bridges them cleanly."

> "The direct Anthropic SDK approach reduced our LLM integration from 100+ lines of custom wrapper code to about 10 lines. That's the power of using tools as designed versus fighting platform constraints."

> "SSE streaming in Next.js is elegant - create a ReadableStream, encode chunks as events, return with the right content-type. The client parses events and updates state in real-time."

---

## Part 5: Custom MCP Server vs Managed MCP vs Direct API Calls

This section captures an important architectural decision about MCP servers that's often misunderstood.

### The Terminology Confusion

| Term | What It Actually Means |
|------|------------------------|
| **Databricks Managed MCP** | Built-in MCP servers Databricks provides (Unity Catalog, SQL Warehouse) - you don't write code |
| **Custom MCP Server** | Your own server implementing the MCP protocol - you write the code |
| **Direct API Calls** | No MCP at all - just call REST APIs directly |

These are **completely different things** with different tradeoffs.

### Option A: Databricks Managed MCP Servers

Databricks provides out-of-the-box MCP servers for their services:

```
┌─────────────────────────────────────────────────────────────────┐
│                         VERCEL                                   │
│   Claude Agent                                                   │
│       │                                                          │
│       ├── MCP ──▶ Databricks Unity Catalog MCP (managed)        │
│       ├── MCP ──▶ Databricks SQL Warehouse MCP (managed)        │
│       └── ??? ──▶ GitHub (not a Databricks service!)            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Limitation**: You CANNOT add custom tools (like GitHub) to a managed MCP server. It's not your code to modify.

### Option B: Direct API Calls (No MCP)

The simplest approach - call APIs directly from Vercel:

```typescript
// All tools are just HTTP calls
async function executeSQL(query: string) {
  return fetch(`${DATABRICKS_HOST}/api/2.0/sql/statements`, {
    headers: { Authorization: `Bearer ${DATABRICKS_TOKEN}` },
    // Token exposed in Vercel environment
  })
}

async function searchCode(term: string) {
  return fetch(`https://api.github.com/search/code?q=${term}`, {
    headers: { Authorization: `token ${GITHUB_TOKEN}` }
  })
}
```

**Tradeoffs**:
- ✅ Simplest architecture
- ✅ Fewest moving parts
- ❌ Tokens (Databricks, GitHub) must be in Vercel environment
- ❌ No single gateway - each tool is independent
- ❌ Harder to add authentication/logging in one place

### Option C: Custom MCP Server (What We Built)

A single gateway server that implements MCP and wraps ALL tools:

```
┌─────────────────────────────────────────────────────────────────┐
│                         VERCEL                                   │
│   Claude Agent                                                   │
│       │                                                          │
│       │  Only needs MCP_SERVER_URL + MCP_AUTH_TOKEN             │
│       │  (No Databricks token, no GitHub token)                 │
│       ▼                                                          │
│   MCP Client ────────────────────────────────────────────────── │
└───────┼─────────────────────────────────────────────────────────┘
        │
        │ Single MCP connection
        ▼
┌───────────────────────────────────────────────────────────────────┐
│           CUSTOM MCP SERVER (Databricks App)                      │
│                                                                   │
│   All secrets live here (secure environment):                    │
│   ├── DATABRICKS_TOKEN                                           │
│   ├── GITHUB_TOKEN                                               │
│   └── Any other API keys                                         │
│                                                                   │
│   Tools exposed via MCP:                                         │
│   ├── execute_sql      → Databricks SQL API                     │
│   ├── search_patterns  → Databricks Vector Search               │
│   ├── get_table_schema → Databricks Unity Catalog               │
│   ├── search_code      → GitHub API                             │
│   └── get_file         → GitHub API                             │
│                                                                   │
│   Benefits:                                                       │
│   • Single authentication point                                  │
│   • All tokens stay in Databricks (trusted environment)         │
│   • Centralized logging/monitoring                               │
│   • Add new tools without changing Vercel code                   │
└───────────────────────────────────────────────────────────────────┘
```

**Tradeoffs**:
- ✅ All secrets stay in Databricks (never in Vercel)
- ✅ Single gateway - one connection, all tools
- ✅ Centralized auth, logging, rate limiting
- ✅ Add new tools by updating MCP server only
- ✅ Learn MCP protocol deeply (valuable skill)
- ❌ More infrastructure to manage
- ❌ Additional network hop (slightly more latency)
- ❌ You must implement and maintain the MCP server

### Why We Chose Custom MCP Server

For this project, we want:

1. **Security**: Keep Databricks and GitHub tokens out of Vercel
2. **Single Gateway**: One MCP endpoint for all tools
3. **Learning**: Understand how MCP servers work with agents
4. **Extensibility**: Add new tools without touching the agent

### The Architecture Decision Matrix

| Factor | Managed MCP | Direct API | Custom MCP |
|--------|-------------|------------|------------|
| **Setup complexity** | Low | Low | Medium |
| **Token security** | N/A | ❌ Tokens in Vercel | ✅ Tokens in Databricks |
| **Custom tools (GitHub)** | ❌ Cannot add | ✅ Yes | ✅ Yes |
| **Single gateway** | ❌ One per service | ❌ No gateway | ✅ Yes |
| **Centralized logging** | ❌ Per service | ❌ No | ✅ Yes |
| **Add new tools** | ❌ Can't | Update Vercel | Update MCP server |
| **Learning value** | Low | Low | ✅ High |

### Interview Talking Points

> "There's an important distinction between Databricks' managed MCP servers and a custom MCP server. Managed servers are out-of-the-box for Databricks services, but you can't add custom tools like GitHub. A custom MCP server lets you create a single gateway that wraps ALL your tools - Databricks, GitHub, whatever you need - with centralized auth and logging."

> "We chose a custom MCP server for security reasons. With direct API calls, every token (Databricks, GitHub) must be in the Vercel environment. With a custom MCP server, all tokens stay in Databricks - the Vercel app only needs the MCP server URL and an auth token for that single gateway."

> "The custom MCP server also has learning value. Implementing the protocol from scratch teaches you how MCP works - the JSON-RPC 2.0 format, tool schemas, the initialize/list/call flow. This is valuable as MCP becomes more prevalent in AI engineering."

---

## Part 6: Galileo AI - Observability and Evaluation

We integrated Galileo AI to add observability and evaluation capabilities to DataScope. This is critical for production AI applications.

### What is Galileo AI?

Galileo is an **AI Observability and Evaluation Platform** that provides:

| Feature | Description |
|---------|-------------|
| **Traces** | Complete execution paths through your AI app |
| **Spans** | Individual operations (LLM calls, tool calls) within traces |
| **Evaluations** | 20+ built-in metrics (hallucination, context adherence, etc.) |
| **Guardrails** | Turn offline evals into production guardrails |
| **Luna Models** | Fast, low-cost evaluation models for real-time monitoring |

### Why We Need Observability

Without observability, you're flying blind:

```
Production Issue: "Agent gave wrong answer"

Without Observability:
├── Which LLM call failed?
├── What tools did it use?
├── How long did each step take?
├── What was the actual prompt/response?
└── "I don't know, let me add logging..."

With Galileo:
├── Open trace in Galileo Console
├── See Timeline view of all LLM and tool calls
├── Click on problematic step
├── See exact input/output
└── "The SQL query returned wrong data because..."
```

### Architecture with Galileo

```
┌─────────────────────────────────────────────────────────────────┐
│                         VERCEL                                   │
│                                                                  │
│   ┌────────────────────────────────────────────────────────┐   │
│   │                 DataScope Agent                         │   │
│   │                                                         │   │
│   │   User Question                                         │   │
│   │        │                                                │   │
│   │        ▼                                                │   │
│   │   ┌─────────────────────────────────────────────────┐  │   │
│   │   │  Galileo Tracer (createTracer)                  │  │   │
│   │   │                                                  │  │   │
│   │   │  ┌─────────┐    ┌─────────┐    ┌─────────┐    │  │   │
│   │   │  │LLM Span │ → │Tool Span│ → │LLM Span │    │  │   │
│   │   │  │Claude   │    │SQL Call │    │Claude   │    │  │   │
│   │   │  │500ms    │    │1200ms   │    │800ms    │    │  │   │
│   │   │  └─────────┘    └─────────┘    └─────────┘    │  │   │
│   │   └─────────────────────────────────────────────────┘  │   │
│   │        │                                                │   │
│   │        ▼                                                │   │
│   │   Final Answer                                          │   │
│   └────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              │ Traces                           │
│                              ▼                                   │
│                     Galileo Console                             │
└─────────────────────────────────────────────────────────────────┘
```

### Implementation

**MCP Server (Python) - Tool Call Tracing:**

```python
# datascope-mcp-server/app.py

from galileo import galileo_context
from galileo.logger import GalileoLogger

# Initialize on startup
galileo_context.init(
    project="datascope-mcp",
    log_stream="mcp-tools"
)

def log_tool_span(tool_name, input_args, output, duration_ms, session_id=None):
    """Log a tool execution as a span in Galileo."""
    galileo_logger = GalileoLogger(
        project="datascope-mcp",
        log_stream="mcp-tools"
    )

    galileo_logger.add_tool_span(
        input=json.dumps(input_args),
        output=json.dumps(output),
        name=tool_name,
        duration_ns=int(duration_ms * 1_000_000),
        tags={"mcp.tool": tool_name, "mcp.session_id": session_id}
    )

    galileo_logger.flush()
```

**Vercel App (TypeScript) - LLM Call Tracing:**

```typescript
// lib/observability/galileo.ts

export class GalileoTracer {
  private traceId: string
  private sessionId: string
  private spans: Array<LLMSpan | ToolSpan> = []

  logLLMCall(span: LLMSpan): void {
    this.spans.push(span)
    // Send to Galileo
  }

  logToolCall(span: ToolSpan): void {
    this.spans.push(span)
    // Send to Galileo
  }

  async complete(userInput: string, agentOutput: string): Promise<void> {
    // Send complete trace to Galileo
    const trace = {
      traceId: this.traceId,
      input: userInput,
      output: agentOutput,
      spans: this.spans
    }
    // await fetch('https://api.galileo.ai/v1/traces', { body: trace })
  }
}
```

**Agent Integration:**

```typescript
// lib/agent/index.ts

async investigate(question: string): Promise<AgentMessage> {
  // Start trace
  this.tracer = createTracer(this.sessionId)

  // LLM call
  const llmStart = Date.now()
  const response = await this.client.messages.create({ ... })
  this.tracer.logLLMCall({
    model: 'claude-sonnet-4-20250514',
    durationMs: Date.now() - llmStart,
    inputTokens: response.usage?.input_tokens
  })

  // Tool calls
  for (const toolCall of response.tool_calls) {
    const toolStart = Date.now()
    const result = await executeTool(toolCall.name, toolCall.input)
    this.tracer.logToolCall({
      name: toolCall.name,
      durationMs: Date.now() - toolStart
    })
  }

  // Complete trace
  await this.tracer.complete(question, fullText)
}
```

### What Galileo Shows You

**Timeline View:**
- Step-by-step execution of your agent
- Duration of each LLM call and tool call
- Where bottlenecks occur

**Conversation View:**
- Full conversation history
- What the user asked, what the agent answered
- Which tools were used

**Evaluation Metrics:**
- Context Adherence: Did the agent use the tool results correctly?
- Hallucination: Did the agent make up information?
- Chunk Utilization: For RAG, how well were retrieved chunks used?

### Interview Talking Points

> "Observability is critical for production AI applications. Without it, debugging issues is like finding a needle in a haystack. Galileo gives us traces, spans, and evaluations so we can see exactly what happened when something goes wrong."

> "We integrated Galileo at two levels: the MCP server logs tool calls as spans, and the Vercel app logs LLM calls. This gives us end-to-end visibility into every investigation."

> "Galileo's Luna models are interesting - they're small language models specifically trained for evaluation. They can run 20+ metrics in real-time without the cost and latency of using a full LLM like GPT-4 for evaluation."

> "The key insight is that AI applications need different observability than traditional software. You can't just log request/response - you need to trace the reasoning process, evaluate the quality of outputs, and monitor for hallucinations."

### Galileo vs Other Observability Tools

| Tool | Focus | Best For |
|------|-------|----------|
| **Galileo** | AI-native observability + evaluation | Agent debugging, LLM quality |
| **LangSmith** | LangChain tracing | LangChain apps specifically |
| **Helicone** | LLM API proxy with logging | Simple LLM logging |
| **OpenTelemetry** | General distributed tracing | Traditional observability |
| **Datadog/New Relic** | APM + infrastructure | Overall system monitoring |

Galileo stands out because it combines observability (traces, spans) with evaluation (metrics, guardrails) specifically for AI applications.

### LangGraph Integration

The LangGraph version of DataScope (`datascope-langgraph-app/`) also includes Galileo observability:

```python
# agent/observability.py - Complete observability module

class GalileoTracer:
    """Tracer for DataScope investigations."""

    def log_llm_call(self, model, input_messages, output_content, duration_ms, ...):
        """Log an LLM call to the trace."""

    def log_tool_call(self, name, input_args, output, duration_ms, error=None):
        """Log a tool call to the trace."""

    async def complete(self, user_input, final_output):
        """Complete the trace and send to Galileo."""

# Usage in graph.py
def invoke_agent(question: str, conversation_id: str):
    tracer = create_tracer(session_id=conversation_id)
    agent = create_agent(config, tracer=tracer)
    # ... agent invocation
    await tracer.complete(question, response)
```

The LangGraph implementation hooks tracing directly into the custom LLM wrapper (`DatabricksExternalLLM`), automatically capturing every LLM call with timing and token counts.

---

*Document created: January 1, 2025*
*Updated: January 2, 2026 - Added Galileo AI to LangGraph app*
*Project: DataScope - Data Debugging Agent*
*Author: Festus Asareyeboah*
