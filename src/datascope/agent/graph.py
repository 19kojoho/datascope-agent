"""LangGraph workflow for the DataScope data debugging agent.

Architecture:
- LLM: Claude via Databricks External Endpoint (AI Gateway)
- Tools: Databricks Managed MCPs (SQL, UC, Vector Search) + Custom GitHub MCP
- State: Lakebase PostgresCheckpointer for multi-turn conversations
- Deployment: MLflow model on Databricks Model Serving
- Tracing: MLflow for observability

Reference: docs/ARCHITECTURE_DECISIONS.md
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal, Optional, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

# MLflow tracing for observability
import mlflow
mlflow.langchain.autolog()

from datascope.agent.prompts import (
    ANALYSIS_PROMPT,
    CLASSIFICATION_PROMPT,
    SYSTEM_PROMPT,
)
from datascope.agent.state import AgentState, create_initial_state


# =============================================================================
# Configuration
# =============================================================================

class DataScopeConfig(BaseModel):
    """Configuration for the DataScope agent."""

    # Databricks workspace
    databricks_host: str
    databricks_token: str

    # LLM endpoint (External Endpoint for Claude)
    llm_endpoint_name: str = "databricks-claude-sonnet"

    # SQL Warehouse for direct queries (fallback)
    sql_warehouse_id: Optional[str] = None

    # Lakebase for state management
    lakebase_connection_string: Optional[str] = None

    # GitHub MCP App URL (custom MCP on Databricks Apps)
    github_mcp_url: Optional[str] = None

    # Catalog configuration
    catalog: str = "novatech"
    schema_bronze: str = "bronze"
    schema_silver: str = "silver"
    schema_gold: str = "gold"

    @classmethod
    def from_env(cls) -> "DataScopeConfig":
        """Load configuration from environment variables."""
        host = os.environ.get("DATABRICKS_HOST", "")
        if not host:
            raise ValueError("DATABRICKS_HOST environment variable required")

        return cls(
            databricks_host=host.rstrip("/"),
            databricks_token=os.environ.get("DATABRICKS_TOKEN", ""),
            llm_endpoint_name=os.environ.get("LLM_ENDPOINT_NAME", "databricks-claude-sonnet"),
            sql_warehouse_id=os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID"),
            lakebase_connection_string=os.environ.get("LAKEBASE_CONNECTION_STRING"),
            github_mcp_url=os.environ.get("GITHUB_MCP_APP_URL"),
            catalog=os.environ.get("DATASCOPE_CATALOG", "novatech"),
        )


# =============================================================================
# LLM Setup - Databricks External Endpoint
# =============================================================================

def get_llm(config: Optional[DataScopeConfig] = None):
    """
    Get LLM via Databricks External Endpoint.

    Uses OpenAI-compatible API to call Claude through Databricks Model Serving.
    This provides:
    - Centralized credential management
    - AI Gateway features (rate limiting, monitoring)
    - Unified audit trail in system tables
    """
    if config is None:
        config = DataScopeConfig.from_env()

    # Note: Databricks endpoints use 'max_tokens' not 'max_completion_tokens'
    # so we need to set it via extra_body
    return ChatOpenAI(
        model=config.llm_endpoint_name,
        base_url=f"{config.databricks_host}/serving-endpoints",
        api_key=config.databricks_token,
        temperature=0,
        extra_body={"max_tokens": 4096},
    )


# =============================================================================
# MCP Tool Integration
# =============================================================================

def get_mcp_tools(config: Optional[DataScopeConfig] = None) -> list:
    """
    Get tools from Databricks Managed MCP servers.

    Connects to:
    - SQL MCP: Execute queries against SQL Warehouse
    - Unity Catalog MCP: Get schemas, lineage, functions
    - Vector Search MCP: Pattern matching (optional)
    - GitHub MCP: Code search (custom app)
    """
    if config is None:
        config = DataScopeConfig.from_env()

    tools = []

    try:
        from databricks_mcp import DatabricksMCPClient
        from databricks.sdk import WorkspaceClient

        # Create workspace client
        workspace_client = WorkspaceClient(
            host=config.databricks_host,
            token=config.databricks_token,
        )
        host = config.databricks_host

        # SQL MCP - Execute queries
        try:
            sql_mcp = DatabricksMCPClient(
                server_url=f"{host}/api/2.0/mcp/sql",
                workspace_client=workspace_client,
            )
            tools.extend(sql_mcp.list_tools())
        except Exception as e:
            print(f"Warning: Could not connect to SQL MCP: {e}")

        # Unity Catalog MCP - Schemas and functions
        try:
            uc_mcp = DatabricksMCPClient(
                server_url=f"{host}/api/2.0/mcp/functions/{config.catalog}/{config.schema_gold}",
                workspace_client=workspace_client,
            )
            tools.extend(uc_mcp.list_tools())
        except Exception as e:
            print(f"Warning: Could not connect to UC MCP: {e}")

        # GitHub MCP (Custom App) - Code search
        if config.github_mcp_url:
            try:
                github_mcp = DatabricksMCPClient(
                    server_url=f"{config.github_mcp_url.rstrip('/')}/mcp",
                    workspace_client=workspace_client,
                )
                tools.extend(github_mcp.list_tools())
            except Exception as e:
                print(f"Warning: Could not connect to GitHub MCP: {e}")

    except ImportError:
        print("Warning: databricks_mcp not installed. Using fallback tools.")
        tools = create_fallback_tools(config)

    # If no MCP tools available, use fallback
    if not tools:
        print("No MCP tools available. Using fallback tools.")
        tools = create_fallback_tools(config)

    return tools


def create_fallback_tools(config: Optional[DataScopeConfig] = None):
    """
    Create fallback tools when MCP is not available.

    These use the Databricks SDK directly. Useful for:
    - Local development without MCP setup
    - Testing without full Databricks connection
    """
    if config is None:
        config = DataScopeConfig.from_env()

    # Import our custom tool implementations
    from datascope.tools.sql_tool import SQLTool
    from datascope.tools.schema_tool import SchemaTool
    from datascope.tools.lineage_tool import LineageTool

    sql_tool = SQLTool()
    schema_tool = SchemaTool()
    lineage_tool = LineageTool()

    @tool
    def execute_sql(query: str) -> str:
        """
        Execute a SQL query against Databricks SQL Warehouse.

        Use this to:
        - Count records: SELECT COUNT(*) FROM table WHERE condition
        - Sample data: SELECT * FROM table WHERE condition LIMIT 10
        - Compare values between tables
        - Check for NULL values

        Args:
            query: The SQL query to execute

        Returns:
            Markdown-formatted results or error message
        """
        result = sql_tool.execute(query)
        return result.to_markdown()

    @tool
    def get_table_schema(table_name: str) -> str:
        """
        Get the schema (columns and types) of a table from Unity Catalog.

        Args:
            table_name: Fully qualified table name (catalog.schema.table)
                       Example: novatech.gold.churn_predictions

        Returns:
            Markdown-formatted table schema
        """
        try:
            info = schema_tool.get_table_info(table_name)
            return info.to_markdown()
        except Exception as e:
            return f"**Error:** {e}"

    @tool
    def list_tables(catalog: str, schema_name: str) -> str:
        """
        List all tables in a schema.

        Args:
            catalog: Catalog name (e.g., 'novatech')
            schema_name: Schema name (e.g., 'gold', 'silver', 'bronze')

        Returns:
            Markdown-formatted list of table names
        """
        try:
            schema_list = schema_tool.list_tables(catalog, schema_name)
            return schema_list.to_markdown()
        except Exception as e:
            return f"**Error:** {e}"

    @tool
    def get_table_lineage(table_name: str) -> str:
        """
        Get upstream and downstream tables for a table.

        Args:
            table_name: Fully qualified table name (catalog.schema.table)

        Returns:
            Markdown-formatted lineage information
        """
        try:
            lineage = lineage_tool.get_table_lineage(table_name)
            return lineage.to_markdown()
        except Exception as e:
            return f"**Error:** {e}"

    @tool
    def get_column_lineage(table_name: str, column_name: str) -> str:
        """
        Get lineage for a specific column.

        Args:
            table_name: Fully qualified table name (catalog.schema.table)
            column_name: Name of the column to trace

        Returns:
            Markdown-formatted column lineage
        """
        try:
            lineage = lineage_tool.get_column_lineage(table_name, column_name)
            return lineage.to_markdown()
        except Exception as e:
            return f"**Error:** {e}"

    @tool
    def search_transformation_code(search_term: str) -> str:
        """
        Search for transformation code containing a specific term.

        Uses the deployed GitHub Code Search App to search the novatech-transformations
        repository. Falls back to local file search if the app is not configured.

        Args:
            search_term: Term to search for (e.g., 'churn_risk', 'CASE WHEN')

        Returns:
            Matching code snippets with file paths and line numbers
        """
        import glob
        import requests

        # Try GitHub Code Search App first
        github_app_url = config.github_mcp_url if config else os.environ.get("GITHUB_MCP_APP_URL")

        if github_app_url:
            try:
                from databricks.sdk import WorkspaceClient

                # Get OAuth token from Databricks SDK
                db_host = config.databricks_host if config else os.environ.get("DATABRICKS_HOST", "")
                db_token = config.databricks_token if config else os.environ.get("DATABRICKS_TOKEN", "")

                ws_client = WorkspaceClient(host=db_host, token=db_token)
                # Use the SDK to get an app-scoped token
                oauth_token = ws_client.config.token

                search_url = f"{github_app_url.rstrip('/')}/search"
                headers = {
                    "Authorization": f"Bearer {oauth_token}",
                    "Content-Type": "application/json"
                }
                response = requests.post(
                    search_url,
                    json={"query": search_term, "file_extension": "sql"},
                    headers=headers,
                    timeout=30
                )

                if response.status_code == 200:
                    data = response.json()

                    if "error" in data:
                        # Fall through to local search
                        pass
                    elif data.get("results"):
                        output = [f"## Code Search Results for '{search_term}' (from GitHub)\n"]
                        for result in data["results"][:5]:
                            output.append(f"### File: `{result['file']}`\n")
                            for match in result.get("matches", []):
                                output.append(f"**Line {match.get('line', 'N/A')}:**")
                                output.append(f"```sql\n{match.get('context', '')}\n```\n")
                        return "\n".join(output)
                    else:
                        return f"No matches found for '{search_term}' in GitHub repository."
            except Exception:
                # Fall through to local search
                pass

        # Fallback: search local files
        results = []
        sql_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "sql")
        sql_dir = os.path.normpath(sql_dir)

        if not os.path.exists(sql_dir):
            return f"SQL directory not found and GitHub app not available. Configure GITHUB_MCP_APP_URL for code search."

        for sql_file in glob.glob(os.path.join(sql_dir, "**", "*.sql"), recursive=True):
            try:
                with open(sql_file, "r") as f:
                    content = f.read()

                if search_term.lower() in content.lower():
                    lines = content.split("\n")
                    matching_lines = []
                    for i, line in enumerate(lines, 1):
                        if search_term.lower() in line.lower():
                            start = max(0, i - 3)
                            end = min(len(lines), i + 2)
                            context = lines[start:end]
                            matching_lines.append({
                                "line_number": i,
                                "context": "\n".join(context)
                            })

                    rel_path = os.path.relpath(sql_file, sql_dir)
                    results.append({"file": rel_path, "matches": matching_lines[:3]})
            except Exception:
                continue

        if not results:
            return f"No matches found for '{search_term}'."

        output = [f"## Code Search Results for '{search_term}'\n"]
        for result in results[:5]:
            output.append(f"### File: `{result['file']}`\n")
            for match in result["matches"]:
                output.append(f"**Line {match['line_number']}:**")
                output.append(f"```sql\n{match['context']}\n```\n")

        return "\n".join(output)

    return [
        execute_sql,
        get_table_schema,
        list_tables,
        get_table_lineage,
        get_column_lineage,
        search_transformation_code,
    ]


# =============================================================================
# Node Functions
# =============================================================================

def classify_question(state: AgentState) -> dict:
    """Classify the user's question to determine investigation strategy."""
    llm = get_llm()

    prompt = CLASSIFICATION_PROMPT.format(question=state["original_question"])

    response = llm.invoke([
        SystemMessage(content="You are a data quality expert. Classify the question and extract relevant entities."),
        HumanMessage(content=prompt),
    ])

    # Parse JSON response
    try:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        classification = json.loads(content.strip())
    except (json.JSONDecodeError, IndexError):
        classification = {
            "category": "DATA_QUALITY",
            "likely_tables": ["novatech.gold.churn_predictions"],
            "columns_mentioned": [],
            "specific_values": [],
            "confidence": 0.5
        }

    return {
        "question_category": classification.get("category", "DATA_QUALITY"),
        "current_step": "retrieve",
        "messages": [
            HumanMessage(content=state["original_question"]),
            AIMessage(content=f"Classification: {json.dumps(classification, indent=2)}"),
        ],
    }


