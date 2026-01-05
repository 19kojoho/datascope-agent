# LangGraph Deployment Debugging: A Case Study

This document captures the debugging journey when deploying the LangGraph version of DataScope to Databricks Apps.

---

## The Problem

After creating the LangGraph app and attempting to deploy it to Databricks Apps, the deployment failed with:

```
Error: app crashed unexpectedly. Please check /logz for more details
```

---

## Debugging Journey

### Step 1: Initial Deploy Attempt

```bash
databricks apps deploy datascope-langgraph \
  --source-code-path /Workspace/Users/fasareyeboah@gmail.com/datascope-langgraph-app
```

**Result**: `FAILED` - App crashed unexpectedly

### Step 2: Try to Access Logs

```bash
databricks apps logs datascope-langgraph
```

**Result**: `Error: OAuth Token not supported for current auth type pat`

The logs command didn't work with PAT authentication, so we couldn't see the actual error directly.

### Step 3: Check Deployment Details

```bash
databricks apps list-deployments datascope-langgraph
```

**Result**:
```json
{
  "status": {
    "message": "Error: app crashed unexpectedly. Please check /logz for more details",
    "state": "FAILED"
  }
}
```

No useful information about the actual crash reason.

### Step 4: Test Imports Locally

Since we couldn't get logs from Databricks, we tested locally:

```bash
cd datascope-langgraph-app
python3 -c "from agent.config import Config; from agent.graph import create_agent; print('Import successful')"
```

**Result**: `Import successful`

Imports worked fine locally. The issue wasn't a missing module.

### Step 5: Test with Real Configuration

We then tested with actual environment variables set:

```bash
export DATABRICKS_HOST="https://xxx.databricks.com"
export DATABRICKS_TOKEN="dapi..."
export DATABRICKS_SQL_WAREHOUSE_ID="..."
export LLM_ENDPOINT_NAME="claude-sonnet-endpoint"

python3 -c "
from agent.config import get_config
from agent.graph import create_agent
config = get_config()
agent = create_agent(config)
"
```

**Result**:
```
TypeError: create_react_agent() got unexpected keyword arguments: {'state_modifier': "You are DataScope..."}
```

**Root cause found!** The `state_modifier` parameter doesn't exist in the current version of LangGraph.

---

## Root Cause Analysis

### Issue 1: Wrong Parameter Name

The original code used:

```python
_agent = create_react_agent(
    model=llm,
    tools=tools,
    checkpointer=checkpointer,
    state_modifier=SYSTEM_PROMPT,  # WRONG!
)
```

The `state_modifier` parameter was from an older version of LangGraph. In newer versions, it's been renamed to `prompt`.

### How We Confirmed This

Checked the current API signature:

```bash
python3 -c "from langgraph.prebuilt import create_react_agent; help(create_react_agent)"
```

Output showed the correct parameter:

```
prompt: An optional prompt for the LLM. Can take a few different forms:
    - str: This is converted to a SystemMessage and added to the beginning...
    - SystemMessage: this is added to the beginning...
    - Callable: This function should take in full graph state...
    - Runnable: This runnable should take in full graph state...
```

### Issue 2: SqliteSaver Initialization (Potential)

We also simplified the checkpointer from `SqliteSaver` to `MemorySaver` during debugging:

**Original**:
```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string(db_path)
```

**Simplified to**:
```python
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()
```

While this may not have been the direct cause of the crash, it removed a potential source of issues with file-based persistence in the Databricks Apps environment.

---

## The Fix

### Change 1: Correct Parameter Name

```python
# Before (broken)
_agent = create_react_agent(
    model=llm,
    tools=tools,
    checkpointer=checkpointer,
    state_modifier=SYSTEM_PROMPT,
)

# After (fixed)
_agent = create_react_agent(
    model=llm,
    tools=tools,
    checkpointer=checkpointer,
    prompt=SYSTEM_PROMPT,  # Correct parameter name
)
```

### Change 2: Simplify Checkpointer

