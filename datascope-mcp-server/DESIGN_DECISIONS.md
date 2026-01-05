# MCP Server Design Decisions & Journey

This document captures the architectural decisions, trade-offs, and reasoning behind the DataScope MCP Server. Use this for interview preparation to demonstrate thoughtful system design.

---

## The Problem

**Goal**: Build an MCP (Model Context Protocol) server that allows an AI agent to query Databricks data for debugging data quality issues.

**Key Constraints**:
1. Multiple users with different access levels in Databricks
2. Must enforce Unity Catalog permissions (RLS, column masks, table grants)
3. Secure communication between Vercel frontend and Databricks backend
4. Production-ready authentication (no hardcoded secrets)

---

## Decision 1: Where to Host the MCP Server?

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **Vercel (with frontend)** | Simple deployment, one platform | Databricks credentials in Vercel, latency to Databricks |
| **AWS Lambda** | Serverless, scalable | Cold starts, credential management, network config |
| **Databricks Apps** ✅ | Close to data, auto-managed SP, platform integration | Locked into Databricks |

### Decision: Databricks Apps

**Why**:
- Direct network access to Databricks APIs (no VPN/firewall issues)
- Databricks automatically provisions a Service Principal for the app
- Lower latency (same network as SQL Warehouse)
- Secrets stay within Databricks platform

**Interview Answer**:
> "I chose Databricks Apps because it keeps the MCP server close to the data layer. This reduces latency for SQL queries and eliminates the need to expose Databricks credentials to external platforms. Databricks also auto-provisions a Service Principal, which simplifies credential management."

---

## Decision 2: How to Handle Multi-User Authentication?

### The Challenge

Different users have different access levels in Databricks:
- User A can see tables X, Y, Z
- User B can only see table X
- Row-level security filters data by user's region/team

If we use a single Service Principal token, ALL users get the same access - breaking permission boundaries.

### Options Considered

| Option | Per-User Permissions? | Complexity |
|--------|----------------------|------------|
| **Service Principal only** | ❌ No - everyone same access | Low |
| **User token pass-through** | ✅ Yes - each user's permissions | Medium |
| **Re-implement permissions in app** | ⚠️ Partial - error-prone | High |

### Decision: Hybrid Two-Token Model

```
Authorization: Bearer <SP_token>     → Proves "request is from Vercel app"
X-User-Token: <user_token>           → User's permissions for data access
```

**Why**:
- **SP token**: Authenticates the app itself (prevents unauthorized apps from calling MCP)
- **User token**: Ensures Unity Catalog enforces per-user permissions
- **Separation of concerns**: App auth vs data authorization are different problems

**Interview Answer**:
> "I implemented a two-token model because app authentication and data authorization are separate concerns. The Service Principal token proves the request comes from our legitimate Vercel app - this is app-to-app authentication. The user token is passed through to Databricks for data queries, ensuring Unity Catalog enforces that specific user's permissions including row-level security and column masks."

---

## Decision 3: Static Secret vs OAuth for App Authentication

### Initial Approach (v3.0)

We started with a static shared secret (`MCP_AUTH_TOKEN`):
```python
if hmac.compare_digest(token, MCP_AUTH_TOKEN):
    return authorized
```

**Problem**: Static secrets are:
- Hard to rotate
- Not tied to identity (any leaked secret works)
- Not the "modern" approach

### Research: What Does Databricks Recommend?

We researched Databricks documentation and found:
- OAuth M2M (client credentials) is recommended for service-to-service auth
- Service Principals can have OAuth secrets (valid up to 2 years)
- Tokens are short-lived (1 hour) and self-describing (JWT)

### Decision: OAuth M2M (v3.1)

```python
# Vercel gets token via M2M flow
POST /oidc/v1/token
  grant_type=client_credentials
  client_id=<SP_APP_ID>
  client_secret=<SP_SECRET>

# MCP validates by:
# 1. Decode JWT, check 'sub' claim matches allowed SP
# 2. Verify token with Databricks API
# 3. Cache valid tokens (5 min TTL)
```

**Why OAuth over static secret**:
1. **Tokens expire** - leaked token only works for 1 hour
2. **Identity-based** - we know exactly which SP made the request
3. **Standard protocol** - well-understood, auditable
4. **Rotation built-in** - just refresh the token

**Interview Answer**:
> "I upgraded from static secrets to OAuth M2M because OAuth provides defense in depth. Tokens expire after 1 hour, so a leaked token has limited blast radius. The JWT contains the client_id, so we can verify exactly which service principal made the request. And it follows industry standards - any security auditor will recognize and approve this pattern."

---

## Decision 4: How to Validate OAuth Tokens?

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **Decode JWT only** | Fast, no API call | Doesn't verify token is still valid |
| **Token introspection endpoint** | Standard OAuth | Databricks may not support it |
| **Call Databricks API with token** ✅ | Proves token works | Extra API call per request |
| **JWT + API call** ✅ | Best of both | Slightly more complex |

### Decision: JWT Claims Check + API Verification + Caching

```python
def validate_oauth_token(token):
    # 1. Check cache first
    if token in cache and not expired:
        return cached_result

    # 2. Decode JWT, check client_id
    claims = decode_jwt(token)
    if claims['sub'] != ALLOWED_SP_APP_ID:
        return False, "Unauthorized client"

    # 3. Verify with Databricks API
    response = requests.get(
        f"{DATABRICKS_HOST}/api/2.0/preview/scim/v2/Me",
        headers={"Authorization": f"Bearer {token}"}
    )

    # 4. Cache result (5 min TTL)
    cache[token] = (is_valid, time.time() + 300)
    return is_valid
```

**Why this approach**:
1. **JWT check is fast** - catches wrong SP immediately without API call
2. **API call verifies token is valid** - not revoked, not expired
3. **Caching reduces latency** - don't call API for every request
4. **5-minute TTL is safe** - tokens last 1 hour, so 5 min cache is fine

**Interview Answer**:
> "I validate tokens in three steps: First, I decode the JWT and check the client_id claim matches our allowed Service Principal - this is fast and catches unauthorized clients immediately. Second, I call the Databricks SCIM API to verify the token is actually valid and not revoked. Third, I cache successful validations for 5 minutes to reduce API calls. The cache TTL is much shorter than the token lifetime, so we'll catch revoked tokens quickly."

---

## Decision 5: User Token Handling