def retrieve_context(state: AgentState) -> dict:
    """Retrieve relevant context using MCP tools."""
    llm = get_llm()
    tools = get_mcp_tools()
    llm_with_tools = llm.bind_tools(tools)

    category = state.get("question_category", "DATA_QUALITY")
    question = state["original_question"]

    retrieval_prompt = f"""Based on this data quality question, retrieve relevant context.

Question: {question}
Category: {category}

Your task (do ALL of these):
1. Run a SQL query to quantify the problem (e.g., count affected records, sample affected rows)
2. Get the schema of the affected table(s)
3. **CRITICAL: Use search_transformation_code** to find the SQL transformation that creates
   the affected column. Search for the column name mentioned in the question.
   For example, if asking about NULL churn_risk, search for "churn_risk".

The bug is usually in the transformation code! You MUST search for it.

Start by identifying which tables and columns are involved, then gather all evidence including the code."""

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=retrieval_prompt),
    ]

    response = llm_with_tools.invoke(messages)

    return {
        "messages": state["messages"] + [response],
        "current_step": "analyze",
    }


def run_tools(state: AgentState) -> dict:
    """Execute tool calls from the LLM."""
    tools = get_mcp_tools()

    last_message = state["messages"][-1]

    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return {"current_step": "analyze"}

    tool_results = []
    tool_dict = {t.name: t for t in tools}

    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]

        if tool_name in tool_dict:
            try:
                result = tool_dict[tool_name].invoke(tool_args)
                tool_results.append(
                    ToolMessage(content=str(result), tool_call_id=tool_call["id"])
                )
            except Exception as e:
                tool_results.append(
                    ToolMessage(content=f"Error: {e}", tool_call_id=tool_call["id"])
                )
        else:
            tool_results.append(
                ToolMessage(content=f"Unknown tool: {tool_name}", tool_call_id=tool_call["id"])
            )

    # Track executed SQL queries
    queries_executed = list(state.get("queries_executed", []))
    for tool_call in last_message.tool_calls:
        if "sql" in tool_call["name"].lower():
            queries_executed.append(tool_call["args"].get("query", str(tool_call["args"])))

    return {
        "messages": state["messages"] + tool_results,
        "queries_executed": queries_executed,
        "iteration_count": state.get("iteration_count", 0) + 1,
    }