```python
# Before
from langgraph.checkpoint.sqlite import SqliteSaver
_checkpointer = SqliteSaver.from_conn_string(db_path)

# After
from langgraph.checkpoint.memory import MemorySaver
_checkpointer = MemorySaver()
```

### Change 3: Update requirements.txt

Removed the sqlite dependency since we're using MemorySaver:

```
# Before
langgraph>=0.2.50
langgraph-checkpoint-sqlite>=2.0.0

# After
langgraph>=0.2.50
```

---

## Verification

### Local Test After Fix

```bash
python3 -c "
from agent.config import get_config
from agent.graph import create_agent
config = get_config()
agent = create_agent(config)
print('Agent type:', type(agent))
"
```

**Result**:
```
Agent created successfully
Agent type: <class 'langgraph.graph.state.CompiledStateGraph'>
```

### Deployment After Fix

```bash
databricks apps deploy datascope-langgraph \
  --source-code-path /Workspace/Users/fasareyeboah@gmail.com/datascope-langgraph-app
```

**Result**:
```json
{
  "status": {
    "message": "App started successfully",
    "state": "SUCCEEDED"
  }
}
```

---

## Lessons Learned

### 1. Test Locally with Real Config

The imports worked fine, but the actual agent creation failed. Always test with real configuration values, not just imports.

```python
# Not enough:
from agent.graph import create_agent

# Better:
config = get_config()  # Load real config
agent = create_agent(config)  # Actually create the agent
```

### 2. Check API Signatures for Framework Updates

LangGraph is actively developed. Parameter names change:
- `state_modifier` → `prompt`

Always check the current API:
```python
help(create_react_agent)
```

Or check the source:
```bash
python3 -c "import langgraph; print(langgraph.__version__)"
```

### 3. Databricks Apps Logs Are Hard to Access

The `databricks apps logs` command didn't work with PAT authentication. When logs aren't accessible:
1. Test locally with the same environment
2. Add explicit error handling and logging
3. Consider adding a `/debug` endpoint that shows configuration

### 4. Start Simple, Add Complexity Later

We switched from `SqliteSaver` (persistent) to `MemorySaver` (in-memory) to get the app working first.

**Approach**:
1. Get basic functionality working
2. Add persistence later once core works
3. Each feature addition should be testable independently

### 5. Error Messages Can Be Misleading

The error "app crashed unexpectedly" doesn't tell you anything. The actual error was a `TypeError` for an invalid parameter name - a simple fix once identified.

---

## Debugging Checklist for Future Deployments

When a Databricks App crashes:

- [ ] Check deployment status: `databricks apps list-deployments <app-name>`
- [ ] Try to get logs: `databricks apps logs <app-name>`
- [ ] Test imports locally: `python3 -c "from module import ..."`
- [ ] Test with real config: Set env vars and create actual objects
- [ ] Check framework API changes: `help(function_name)`
- [ ] Simplify dependencies: Remove optional features temporarily
- [ ] Add debug endpoints: `/health`, `/debug` for configuration visibility
- [ ] Check Python version compatibility between local and Databricks

---

## Files Changed

| File | Change |
|------|--------|
| `agent/graph.py` | Changed `state_modifier` to `prompt`, switched to `MemorySaver` |
| `requirements.txt` | Removed `langgraph-checkpoint-sqlite` |

---

## Timeline

| Time | Action | Result |
|------|--------|--------|
| 09:10 | First deploy attempt | FAILED |
| 09:15 | Try to access logs | Logs not accessible |
| 09:20 | Test imports locally | Passed |
| 09:25 | Test with real config | Found TypeError |
| 09:30 | Check API signature | Found parameter rename |
| 09:35 | Apply fixes | Code updated |
| 09:40 | Redeploy | SUCCEEDED |

**Total debugging time**: ~30 minutes

---

## Issue 3: Runtime Error - Network Policy Blocking Anthropic API

### Symptoms

After fixing the startup crash and successfully deploying the app, runtime requests failed with:

```json
{
  "error_code": "CUSTOMER_UNAUTHORIZED",
  "message": "CUSTOMER_UNAUTHORIZED: Access to api.anthropic.com is denied because of serverless network policy."
}
```

### Investigation