### The Question

Should we validate the user token, or just pass it through?

### Decision: Pass-Through Only

```python
def execute_sql(query):
    user_token = request.headers.get("X-User-Token")

    # Just pass it to Databricks - they validate it
    response = requests.post(
        f"{DATABRICKS_HOST}/api/2.0/sql/statements",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"statement": query}
    )
```

**Why pass-through**:
1. **Databricks validates it anyway** - no benefit to double-validation
2. **We can't revoke user tokens** - not our auth system
3. **Simpler code** - less to go wrong
4. **User gets clear errors** - "401 Unauthorized" from Databricks if token is bad

**Interview Answer**:
> "For user tokens, I chose pass-through rather than validation. Databricks will validate the token when we make the SQL API call, so pre-validating would just add latency without benefit. This also means users get clear error messages directly from Databricks if their token is expired or invalid."

---

## Trade-Offs Accepted

### 1. Databricks Platform Lock-In

**Trade-off**: MCP server only works with Databricks
**Accepted because**: This is a Databricks-specific tool anyway. The data is in Databricks, the permissions are in Unity Catalog. Platform lock-in is acceptable for platform-specific tooling.

### 2. OAuth Adds Latency

**Trade-off**: Token validation adds ~100-200ms per request (first time)
**Mitigated by**: Caching valid tokens for 5 minutes. Subsequent requests use cache.

### 3. Service Principal Credential Management

**Trade-off**: Need to store SP secret somewhere (Vercel env vars)
**Accepted because**:
- Secret rotates every 2 years (configurable)
- Better than storing Databricks PAT tokens
- Standard practice for M2M auth

---

## What We Learned

### 1. SCIM API Doesn't Return applicationId for SPs

**Problem**: We tried to get the SP's applicationId from `/scim/v2/Me` response
**Reality**: SCIM returns empty applicationId for service principal tokens
**Solution**: Decode JWT and extract client_id from the `sub` claim instead

### 2. Databricks Apps OAuth Flow is Different

**Assumption**: Apps on Databricks Apps could use auto-injected credentials
**Reality**: For external clients (Vercel on vercel.com), you need manual SP setup
**Learning**: Databricks Apps auto-credentials only work for apps calling Databricks APIs directly, not for incoming auth

### 3. Token Caching is Essential

**Without cache**: Every MCP request = 1 token validation API call = 100-200ms overhead
**With cache**: Only first request pays the cost, rest are instant
**Learning**: Always cache auth decisions with appropriate TTL

---

## Architecture Evolution

### v1.0 - Simple PAT Token
```
Vercel → MCP Server (uses DATABRICKS_TOKEN env var) → Databricks
```
**Problem**: Single token, no per-user permissions

### v2.0 - User Token Pass-Through
```
Vercel → MCP Server (uses X-User-Token header) → Databricks
```
**Problem**: Any client can call MCP server (no app auth)

### v3.0 - Static Secret + User Token
```
Vercel (with MCP_AUTH_TOKEN) → MCP Server → Databricks (with user token)
```
**Problem**: Static secret is not modern/rotatable

### v3.1 - OAuth M2M + User Token (Final)
```
Vercel (with SP OAuth token) → MCP Server (validates JWT) → Databricks (with user token)
```
**Benefits**: Modern auth, per-user permissions, auditable, rotatable

---

## Interview Talking Points

### 1. "Why not just use a single token?"

> "A single token can't solve both problems. App authentication proves the request comes from our Vercel app - this prevents unauthorized clients from calling our MCP server. Data authorization determines what data the specific user can see - this requires their personal token so Unity Catalog can enforce their permissions. Conflating these would either break security (any client can call MCP) or break multi-tenancy (all users see same data)."

### 2. "Why OAuth instead of API keys?"

> "OAuth tokens are short-lived (1 hour), self-describing (JWT contains identity), and follow industry standards. API keys are long-lived, anonymous, and proprietary. If an OAuth token leaks, it expires quickly. If an API key leaks, it works until someone notices and rotates it manually."

### 3. "How do you ensure per-user access control?"

> "I pass the user's Databricks OAuth token through to all Databricks API calls. This means Databricks sees the request as coming from that specific user, and applies all their Unity Catalog permissions - table grants, row-level security filters, column masks. The MCP server never evaluates permissions itself; it delegates entirely to Databricks, which is the source of truth."

### 4. "What happens if a token expires?"

> "The SP OAuth token expires after 1 hour. Vercel should cache it and refresh before expiry using the client credentials flow again. User tokens also expire after 1 hour; the user would need to re-authenticate with Databricks. The MCP server returns clear 401 errors so the client knows to refresh tokens."

### 5. "How does caching work?"

> "I cache successful token validations for 5 minutes using a simple in-memory dictionary with expiry timestamps. This is safe because tokens are valid for 1 hour - a 5-minute cache can't serve a revoked token for long. The cache key is a hash of the token, so different tokens get different cache entries."

---

## Future Improvements

If I had more time, I would add:

1. **Redis cache** - Current in-memory cache doesn't work across multiple gunicorn workers
2. **Token refresh helper** - Endpoint to check if tokens are near expiry
3. **Metrics** - Track auth failures, cache hit rates, latency percentiles
4. **Rate limiting** - Prevent brute force attacks on the MCP endpoint
5. **Audit logging** - Log which SP and user made each request

---

## Local Development vs Production Deployment

### The Production Vision (Databricks Apps)

```
┌──────────────────┐     OAuth Token      ┌────────────────────────┐
│   Vercel App     │ ──────────────────►  │  Databricks Apps       │
│   (Next.js)      │                      │  (MCP Server)          │
│                  │                      │                        │
│  User logs in    │                      │  Platform validates    │
│  via Databricks  │                      │  OAuth at gateway      │
│  OAuth PKCE      │                      │  before reaching app   │
└──────────────────┘                      └───────────┬────────────┘
                                                      │
                                                      ▼
                                          ┌────────────────────────┐
                                          │  Databricks SQL        │
                                          │  (Unity Catalog)       │
                                          │                        │
                                          │  Per-user permissions  │
                                          └────────────────────────┘
```

**Benefits:**
- Zero secrets in Vercel (user authenticates directly)
- Per-user permissions via Unity Catalog
- Databricks Apps validates tokens at platform level
- Auto-provisioned Service Principal

