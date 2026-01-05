"""Galileo AI Observability for DataScope LangGraph Agent.

This module provides observability and tracing for the LangGraph agent,
enabling debugging, performance monitoring, and evaluation capabilities.

Key Features:
- LLM call tracing with latency and token counts
- Tool call tracing with inputs/outputs
- Investigation traces grouping related calls
- Evaluation metrics for quality assessment

For interview context: Galileo AI is an observability and evaluation platform
for LLM applications. This integration demonstrates production-ready
observability patterns.
"""

import os
import json
import logging
import time
import uuid
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Check if Galileo is available and enabled
GALILEO_ENABLED = bool(os.environ.get("GALILEO_API_KEY"))
GALILEO_PROJECT = os.environ.get("GALILEO_PROJECT", "datascope-langgraph")
GALILEO_LOG_STREAM = os.environ.get("GALILEO_LOG_STREAM", "investigations")


def is_galileo_enabled() -> bool:
    """Check if Galileo observability is enabled."""
    return GALILEO_ENABLED


@dataclass
class LLMSpan:
    """Represents an LLM call span for tracing."""
    model: str
    input_messages: List[Dict[str, Any]]
    output_content: str
    duration_ms: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


@dataclass
class ToolSpan:
    """Represents a tool call span for tracing."""
    name: str
    input_args: Dict[str, Any]
    output: Any
    duration_ms: float
    error: Optional[str] = None


@dataclass
class InvestigationTrace:
    """A complete trace for one investigation (user question -> answer).

    Traces group all LLM and tool calls for a single investigation,
    enabling end-to-end debugging and performance analysis.
    """
    trace_id: str
    session_id: str
    user_input: str = ""
    final_output: str = ""
    llm_spans: List[LLMSpan] = field(default_factory=list)
    tool_spans: List[ToolSpan] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        """Total duration of the investigation in milliseconds."""
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return (time.time() - self.start_time) * 1000

    @property
    def total_llm_calls(self) -> int:
        return len(self.llm_spans)

    @property
    def total_tool_calls(self) -> int:
        return len(self.tool_spans)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens or 0 for s in self.llm_spans)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens or 0 for s in self.llm_spans)


