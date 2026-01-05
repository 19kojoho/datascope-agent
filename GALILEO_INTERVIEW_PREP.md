# Galileo Sales Engineer Interview Prep

## How to Describe What You Built

### The Elevator Pitch (30 seconds)

> "I built **DataScope**, an AI agent that helps data engineers debug data quality issues in Databricks. Instead of spending 4 hours manually tracing why a metric is wrong, a user asks a question like 'Why does ARR show $125M but Finance reports $165M?' and the agent investigates - querying data, checking transformation code, finding similar past issues - and returns a root cause analysis in minutes."

### The Expanded Version (2 minutes)

> "DataScope is a **data debugging agent** inspired by Asana's internal Data Debugger tool. Data engineers waste hours investigating issues like 'why do some customers have NULL values in their churn risk score?'
>
> The agent uses Claude as the reasoning engine and has access to six tools:
> - **SQL execution** to query Databricks tables
> - **Schema inspection** to understand data structure
> - **Vector Search** to find similar past issues from a pattern library
> - **Code search** to examine transformation SQL on GitHub
>
> When a user asks a question, the agent doesn't just run one query - it **iteratively investigates**. It might first search for known patterns, then count affected records, then trace the data lineage, then examine the transformation code. Each step informs the next.
>
> I built three versions exploring different architectures - Databricks-native, LangGraph, and direct Anthropic API - before landing on the current design. **Galileo was critical** for understanding why each version behaved the way it did."

---

## The Components (Non-Technical)

### 1. The Frontend (User Interface)
- A chat interface where users type questions
- Shows the agent's thinking in real-time (streaming)
- Displays tool calls as they happen ("Searching patterns...", "Running SQL...")

### 2. The Agent (Brain)
- Takes the user's question and decides what to investigate
- Calls tools to gather information
- Reasons about what it found and decides next steps
- Continues until it has enough evidence to explain the root cause

### 3. The Tools (Hands)
- **execute_sql**: Runs queries against the data warehouse
- **search_patterns**: Finds similar past issues using semantic search
- **get_table_schema**: Understands what columns exist
- **list_sql_files / get_file**: Examines transformation code
- **search_code**: Searches codebase for keywords

### 4. The Pattern Library (Memory)
- 10 curated patterns of common data quality issues
- Stored with embeddings in Databricks Vector Search
- When a new issue comes in, semantic search finds similar past issues
- Gives the agent a "head start" - it doesn't investigate from scratch

### 5. The Observability Layer (Eyes)
- Every LLM call and tool call is traced
- Captures: inputs, outputs, latency, token usage
- Groups all calls for one investigation into a single trace
- **This is where Galileo comes in**

---

## How the Agent Works (The Loop)

```
User asks: "Why do some customers have NULL churn_risk?"
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  AGENT LOOP                                                  │
│                                                              │
│  1. Claude receives question + available tools               │
│     → Decides: "I should check for similar past issues"      │
│     → Calls: search_patterns("NULL churn_risk")              │
│                                                              │
│  2. Pattern found: "CASE statements missing ELSE clause"     │
│     → Claude reasons: "Let me verify with actual data"       │
│     → Calls: execute_sql("SELECT COUNT(*) WHERE ... IS NULL")│
│                                                              │
│  3. Result: 847 records affected                             │
│     → Claude reasons: "Pattern suggests code issue"          │
│     → Calls: get_file("sql/gold/churn_predictions.sql")      │
│                                                              │
│  4. Found: CASE statement without ELSE clause                │
│     → Claude: "I have enough evidence"                       │
│     → Returns: Root cause + evidence + fix recommendation    │
└─────────────────────────────────────────────────────────────┘
```

**Key Point**: The agent makes **multiple decisions** during one investigation. Each decision (which tool to call, what query to run) affects the outcome. **This is why observability matters** - you need to see every decision to understand and improve behavior.

---

## Why I Built Three Versions