**Limitation Encountered:**
When we tried to deploy, we hit: `"OAuth application with client_id not available in Databricks account"`

This happens because:
1. Service Principals must be created at the account level
2. Our workspace didn't have access to the SP we created
3. Databricks Apps OAuth requires proper account-level configuration

### The Local Development Approach (Current)

```
┌──────────────────┐                      ┌────────────────────────┐
│   Vercel App     │  HTTP (localhost)    │  Local MCP Server      │
│   (Next.js)      │ ──────────────────►  │  (Python/Flask)        │
│   localhost:3000 │                      │  localhost:8001        │
│                  │                      │                        │
│  No user auth    │                      │  No auth required      │
│  required        │                      │  Uses PAT token        │
└──────────────────┘                      └───────────┬────────────┘
                                                      │
                                                      ▼
                                          ┌────────────────────────┐
                                          │  Databricks SQL        │
                                          │  (Unity Catalog)       │
                                          │                        │
                                          │  Single user (PAT)     │
                                          └────────────────────────┘
```

**Benefits:**
- Works immediately without account-level configuration
- Fast iteration for development and testing
- Same MCP protocol and tools work identically
- Perfect for demos and POC

**Trade-offs:**
- Single-user access (PAT token permissions only)
- No multi-user support
- Not production-ready security

### Architecture Comparison

| Aspect | Production (Databricks Apps) | Local Development |
|--------|------------------------------|-------------------|
| **Hosting** | Databricks Apps | Local Python process |
| **Auth** | OAuth (platform gateway) | None (dev mode) |
| **Data Access** | Per-user OAuth token | Single PAT token |
| **Multi-user** | ✅ Yes | ❌ No |
| **Network** | Public internet | localhost only |
| **Setup** | Account-level SP config | Run `python3 app.py` |

### Key Insight: MCP Protocol is Identical

The MCP (Model Context Protocol) works exactly the same in both modes:

```python
# Same JSON-RPC 2.0 protocol
POST /mcp
{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "execute_sql",
        "arguments": {"query": "SELECT * FROM table"}
    }
}
```

The only differences are:
1. **URL**: `localhost:8001` vs `datascope-mcp-xxx.databricksapps.com`
2. **Auth**: None vs OAuth Bearer token
3. **Token for Databricks**: Env var PAT vs User's OAuth token

**Interview Answer:**
> "The MCP protocol is transport-agnostic. Whether hosted locally or on Databricks Apps, the JSON-RPC 2.0 interface is identical. This allowed us to develop and test locally while designing for production deployment. The main difference is authentication - production uses OAuth validated by Databricks Apps' platform gateway, while local development skips auth for faster iteration."

### Path to Production

To move from local dev to production:

1. **Register Service Principal** at Databricks account level
2. **Configure OAuth client** with proper redirect URIs
3. **Deploy to Databricks Apps** with app.yaml
4. **Update Vercel** to use production MCP URL
5. **Implement OAuth PKCE** in Vercel for user login

The code is already OAuth-ready (see `lib/auth/databricks-oauth.ts`), just needs the account-level configuration.

---

## Summary

| Aspect | Decision | Reasoning |
|--------|----------|-----------|
| **Hosting** | Databricks Apps | Close to data, auto SP, no credential exposure |
| **App Auth** | OAuth M2M | Modern, short-lived tokens, identity-based |
| **User Auth** | Token pass-through | Per-user permissions, Unity Catalog enforced |
| **Validation** | JWT + API + Cache | Fast reject, verified valid, low latency |
| **Permissions** | Delegated to Databricks | Single source of truth, no re-implementation |
| **Local Dev** | PAT token + no auth | Fast iteration, same MCP protocol |

---

## Debugging Journey: Issues Encountered and Fixes

This section documents real issues we encountered during development, how we diagnosed them, and the design decisions behind the fixes. These are valuable for interviews to demonstrate debugging skills and system design thinking.

---

### Issue 1: Agent Streaming Loop Hanging

**Symptom**: User asks a complex question (e.g., "Why does ARR show $125M but Finance reports $165M?"). The agent calls several tools, shows partial results, then hangs indefinitely with no final response or error.

**Initial Investigation**:
```
MCP Server logs showed:
- execute_sql completed ✓
- get_table_schema completed ✓
- search_code completed ✓ (returned error - GitHub not configured)
- No further requests...
```

The MCP server was working fine. The hang was in the Vercel agent code.

**Root Cause Analysis**:

The streaming code had flawed logic for handling Claude's `stop_reason`:

```typescript
// BUGGY CODE
if (finalMessage.stop_reason === 'end_turn') {
  // Make one more request to get final response after tools
  const finalStream = await this.client.messages.stream({...})

  for await (const event of finalStream) {
    // Only yield text deltas
    if (event.delta.type === 'text_delta') {
      yield `event: text\ndata: ${JSON.stringify({...})}\n\n`
    }
  }

  yield `event: done\ndata: {}\n\n`
  break
}
```

**Problems with this approach**:

1. **Incomplete tool handling**: If Claude's "final" response contains MORE tool calls, we ignored them (only yielded text)
2. **Missing history update**: The finalStream response wasn't added to conversation history
3. **Duplicate done events**: Both the inner block and outer loop could yield 'done'
4. **Hang scenario**: Claude calls tools → we execute → stop_reason='end_turn' → we make final request → Claude calls MORE tools → we only stream text → response incomplete → UI shows partial result

**The Fix - Simplified Loop Design**:

```typescript
// FIXED CODE
while (iterations < MAX_ITERATIONS) {
  // Stream response
  const stream = await this.client.messages.stream({...})

  // Process streaming events
  for await (const event of stream) {
    if (event.content_block.type === 'tool_use') {
      hasToolUse = true
    }
    // ... yield events
  }

  // Add complete response to history
  const finalMessage = await stream.finalMessage()
  this.conversationHistory.push({
    role: 'assistant',
    content: finalMessage.content  // Use complete blocks, not partial
  })

  // Simple exit condition: no tools = we're done
  if (!hasToolUse) {
    yield `event: done\ndata: {}\n\n`
    return  // Use return to avoid any duplicate yields
  }

  // Execute tools, add results to history
  // Loop continues naturally - no special end_turn handling needed
}
```

**Design Decision**: Let the loop naturally continue until Claude responds with just text. Don't try to predict when Claude is "done" - just check if there are tools to execute. This is simpler and more robust.