def analyze_evidence(state: AgentState) -> dict:
    """Analyze gathered evidence and form hypotheses."""
    llm = get_llm()
    tools = get_mcp_tools()
    llm_with_tools = llm.bind_tools(tools)

    evidence_summary = []
    for msg in state["messages"]:
        if isinstance(msg, ToolMessage):
            evidence_summary.append(msg.content)

    analysis_prompt = f"""Analyze the evidence gathered so far for this investigation.

## Original Question
{state["original_question"]}

## Evidence Gathered
{chr(10).join(evidence_summary[-5:])}

## Your Task
Based on this evidence:
1. What hypotheses do you have about the root cause?
2. Is there enough evidence to identify the root cause?
3. If not, what additional queries or information do you need?

If you have identified the root cause with high confidence, summarize your findings.
If you need more information, call the appropriate tools to gather it."""

    messages = state["messages"] + [HumanMessage(content=analysis_prompt)]
    response = llm_with_tools.invoke(messages)

    return {
        "messages": state["messages"] + [HumanMessage(content=analysis_prompt), response],
    }


def should_continue(state: AgentState) -> Literal["investigate", "synthesize"]:
    """Determine if we should continue investigating or synthesize results."""
    last_message = state["messages"][-1]
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", 5)

    if iteration_count >= max_iterations:
        return "synthesize"

    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "investigate"

    content = str(last_message.content).lower()
    done_signals = [
        "root cause identified",
        "the root cause is",
        "i have identified",
        "the issue is caused by",
        "bug found",
        "missing else",
        "duplicate records",
        "timezone mismatch",
    ]

    if any(signal in content for signal in done_signals):
        return "synthesize"

    if iteration_count < 2:
        return "investigate"

    return "synthesize"


