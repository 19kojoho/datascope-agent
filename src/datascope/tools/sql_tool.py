"""SQL execution tool for Databricks."""

import os
import time
from typing import Any, Dict, List, Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState
from pydantic import BaseModel


class SQLResult(BaseModel):
    """Result of a SQL query execution."""

    query: str
    columns: List[str]
    rows: List[Dict[str, Any]]
    row_count: int
    execution_time_ms: int
    error: Optional[str] = None

    def to_markdown(self) -> str:
        """Format result as markdown table."""
        if self.error:
            return f"**Error:** {self.error}"

        if not self.rows:
            return "_No results_"

        # Header
        header = "| " + " | ".join(self.columns) + " |"
        separator = "| " + " | ".join(["---"] * len(self.columns)) + " |"

        # Rows (limit to 20 for display)
        display_rows = self.rows[:20]
        row_lines = []
        for row in display_rows:
            values = [str(row.get(col, "")) for col in self.columns]
            row_lines.append("| " + " | ".join(values) + " |")

        result = "\n".join([header, separator] + row_lines)

        if len(self.rows) > 20:
            result += f"\n\n_Showing 20 of {self.row_count} rows_"

        return result


class SQLTool:
    """Execute SQL queries against Databricks SQL Warehouse."""

    def __init__(
        self,
        host: Optional[str] = None,
        token: Optional[str] = None,
        warehouse_id: Optional[str] = None,
    ):
        """
        Initialize SQL tool.

        Args:
            host: Databricks workspace URL (or DATABRICKS_HOST env var)
            token: Personal access token (or DATABRICKS_TOKEN env var)
            warehouse_id: SQL warehouse ID (or DATABRICKS_SQL_WAREHOUSE_ID env var)
        """
        self.host = host or os.environ.get("DATABRICKS_HOST")
        self.token = token or os.environ.get("DATABRICKS_TOKEN")
        self.warehouse_id = warehouse_id or os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID")

        if not all([self.host, self.token, self.warehouse_id]):
            raise ValueError(
                "Missing required configuration. Set DATABRICKS_HOST, "
                "DATABRICKS_TOKEN, and DATABRICKS_SQL_WAREHOUSE_ID environment variables."
            )

        self.client = WorkspaceClient(host=self.host, token=self.token)

    def execute(self, query: str, timeout_seconds: int = 60) -> SQLResult:
        """
        Execute a SQL query and return results.

        Args:
            query: SQL query to execute
            timeout_seconds: Maximum time to wait for results

        Returns:
            SQLResult with columns, rows, and metadata
        """
        start_time = time.time()

        try:
            # Execute statement
            response = self.client.statement_execution.execute_statement(
                warehouse_id=self.warehouse_id,
                statement=query,
                wait_timeout="50s",  # API max is 50s
            )

            # Check status
            if response.status.state == StatementState.FAILED:
                return SQLResult(
                    query=query,
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    error=response.status.error.message if response.status.error else "Query failed",
                )

            if response.status.state != StatementState.SUCCEEDED:
                return SQLResult(
                    query=query,
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    error=f"Query did not succeed. State: {response.status.state}",
                )

            # Extract results
            manifest = response.manifest
            result_data = response.result

            if not manifest or not manifest.schema or not manifest.schema.columns:
                return SQLResult(
                    query=query,
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            columns = [col.name for col in manifest.schema.columns]

            rows = []
            if result_data and result_data.data_array:
                for row_array in result_data.data_array:
                    row_dict = dict(zip(columns, row_array))
                    rows.append(row_dict)

            return SQLResult(
                query=query,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        except Exception as e:
            return SQLResult(
                query=query,
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=int((time.time() - start_time) * 1000),
                error=str(e),
            )

    def count_nulls(self, table: str, column: str) -> SQLResult:
        """Count NULL values in a column."""
        query = f"""
        SELECT 
            COUNT(*) as total_rows,
            SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) as null_count,
            ROUND(SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) as null_pct
        FROM {table}
        """
        return self.execute(query)

    def sample_where(self, table: str, condition: str, limit: int = 10) -> SQLResult:
        """Get sample rows matching a condition."""
        query = f"SELECT * FROM {table} WHERE {condition} LIMIT {limit}"
        return self.execute(query)

    def compare_totals(self, query1: str, query2: str, label1: str, label2: str) -> SQLResult:
        """Compare totals from two queries."""
        query = f"""
        SELECT 
            '{label1}' as source, ({query1}) as total
        UNION ALL
        SELECT 
            '{label2}' as source, ({query2}) as total
        """
        return self.execute(query)


# For LangChain/LangGraph tool registration
def create_sql_tool_function():
    """Create a tool function for use with LangGraph."""
    tool = SQLTool()

    def execute_sql(query: str) -> str:
        """
        Execute a SQL query against Databricks.

        Args:
            query: The SQL query to execute

        Returns:
            Markdown-formatted results or error message
        """
        result = tool.execute(query)
        return result.to_markdown()

    return execute_sql


if __name__ == "__main__":
    # Quick test
    from dotenv import load_dotenv

    load_dotenv()

    tool = SQLTool()
    result = tool.execute("SELECT COUNT(*) as cnt FROM novatech.gold.churn_predictions")
    print(result.to_markdown())