**Interview Answer**:
> "The original code tried to optimize for the 'end_turn' case by making a special final request. But this created edge cases where Claude's 'final' response might still contain tools. The fix was to simplify: just loop until there are no more tools. This follows the principle of 'make the common case simple' - most responses either have tools (continue) or don't (stop). No special cases needed."

---

### Issue 2: Partial Content Blocks in Streaming

**Symptom**: Anthropic API returned error: `"tool_use ids were found without tool_result blocks immediately after"`

**Root Cause**:

During streaming, we built tool_use blocks incrementally:

```typescript
// BUGGY CODE
currentToolUse = {
  type: 'tool_use',
  id: event.content_block.id,
  name: event.content_block.name,
  input: {}  // Empty! Input comes in deltas
}

// Later...
} else if (event.delta.type === 'input_json_delta') {
  // We captured this but never used it!
}

// Added partial block to history
assistantContent.push(currentToolUse)  // Has empty input
```

When we added this to conversation history, the tool_use blocks had empty inputs. Claude's API saw tool_use blocks that didn't match the tool_result blocks (different structure).

**The Fix**:

```typescript
// FIXED CODE
// Don't build content during streaming - just track if tools exist
for await (const event of stream) {
  if (event.content_block.type === 'tool_use') {
    hasToolUse = true  // Just track, don't build
  }
  // Stream text deltas to UI
}

// Use finalMessage which has COMPLETE content blocks
const finalMessage = await stream.finalMessage()
this.conversationHistory.push({
  role: 'assistant',
  content: finalMessage.content  // Complete, not partial
})
```

**Design Decision**: Streaming is for UI responsiveness, not for building state. Use `finalMessage()` for any state that needs to be accurate (conversation history, tool execution).

**Interview Answer**:
> "There's a separation of concerns here. Streaming events are optimized for real-time UI updates - they come in small deltas. But for conversation history, we need complete, validated content blocks. The Anthropic SDK provides `finalMessage()` exactly for this purpose. Using streaming deltas for state management is an anti-pattern."

---

### Issue 3: Observability Not Working (Galileo)

**Symptom**: No Galileo logs appearing. User couldn't debug agent behavior or measure performance.

**Root Causes** (multiple):

1. **Missing environment variable**: `GALILEO_API_KEY` wasn't in Vercel's `.env.local`
2. **Tracing only in non-streaming method**: The `investigate()` method had Galileo tracing, but `streamInvestigation()` didn't
3. **Placeholder implementation**: The actual Galileo API call was commented out

**The Fix**:

```typescript
// Added to streamInvestigation()
async *streamInvestigation(question: string): AsyncGenerator<string> {
  // Create tracer at start
  const tracer = createTracer(this.sessionId)
  let fullText = ''

  while (...) {
    const streamStartTime = Date.now()

    // Stream and accumulate text
    for await (const event of stream) {
      if (event.delta.type === 'text_delta') {
        fullText += event.delta.text  // Accumulate for trace
        yield ...
      }
    }

    // Log LLM call
    tracer.logLLMCall({
      model: 'claude-sonnet-4-20250514',
      durationMs: Date.now() - streamStartTime,
      inputTokens: finalMessage.usage?.input_tokens,
      outputTokens: finalMessage.usage?.output_tokens
    })

    // Log each tool call
    for (const block of finalMessage.content) {
      if (block.type === 'tool_use') {
        const toolStartTime = Date.now()
        const result = await executeTool(...)

        tracer.logToolCall({
          name: toolBlock.name,
          input: toolBlock.input,
          durationMs: Date.now() - toolStartTime,
          error: result.is_error ? resultContent : undefined
        })
      }
    }
  }

  // Complete trace at end
  await tracer.complete(question, fullText)
}
```

**Design Decision**: Observability should be in the hot path (streaming), not just the batch path. Every method that handles requests should be instrumented.

**Interview Answer**:
> "Observability is a cross-cutting concern that should be consistent across all code paths. Having tracing only in the non-streaming method meant production traffic (which uses streaming) was invisible. The fix ensures we capture LLM latency, tool latency, token usage, and errors regardless of which method is called."

---

### Issue 4: Tool Results Truncated in UI

**Symptom**: UI showed partial tool results (cut off at 500 chars), making debugging difficult.

**Root Cause**:

```typescript
yield `event: tool_result\ndata: ${JSON.stringify({
  name: toolBlock.name,
  result: resultContent.slice(0, 500)  // Arbitrary truncation
})}\n\n`
```

**Design Decision**: This is actually intentional. SSE payloads should be small for performance. The full result is:
1. Logged to Galileo (for debugging)
2. Added to conversation history (for Claude to use)
3. Truncated only for UI streaming

**Interview Answer**:
> "This is a conscious trade-off between debuggability and performance. The UI doesn't need full SQL result sets - it just needs to show progress. The full data goes to Galileo for debugging and to the conversation history for Claude. If users need to see full results, they can check Galileo traces or we could add a 'show full result' UI feature."

---

### System Design Principles Applied

| Principle | Application |
|-----------|-------------|
| **Separation of Concerns** | Streaming for UI, finalMessage for state, Galileo for debugging |
| **Fail-Safe Defaults** | Loop continues until explicitly done, not until special case |
| **Observability by Default** | Every code path instrumented, not just happy path |
| **Single Source of Truth** | Use SDK's finalMessage(), don't reconstruct from deltas |
| **Graceful Degradation** | Missing GitHub config → return error, don't crash |

---

### Debugging Checklist (for future issues)

1. **Check MCP server logs** - Is the tool being called? Is it returning?
2. **Check for streaming vs batch** - Are both code paths equivalent?
3. **Check conversation history** - Are we using complete content blocks?
4. **Check exit conditions** - Are there multiple ways to exit the loop?
5. **Check observability** - Is the code path instrumented?
6. **Check environment variables** - Are all required vars set in all environments?

---

## Galileo AI Integration: Observability for LLM Applications

This section documents our integration of Galileo AI for observability, the challenges we encountered, and the architectural decisions we made. This is a real-world example of adding production-grade observability to an AI agent application.

---

### Why Galileo?

**The Problem**: Our DataScope agent makes multiple LLM calls and tool calls per investigation. When something goes wrong (hallucination, slow response, wrong tool choice), we need to understand:
- What did the LLM see as input?
- What tools did it call and why?
- Where did time go?
- What was the token usage?

