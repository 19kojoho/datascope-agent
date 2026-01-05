"""DataScope UI - Simple HTML Chat Interface.

A lightweight chat interface for investigating data quality issues.
Uses only Python's built-in libraries + requests.
"""

import http.server
import socketserver
import json
import os
import requests
from urllib.parse import parse_qs

PORT = 8000
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT_NAME", "claude-sonnet-endpoint")
SQL_WAREHOUSE_ID = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "")
GITHUB_MCP_APP_URL = os.environ.get("GITHUB_MCP_APP_URL", "")
VS_ENDPOINT = os.environ.get("VS_ENDPOINT_NAME", "datascope-vs-endpoint")
VS_INDEX = os.environ.get("VS_INDEX_NAME", "novatech.gold.datascope_patterns_index")
LAKEBASE_ENABLED = os.environ.get("LAKEBASE_ENABLED", "true").lower() == "true"
LAKEBASE_CATALOG = os.environ.get("LAKEBASE_CATALOG", "novatech")
LAKEBASE_SCHEMA = os.environ.get("LAKEBASE_SCHEMA", "datascope")

# OAuth token cache - short TTL to pick up permission changes
_oauth_token = None
_oauth_token_expiry = 0
_TOKEN_TTL = 300  # Refresh token every 5 minutes

def get_databricks_host():
    """Get Databricks host URL."""
    host = os.environ.get("DATABRICKS_HOST", "")
    if host:
        return host.rstrip("/")
    # Try SDK
    try:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient()
        return client.config.host.rstrip("/")
    except Exception:
        return ""

def get_oauth_token():
    """Get OAuth token using service principal credentials (M2M flow)."""
    global _oauth_token, _oauth_token_expiry
    import time

    # Return cached token if still valid
    if _oauth_token and time.time() < _oauth_token_expiry:
        return _oauth_token

    # Get client credentials from environment (Databricks Apps injects these)
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        return None

    # Get token endpoint
    host = get_databricks_host()
    token_url = f"{host}/oidc/v1/token"

    try:
        resp = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "scope": "all-apis"
            },
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        if resp.status_code == 200:
            data = resp.json()
            _oauth_token = data.get("access_token")
            # Use shorter TTL to pick up permission changes faster
            _oauth_token_expiry = time.time() + min(_TOKEN_TTL, data.get("expires_in", 3600))
            return _oauth_token
    except Exception:
        pass

    return None