def synthesize_response(state: AgentState) -> dict:
    """Generate the final investigation report."""
    llm = get_llm()

    evidence_parts = []
    for msg in state["messages"]:
        if isinstance(msg, ToolMessage):
            evidence_parts.append(msg.content)
        elif isinstance(msg, AIMessage) and msg.content:
            evidence_parts.append(str(msg.content))

    synthesis_prompt = f"""Generate a comprehensive investigation report.

## Original Question
{state["original_question"]}

## Evidence Gathered
{chr(10).join(evidence_parts[-10:])}

## Queries Executed
{chr(10).join(state.get("queries_executed", [])[-5:])}

Generate a clear, well-structured report in markdown with:

1. **Summary** - One sentence answer to the question
2. **Root Cause** - Technical explanation of the bug/issue
3. **Evidence** - SQL queries and results that prove the root cause
4. **Impact** - How many records affected, business impact
5. **Recommended Fix** - Specific code/query change to fix it
6. **Confidence Score** - Your confidence level (0-100%)

Be specific and cite the actual data you found."""

    response = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=synthesis_prompt),
    ])

    return {
        "final_response": response.content,
        "current_step": "complete",
        "should_continue": False,
    }


# =============================================================================
# Graph Definition
# =============================================================================

def create_datascope_agent(checkpointer=None):
    """
    Create the DataScope agent graph.

    Args:
        checkpointer: Optional LangGraph checkpointer for state persistence.
                     Use PostgresSaver with Lakebase for production.

    Returns:
        Compiled LangGraph workflow
    """
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("classify", classify_question)
    workflow.add_node("retrieve", retrieve_context)
    workflow.add_node("investigate", run_tools)
    workflow.add_node("analyze", analyze_evidence)
    workflow.add_node("synthesize", synthesize_response)

    # Set entry point
    workflow.set_entry_point("classify")

    # Add edges
    workflow.add_edge("classify", "retrieve")
    workflow.add_edge("retrieve", "investigate")
    workflow.add_edge("investigate", "analyze")

    # Conditional edge after analysis
    workflow.add_conditional_edges(
        "analyze",
        should_continue,
        {
            "investigate": "investigate",
            "synthesize": "synthesize",
        }
    )

    workflow.add_edge("synthesize", END)

    # Compile with optional checkpointer
    if checkpointer:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()