**Why Galileo over alternatives**:

| Feature | Galileo | LangSmith | Custom Logging |
|---------|---------|-----------|----------------|
| LLM-specific semantics | ✅ Native | ✅ Native | ❌ Build yourself |
| Token tracking | ✅ Built-in | ✅ Built-in | ❌ Parse yourself |
| Trace visualization | ✅ Dashboard | ✅ Dashboard | ❌ Build yourself |
| Evaluation hooks | ✅ Built-in | ✅ Built-in | ❌ Build yourself |
| TypeScript SDK | ✅ Official | ⚠️ Community | N/A |

**Interview Answer**:
> "I chose Galileo because it's purpose-built for LLM observability. Unlike generic APM tools, Galileo understands LLM semantics - it knows about prompts, completions, tokens, and tool calls. The TypeScript SDK made integration straightforward, and the trace visualization helps debug complex multi-turn agent conversations."

---

### Integration Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     DataScope Agent                              │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ User Query   │───▶│ Claude API   │───▶│ MCP Tools    │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│         │                   │                   │               │
│         ▼                   ▼                   ▼               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   GalileoTracer                          │   │
│  │  - startTrace(input)                                     │   │
│  │  - logLLMCall(model, input, output, tokens, duration)   │   │
│  │  - logToolCall(name, input, output, duration)           │   │
│  │  - complete(output) + flush()                            │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  Galileo Cloud   │
                    │  api.galileo.ai  │
                    │                  │
                    │  - Trace storage │
                    │  - Visualization │
                    │  - Evaluation    │
                    └──────────────────┘
```

**Key Design Decisions**:

1. **Tracer per investigation**: Each user question creates a new `GalileoTracer` instance
2. **Lazy initialization**: Logger is created on first use, not at startup
3. **Async but non-blocking**: Galileo calls don't block the response stream
4. **Flush on complete**: Data is sent only when investigation finishes

---

### Issue 1: SDK API Mismatch

**Symptom**:
```
TypeError: this.trace.addLlmSpan is not a function
```

**What Happened**:

The documentation showed an API pattern that didn't match the actual SDK:

```typescript
// WHAT DOCUMENTATION SHOWED (incorrect)
const trace = await logger.startTrace({...})
await trace.addLlmSpan({...})  // ❌ Method doesn't exist on trace object

// WHAT SDK ACTUALLY PROVIDES (correct)
logger.startTrace(input, output, name, createdAt, durationNs, metadata, tags)
logger.addLlmSpan({...})  // ✅ Methods are on logger, not trace
logger.conclude({...})
await logger.flush()
```

**Root Cause**: The SDK uses a different pattern - methods are on the `GalileoLogger` instance, not on a returned trace object. The logger maintains internal state about the current trace/span hierarchy.

**The Fix**:

```typescript
// Before: Tried to use trace object
const trace = await logger.startTrace({...})
await trace.addLlmSpan({...})  // ❌ Error

// After: Use logger methods directly
logger.startTrace(input, undefined, name, Date.now() * 1_000_000, ...)
logger.addLlmSpan({
  input: [{ role: 'user', content: span.input }],
  output: { role: 'assistant', content: span.output },
  model: span.model,
  durationNs: span.durationMs * 1_000_000
})
logger.conclude({ output, durationNs })
await logger.flush()
```

**Interview Answer**:
> "The SDK documentation showed an object-oriented pattern with methods on the trace object, but the actual SDK uses a stateful logger pattern. I debugged this by checking the SDK source on GitHub, which revealed the correct method signatures. This is a common challenge with newer SDKs - always verify against the actual implementation."

---

### Issue 2: OpenAI Module Warning

**Symptom**:
```
⚠ ./node_modules/galileo/dist/openai.js
Module not found: Can't resolve 'openai'
```

**What Happened**: The Galileo SDK has optional OpenAI wrapper functionality. When the `openai` package isn't installed, the bundler warns about the missing dependency.

**Analysis**:
- We're using Anthropic (Claude), not OpenAI
- The warning is from unused code paths in the SDK
- Functionality works fine despite the warning

**Decision**: Accept the warning rather than installing unnecessary dependencies.

**Alternative Considered**: Install `openai` package just to silence the warning.
**Rejected Because**: Adding unused dependencies increases bundle size and potential security surface.

**Interview Answer**:
> "The Galileo SDK supports multiple LLM providers through optional integrations. Since we're using Anthropic, the OpenAI wrapper code is dead code for us. I chose to accept the build warning rather than add an unnecessary dependency. In a production deployment, I'd configure the bundler to ignore this specific warning."

---

### Issue 3: Streaming vs Batch Instrumentation

**Symptom**: Traces were only appearing for some requests, not others.

**Root Cause**: We had two code paths:
1. `investigate()` - Non-streaming, had Galileo instrumentation
2. `streamInvestigation()` - Streaming, was missing instrumentation

The API route used streaming, so production traffic wasn't traced.

**The Fix**: Add identical instrumentation to both methods:

```typescript
async *streamInvestigation(question: string): AsyncGenerator<string> {
  // Create tracer at the START of the method
  const tracer = createTracer(this.sessionId)
  let fullText = ''  // Accumulate output for trace

  while (iterations < MAX_ITERATIONS) {
    const streamStartTime = Date.now()

    for await (const event of stream) {
      if (event.delta.type === 'text_delta') {
        fullText += event.delta.text  // Capture for trace
        yield ...
      }
    }

    // Log after stream completes (we have full content)
    await tracer.logLLMCall({
      model: 'claude-sonnet-4-20250514',
      input: JSON.stringify(this.conversationHistory.slice(-1)),
      output: JSON.stringify(finalMessage.content),
      durationMs: Date.now() - streamStartTime,
      inputTokens: finalMessage.usage?.input_tokens,
      outputTokens: finalMessage.usage?.output_tokens
    })

    // ... tool execution with tracing ...
  }

  // Complete trace before returning
  await tracer.complete(question, fullText)
  yield `event: done\ndata: {}\n\n`
}
```

**Design Decision**: Instrument both code paths identically, even though it means some code duplication. The alternative (shared instrumentation middleware) would add complexity without clear benefit.

**Interview Answer**:
> "Observability must cover all code paths, especially the production hot path. We had streaming and non-streaming methods, but only the non-streaming one was instrumented. I fixed this by adding tracing to the streaming method, ensuring we capture LLM calls, tool calls, and timing regardless of which method is used."

---

### Issue 4: Duration Units (Milliseconds vs Nanoseconds)

**Symptom**: Durations in Galileo dashboard showed as 0 or extremely small values.

**Root Cause**: Galileo SDK expects nanoseconds, but we were passing milliseconds:

```typescript
// WRONG - passing milliseconds
durationNs: span.durationMs  // 3000 (interpreted as 3000ns = 0.003ms)