1. **Initial hypothesis**: Wrong LLM client library
   - Original code used `ChatOpenAI` from `langchain-openai`
   - Changed to `ChatDatabricks` from `databricks-langchain`

2. **Testing the fix locally**:
   ```bash
   export DATABRICKS_HOST="https://xxx.databricks.com"
   export DATABRICKS_TOKEN="dapi..."

   python3 -c "
   from agent.graph import invoke_agent
   result = invoke_agent('Say hello', 'test-123')
   print(result)
   "
   ```
   **Result**: Same error - `Access to api.anthropic.com is denied`

3. **Direct endpoint test**:
   ```bash
   curl -X POST "https://xxx.databricks.com/serving-endpoints/claude-sonnet-endpoint/invocations" \
     -H "Authorization: Bearer dapi..." \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"Hello"}]}'
   ```
   **Result**: Same error - the endpoint itself is blocked

4. **Tested pure Python app**: Also returning 503

### Root Cause

The issue is **not a code problem** but a **Databricks workspace infrastructure issue**:

- The External Endpoint `claude-sonnet-endpoint` is correctly configured
- It proxies requests to Anthropic's API at `api.anthropic.com`
- The serverless compute environment has a **network policy** that blocks outbound connections to `api.anthropic.com`
- This affects ALL apps using this endpoint, not just the LangGraph version

### Resolution Options

1. **Workspace Admin Action**: Update the serverless network policy to allow `api.anthropic.com`
   - Navigate to Admin Console → Network Policies
   - Add `api.anthropic.com` to the allowed outbound destinations

2. **Use Foundation Model APIs**: Switch to Databricks' built-in foundation model endpoints
   - These don't require external network access
   - Available endpoints: `databricks-meta-llama-3-3-70b-instruct`, `databricks-gpt-oss-*`

3. **Use Classic Compute**: Deploy the serving endpoint on classic compute instead of serverless
   - Classic compute has fewer network restrictions by default

### Code Changes Made (Still Valid)

Even though this didn't fix the network issue, these changes are correct for Databricks:

**agent/graph.py**:
```python
# Before
from langchain_openai import ChatOpenAI

def get_llm(config):
    return ChatOpenAI(
        model=config.llm_endpoint,
        base_url=f"{config.databricks_host}/serving-endpoints",
        api_key=config.databricks_token,
    )

# After
from databricks_langchain import ChatDatabricks

def get_llm(config):
    return ChatDatabricks(
        endpoint=config.llm_endpoint,
        temperature=0,
        max_tokens=4096,
    )
```

**requirements.txt**:
```
# Before
langchain-openai>=0.3.0

# After
databricks-langchain>=0.1.0
```

### Key Insight

The `ChatDatabricks` client automatically uses environment variables (`DATABRICKS_HOST`, `DATABRICKS_TOKEN`) or service principal credentials when running in Databricks. This is the correct integration for Databricks-hosted LLM endpoints.

---

## Issue 4: Endpoint Not Found After Network Policy Fix

### Symptoms

After the Databricks admin fixed the serverless network policy, the app returned:

```json
{
  "error_code": "ENDPOINT_NOT_FOUND",
  "message": "The given endpoint does not exist, please retry after checking the specified model and version deployment exists."
}
```

### Root Cause

During debugging, we had deleted the old `claude-sonnet-endpoint` and created a new one `claude-sonnet-endpoint-v2`. The app config wasn't updated to use the new endpoint name.

### Fix

Updated `app.yaml` to use the new endpoint:

```yaml
# Before
- name: LLM_ENDPOINT_NAME
  value: "claude-sonnet-endpoint"

# After
- name: LLM_ENDPOINT_NAME
  value: "claude-sonnet-endpoint-v2"
```

Also updated the pure Python app (`datascope-ui-app/app.yaml`) to use the same endpoint.

### Key Learning

When recreating external endpoints, update ALL apps that depend on them. Keep endpoint names consistent or use a configuration management approach.

---

## Issue 5: App Crash After Switching to databricks-langchain

### Symptoms

After deploying with `databricks-langchain` package, the app crashed:

