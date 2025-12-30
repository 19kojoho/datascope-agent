"""Basic tests for DataScope tools."""

import pytest
from unittest.mock import Mock, patch


class TestSQLTool:
    """Tests for SQL tool."""

    def test_sql_result_to_markdown_empty(self):
        """Test markdown output for empty result."""
        from datascope.tools.sql_tool import SQLResult

        result = SQLResult(
            query="SELECT 1",
            columns=[],
            rows=[],
            row_count=0,
            execution_time_ms=100,
        )

        assert result.to_markdown() == "_No results_"

    def test_sql_result_to_markdown_with_data(self):
        """Test markdown output with data."""
        from datascope.tools.sql_tool import SQLResult

        result = SQLResult(
            query="SELECT name, value FROM test",
            columns=["name", "value"],
            rows=[
                {"name": "foo", "value": 1},
                {"name": "bar", "value": 2},
            ],
            row_count=2,
            execution_time_ms=100,
        )

        md = result.to_markdown()
        assert "| name | value |" in md
        assert "| foo | 1 |" in md
        assert "| bar | 2 |" in md

    def test_sql_result_to_markdown_with_error(self):
        """Test markdown output with error."""
        from datascope.tools.sql_tool import SQLResult

        result = SQLResult(
            query="SELECT * FROM nonexistent",
            columns=[],
            rows=[],
            row_count=0,
            execution_time_ms=100,
            error="Table not found",
        )

        assert "**Error:** Table not found" in result.to_markdown()


class TestAgentState:
    """Tests for agent state."""

    def test_create_initial_state(self):
        """Test initial state creation."""
        from datascope.agent.state import create_initial_state

        state = create_initial_state("Why is X broken?")

        assert state["original_question"] == "Why is X broken?"
        assert state["current_step"] == "classify"
        assert state["iteration_count"] == 0
        assert state["should_continue"] is True
        assert state["trace_id"] is not None


class TestPrompts:
    """Tests for prompts."""

    def test_system_prompt_contains_tables(self):
        """Test that system prompt lists available tables."""
        from datascope.agent.prompts import SYSTEM_PROMPT

        assert "novatech.gold.churn_predictions" in SYSTEM_PROMPT
        assert "novatech.silver.fct_subscriptions" in SYSTEM_PROMPT

    def test_classification_prompt_has_placeholder(self):
        """Test classification prompt has question placeholder."""
        from datascope.agent.prompts import CLASSIFICATION_PROMPT

        assert "{question}" in CLASSIFICATION_PROMPT