// CORRECT - convert to nanoseconds
durationNs: span.durationMs * 1_000_000  // 3000000000 (= 3 seconds)
```

**Interview Answer**:
> "The SDK uses nanoseconds for precision, which is common in tracing systems (OpenTelemetry also uses nanoseconds). I had to convert our millisecond measurements. This is a subtle bug that's easy to miss - the code works, but the dashboard shows wrong values."

---

### Issue 5: Trace Lifecycle Management

**Challenge**: When should we start/end traces? What if the user abandons mid-conversation?

**Design Decisions**:

1. **Start trace on first LLM call**, not on tracer creation:
```typescript
async logLLMCall(span: LLMSpan): Promise<void> {
  // Lazy start - only create trace when we have actual content
  this.startTrace(span.input)
  // ...
}
```

2. **One tracer per investigation**, not per request:
```typescript
// In streamInvestigation()
const tracer = createTracer(this.sessionId)  // Session groups related calls
```

3. **Explicit flush on complete**:
```typescript
async complete(userInput: string, agentOutput: string): Promise<void> {
  logger.conclude({ output: agentOutput, durationNs: ... })
  await logger.flush()  // Ensure data is sent before response ends
}
```

**Trade-off**: If the server crashes mid-investigation, unflushed spans are lost. We accepted this because:
- Crash recovery is a separate concern
- Buffering reduces API calls to Galileo
- Most investigations complete normally

**Interview Answer**:
> "I chose lazy initialization and explicit flushing to balance reliability and efficiency. Traces start on first LLM call (not tracer creation) so we don't create empty traces. We flush at the end rather than per-span to reduce API calls. The trade-off is losing data on crashes, but that's acceptable for observability data."

---

### Data Model: What We Send to Galileo

```typescript
// Trace (one per investigation)
{
  input: "Why does ARR show $125M but Finance reports $165M?",
  output: "The discrepancy is caused by...",
  name: "Investigation: abc-123",
  durationNs: 56000000000,  // 56 seconds
  metadata: { sessionId: "abc-123" },
  tags: ["investigation", "datascope"]
}

// LLM Span (one per Claude API call)
{
  input: [{ role: "user", content: "..." }],
  output: { role: "assistant", content: "..." },
  model: "claude-sonnet-4-20250514",
  name: "LLM Call (claude-sonnet-4-20250514)",
  durationNs: 3000000000,  // 3 seconds
  numInputTokens: 1500,
  numOutputTokens: 800,
  tags: ["llm", "anthropic"]
}

// Tool Span (one per MCP tool call)
{
  input: '{"query": "SELECT ..."}',
  output: '{"rows": [...]}',
  name: "Tool: execute_sql",
  durationNs: 2000000000,  // 2 seconds
  userMetadata: { toolName: "execute_sql" },
  tags: ["tool", "mcp", "execute_sql"]
}
```

**Interview Answer**:
> "I structured the trace data to maximize debuggability. Each LLM span includes the exact prompt and response, plus token counts for cost tracking. Tool spans include both input parameters and results, tagged by tool type for filtering. The trace-level metadata includes session ID so we can correlate multiple investigations from the same user."

---

### Production Considerations

**What I'd add for production**:

1. **Sampling**: Not every request needs full tracing
```typescript
const shouldTrace = Math.random() < 0.1  // 10% sampling
const tracer = shouldTrace ? createTracer() : nullTracer
```

2. **Error tracking**: Separate error spans for failures
```typescript
if (result.is_error) {
  tracer.logError({
    name: toolBlock.name,
    error: resultContent,
    tags: ['error', 'tool-failure']
  })
}
```

3. **Cost tracking**: Calculate API costs per investigation
```typescript
const inputCost = (inputTokens / 1000) * 0.003  // $3/1M input
const outputCost = (outputTokens / 1000) * 0.015  // $15/1M output
tracer.addMetadata({ estimatedCost: inputCost + outputCost })
```

4. **User context**: Include user ID for per-user debugging
```typescript
tracer.addMetadata({
  userId: session.userId,
  userEmail: session.email
})
```

**Interview Answer**:
> "For production, I'd add sampling to control costs and volume. I'd also add cost tracking per investigation since LLM costs can vary dramatically. User context would help debug issues for specific customers. The current implementation is a solid foundation that can be extended."

---

### Lessons Learned

1. **SDK documentation vs reality**: Always verify against actual SDK code
2. **Unit consistency**: Check if APIs expect milliseconds, seconds, or nanoseconds
3. **Streaming is different**: Observability patterns that work for batch may not work for streaming
4. **Lazy initialization**: Don't create resources until you need them
5. **Explicit lifecycle**: Make start/end explicit rather than implicit

---

### Files Modified for Galileo Integration

| File | Changes |
|------|---------|
| `lib/observability/galileo.ts` | Complete rewrite to use official SDK |
| `lib/agent/index.ts` | Added tracer to streamInvestigation() |
| `.env.local` | Added GALILEO_API_KEY, GALILEO_PROJECT, GALILEO_LOG_STREAM |
| `package.json` | Added `galileo` dependency |

---

### Useful Links

- [Galileo TypeScript SDK](https://github.com/rungalileo/galileo-js)
- [Galileo Documentation](https://v2docs.galileo.ai/sdk-api/typescript/overview)
- [OpenTelemetry Integration](https://v2docs.galileo.ai/sdk-api/third-party-integrations/opentelemetry-and-openinference)

---

## Databricks Vector Search: Semantic Pattern Matching

This section documents the design and implementation of Vector Search for finding similar past data quality issues. This is a key capability that allows the agent to learn from past investigations.

---

### Why Vector Search for Pattern Matching?

**The Problem**: When a user asks "Why does ARR show $125M but Finance reports $165M?", the agent should recognize this as similar to past issues involving:
- Aggregation filters excluding records
- Different metric definitions
- Missing data categories

Keyword search won't work because:
- User might say "totals don't match" instead of "ARR discrepancy"
- The same pattern manifests with different table names
- Symptoms are described in natural language

**Solution**: Semantic similarity search using embeddings.

```
User Question: "Why do some customers have NULL churn_risk?"
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                 Databricks Vector Search                     │
│                                                             │
│  1. Embed query using databricks-bge-large-en              │
│  2. Search pattern_library index for similar symptoms       │
│  3. Return top-k patterns with similarity scores            │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
Pattern Found: PAT-005 "NULL Values Not Handled in Conditional Logic"
  - Symptoms: "NULL values in fields that should have values"
  - Root Cause: "CASE statements don't handle NULL cases"
  - Resolution: "Add ELSE clause to CASE statements"
  - Investigation SQL: "SELECT COUNT(*) WHERE churn_risk IS NULL"