def get_auth_headers():
    """Get authorization headers."""
    # Try PAT token first (more reliable for external model endpoints)
    token = os.environ.get("DATABRICKS_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Fallback: Try OAuth token (Databricks Apps service principal)
    token = get_oauth_token()
    if token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Fallback: Try SDK
    try:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient()
        token = client.config.token
        if token:
            return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    except Exception:
        pass

    return {"Content-Type": "application/json"}

DATABRICKS_HOST = get_databricks_host()


# ============================================================================
# Lakebase Functions for Conversation State
# ============================================================================

import uuid
from datetime import datetime

def generate_id():
    """Generate a unique ID."""
    return str(uuid.uuid4())


def save_conversation(conversation_id: str, title: str, user_id: str = "anonymous") -> bool:
    """Save a new conversation to Lakebase."""
    if not LAKEBASE_ENABLED:
        return False
    try:
        table = f"{LAKEBASE_CATALOG}.{LAKEBASE_SCHEMA}.conversations"
        query = f"""
        INSERT INTO {table} (conversation_id, user_id, title, created_at, updated_at, status)
        VALUES ('{conversation_id}', '{user_id}', '{title.replace("'", "''")}', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), 'active')
        """
        execute_sql_internal(query)
        return True
    except Exception as e:
        print(f"Error saving conversation: {e}")
        return False


def save_message(conversation_id: str, role: str, content: str, tool_calls: str = None, tool_call_id: str = None) -> str:
    """Save a message to Lakebase."""
    if not LAKEBASE_ENABLED:
        return None
    try:
        message_id = generate_id()
        table = f"{LAKEBASE_CATALOG}.{LAKEBASE_SCHEMA}.messages"
        content_escaped = content.replace("'", "''") if content else ""
        tool_calls_escaped = tool_calls.replace("'", "''") if tool_calls else "NULL"
        tool_call_id_val = f"'{tool_call_id}'" if tool_call_id else "NULL"
        tool_calls_val = f"'{tool_calls_escaped}'" if tool_calls else "NULL"

        query = f"""
        INSERT INTO {table} (message_id, conversation_id, role, content, tool_calls, tool_call_id, created_at)
        VALUES ('{message_id}', '{conversation_id}', '{role}', '{content_escaped}', {tool_calls_val}, {tool_call_id_val}, CURRENT_TIMESTAMP())
        """
        execute_sql_internal(query)
        return message_id
    except Exception as e:
        print(f"Error saving message: {e}")
        return None


def get_conversation_summary(conversation_id: str) -> str:
    """Get a summary of previous conversation turns for context.

    Returns a text summary of previous Q&A pairs that can be injected
    into the system prompt without causing tool_call/tool_result issues.
    """
    if not LAKEBASE_ENABLED:
        return ""
    try:
        table = f"{LAKEBASE_CATALOG}.{LAKEBASE_SCHEMA}.messages"
        # Get previous user questions and assistant answers (skip the current one)
        query = f"""
        SELECT role, content
        FROM {table}
        WHERE conversation_id = '{conversation_id}'
          AND role IN ('user', 'assistant')
          AND content IS NOT NULL
          AND LENGTH(content) > 10
        ORDER BY created_at ASC
        """
        result = execute_sql_internal(query, return_data=True)
        if not result or len(result) < 2:
            return ""

        # Build summary of previous exchanges (exclude the last user message - that's the current one)
        summary_parts = []
        exchanges = []

        # Pair up user/assistant messages
        i = 0
        while i < len(result) - 1:  # -1 to skip current question
            if result[i][0] == 'user' and i + 1 < len(result) and result[i + 1][0] == 'assistant':
                user_q = result[i][1][:200]  # Truncate for brevity
                asst_a = result[i + 1][1][:500]  # Keep more of the answer
                exchanges.append((user_q, asst_a))
                i += 2
            else:
                i += 1

        if not exchanges:
            return ""

        # Format as context summary (only include last 2 exchanges to save tokens)
        for q, a in exchanges[-2:]:
            summary_parts.append(f"User asked: \"{q}\"")
            summary_parts.append(f"You found: \"{a[:400]}{'...' if len(a) > 400 else ''}\"")
            summary_parts.append("")

        return "## Previous Context\n\n" + "\n".join(summary_parts)
    except Exception as e:
        print(f"Error getting conversation summary: {e}")
        return ""


def save_investigation(conversation_id: str, question: str, tools_used: list, summary: str, duration: float) -> str:
    """Save investigation metadata to Lakebase."""
    if not LAKEBASE_ENABLED:
        return None
    try:
        investigation_id = generate_id()
        table = f"{LAKEBASE_CATALOG}.{LAKEBASE_SCHEMA}.investigations"
        tools_json = json.dumps(tools_used).replace("'", "''")
        summary_escaped = summary.replace("'", "''") if summary else ""

        query = f"""
        INSERT INTO {table} (investigation_id, conversation_id, question, status, started_at, completed_at, duration_seconds, tools_used, summary)
        VALUES ('{investigation_id}', '{conversation_id}', '{question.replace("'", "''")}', 'completed', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), {duration}, '{tools_json}', '{summary_escaped[:1000]}')
        """
        execute_sql_internal(query)
        return investigation_id
    except Exception as e:
        print(f"Error saving investigation: {e}")
        return None


def execute_sql_internal(query: str, return_data: bool = False):
    """Execute SQL via Databricks - internal version without formatting."""
    try:
        url = f"{DATABRICKS_HOST}/api/2.0/sql/statements"
        headers = get_auth_headers()

        resp = requests.post(url, headers=headers, json={
            "warehouse_id": SQL_WAREHOUSE_ID,
            "statement": query,
            "wait_timeout": "30s"
        })

        if resp.status_code != 200:
            return None

        data = resp.json()
        if data.get("status", {}).get("state") == "SUCCEEDED":
            if return_data:
                return data.get("result", {}).get("data_array", [])
            return True
        return None
    except Exception:
        return None


# In-memory conversation store (fallback when Lakebase is unavailable)
_conversations = {}


# System prompt
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

Explain things like you're talking to a smart colleague who doesn't know SQL.
"""

# HTML template
HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>DataScope - Data Debugging Agent</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f7fa;
            min-height: 100vh;
        }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        header {
            text-align: center;
            padding: 30px 0 20px;
            border-bottom: 1px solid #e0e0e0;
            margin-bottom: 20px;
        }
        h1 { color: #1a1a1a; font-size: 2em; margin-bottom: 8px; }
        .subtitle { color: #666; font-size: 1.1em; }

        .chat-container {
            background: white;
            border-radius: 12px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.08);
            overflow: hidden;
        }

        #messages {
            height: 500px;
            overflow-y: auto;
            padding: 20px;
            background: #fafafa;
        }

        .message {
            margin-bottom: 16px;
            animation: fadeIn 0.3s ease;
        }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

        .user-msg {
            text-align: right;
        }
        .user-msg .bubble {
            background: #0066cc;
            color: white;
            display: inline-block;
            padding: 12px 18px;
            border-radius: 18px 18px 4px 18px;
            max-width: 80%;
            text-align: left;
        }

        .assistant-msg .bubble {
            background: white;
            border: 1px solid #e0e0e0;
            display: inline-block;
            padding: 16px 20px;
            border-radius: 18px 18px 18px 4px;
            max-width: 90%;
            line-height: 1.6;
        }
        .assistant-msg .bubble strong { color: #0066cc; }
        .assistant-msg .bubble code {
            background: #f0f0f0;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: 'SF Mono', Consolas, monospace;
            font-size: 0.9em;
        }
        .assistant-msg .bubble pre {
            background: #1e1e1e;
            color: #d4d4d4;
            padding: 12px;
            border-radius: 8px;
            overflow-x: auto;
            margin: 10px 0;
            font-family: 'SF Mono', Consolas, monospace;
            font-size: 0.85em;
        }

        .input-area {
            display: flex;
            padding: 16px;
            background: white;
            border-top: 1px solid #e8e8e8;
        }
        #question {
            flex: 1;
            padding: 14px 18px;
            border: 2px solid #e0e0e0;
            border-radius: 24px;
            font-size: 16px;
            outline: none;
            transition: border-color 0.2s;
        }
        #question:focus { border-color: #0066cc; }
        #submit {
            margin-left: 12px;
            padding: 14px 28px;
            background: #0066cc;
            color: white;
            border: none;
            border-radius: 24px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }
        #submit:hover { background: #0052a3; }
        #submit:disabled { background: #ccc; cursor: not-allowed; }

        .examples {
            margin-top: 20px;
            text-align: center;
        }
        .examples span { color: #888; font-size: 0.9em; }
        .example-btn {
            display: inline-block;
            margin: 8px 4px;
            padding: 8px 16px;
            background: #e8f0fe;
            color: #0066cc;
            border: none;
            border-radius: 20px;
            cursor: pointer;
            font-size: 0.9em;
            transition: background 0.2s;
        }
        .example-btn:hover { background: #d0e3fc; }

        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #e0e0e0;
            border-top-color: #0066cc;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>DataScope</h1>
            <p class="subtitle">Data Debugging Agent for Databricks</p>
        </header>

        <div class="chat-container">
            <div id="messages">
                <div class="message assistant-msg">
                    <div class="bubble">
                        <strong>Welcome!</strong> I'm DataScope, your data debugging assistant.<br><br>
                        Ask me questions about data quality issues in plain English. For example:<br>
                        - "Why do some customers have NULL churn_risk?"<br>
                        - "Why does ARR seem lower than expected?"<br><br>
                        I'll investigate and explain what I find.
                    </div>
                </div>
            </div>

            <div class="input-area">
                <input type="text" id="question" placeholder="Ask about a data quality issue..." autocomplete="off">
                <button id="submit">Investigate</button>
            </div>
        </div>

        <div class="examples">
            <span>Try these:</span><br>
            <button class="example-btn" onclick="askExample(this)">Why do some customers have NULL churn_risk?</button>
            <button class="example-btn" onclick="askExample(this)">Why does ARR show less than expected?</button>
            <button class="example-btn" onclick="askExample(this)">How many customers are high churn risk?</button>
        </div>
    </div>

    <script>
        const messagesEl = document.getElementById('messages');
        const questionEl = document.getElementById('question');
        const submitBtn = document.getElementById('submit');
        let currentConversationId = null;  // Track conversation for follow-ups

        function addMessage(content, isUser) {
            const div = document.createElement('div');
            div.className = 'message ' + (isUser ? 'user-msg' : 'assistant-msg');
            const bubble = document.createElement('div');
            bubble.className = 'bubble';
            bubble.innerHTML = isUser ? content : formatMarkdown(content);
            div.appendChild(bubble);
            messagesEl.appendChild(div);
            messagesEl.scrollTop = messagesEl.scrollHeight;
            return bubble;
        }

        function formatMarkdown(text) {
            // Basic markdown formatting
            return text
                .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
                .replace(/`([^`]+)`/g, '<code>$1</code>')
                .replace(/```([\\s\\S]*?)```/g, '<pre>$1</pre>')
                .replace(/\\n/g, '<br>');
        }

        async function investigate() {
            const question = questionEl.value.trim();
            if (!question) return;

            // Add user message
            addMessage(question, true);
            questionEl.value = '';
            submitBtn.disabled = true;

            // Add loading indicator
            const loadingBubble = addMessage('<div class="loading"></div> Investigating...', false);

            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        question: question,
                        conversation_id: currentConversationId  // Include for follow-ups
                    })
                });

                const data = await response.json();
                loadingBubble.innerHTML = formatMarkdown(data.response || data.error || 'No response');

                // Store conversation ID for follow-up questions
                if (data.conversation_id) {
                    currentConversationId = data.conversation_id;
                }
            } catch (e) {
                loadingBubble.innerHTML = '<strong>Error:</strong> ' + e.message;
            }

            submitBtn.disabled = false;
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }

        function askExample(btn) {
            // Start a new conversation for example questions
            currentConversationId = null;
            questionEl.value = btn.textContent;
            investigate();
        }

        function newConversation() {
            currentConversationId = null;
            messagesEl.innerHTML = `
                <div class="message assistant-msg">
                    <div class="bubble">
                        <strong>Welcome!</strong> I'm DataScope, your data debugging assistant.<br><br>
                        Ask me questions about data quality issues in plain English. For example:<br>
                        - "Why do some customers have NULL churn_risk?"<br>
                        - "Why does ARR seem lower than expected?"<br><br>
                        I'll investigate and explain what I find.
                    </div>
                </div>
            `;
        }

        submitBtn.onclick = investigate;
        questionEl.onkeypress = (e) => { if (e.key === 'Enter') investigate(); };
    </script>
</body>
</html>
"""


def execute_sql(query: str) -> str:
    """Execute SQL via Databricks Statement Execution API."""
    try:
        url = f"{DATABRICKS_HOST}/api/2.0/sql/statements"
        headers = get_auth_headers()

        resp = requests.post(url, headers=headers, json={
            "warehouse_id": SQL_WAREHOUSE_ID,
            "statement": query,
            "wait_timeout": "30s"
        })

        if resp.status_code != 200:
            return f"SQL Error: {resp.text[:200]}"

        data = resp.json()
        if data.get("status", {}).get("state") == "SUCCEEDED":
            result = data.get("result", {})
            if result.get("data_array"):
                columns = [c["name"] for c in data.get("manifest", {}).get("schema", {}).get("columns", [])]
                rows = result["data_array"][:15]

                # Format as markdown table
                header = "| " + " | ".join(columns) + " |"
                sep = "| " + " | ".join(["---"] * len(columns)) + " |"
                body = "\n".join("| " + " | ".join(str(v) if v else "NULL" for v in row) + " |" for row in rows)
                return f"```\n{header}\n{sep}\n{body}\n```"
            return "Query returned no results."
        else:
            error = data.get("status", {}).get("error", {}).get("message", "Unknown error")
            return f"Query error: {error}"
    except Exception as e:
        return f"Error: {str(e)}"


def search_code(term: str) -> str:
    """Search code via GitHub MCP app."""
    if not GITHUB_MCP_APP_URL:
        return "Code search not configured."
    try:
        resp = requests.post(
            f"{GITHUB_MCP_APP_URL.rstrip('/')}/search",
            json={"query": term, "file_extension": "sql"},
            headers=get_auth_headers(),
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("results"):
                out = []
                for r in data["results"][:2]:
                    out.append(f"**File: {r['file']}**")
                    for m in r.get("matches", [])[:1]:
                        out.append(f"```sql\n{m.get('context', '')}\n```")
                return "\n".join(out)
            return f"No code found for '{term}'."
        return f"Code search error: {resp.status_code}"
    except Exception as e:
        return f"Code search error: {str(e)}"


def search_patterns(query: str) -> str:
    """Search for similar data quality patterns using Vector Search."""
    try:
        url = f"{DATABRICKS_HOST}/api/2.0/vector-search/indexes/{VS_INDEX}/query"
        headers = get_auth_headers()

        resp = requests.post(url, headers=headers, json={
            "query_text": query,
            "columns": ["pattern_id", "title", "symptoms", "root_cause", "resolution", "investigation_sql"],
            "num_results": 3
        })

        if resp.status_code != 200:
            return f"Vector search unavailable: {resp.status_code}"

        data = resp.json()
        results = data.get("result", {}).get("data_array", [])

        if not results:
            return "No similar patterns found."

        out = ["**Similar Past Issues Found:**\n"]
        for row in results:
            pattern_id, title, symptoms, root_cause, resolution, investigation_sql = row[:6]
            out.append(f"### {pattern_id}: {title}")
            out.append(f"**Symptoms:** {symptoms[:200]}...")
            out.append(f"**Root Cause:** {root_cause}")
            out.append(f"**Resolution:** {resolution}")
            if investigation_sql:
                out.append(f"**Suggested SQL:** `{investigation_sql[:100]}...`")
            out.append("")

        return "\n".join(out)

    except Exception as e:
        return f"Pattern search error: {str(e)}"


def chat_with_llm(question: str, conversation_id: str = None) -> tuple:
    """Send question to LLM and handle tool calls.

    Args:
        question: The user's question
        conversation_id: Optional conversation ID for multi-turn context

    Returns:
        Tuple of (response_text, conversation_id)
    """
    import time
    start_time = time.time()

    # Create or load conversation
    if not conversation_id:
        conversation_id = generate_id()
        save_conversation(conversation_id, question[:100])

    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_patterns",
                "description": "Search for similar past data quality issues. Use this FIRST to get context on common patterns before investigating.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Description of the data issue to find similar patterns for"}},
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "execute_sql",
                "description": "Execute SQL query to investigate data issues. Use this to count records, check for NULLs, compare values, etc.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "SQL query to execute"}},
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": "Search SQL transformation code to find the source of bugs",
                "parameters": {
                    "type": "object",
                    "properties": {"term": {"type": "string", "description": "Search term to look for in SQL files"}},
                    "required": ["term"]
                }
            }
        }
    ]

    # Build messages with context from previous turns
    # We inject a summary of previous Q&A into the system prompt
    # This avoids tool_use/tool_result pairing issues with Anthropic's API
    context_summary = get_conversation_summary(conversation_id)

    if context_summary:
        system_content = SYSTEM_PROMPT + "\n\n" + context_summary + "\n\nNow answer the user's follow-up question using the context above."
    else:
        system_content = SYSTEM_PROMPT

    messages = [{"role": "system", "content": system_content}]

    # Add current question
    messages.append({"role": "user", "content": question})

    # Save user message to Lakebase
    save_message(conversation_id, "user", question)

    url = f"{DATABRICKS_HOST}/serving-endpoints/{LLM_ENDPOINT}/invocations"
    headers = get_auth_headers()
    tool_results_collected = []

    try:
        # Phase 1: Investigation with tools (max 5 iterations)
        for iteration in range(5):
            resp = requests.post(url, headers=headers, json={
                "messages": messages,
                "tools": tools,
                "max_tokens": 4096,
                "temperature": 0
            })

            if resp.status_code != 200:
                return (f"LLM Error: {resp.text[:300]}", conversation_id)

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            # If LLM returns content without tool calls, it's done investigating
            if not tool_calls:
                if content:
                    duration = time.time() - start_time
                    save_message(conversation_id, "assistant", content)
                    save_investigation(conversation_id, question, tool_results_collected, content, duration)
                    return (content, conversation_id)
                # No content and no tool calls - ask for summary
                break

            # If we have substantial content that looks like a final answer, return it
            if content and len(content) > 300:
                keywords = ["**What I Found**", "**The Problem**", "Root Cause", "How to Fix"]
                if any(kw in content for kw in keywords):
                    duration = time.time() - start_time
                    save_message(conversation_id, "assistant", content)
                    save_investigation(conversation_id, question, tool_results_collected, content, duration)
                    return (content, conversation_id)

            # Execute tool calls
            # Build assistant message - only include content if non-empty
            # For tool calls without content, omit the content field entirely
            assistant_msg = {"role": "assistant", "tool_calls": tool_calls}
            if content and content.strip():
                assistant_msg["content"] = content
            else:
                # Anthropic requires content to be non-empty if present
                # So we omit it entirely when there's no content
                pass
            messages.append(assistant_msg)

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except:
                    args = {}

                if name == "search_patterns":
                    result = search_patterns(args.get("query", ""))
                    tool_results_collected.append(f"Pattern search: {args.get('query', '')[:50]}...")
                elif name == "execute_sql":
                    result = execute_sql(args.get("query", ""))
                    tool_results_collected.append(f"SQL: {args.get('query', '')[:100]}...")
                elif name == "search_code":
                    result = search_code(args.get("term", ""))
                    tool_results_collected.append(f"Code search: {args.get('term', '')}")
                else:
                    result = f"Unknown tool: {name}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": result
                })

        # Phase 2: Force summary generation (no tools)
        summary_prompt = """Based on your investigation above, provide your final answer to the user's question.

You MUST respond with a clear explanation, NOT with more tool calls.

Format your response as:
**What I Found:** [One sentence summary of the issue]

**The Problem:** [Explain what's wrong in simple terms]

**Why It Happened:** [Root cause - what in the data/code caused this]

**How Many Records:** [Quantify the impact - X records affected, Y% of total, etc.]

**How to Fix It:** [Specific actionable recommendation]

Write this response NOW."""

        messages.append({"role": "user", "content": summary_prompt})

        # Make request WITHOUT tools to force text response
        resp = requests.post(url, headers=headers, json={
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0
        })

        if resp.status_code != 200:
            return (f"Error generating summary: {resp.text[:200]}", conversation_id)

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content", "")

        if content:
            duration = time.time() - start_time
            save_message(conversation_id, "assistant", content)
            save_investigation(conversation_id, question, tool_results_collected, content, duration)
            return (content, conversation_id)

        # Last resort: if still no content, construct a minimal response
        if tool_results_collected:
            fallback = f"**Investigation completed** but the model didn't generate a summary.\n\nTools used during investigation:\n" + "\n".join(f"- {r}" for r in tool_results_collected[:5]) + "\n\nPlease try rephrasing your question."
            save_message(conversation_id, "assistant", fallback)
            return (fallback, conversation_id)

        return ("I wasn't able to complete the investigation. Please try a different question.", conversation_id)

    except Exception as e:
        return (f"Error: {str(e)}", conversation_id)


