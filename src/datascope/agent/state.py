"""Agent state definition for DataScope."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict, Optional, List, Dict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """
    State maintained throughout a data debugging investigation.

    This state flows through all nodes in the LangGraph workflow.
    """

    # Input
    original_question: str
    question_category: Optional[str]  # DATA_QUALITY, METRIC_DISCREPANCY, etc.

    # Messages (for chat-style interaction)
    messages: Annotated[list, add_messages]

    # Retrieved context
    table_schemas: List[Dict]  # Schema info for relevant tables
    lineage_info: List[Dict]  # Lineage for investigated tables/columns
    sql_results: List[Dict]  # Results of SQL queries executed
    code_snippets: List[Dict]  # Transformation code found

    # Investigation progress
    hypotheses: List[str]  # Generated hypotheses about root cause
    evidence: List[Dict]  # Evidence for/against each hypothesis
    tables_investigated: List[str]  # Tables we've looked at
    queries_executed: List[str]  # SQL queries we've run

    # Findings
    root_cause: Optional[str]  # Identified root cause
    bug_id: Optional[str]  # If matches known bug pattern
    affected_records: Optional[int]  # Count of affected records
    impact_description: Optional[str]  # Business impact
    recommended_fix: Optional[str]  # Suggested fix

    # Control flow
    current_step: Literal[
        "classify",
        "retrieve",
        "analyze",
        "investigate",
        "synthesize",
        "complete",
    ]
    iteration_count: int
    max_iterations: int
    should_continue: bool

    # Output
    final_response: Optional[str]
    confidence_score: Optional[float]

    # Tracing
    trace_id: Optional[str]
    start_time: Optional[float]


def create_initial_state(question: str) -> AgentState:
    """Create initial state for a new investigation."""
    import time
    import uuid

    return AgentState(
        original_question=question,
        question_category=None,
        messages=[],
        table_schemas=[],
        lineage_info=[],
        sql_results=[],
        code_snippets=[],
        hypotheses=[],
        evidence=[],
        tables_investigated=[],
        queries_executed=[],
        root_cause=None,
        bug_id=None,
        affected_records=None,
        impact_description=None,
        recommended_fix=None,
        current_step="classify",
        iteration_count=0,
        max_iterations=5,
        should_continue=True,
        final_response=None,
        confidence_score=None,
        trace_id=str(uuid.uuid4()),
        start_time=time.time(),
    )