```

**Interview Answer**:
> "I chose Vector Search over keyword search because data quality issues are described differently by different users. 'NULL values appearing' and 'missing data in column' are semantically similar but lexically different. Databricks Vector Search with BGE embeddings captures this semantic similarity, allowing the agent to find relevant patterns even when wording differs."

---

### Design Decision: Pattern Library Structure

**Options Considered**:

| Option | Pros | Cons |
|--------|------|------|
| **Flat text per pattern** | Simple embedding | Loses structure, harder to use results |
| **Structured JSON** | Rich information | Complex embedding, what to embed? |
| **Computed embedding column** ✅ | Best of both | Slightly more complex table design |

**Decision**: Structured storage with computed embedding column

```sql
CREATE TABLE novatech.gold.datascope_patterns (
    -- Structured fields for retrieval
    pattern_id STRING,
    title STRING,
    category STRING,
    symptoms STRING,      -- JSON array
    root_cause STRING,
    resolution STRING,
    investigation_sql STRING,

    -- Computed column combining key fields for embedding
    embedding_text STRING GENERATED ALWAYS AS (
        CONCAT(
            'Issue: ', title, '. ',
            'Symptoms: ', COALESCE(symptoms, ''), '. ',
            'Cause: ', COALESCE(root_cause, '')
        )
    )
)
```

**Why This Design**:

1. **Separate storage from embedding**: The embedding column combines title + symptoms + root_cause, but we store them separately for structured retrieval
2. **JSON arrays in SQL**: Symptoms are stored as JSON strings for flexibility, parsed in application code
3. **Computed column**: Ensures embedding always reflects current data, auto-updates on row changes
4. **Change Data Feed enabled**: Supports incremental index updates via DELTA_SYNC

**Interview Answer**:
> "I separated the storage schema from the embedding strategy. We store structured fields like symptoms and root_cause separately because the agent needs to use them programmatically. But for similarity search, I combine them into a single `embedding_text` computed column. This gives us both structured retrieval and semantic search without data duplication."

---

### Design Decision: Index Type (DELTA_SYNC vs DIRECT_ACCESS)

**Options**:

| Index Type | Description | Best For |
|------------|-------------|----------|
| **DIRECT_ACCESS** | Compute embeddings at query time | Small datasets, real-time updates |
| **DELTA_SYNC** ✅ | Pre-computed embeddings, synced from Delta table | Larger datasets, consistent performance |

**Decision**: DELTA_SYNC with TRIGGERED pipeline

```python
payload = {
    "name": INDEX_NAME,
    "endpoint_name": ENDPOINT_NAME,
    "index_type": "DELTA_SYNC",
    "delta_sync_index_spec": {
        "source_table": f"{CATALOG}.{SCHEMA}.{TABLE_NAME}",
        "pipeline_type": "TRIGGERED",  # vs CONTINUOUS
        "embedding_source_columns": [
            {
                "name": "embedding_text",
                "embedding_model_endpoint_name": "databricks-bge-large-en"
            }
        ]
    }
}
```

**Why DELTA_SYNC**:

1. **Query latency**: Pre-computed embeddings = faster queries
2. **Cost efficiency**: Embedding computed once per pattern, not per query
3. **Consistency**: All searches use same embedding model version
4. **Scale**: Can handle thousands of patterns without query slowdown

**Why TRIGGERED vs CONTINUOUS**:

1. **Pattern library changes infrequently**: New patterns added monthly, not per-second
2. **Cost control**: Don't need continuous compute for static data
3. **Manual control**: Can trigger sync after batch pattern updates

**Interview Answer**:
> "I chose DELTA_SYNC over DIRECT_ACCESS because our pattern library is relatively static - we add new patterns periodically, not continuously. Pre-computing embeddings reduces query latency and costs. I used TRIGGERED pipeline type because continuous sync would waste resources on a slowly-changing table. We can manually trigger sync after adding new patterns."

---

### Design Decision: Embedding Model Selection

**Options Available in Databricks**:

| Model | Dimensions | Strengths |
|-------|------------|-----------|
| databricks-bge-large-en | 1024 | Best quality, English-focused |
| databricks-bge-base-en | 768 | Good balance of quality/speed |
| databricks-gte-large-en | 1024 | Good for retrieval tasks |
| OpenAI text-embedding-ada-002 | 1536 | External API, high quality |

**Decision**: `databricks-bge-large-en`

**Why**:

1. **Native to Databricks**: No external API calls, lower latency
2. **Quality**: BGE-large is top-tier for retrieval tasks
3. **English-focused**: Our patterns and queries are all English
4. **Cost**: Included in Databricks, no per-token charges

**Trade-off Accepted**: BGE-large is ~40% slower than BGE-base, but pattern search is not latency-critical (runs once per investigation, not per token).

**Interview Answer**:
> "I chose databricks-bge-large-en because it's native to the platform (no external API latency), high quality (MTEB benchmarks show it's competitive with OpenAI embeddings), and cost-effective (included in Databricks pricing). The slightly higher latency vs base model is acceptable since pattern search runs once per investigation."

---

### Integration with MCP Server

The `search_patterns` tool in the MCP server queries Vector Search:

```python
def search_patterns(query: str, user_token: str = None) -> dict:
    """Search for similar past data quality issues."""
    if not VS_INDEX:
        return {"error": "Vector Search not configured", "patterns": []}

    url = f"{DATABRICKS_HOST}/api/2.0/vector-search/indexes/{VS_INDEX}/query"
    resp = requests.post(
        url,
        headers=get_databricks_headers(user_token),
        json={
            "query_text": query,
            "columns": [
                "pattern_id", "title", "symptoms",
                "root_cause", "resolution", "investigation_sql"
            ],
            "num_results": 3
        }
    )

    # Parse results...
    return {"patterns": patterns, "count": len(patterns)}