class Handler(http.server.BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def send_html(self, html):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_html(HTML_TEMPLATE)
        elif self.path == "/health":
            self.send_json({"status": "healthy", "service": "datascope-ui"})
        elif self.path == "/debug":
            # Debug endpoint to check auth
            client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
            client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
            pat_token = os.environ.get("DATABRICKS_TOKEN", "")
            oauth_token = get_oauth_token()

            self.send_json({
                "databricks_host": DATABRICKS_HOST,
                "llm_endpoint": LLM_ENDPOINT,
                "sql_warehouse_id": SQL_WAREHOUSE_ID,
                "has_pat_token": bool(pat_token),
                "pat_token_preview": pat_token[:10] + "..." if pat_token else None,
                "has_client_id": bool(client_id),
                "has_client_secret": bool(client_secret),
                "oauth_token_obtained": bool(oauth_token),
            })
        elif self.path == "/test":
            # Test a simple LLM call
            url = f"{DATABRICKS_HOST}/serving-endpoints/{LLM_ENDPOINT}/invocations"
            headers = get_auth_headers()

            resp = requests.post(url, headers=headers, json={
                "messages": [
                    {"role": "user", "content": "Say hello in one sentence."}
                ],
                "max_tokens": 100,
                "temperature": 0
            })

            self.send_json({
                "status_code": resp.status_code,
                "response": resp.json() if resp.status_code == 200 else resp.text[:500]
            })
        elif self.path == "/stats":
            # Get investigation statistics from Lakebase
            stats = {"lakebase_enabled": LAKEBASE_ENABLED}

            if LAKEBASE_ENABLED:
                try:
                    # Total investigations
                    result = execute_sql_internal(
                        f"SELECT COUNT(*) FROM {LAKEBASE_CATALOG}.{LAKEBASE_SCHEMA}.investigations",
                        return_data=True
                    )
                    stats["total_investigations"] = result[0][0] if result else 0

                    # Average duration
                    result = execute_sql_internal(
                        f"SELECT AVG(duration_seconds) FROM {LAKEBASE_CATALOG}.{LAKEBASE_SCHEMA}.investigations WHERE duration_seconds IS NOT NULL",
                        return_data=True
                    )
                    stats["avg_duration_seconds"] = round(float(result[0][0]), 2) if result and result[0][0] else 0

                    # Investigations today
                    result = execute_sql_internal(
                        f"SELECT COUNT(*) FROM {LAKEBASE_CATALOG}.{LAKEBASE_SCHEMA}.investigations WHERE DATE(started_at) = CURRENT_DATE",
                        return_data=True
                    )
                    stats["investigations_today"] = result[0][0] if result else 0

                    # Total conversations
                    result = execute_sql_internal(
                        f"SELECT COUNT(*) FROM {LAKEBASE_CATALOG}.{LAKEBASE_SCHEMA}.conversations",
                        return_data=True
                    )
                    stats["total_conversations"] = result[0][0] if result else 0

                except Exception as e:
                    stats["error"] = str(e)

            self.send_json(stats)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if self.path == "/chat":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            question = body.get("question", "")
            conversation_id = body.get("conversation_id")  # Optional for follow-ups

            if not question:
                self.send_json({"error": "No question provided"})
                return

            response, conv_id = chat_with_llm(question, conversation_id)
            self.send_json({
                "response": response,
                "conversation_id": conv_id  # Return for follow-up questions
            })
        else:
            self.send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    print(f"Starting DataScope UI on port {PORT}...")
    print(f"LLM Endpoint: {LLM_ENDPOINT}")
    print(f"SQL Warehouse: {SQL_WAREHOUSE_ID}")

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving at http://localhost:{PORT}")
        httpd.serve_forever()
