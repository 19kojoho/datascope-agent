"""DataScope LangGraph UI - Production HTTP Server.

A production-ready chat interface for investigating data quality issues.
Uses LangGraph for agent orchestration with persistent state.
"""

import http.server
import socketserver
import json
import logging
import signal
import sys
import time
import uuid
import requests
from typing import Optional

from agent.config import get_config, Config
from agent.graph import create_agent, invoke_agent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global config
config: Optional[Config] = None


# ============================================================================
# Lakebase Analytics Functions
# ============================================================================

def save_investigation(
    conversation_id: str,
    question: str,
    response: str,
    duration: float
) -> bool:
    """Save investigation metadata to Lakebase for analytics."""
    if not config or not config.lakebase_enabled:
        return False

    try:
        investigation_id = str(uuid.uuid4())
        table = f"{config.lakebase_catalog}.{config.lakebase_schema}.investigations"

        # Escape quotes for SQL
        question_escaped = question.replace("'", "''")[:500]
        summary_escaped = response.replace("'", "''")[:1000]

        query = f"""
        INSERT INTO {table} (investigation_id, conversation_id, question, status, started_at, completed_at, duration_seconds, summary)
        VALUES ('{investigation_id}', '{conversation_id}', '{question_escaped}', 'completed', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), {duration}, '{summary_escaped}')
        """

        url = f"{config.databricks_host}/api/2.0/sql/statements"
        headers = config.get_auth_headers()

        resp = requests.post(url, headers=headers, json={
            "warehouse_id": config.sql_warehouse_id,
            "statement": query,
            "wait_timeout": "10s"
        }, timeout=15)

        return resp.status_code == 200

    except Exception as e:
        logger.error(f"Failed to save investigation: {e}")
        return False


def get_stats() -> dict:
    """Get investigation statistics from Lakebase."""
    stats = {"lakebase_enabled": config.lakebase_enabled if config else False}

    if not config or not config.lakebase_enabled:
        return stats

    try:
        headers = config.get_auth_headers()
        base_url = f"{config.databricks_host}/api/2.0/sql/statements"

        def run_query(query: str):
            resp = requests.post(base_url, headers=headers, json={
                "warehouse_id": config.sql_warehouse_id,
                "statement": query,
                "wait_timeout": "10s"
            }, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status", {}).get("state") == "SUCCEEDED":
                    return data.get("result", {}).get("data_array", [])
            return None

        table = f"{config.lakebase_catalog}.{config.lakebase_schema}.investigations"

        # Total investigations
        result = run_query(f"SELECT COUNT(*) FROM {table}")
        if result:
            stats["total_investigations"] = result[0][0]

        # Average duration
        result = run_query(f"SELECT AVG(duration_seconds) FROM {table} WHERE duration_seconds IS NOT NULL")
        if result and result[0][0]:
            stats["avg_duration_seconds"] = round(float(result[0][0]), 2)

        # Investigations today
        result = run_query(f"SELECT COUNT(*) FROM {table} WHERE DATE(started_at) = CURRENT_DATE")
        if result:
            stats["investigations_today"] = result[0][0]

    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        stats["error"] = str(e)

    return stats


# ============================================================================
# HTML Template
# ============================================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>DataScope (LangGraph) - Data Debugging Agent</title>
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
        .badge {
            display: inline-block;
            background: #10b981;
            color: white;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75em;
            margin-left: 8px;
        }

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
            <h1>DataScope <span class="badge">LangGraph</span></h1>
            <p class="subtitle">Data Debugging Agent for Databricks</p>
        </header>

        <div class="chat-container">
            <div id="messages">
                <div class="message assistant-msg">
                    <div class="bubble">
                        <strong>Welcome!</strong> I'm DataScope (powered by LangGraph).<br><br>
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
        let currentConversationId = null;

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
            return text
                .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
                .replace(/`([^`]+)`/g, '<code>$1</code>')
                .replace(/```([\\s\\S]*?)```/g, '<pre>$1</pre>')
                .replace(/\\n/g, '<br>');
        }

        async function investigate() {
            const question = questionEl.value.trim();
            if (!question) return;

            addMessage(question, true);
            questionEl.value = '';
            submitBtn.disabled = true;

            const loadingBubble = addMessage('<div class="loading"></div> Investigating...', false);

            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        question: question,
                        conversation_id: currentConversationId
                    })
                });

                const data = await response.json();
                loadingBubble.innerHTML = formatMarkdown(data.response || data.error || 'No response');

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
            currentConversationId = null;
            questionEl.value = btn.textContent;
            investigate();
        }

        submitBtn.onclick = investigate;
        questionEl.onkeypress = (e) => { if (e.key === 'Enter') investigate(); };
    </script>
</body>
</html>
"""


# ============================================================================
# HTTP Handler
# ============================================================================

class DataScopeHandler(http.server.BaseHTTPRequestHandler):
    """Production HTTP handler for DataScope."""

    def log_message(self, format, *args):
        """Override to use Python logging."""
        logger.info(f"{self.address_string()} - {format % args}")

    def send_json(self, data: dict, status: int = 200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def send_html(self, html: str):
        """Send HTML response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/" or self.path == "/index.html":
            self.send_html(HTML_TEMPLATE)

        elif self.path == "/health":
            # Health check with dependency verification
            health = {
                "status": "healthy",
                "service": "datascope-langgraph",
                "version": "1.0.0"
            }

            # Check if agent can be created
            try:
                create_agent()
                health["agent"] = "ok"
            except Exception as e:
                health["agent"] = f"error: {str(e)}"
                health["status"] = "degraded"

            self.send_json(health)

        elif self.path == "/stats":
            stats = get_stats()
            self.send_json(stats)

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        """Handle POST requests."""
        if self.path == "/chat":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}

                question = body.get("question", "").strip()
                conversation_id = body.get("conversation_id") or str(uuid.uuid4())

                if not question:
                    self.send_json({"error": "No question provided"}, 400)
                    return

                logger.info(f"Chat request: conversation={conversation_id}, question={question[:100]}")

                # Invoke the LangGraph agent
                start_time = time.time()
                result = invoke_agent(question, conversation_id)
                duration = time.time() - start_time

                # Save to Lakebase for analytics
                save_investigation(
                    conversation_id,
                    question,
                    result.get("response", ""),
                    duration
                )

                self.send_json({
                    "response": result.get("response", "No response"),
                    "conversation_id": conversation_id,
                    "duration_seconds": round(duration, 2)
                })

            except Exception as e:
                logger.error(f"Chat error: {e}")
                self.send_json({"error": str(e)}, 500)

        else:
            self.send_json({"error": "Not found"}, 404)


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point."""
    global config

    # Load configuration
    try:
        config = get_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # Pre-create agent to fail fast if there are issues
    try:
        logger.info("Initializing DataScope agent...")
        create_agent(config)
        logger.info("Agent initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize agent: {e}")
        sys.exit(1)

    # Setup graceful shutdown
    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received, exiting...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # Start server
    port = config.port
    with socketserver.TCPServer(("", port), DataScopeHandler) as httpd:
        logger.info(f"DataScope LangGraph server starting on port {port}")
        logger.info(f"LLM Endpoint: {config.llm_endpoint}")
        logger.info(f"SQL Warehouse: {config.sql_warehouse_id}")
        logger.info(f"Open http://localhost:{port} in your browser")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server stopped")


if __name__ == "__main__":
    main()