```

**Design Decisions**:

1. **Pass user token**: Vector Search respects Unity Catalog permissions
2. **Return top 3**: More results = more context for LLM, but diminishing returns
3. **Include investigation_sql**: Gives agent concrete next steps
4. **Graceful degradation**: Return empty patterns if VS not configured, don't crash

**Interview Answer**:
> "The MCP tool wraps the Vector Search API with proper error handling and authentication. I pass the user's token so Unity Catalog permissions apply - even patterns might be access-controlled. Returning top 3 matches gives the agent enough context without overwhelming it. The graceful degradation ensures the agent works even if Vector Search isn't set up."

---

### Pattern Library Content

The pattern library (`config/pattern_library.json`) contains 10 curated patterns:

| Pattern ID | Title | Related Bugs |
|------------|-------|--------------|
| PAT-001 | Timezone Mismatch Between Source Systems | BUG-001 |
| PAT-002 | Late-Arriving Data Not Reflected in Status | BUG-002 |
| PAT-003 | Aggregation Excludes Relevant Records | BUG-003 |
| PAT-004 | Duplicate Records Inflating Metrics | BUG-004 |
| PAT-005 | NULL Values Not Handled in Conditional Logic | BUG-005 |
| PAT-006 | Join Fanout Causing Row Multiplication | BUG-006 |
| PAT-007 | Schema Drift Breaking Downstream | BUG-007 |
| PAT-008 | Incorrect Customer Classification | BUG-001, BUG-005 |
| PAT-009 | Metric Discrepancy Between Reports | BUG-003, BUG-004 |
| PAT-010 | Missing Historical Data | - |

**Pattern Structure**:
```json
{
  "pattern_id": "PAT-005",
  "title": "NULL Values Not Handled in Conditional Logic",
  "category": "Data Quality",
  "symptoms": [
    "NULL values in fields that should have values",
    "CASE statements returning unexpected NULL",
    "Aggregations excluding records silently"
  ],
  "root_cause": "CASE statements or WHERE clauses don't handle NULL cases.",
  "investigation_sql": "SELECT COUNT(*) FROM gold.churn_predictions WHERE churn_risk IS NULL",
  "resolution": "Add ELSE clause to CASE statements. Use COALESCE() for default values.",
  "related_bugs": ["BUG-005"],
  "databricks_features": ["Data quality expectations", "Unity Catalog column statistics"]
}
```

**Interview Answer**:
> "The pattern library is curated from common data quality issues. Each pattern includes symptoms (for matching), root cause (for explanation), investigation SQL (for immediate action), and resolution (for fixing). This structure allows the agent to not just identify the problem but also guide the user toward a solution."

---

### Setup Process

The setup script (`scripts/setup_vector_search.py`) automates:

1. **Create patterns table** with computed embedding column
2. **Load pattern data** from JSON file
3. **Create Vector Search endpoint** (if needed)
4. **Create DELTA_SYNC index** on the table
5. **Test search** with sample queries

**Key Implementation Details**:

```python
# Enable Change Data Feed for incremental sync
TBLPROPERTIES (
    'delta.enableChangeDataFeed' = 'true',
    'delta.columnMapping.mode' = 'name'
)

# Wait for endpoint to come online (can take 10-15 minutes)
while status != "ONLINE":
    time.sleep(30)
    status = check_endpoint_status()

# Wait for index to be ready (can take 15-20 minutes)
while not (status == "ONLINE" and ready == True):
    time.sleep(30)
    status, ready = check_index_status()
```

**Challenges and Solutions**:

| Challenge | Solution |
|-----------|----------|
| Endpoint provisioning takes 10+ minutes | Polling with status messages |
| Index sync takes 15+ minutes | Separate status check with ready flag |
| JSON arrays in SQL | Store as JSON strings, parse in Python |
| Embedding column must be STRING | Use computed column with CONCAT |

**Interview Answer**:
> "The setup script handles the full lifecycle: table creation, data loading, endpoint provisioning, and index creation. The main challenge is timing - Vector Search resources can take 15-20 minutes to provision. I implemented polling with clear status messages so users know what's happening. The script is idempotent - it can be re-run safely."

---

### Production Considerations

**What's Implemented**:
- Basic similarity search
- Top-k retrieval with configurable limit
- User token pass-through for permissions
- Graceful degradation when not configured

**What I'd Add for Production**:

1. **Relevance threshold**: Filter out low-similarity matches
```python
if similarity_score < 0.7:
    continue  # Too different to be useful
```

2. **Pattern versioning**: Track which pattern version was used
```python
pattern_result["matched_at"] = datetime.now()
pattern_result["index_version"] = index_metadata.version
```

3. **Feedback loop**: Learn from which patterns were helpful
```python
if user_marked_helpful:
    record_pattern_success(pattern_id, question_embedding)
```

4. **Hybrid search**: Combine vector + keyword for precision
```python
# First: semantic search for similar concepts
# Then: keyword filter for specific table names
```

5. **Monitoring**: Track search latency and match quality
```python
galileo.log_tool_call({
    "name": "search_patterns",
    "similarity_scores": [p["score"] for p in patterns],
    "latency_ms": duration
})
```

**Interview Answer**:
> "For production, I'd add relevance thresholds to avoid surfacing poor matches. A feedback loop would help improve the pattern library over time - if users consistently find a pattern unhelpful, we should investigate. Hybrid search combining vectors with keyword filters would improve precision for specific table names or metrics."

---

### Files for Vector Search

| File | Purpose |
|------|---------|
| `config/pattern_library.json` | Source patterns (10 curated issues) |
| `scripts/setup_vector_search.py` | Creates table, endpoint, and index |
| `app.py` (search_patterns function) | MCP tool implementation |
| `.env` (VS_INDEX) | Index name configuration |

---

### Useful Links

- [Databricks Vector Search](https://docs.databricks.com/en/generative-ai/vector-search.html)
- [BGE Embedding Models](https://huggingface.co/BAAI/bge-large-en-v1.5)
- [Delta Sync Indexes](https://docs.databricks.com/en/generative-ai/create-query-vector-search.html#create-a-delta-sync-index)
