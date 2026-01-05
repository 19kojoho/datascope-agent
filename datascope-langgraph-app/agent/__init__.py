"""DataScope LangGraph Agent Package.

Production-ready data debugging agent using LangGraph.
Includes Galileo AI observability for tracing and debugging.
"""

from .config import Config
from .graph import create_agent, invoke_agent
from .tools import search_patterns, execute_sql, search_code
from .observability import (
    GalileoTracer,
    create_tracer,
    is_galileo_enabled,
    log_evaluation,
)

__all__ = [
    # Config
    "Config",
    # Agent
    "create_agent",
    "invoke_agent",
    # Tools
    "search_patterns",
    "execute_sql",
    "search_code",
    # Observability
    "GalileoTracer",
    "create_tracer",
    "is_galileo_enabled",
    "log_evaluation",
]
