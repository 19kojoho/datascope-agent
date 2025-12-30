"""Schema and metadata tool for Unity Catalog."""

import os
from typing import Any, List, Optional

from databricks.sdk import WorkspaceClient
from pydantic import BaseModel


class ColumnInfo(BaseModel):
    """Information about a table column."""

    name: str
    data_type: str
    nullable: bool
    comment: Optional[str] = None


class TableInfo(BaseModel):
    """Information about a table."""

    catalog: str
    schema_name: str
    table_name: str
    full_name: str
    columns: List[ColumnInfo]
    comment: Optional[str] = None
    owner: Optional[str] = None
    created_at: Optional[str] = None
    table_type: Optional[str] = None

    def to_markdown(self) -> str:
        """Format table info as markdown."""
        lines = [
            f"## Table: `{self.full_name}`",
            "",
            f"**Type:** {self.table_type or 'Unknown'}",
            f"**Owner:** {self.owner or 'Unknown'}",
        ]

        if self.comment:
            lines.append(f"**Description:** {self.comment}")

        lines.extend(["", "### Columns", ""])
        lines.append("| Column | Type | Nullable | Comment |")
        lines.append("|--------|------|----------|---------|")

        for col in self.columns:
            nullable = "YES" if col.nullable else "NO"
            comment = col.comment or ""
            lines.append(f"| {col.name} | {col.data_type} | {nullable} | {comment} |")

        return "\n".join(lines)


class SchemaList(BaseModel):
    """List of tables in a schema."""

    catalog: str
    schema_name: str
    tables: List[str]

    def to_markdown(self) -> str:
        """Format as markdown list."""
        lines = [f"## Tables in `{self.catalog}.{self.schema_name}`", ""]
        for table in self.tables:
            lines.append(f"- `{table}`")
        return "\n".join(lines)


class SchemaTool:
    """Get table schemas and metadata from Unity Catalog."""

    def __init__(
        self,
        host: Optional[str] = None,
        token: Optional[str] = None,
    ):
        """
        Initialize schema tool.

        Args:
            host: Databricks workspace URL (or DATABRICKS_HOST env var)
            token: Personal access token (or DATABRICKS_TOKEN env var)
        """
        self.host = host or os.environ.get("DATABRICKS_HOST")
        self.token = token or os.environ.get("DATABRICKS_TOKEN")

        if not all([self.host, self.token]):
            raise ValueError(
                "Missing required configuration. Set DATABRICKS_HOST and "
                "DATABRICKS_TOKEN environment variables."
            )

        self.client = WorkspaceClient(host=self.host, token=self.token)

    def get_table_info(self, full_table_name: str) -> TableInfo:
        """
        Get detailed information about a table.

        Args:
            full_table_name: Fully qualified table name (catalog.schema.table)

        Returns:
            TableInfo with columns and metadata
        """
        parts = full_table_name.split(".")
        if len(parts) != 3:
            raise ValueError(f"Expected format: catalog.schema.table, got: {full_table_name}")

        catalog, schema_name, table_name = parts

        try:
            table = self.client.tables.get(full_name=full_table_name)

            columns = []
            if table.columns:
                for col in table.columns:
                    columns.append(
                        ColumnInfo(
                            name=col.name,
                            data_type=col.type_text or str(col.type_name),
                            nullable=col.nullable if col.nullable is not None else True,
                            comment=col.comment,
                        )
                    )

            return TableInfo(
                catalog=catalog,
                schema_name=schema_name,
                table_name=table_name,
                full_name=full_table_name,
                columns=columns,
                comment=table.comment,
                owner=table.owner,
                created_at=str(table.created_at) if table.created_at else None,
                table_type=str(table.table_type) if table.table_type else None,
            )

        except Exception as e:
            raise RuntimeError(f"Failed to get table info for {full_table_name}: {e}")

    def list_tables(self, catalog: str, schema_name: str) -> SchemaList:
        """
        List all tables in a schema.

        Args:
            catalog: Catalog name
            schema_name: Schema name

        Returns:
            SchemaList with table names
        """
        try:
            tables = self.client.tables.list(catalog_name=catalog, schema_name=schema_name)
            table_names = [t.name for t in tables if t.name]

            return SchemaList(
                catalog=catalog,
                schema_name=schema_name,
                tables=sorted(table_names),
            )

        except Exception as e:
            raise RuntimeError(f"Failed to list tables in {catalog}.{schema_name}: {e}")

    def list_schemas(self, catalog: str) -> List[str]:
        """List all schemas in a catalog."""
        try:
            schemas = self.client.schemas.list(catalog_name=catalog)
            return sorted([s.name for s in schemas if s.name])
        except Exception as e:
            raise RuntimeError(f"Failed to list schemas in {catalog}: {e}")


# For LangChain/LangGraph tool registration
def create_schema_tool_functions():
    """Create tool functions for use with LangGraph."""
    tool = SchemaTool()

    def get_table_schema(table_name: str) -> str:
        """
        Get the schema (columns, types) of a table.

        Args:
            table_name: Fully qualified table name (catalog.schema.table)

        Returns:
            Markdown-formatted table schema
        """
        try:
            info = tool.get_table_info(table_name)
            return info.to_markdown()
        except Exception as e:
            return f"**Error:** {e}"

    def list_tables_in_schema(catalog: str, schema_name: str) -> str:
        """
        List all tables in a schema.

        Args:
            catalog: Catalog name (e.g., 'novatech')
            schema_name: Schema name (e.g., 'gold')

        Returns:
            Markdown-formatted list of tables
        """
        try:
            schema_list = tool.list_tables(catalog, schema_name)
            return schema_list.to_markdown()
        except Exception as e:
            return f"**Error:** {e}"

    return get_table_schema, list_tables_in_schema


if __name__ == "__main__":
    # Quick test
    from dotenv import load_dotenv

    load_dotenv()

    tool = SchemaTool()

    # List tables
    tables = tool.list_tables("novatech", "gold")
    print(tables.to_markdown())
    print()

    # Get table info
    info = tool.get_table_info("novatech.gold.churn_predictions")
    print(info.to_markdown())