def create_agent_with_memory(lakebase_connection_string: Optional[str] = None):
    """
    Create agent with Lakebase-backed memory for multi-turn conversations.

    Args:
        lakebase_connection_string: Postgres connection string for Lakebase.
                                   If None, uses LAKEBASE_CONNECTION_STRING env var.

    Returns:
        Compiled LangGraph workflow with checkpointing
    """
    conn_string = lakebase_connection_string or os.environ.get("LAKEBASE_CONNECTION_STRING")

    if not conn_string:
        print("Warning: No Lakebase connection string. Using in-memory state.")
        return create_datascope_agent()

    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer = PostgresSaver.from_conn_string(conn_string)
        return create_datascope_agent(checkpointer=checkpointer)
    except ImportError:
        print("Warning: langgraph-checkpoint-postgres not installed. Using in-memory state.")
        return create_datascope_agent()
    except Exception as e:
        print(f"Warning: Could not connect to Lakebase: {e}. Using in-memory state.")
        return create_datascope_agent()


# =============================================================================
# Main Entry Points
# =============================================================================

@mlflow.trace(name="datascope_investigation")
def investigate(question: str, thread_id: Optional[str] = None) -> str:
    """
    Run a data quality investigation.

    Args:
        question: The data quality question to investigate
        thread_id: Optional thread ID for multi-turn conversations.
                  If provided and Lakebase is configured, conversation
                  history will be preserved.

    Returns:
        Markdown-formatted investigation report
    """
    # Log the input question as a span attribute
    mlflow.log_param("question", question[:100])  # Truncate for param limit

    state = create_initial_state(question)

    # Try to create agent with memory if Lakebase is configured
    agent = create_agent_with_memory()

    # Run with thread ID if provided
    config = {}
    if thread_id:
        config = {"configurable": {"thread_id": thread_id}}

    final_state = agent.invoke(state, config=config if config else None)

    # Log key metrics
    mlflow.log_metric("iteration_count", final_state.get("iteration_count", 0))
    mlflow.log_metric("queries_executed", len(final_state.get("queries_executed", [])))

    return final_state.get("final_response", "Investigation could not be completed.")