### Version 1: Databricks-Native (MLflow/Lakebase)

**What I tried**: Keep everything in Databricks - use their External Endpoints to proxy Claude, use Lakebase for monitoring.

**What happened**:
- Hit rate limits constantly (429 errors)
- External Endpoints are designed for batch inference, not rapid agent interactions
- No streaming - users waited with no feedback
- Lakebase captured metrics but not the **trace structure** I needed

**What I learned**: Enterprise platforms optimize for different use cases. Model serving != interactive agents.

### Version 2: LangGraph

**What I tried**: Use LangGraph's `create_react_agent` for the agent loop, MemorySaver for conversation state.

**What happened**:
- Beautiful abstractions, quick to build
- But when things went wrong, debugging through LangChain's layers was painful
- Still hit Databricks rate limits (hadn't moved off External Endpoints yet)
- LangSmith integration exists but I wanted to try Galileo

**What I learned**: Abstractions are great until you need to understand what's actually happening. For agents, you eventually need to see the raw inputs and outputs.

### Version 3: Direct Anthropic + Galileo (Current)

**What I did**:
- Direct Anthropic API (no proxy, no rate limits)
- Custom agent loop (50 lines of code, full control)
- MCP server for tools (clean separation)
- Galileo for observability

**Why this works**:
- See exactly what Claude receives and returns
- Full streaming for great UX
- Galileo shows me the complete trace - every LLM call, every tool call, every decision

---

## Observability Comparison: MLflow vs LangGraph vs Galileo

This is the key insight from building three versions:

### MLflow/Lakebase (Databricks)

**What it captures**:
- Model metrics (latency, throughput)
- Input/output logging
- Model versioning

**What's missing for agents**:
- **No trace structure** - doesn't understand that 4 LLM calls belong to one investigation
- **No tool call semantics** - sees HTTP requests, not "agent called execute_sql"
- **No reasoning visibility** - can't see why the model chose a tool

**Good for**: Traditional ML models, batch inference, A/B testing models

**Not good for**: Multi-turn agents where you need to trace a sequence of decisions

### LangGraph/LangSmith

**What it captures**:
- Trace structure (parent/child spans)
- LangChain-specific abstractions (chains, tools, agents)
- Token counts and latency

**What's different**:
- Deeply integrated with LangChain ecosystem
- Abstractions can hide details (you see "AgentExecutor" not the raw prompt)
- Great if you're fully in LangChain

**Good for**: Teams standardized on LangChain who want integrated observability

**Challenge**: When you step outside LangChain patterns, instrumentation gets harder

### Galileo

**What it captures**:
- Full trace structure (traces → spans)
- LLM-native semantics (prompts, completions, tool calls)
- Works with any framework (Anthropic, OpenAI, LangChain, custom)

**What makes it different**:
- **Framework agnostic** - I used raw Anthropic SDK, still got full traces
- **LLM-first design** - understands prompts, completions, tokens natively
- **Evaluation hooks** - can score traces after the fact
- **Clear data model** - trace contains spans, spans have inputs/outputs/duration

**Good for**: Teams building custom agents, teams using multiple frameworks, teams who need to see exactly what's happening

### The Key Difference (What to Tell the Hiring Manager)

> "MLflow thinks in terms of **models** - it's great for 'I deployed a model, how's it performing?' But agents aren't single models, they're **orchestrated sequences of model calls and tool calls**.
>
> LangSmith thinks in terms of **LangChain abstractions** - it's great if you're using Chains and Agents from their library.
>
> Galileo thinks in terms of **LLM interactions** - traces, prompts, completions, tools. It doesn't care if I'm using LangChain, raw OpenAI, or Anthropic. It captures what matters: what did the model see, what did it return, how long did it take.
>
> For my agent, I needed to debug questions like 'why did it call execute_sql before search_patterns?' That requires seeing the actual prompt and understanding the decision. Galileo gives me that."

---

## Real Issues We Encountered (Galileo Debugging Stories)

These are **actual issues** from building DataScope - the kind of war stories interviewers love.

---

### Issue 1: The Hanging Agent (Most Important Story)

**What happened**: User asks "Why does ARR show $125M but Finance reports $165M?" The agent calls several tools, shows partial results in the UI... then **hangs forever**. No error, no completion, just stuck.

**The debugging nightmare without observability**:
- MCP server logs showed all tools completed successfully
- Browser console showed streaming was still "open"
- No idea where in the agent loop things got stuck

**What Galileo would have shown immediately**:
```
Trace: investigation-abc123
├─ LLM Call #1: 3200ms ✓
├─ Tool: execute_sql: 1800ms ✓
├─ LLM Call #2: 2900ms ✓
├─ Tool: search_code: 950ms ✓ (returned 0 results)
├─ LLM Call #3: ??? ← STILL RUNNING / NEVER COMPLETED
```

**Root cause discovered**: The streaming loop had a bug where if Claude returned `stop_reason: 'end_turn'` but ALSO included tool calls, we only processed the text and ignored the tools. The agent was waiting for tool results that were never executed.

**The fix**: Simplified the loop - don't try to predict "end_turn", just check if there are tools. If tools exist, execute them. If not, we're done.

**Interview soundbite**:
> "Without tracing, I spent 2 hours adding console.logs everywhere. With Galileo, I would have seen in 30 seconds that LLM Call #3 never completed because the tool results from Call #2 were malformed."

---

### Issue 2: The Empty Tool Inputs

**What happened**: Anthropic API returned a cryptic error: `"tool_use ids were found without tool_result blocks immediately after"`

**What was actually broken**: During streaming, we built tool_use blocks incrementally from deltas. But the `input` field came in separate delta events - which we captured but never assembled. So our conversation history had:
```json
{ "type": "tool_use", "name": "execute_sql", "input": {} }  // Empty!
```

**What Galileo traces showed**: The LLM span output had malformed content blocks. Comparing what Claude sent vs what we stored in history revealed the mismatch immediately.

**The fix**: Don't build state from streaming deltas. Use `finalMessage()` which has complete, validated content blocks.

**Interview soundbite**:
> "Streaming deltas are for UI responsiveness, not for building state. Galileo showed me the exact content block that was malformed - the input was empty. That's a 5-second diagnosis that took me an hour without it."

---

### Issue 3: The Invisible Production Traffic

**What happened**: I had Galileo tracing working in my test method, but **zero traces appeared from real user traffic**.

**Root cause**: I had two methods:
- `investigate()` - Non-streaming, had Galileo tracing
- `streamInvestigation()` - Streaming, **no tracing**

The API route used streaming. So 100% of production traffic was untraced.

**The meta-lesson**: This is exactly why you need observability. I thought tracing was working because my tests passed. But I wasn't testing the actual code path users hit.

**Interview soundbite**:
> "I had a blind spot - my tests used the non-streaming method, but production used streaming. Galileo showed me zero traces, which was itself valuable information. It told me my instrumentation wasn't in the hot path."

---

### Issue 4: GitHub Search Returning Zero Results

**What happened**: User asked about a data issue. The agent called `search_code` to find relevant SQL files. Investigation completed but missed key evidence.

**What Galileo trace showed**:
```
Tool: search_code - 953ms
  Input: {"query": "timezone"}
  Output: {"files_searched": 0, "files_matched": 0, "results": []}
```

**Immediate diagnosis**: GitHub API wasn't returning results. Either auth was wrong or the repo wasn't indexed.

**What I found**: GitHub token wasn't configured in the MCP server. The tool returned gracefully (no error), but with zero results. Without the trace, I might have thought "there's just no relevant code."

**Interview soundbite**:
> "The agent didn't crash - it just gave an incomplete answer. Galileo showed me the tool returned zero results despite relevant code existing. That's a silent failure that's almost impossible to catch without tracing."

---

### Issue 5: Durations Showing as Zero

**What happened**: Everything worked, traces appeared in Galileo dashboard, but all durations showed as **0ms** or **0.001ms**.

**Root cause**: Galileo SDK expects nanoseconds. I was passing milliseconds.

```typescript
// WRONG
durationNs: span.durationMs  // 3000 interpreted as 3000ns = 0.003ms

// CORRECT
durationNs: span.durationMs * 1_000_000  // 3000ms = 3,000,000,000ns
```

**Interview soundbite**:
> "This is a subtle bug. The code works, data flows, but the numbers are wrong by a factor of a million. I only caught it because I knew a 45-second investigation shouldn't show 0ms in the dashboard."

---

### Issue 6: SDK API Surprise

**What happened**: Following Galileo docs, I wrote:
```typescript
const trace = await logger.startTrace({...})
await trace.addLlmSpan({...})  // TypeError: not a function
```

**Root cause**: The SDK uses a stateful logger pattern, not an object-oriented trace pattern. Methods are on the `GalileoLogger`, not on a returned trace object.

```typescript
// CORRECT
logger.startTrace(input, output, name, ...)
logger.addLlmSpan({...})
logger.conclude({...})
await logger.flush()
```

**How I debugged**: Read the actual SDK source on GitHub instead of relying on docs.

**Interview soundbite**:
> "Newer SDKs sometimes have doc/implementation mismatches. I've learned to verify against the actual code. This is feedback I'd share with Galileo's team - the TypeScript SDK could use clearer examples."

---

## Questions You Might Get Asked

### "How did you integrate Galileo?"

> "I used the TypeScript SDK. Created a tracer at the start of each investigation, logged LLM calls with `addLlmSpan()`, logged tool calls with `addWorkflowSpan()`, and called `flush()` at the end. The main gotcha was duration units - Galileo expects nanoseconds, and I was initially passing milliseconds, so everything showed as 0ms."

### "What would you tell a customer evaluating Galileo vs alternatives?"

> "First, what are you building? If it's a simple chatbot with one LLM call per request, most tools work fine. But if you're building agents - multi-turn, tool-using, decision-making systems - you need trace structure that understands LLM semantics. Galileo is built for that.
>
> Second, are you locked into one framework? LangSmith is great for LangChain shops. But if you're using multiple approaches or raw SDKs, Galileo's framework-agnostic approach is valuable.
>
> Third, what's your debugging workflow? Can you go from 'user reported bad answer' to 'here's the exact prompt that caused it' in under a minute? That's the bar for production agents."

### "What challenges did you face building this agent?"

> "The biggest challenge was the iterative nature of debugging agents. Unlike traditional software where you can step through code, agent behavior emerges from the interaction between prompts, context, and model responses. You can't 'step through' Claude's reasoning. You have to infer it from inputs and outputs.
>
> Observability isn't optional for agents - it's the only way to understand what's happening. Galileo became my primary debugging tool. When something went wrong, my first action was always 'check the trace.'"

---

## Your Story Arc for the Interview

1. **Context**: "I'm learning AI engineering by building real systems. I built a data debugging agent for Databricks."

2. **Problem**: "Data engineers spend hours investigating issues. My agent reduces that to minutes."

3. **Journey**: "I built three versions - Databricks-native, LangGraph, and direct API - learning trade-offs between abstraction and control."

4. **Observability Insight**: "Agents are different from traditional ML. You need to trace sequences of decisions, not just model calls. This is where MLflow falls short and Galileo shines."

5. **Personal Experience**: "Galileo became my debugging superpower. Every time the agent misbehaved, the trace showed me exactly why."

6. **Why Galileo (the company)**: "I believe observability is the missing piece for production AI. Most teams are building agents blind. Galileo solves that."

---

## Closing Statement

> "I've experienced firsthand the difference between building agents with and without proper observability. Galileo transforms agent development from guesswork to science. I'd love to help other teams experience that same transformation."