class GalileoTracer:
    """Tracer for DataScope investigations using Galileo AI.

    This tracer captures:
    1. LLM calls - Each Claude API call with messages, responses, latency
    2. Tool calls - Each tool execution with inputs, outputs, errors
    3. Investigation traces - Complete end-to-end traces

    Interview talking points:
    - Traces enable root cause analysis when investigations fail
    - Latency tracking identifies slow tools (SQL often bottleneck)
    - Token counting enables cost attribution
    - Evaluation metrics measure investigation quality
    """

    def __init__(self, session_id: Optional[str] = None):
        """Initialize a new tracer for an investigation session.

        Args:
            session_id: Optional session ID for grouping related traces
        """
        self.trace = InvestigationTrace(
            trace_id=str(uuid.uuid4()),
            session_id=session_id or str(uuid.uuid4())
        )

    def log_llm_call(
        self,
        model: str,
        input_messages: List[Dict[str, Any]],
        output_content: str,
        duration_ms: float,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Log an LLM call to the trace.

        Args:
            model: Model name (e.g., 'claude-sonnet-4-20250514')
            input_messages: List of input messages
            output_content: Response content
            duration_ms: Call duration in milliseconds
            input_tokens: Number of input tokens (if available)
            output_tokens: Number of output tokens (if available)
            tool_calls: List of tool calls requested (if any)
        """
        span = LLMSpan(
            model=model,
            input_messages=input_messages,
            output_content=output_content,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls
        )
        self.trace.llm_spans.append(span)

        if is_galileo_enabled():
            logger.info(f"[Galileo] LLM call: {model}, {duration_ms:.0f}ms, "
                       f"tokens: {input_tokens or '?'}/{output_tokens or '?'}")
        else:
            logger.debug(f"LLM call: {model}, {duration_ms:.0f}ms")

    def log_tool_call(
        self,
        name: str,
        input_args: Dict[str, Any],
        output: Any,
        duration_ms: float,
        error: Optional[str] = None
    ) -> None:
        """Log a tool call to the trace.

        Args:
            name: Tool name (e.g., 'execute_sql')
            input_args: Tool input arguments
            output: Tool output
            duration_ms: Execution duration in milliseconds
            error: Error message if tool failed
        """
        span = ToolSpan(
            name=name,
            input_args=input_args,
            output=output,
            duration_ms=duration_ms,
            error=error
        )
        self.trace.tool_spans.append(span)

        status = "ERROR" if error else "OK"
        if is_galileo_enabled():
            logger.info(f"[Galileo] Tool call: {name}, {duration_ms:.0f}ms, {status}")
        else:
            logger.debug(f"Tool call: {name}, {duration_ms:.0f}ms, {status}")

    async def complete(
        self,
        user_input: str,
        final_output: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Complete the trace and send to Galileo.

        Args:
            user_input: The original user question
            final_output: The final agent response
            metadata: Optional additional metadata

        Returns:
            Trace summary dictionary
        """
        self.trace.user_input = user_input
        self.trace.final_output = final_output
        self.trace.end_time = time.time()

        if metadata:
            self.trace.metadata.update(metadata)

        summary = {
            "trace_id": self.trace.trace_id,
            "session_id": self.trace.session_id,
            "duration_ms": self.trace.duration_ms,
            "llm_calls": self.trace.total_llm_calls,
            "tool_calls": self.trace.total_tool_calls,
            "total_tokens": self.trace.total_input_tokens + self.trace.total_output_tokens
        }

        if is_galileo_enabled():
            await self._send_to_galileo()
            logger.info(f"[Galileo] Trace complete: {self.trace.trace_id}, "
                       f"{self.trace.duration_ms:.0f}ms, "
                       f"{self.trace.total_llm_calls} LLM calls, "
                       f"{self.trace.total_tool_calls} tool calls")
        else:
            logger.info(f"Trace complete (Galileo disabled): {summary}")

        return summary

    async def _send_to_galileo(self) -> None:
        """Send the complete trace to Galileo API.

        In production, this would make an HTTP call to the Galileo API.
        The trace format follows Galileo's expected schema.
        """
        if not is_galileo_enabled():
            return

        try:
            # Build the trace payload
            payload = {
                "project": GALILEO_PROJECT,
                "log_stream": GALILEO_LOG_STREAM,
                "trace_id": self.trace.trace_id,
                "session_id": self.trace.session_id,
                "input": self.trace.user_input,
                "output": self.trace.final_output,
                "duration_ms": self.trace.duration_ms,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "spans": {
                    "llm": [
                        {
                            "model": s.model,
                            "input": json.dumps(s.input_messages)[:1000],
                            "output": s.output_content[:1000],
                            "duration_ms": s.duration_ms,
                            "input_tokens": s.input_tokens,
                            "output_tokens": s.output_tokens
                        }
                        for s in self.trace.llm_spans
                    ],
                    "tool": [
                        {
                            "name": s.name,
                            "input": json.dumps(s.input_args)[:500],
                            "output": str(s.output)[:500],
                            "duration_ms": s.duration_ms,
                            "error": s.error
                        }
                        for s in self.trace.tool_spans
                    ]
                },
                "metadata": self.trace.metadata
            }

            # In production, send to Galileo API:
            # import httpx
            # async with httpx.AsyncClient() as client:
            #     response = await client.post(
            #         "https://api.galileo.ai/v1/traces",
            #         headers={
            #             "Authorization": f"Bearer {os.environ['GALILEO_API_KEY']}",
            #             "Content-Type": "application/json"
            #         },
            #         json=payload
            #     )

            logger.debug(f"[Galileo] Would send trace: {json.dumps(payload)[:500]}...")

        except Exception as e:
            logger.error(f"[Galileo] Failed to send trace: {e}")

    def get_trace_id(self) -> str:
        """Get the trace ID for this investigation."""
        return self.trace.trace_id


def create_tracer(session_id: Optional[str] = None) -> GalileoTracer:
    """Create a new Galileo tracer for an investigation.

    Args:
        session_id: Optional session ID for grouping traces

    Returns:
        New GalileoTracer instance
    """
    return GalileoTracer(session_id)


@contextmanager
def trace_llm_call(tracer: GalileoTracer, model: str):
    """Context manager for tracing LLM calls.

    Usage:
        with trace_llm_call(tracer, "claude-sonnet-4-20250514") as timer:
            response = await llm.invoke(messages)
        timer.complete(messages, response)
    """
    start_time = time.time()
    result = {"messages": None, "response": None, "tokens": {}}

    class Timer:
        def complete(self, messages, response, input_tokens=None, output_tokens=None, tool_calls=None):
            duration_ms = (time.time() - start_time) * 1000
            tracer.log_llm_call(
                model=model,
                input_messages=messages,
                output_content=response,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=tool_calls
            )

    yield Timer()


@contextmanager
def trace_tool_call(tracer: GalileoTracer, tool_name: str, input_args: Dict[str, Any]):
    """Context manager for tracing tool calls.

    Usage:
        with trace_tool_call(tracer, "execute_sql", {"query": "..."}) as timer:
            result = execute_sql(query)
        timer.complete(result)
    """
    start_time = time.time()

    class Timer:
        def __init__(self):
            self.error = None

        def complete(self, output):
            duration_ms = (time.time() - start_time) * 1000
            tracer.log_tool_call(
                name=tool_name,
                input_args=input_args,
                output=output,
                duration_ms=duration_ms,
                error=self.error
            )

        def set_error(self, error: str):
            self.error = error

    yield Timer()


async def log_evaluation(
    trace_id: str,
    metrics: Dict[str, float]
) -> None:
    """Log evaluation metrics for a trace.

    Use this to track investigation quality:
    - accuracy: Did the agent find the correct root cause? (0-1)
    - completeness: Did it answer all aspects? (0-1)
    - efficiency: Did it use minimal tools? (0-1)
    - user_satisfaction: User rating (1-5)

    Args:
        trace_id: The trace ID to evaluate
        metrics: Dictionary of metric names to values
    """
    if not is_galileo_enabled():
        logger.debug(f"Evaluation for {trace_id}: {metrics}")
        return

    logger.info(f"[Galileo] Evaluation for {trace_id}: {metrics}")

    # In production, send to Galileo API:
    # await galileo_client.log_evaluation(trace_id, metrics)