```
App Status: CRASHED
Message: App has status: App crashed unexpectedly
```

### Root Cause

The `databricks-langchain` package has heavy dependencies (mlflow, databricks-vectorsearch, etc.) that caused issues in the Databricks Apps environment.

### Fix

Reverted to `langchain-openai` with proper Databricks endpoint configuration:

**agent/graph.py**:
```python
from langchain_openai import ChatOpenAI

def get_llm(config: Config) -> ChatOpenAI:
    return ChatOpenAI(
        model=config.llm_endpoint,
        base_url=f"{config.databricks_host}/serving-endpoints",
        api_key=config.databricks_token,
        temperature=0,
    )
```

**requirements.txt**:
```
langchain-openai>=0.3.0  # Back to langchain-openai
```

### Key Learning

Simpler dependencies are better. `langchain-openai` with custom `base_url` works fine for Databricks external endpoints. Avoid heavy packages when lighter alternatives exist.

---

## Issue 6: Anthropic Rejects max_completion_tokens Parameter

### Symptoms

```json
{
  "error_code": "BAD_REQUEST",
  "message": "max_completion_tokens: Extra inputs are not permitted"
}
```

### Root Cause

The `langchain-openai` package (v0.3.x) automatically converts `max_tokens` to `max_completion_tokens` in the API request. This is an OpenAI-specific parameter that Anthropic's API rejects.

### Investigation

