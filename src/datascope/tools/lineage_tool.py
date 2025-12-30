"""Lineage tool for Unity Catalog."""

import os
from typing import Any, Dict, List, Optional

from databricks.sdk import WorkspaceClient
from pydantic import BaseModel


class LineageNode(BaseModel):
    """A node in the lineage graph."""

    name: str
    type: str  # "TABLE", "COLUMN", "NOTEBOOK", "JOB"
    catalog: Optional[str] = None
    schema_name: Optional[str] = None


class TableLineage(BaseModel):
    """Lineage information for a table."""

    table_name: str
    upstream_tables: List[str]
    downstream_tables: List[str]

    def to_markdown(self) -> str:
        """Format lineage as markdown."""
        lines = [f"## Lineage for `{self.table_name}`", ""]

        lines.append("### Upstream Tables (sources)")
        if self.upstream_tables:
            for t in self.upstream_tables:
                lines.append(f"- `{t}`")
        else:
            lines.append("_No upstream tables found_")

        lines.extend(["", "### Downstream Tables (dependents)"])
        if self.downstream_tables:
            for t in self.downstream_tables:
                lines.append(f"- `{t}`")
        else:
            lines.append("_No downstream tables found_")

        return "\n".join(lines)


class ColumnLineage(BaseModel):
    """Lineage information for a column."""

    table_name: str
    column_name: str
    upstream_columns: List[str]  # format: "table.column"
    transformations: List[str]  # e.g., ["AVG", "CASE"]

    def to_markdown(self) -> str:
        """Format column lineage as markdown."""
        lines = [f"## Column Lineage: `{self.table_name}.{self.column_name}`", ""]

        lines.append("### Source Columns")
        if self.upstream_columns:
            for col in self.upstream_columns:
                lines.append(f"- `{col}`")
        else:
            lines.append("_No upstream columns found_")

        if self.transformations:
            lines.extend(["", "### Transformations Applied"])
            for t in self.transformations:
                lines.append(f"- {t}")

        return "\n".join(lines)


class LineageTool:
    """Get data lineage from Unity Catalog."""

    def __init__(
        self,
        host: Optional[str] = None,
        token: Optional[str] = None,
    ):
        """
        Initialize lineage tool.

        Note: Unity Catalog lineage API requires Premium/Enterprise tier
        and must be enabled in the workspace.
        """
        self.host = host or os.environ.get("DATABRICKS_HOST")
        self.token = token or os.environ.get("DATABRICKS_TOKEN")

        if not all([self.host, self.token]):
            raise ValueError(
                "Missing required configuration. Set DATABRICKS_HOST and "
                "DATABRICKS_TOKEN environment variables."
            )

        self.client = WorkspaceClient(host=self.host, token=self.token)

    def get_table_lineage(self, full_table_name: str) -> TableLineage:
        """
        Get upstream and downstream tables for a table.

        Args:
            full_table_name: Fully qualified table name (catalog.schema.table)

        Returns:
            TableLineage with upstream/downstream tables
        """
        try:
            # Try to use the lineage API
            # Note: This may not be available in all workspaces
            lineage = self.client.api_client.do(
                "GET",
                f"/api/2.0/lineage-tracking/table-lineage?table_name={full_table_name}",
            )

            upstream = []
            downstream = []

            if "upstreams" in lineage:
                for item in lineage["upstreams"]:
                    if "tableInfo" in item:
                        upstream.append(item["tableInfo"].get("name", "unknown"))

            if "downstreams" in lineage:
                for item in lineage["downstreams"]:
                    if "tableInfo" in item:
                        downstream.append(item["tableInfo"].get("name", "unknown"))

            return TableLineage(
                table_name=full_table_name,
                upstream_tables=upstream,
                downstream_tables=downstream,
            )

        except Exception as e:
            # Lineage API may not be available, return empty result
            # In production, you'd want to handle this more gracefully
            return TableLineage(
                table_name=full_table_name,
                upstream_tables=[],
                downstream_tables=[],
            )

    def get_column_lineage(self, full_table_name: str, column_name: str) -> ColumnLineage:
        """
        Get lineage for a specific column.

        Args:
            full_table_name: Fully qualified table name
            column_name: Column name

        Returns:
            ColumnLineage with upstream columns and transformations
        """
        try:
            # Column lineage API
            lineage = self.client.api_client.do(
                "GET",
                f"/api/2.0/lineage-tracking/column-lineage"
                f"?table_name={full_table_name}&column_name={column_name}",
            )

            upstream_columns = []
            transformations = []

            if "upstream_cols" in lineage:
                for col in lineage["upstream_cols"]:
                    table = col.get("table_name", "unknown")
                    col_name = col.get("name", "unknown")
                    upstream_columns.append(f"{table}.{col_name}")

            return ColumnLineage(
                table_name=full_table_name,
                column_name=column_name,
                upstream_columns=upstream_columns,
                transformations=transformations,
            )

        except Exception:
            # Return empty result if API not available
            return ColumnLineage(
                table_name=full_table_name,
                column_name=column_name,
                upstream_columns=[],
                transformations=[],
            )

    def get_lineage_from_sql(self, sql_content: str) -> Dict[str, Any]:
        """
        Parse SQL to extract lineage information.

        This is a fallback when the API lineage isn't available.
        Extracts table references from SQL text.

        Args:
            sql_content: SQL transformation code

        Returns:
            Dict with extracted table references
        """
        import re

        # Simple regex patterns to find table references
        # This is basic - production would use a proper SQL parser
        from_pattern = r"FROM\s+([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*)"
        join_pattern = r"JOIN\s+([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*)"

        from_tables = re.findall(from_pattern, sql_content, re.IGNORECASE)
        join_tables = re.findall(join_pattern, sql_content, re.IGNORECASE)

        return {
            "source_tables": list(set(from_tables + join_tables)),
            "has_left_join": "LEFT JOIN" in sql_content.upper(),
            "has_case_statement": "CASE" in sql_content.upper(),
            "has_group_by": "GROUP BY" in sql_content.upper(),
        }


# For LangChain/LangGraph tool registration
def create_lineage_tool_functions():
    """Create tool functions for use with LangGraph."""
    tool = LineageTool()

    def get_table_lineage(table_name: str) -> str:
        """
        Get upstream and downstream tables for a table.

        Args:
            table_name: Fully qualified table name (catalog.schema.table)

        Returns:
            Markdown-formatted lineage information
        """
        try:
            lineage = tool.get_table_lineage(table_name)
            return lineage.to_markdown()
        except Exception as e:
            return f"**Error:** {e}"

    def get_column_lineage(table_name: str, column_name: str) -> str:
        """
        Get lineage for a specific column - where the data comes from.

        Args:
            table_name: Fully qualified table name (catalog.schema.table)
            column_name: Name of the column

        Returns:
            Markdown-formatted column lineage
        """
        try:
            lineage = tool.get_column_lineage(table_name, column_name)
            return lineage.to_markdown()
        except Exception as e:
            return f"**Error:** {e}"

    return get_table_lineage, get_column_lineage


if __name__ == "__main__":
    # Quick test
    from dotenv import load_dotenv

    load_dotenv()

    tool = LineageTool()

    # Test table lineage
    lineage = tool.get_table_lineage("novatech.gold.churn_predictions")
    print(lineage.to_markdown())
