"""Command-line interface for DataScope."""

from __future__ import annotations

import sys
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown

from datascope.tools import SQLTool, SchemaTool

console = Console()


def test_connection():
    """Test Databricks connection."""
    load_dotenv()
    
    console.print("[bold]Testing Databricks connection...[/bold]")
    
    try:
        sql_tool = SQLTool()
        result = sql_tool.execute("SELECT 1 as test")
        
        if result.error:
            console.print(f"[red]Connection failed:[/red] {result.error}")
            return False
        
        console.print("[green]✓ SQL connection working[/green]")
        
        # Test schema access
        schema_tool = SchemaTool()
        tables = schema_tool.list_tables("novatech", "gold")
        console.print(f"[green]✓ Found {len(tables.tables)} tables in novatech.gold[/green]")
        
        return True
        
    except Exception as e:
        console.print(f"[red]Connection failed:[/red] {e}")
        return False


def investigate(question: str):
    """Run a simple investigation (tools only, no full agent yet)."""
    load_dotenv()
    
    console.print(f"\n[bold]Question:[/bold] {question}\n")
    console.print("[dim]Running investigation with tools...[/dim]\n")
    
    sql_tool = SQLTool()
    schema_tool = SchemaTool()
    
    # Simple investigation flow for BUG-005 type question
    if "NULL" in question.upper() and "churn" in question.lower():
        console.print("[bold]Step 1: Count NULLs[/bold]")
        result = sql_tool.count_nulls("novatech.gold.churn_predictions", "churn_risk")
        console.print(Markdown(result.to_markdown()))
        
        console.print("\n[bold]Step 2: Sample NULL records[/bold]")
        result = sql_tool.sample_where(
            "novatech.gold.churn_predictions",
            "churn_risk IS NULL",
            limit=5
        )
        console.print(Markdown(result.to_markdown()))
        
        console.print("\n[bold]Step 3: Check if avg_logins is also NULL[/bold]")
        result = sql_tool.execute("""
            SELECT 
                customer_id,
                avg_logins,
                churn_risk
            FROM novatech.gold.churn_predictions
            WHERE churn_risk IS NULL
            LIMIT 5
        """)
        console.print(Markdown(result.to_markdown()))
        
        console.print("\n[bold]Finding:[/bold]")
        console.print(Markdown("""
**Root Cause: BUG-005 - Missing ELSE clause in CASE statement**

The `churn_risk` column is NULL for customers where `avg_logins` is also NULL.

This happens because the CASE statement in the transformation doesn't have an ELSE clause:

```sql
CASE 
    WHEN avg_logins > 20 THEN 'Low Risk'
    WHEN avg_logins > 5 THEN 'Medium Risk'
    WHEN avg_logins <= 5 THEN 'High Risk'
    -- Missing: ELSE 'High Risk'
END as churn_risk
```

When `avg_logins` is NULL (customers with no activity in 30 days), 
none of the conditions match, so `churn_risk` becomes NULL.

**Fix:** Add `ELSE 'High Risk'` to the CASE statement.
"""))
    else:
        console.print("[yellow]Generic investigation not yet implemented.[/yellow]")
        console.print("Try asking: 'Why do some customers have NULL churn_risk?'")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        console.print("[bold]DataScope - Data Debugging Agent[/bold]\n")
        console.print("Usage:")
        console.print("  datascope test          - Test Databricks connection")
        console.print('  datascope "question"    - Investigate a question')
        console.print("\nExample:")
        console.print('  datascope "Why do some customers have NULL churn_risk?"')
        return
    
    command = sys.argv[1]
    
    if command == "test":
        test_connection()
    else:
        # Treat as a question
        question = " ".join(sys.argv[1:])
        investigate(question)


if __name__ == "__main__":
    main()