def investigate_followup(question: str, thread_id: str) -> str:
    """
    Continue an investigation with a follow-up question.

    Requires Lakebase to be configured for state persistence.

    Args:
        question: The follow-up question
        thread_id: Thread ID from the original investigation

    Returns:
        Markdown-formatted response
    """
    return investigate(question, thread_id=thread_id)


# =============================================================================
# MLflow Model Interface (for Model Serving deployment)
# =============================================================================

class DataScopeAgent:
    """
    MLflow-compatible agent for deployment to Databricks Model Serving.

    Usage:
        agent = DataScopeAgent()
        result = agent.predict({"question": "Why do some customers have NULL churn_risk?"})
    """

    def __init__(self):
        self.agent = create_agent_with_memory()

    def predict(self, inputs: dict) -> dict:
        """
        Run investigation.

        Args:
            inputs: Dict with 'question' and optional 'thread_id'

        Returns:
            Dict with 'response' and 'thread_id'
        """
        question = inputs.get("question", inputs.get("input", ""))
        thread_id = inputs.get("thread_id", inputs.get("custom_inputs", {}).get("thread_id"))

        if not thread_id:
            import uuid
            thread_id = str(uuid.uuid4())

        response = investigate(question, thread_id=thread_id)

        return {
            "response": response,
            "thread_id": thread_id,
        }


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    question = "Why do some customers have NULL churn_risk?"
    print(f"Investigating: {question}\n")
    print("=" * 80)

    result = investigate(question)
    print(result)