Searched for solutions and found this is a known issue:
- [GitHub Issue #30113](https://github.com/langchain-ai/langchain/issues/30113) - `max_tokens` replaced with `max_completion_tokens`
- [GitHub Issue #31024](https://github.com/langchain-ai/langchain/issues/31024) - Documentation confusion

### Fix

Removed `max_tokens` parameter entirely. Let the model use its default:

```python
# Before (broken)
return ChatOpenAI(
    model=config.llm_endpoint,
    base_url=f"{config.databricks_host}/serving-endpoints",
    api_key=config.databricks_token,
    temperature=0,
    max_tokens=4096,  # Gets converted to max_completion_tokens
)

# After (fixed)
return ChatOpenAI(
    model=config.llm_endpoint,
    base_url=f"{config.databricks_host}/serving-endpoints",
    api_key=config.databricks_token,
    temperature=0,
    # No max_tokens - Anthropic doesn't accept max_completion_tokens
)
```

### Key Learning

When using `langchain-openai` with non-OpenAI providers (Anthropic, etc.), avoid OpenAI-specific parameters. The library makes assumptions that don't apply to other providers.

---

## Issue 7: Recursion Limit Exceeded

### Symptoms

```
Investigation failed: Recursion limit of 15 reached without hitting a stop condition.
```

### Root Cause

Complex questions require many tool calls. The default recursion limit of 15 (~5 tool iterations) wasn't enough for thorough investigations.

### Fix

Increased recursion limit from 15 to 50:

```python
# Before
result = agent.invoke(
    {"messages": [{"role": "user", "content": question}]},
    config={
        **thread_config,
        "recursion_limit": 15,  # Too low
    }
)

# After
result = agent.invoke(
    {"messages": [{"role": "user", "content": question}]},
    config={
        **thread_config,
        "recursion_limit": 50,  # Allows ~15 tool calls
    }
)
```

### Key Learning

For ReAct agents investigating complex data issues, budget for 10-15 tool calls. Each tool call uses ~3 recursion steps (LLM → Tool → LLM).

---

## Issue 8: "Failed to Fetch" in Browser

### Symptoms

Browser showed "Error: Failed to fetch" for complex questions, but simple questions worked.

### Root Cause

Complex investigations take too long (60+ seconds), exceeding browser's default fetch timeout.

### Workaround

- Use simpler, more specific questions
- Break complex investigations into steps
- (Optional) Add frontend timeout configuration

### Key Learning

Agent investigations are inherently slow (multiple LLM calls + tool executions). Set user expectations or add loading indicators for long-running queries.

---

## Design Decision: Adding LangSmith Observability

### Problem

No visibility into agent reasoning, tool calls, or decision-making process.

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **LangSmith** | Native LangGraph support, simple setup | SaaS dependency |
| **Galileo.ai** | Hallucination detection, guardrails | More complex setup |
| **Custom logging** | Full control | More development work |

### Decision: LangSmith

Chose LangSmith for initial observability because:
1. **Zero code changes** - Just environment variables
2. **Native support** - Built for LangGraph
3. **Free tier** - 5,000 traces/month
4. **Rich UI** - Full trace visualization

### Implementation

Added to `app.yaml`:
```yaml
# LangSmith Observability
- name: LANGSMITH_TRACING
  value: "true"
- name: LANGSMITH_API_KEY
  value: "lsv2_pt_..."
- name: LANGSMITH_PROJECT
  value: "datascope-langgraph"
```

### Result

Full visibility into:
- Every LLM call with inputs/outputs
- Tool calls with arguments and results
- Agent reasoning between steps
- Latency and token usage per step
- Multi-turn conversation threading

---

## Issue 9: SQL Tool Rejecting DESCRIBE and SHOW Statements

### Symptoms

From LangSmith traces, observed the agent attempting:
```sql
DESCRIBE novatech.gold.revenue_recognition
```

But receiving:
```
Error: Only SELECT queries are allowed for safety.
```

### Root Cause

The SQL tool validation was too restrictive:

```python
# Original validation
if not query_upper.startswith("SELECT"):
    return "Error: Only SELECT queries are allowed for safety."
```

This blocked `DESCRIBE` and `SHOW` commands, which are safe read-only commands useful for:
- Understanding table schemas
- Listing available tables
- Checking column data types

### Fix

Updated the validation to allow safe read-only commands:

```python
# Updated validation
allowed_prefixes = ["SELECT", "DESCRIBE", "DESC", "SHOW"]
if not any(query_upper.startswith(prefix) for prefix in allowed_prefixes):
    return "Error: Only SELECT, DESCRIBE, and SHOW queries are allowed for safety."
```

Also updated the tool's docstring to document the new capabilities.

### Key Learning

When building data investigation tools, consider all the read-only commands that would help an agent understand the data:
- `DESCRIBE table` - column names and types
- `SHOW TABLES IN schema` - available tables
- `SHOW COLUMNS IN table` - alternative to DESCRIBE

---

## Current Architecture

After all fixes, the LangGraph app uses:

| Component | Implementation |
|-----------|----------------|
| **LLM Client** | `langchain-openai.ChatOpenAI` with Databricks base_url |
| **LLM Endpoint** | `claude-sonnet-endpoint-v2` (Anthropic via Databricks) |
| **State Management** | `MemorySaver` (in-memory, per-session) |
| **Observability** | LangSmith tracing |
| **Tools** | search_patterns, execute_sql, search_code |
| **Recursion Limit** | 50 steps (~15 tool calls) |

---

## Files Changed Summary

| File | Changes Made |
|------|--------------|
| `agent/graph.py` | ChatOpenAI config, removed max_tokens, increased recursion limit |
| `agent/tools.py` | Allow DESCRIBE/SHOW commands in SQL validation |
| `agent/prompts.py` | Rewrote for adaptive, hypothesis-driven investigation |
| `requirements.txt` | Reverted to langchain-openai |
| `app.yaml` | New endpoint name, LangSmith env vars |
| `datascope-ui-app/app.yaml` | Updated to use new endpoint |

---

## Lessons Learned (Updated)

1. **Test with real providers** - OpenAI-compatible doesn't mean identical behavior
2. **Simpler dependencies win** - Avoid heavy packages when lighter alternatives work
3. **Budget for agent complexity** - ReAct agents need generous recursion limits
4. **Add observability early** - LangSmith setup takes 5 minutes, saves hours of debugging
5. **Update all dependents** - When changing endpoints, update every app that uses them
6. **Parameter compatibility varies** - What works for OpenAI may not work for Anthropic

---

*Document updated: December 31, 2024*
*App URL: https://datascope-langgraph-1262935113136277.gcp.databricksapps.com*
*LangSmith Project: datascope-langgraph*
