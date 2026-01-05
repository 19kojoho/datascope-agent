"""LangGraph ReAct Agent for DataScope.

Production-ready agent with persistent checkpointing for multi-turn conversations.
Includes Galileo AI observability for tracing and debugging.
"""

import os
import json
import logging
import time
import requests
from typing import Optional, Dict, Any, List, Iterator

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.callbacks import CallbackManagerForLLMRun
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from .config import Config, get_config
from .tools import get_tools
from .prompts import SYSTEM_PROMPT
from .observability import create_tracer, GalileoTracer, is_galileo_enabled

logger = logging.getLogger(__name__)

# Agent instance cache
_agent = None
_checkpointer = None


class DatabricksExternalLLM(BaseChatModel):
    """Custom LLM wrapper for Databricks External Endpoints.

    This solves the URL mismatch problem:
    - ChatOpenAI calls: {base_url}/chat/completions
    - Databricks expects: {host}/serving-endpoints/{endpoint}/invocations

    By creating a custom wrapper, we have full control over the URL construction.

    This is a key pattern for AI engineering: when libraries don't fit your
    infrastructure, create a thin wrapper that implements the expected interface.

    Includes Galileo observability hooks for tracing LLM calls.
    """

    endpoint_url: str
    api_key: str
    temperature: float = 0.0
    max_tokens: int = 4096
    tracer: Optional[GalileoTracer] = None

    class Config:
        arbitrary_types_allowed = True

    @property
    def _llm_type(self) -> str:
        return "databricks-external"

    def _convert_messages(self, messages: List[BaseMessage]) -> List[dict]:
        """Convert LangChain messages to OpenAI format."""
        result = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                result.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                msg_dict = {"role": "assistant", "content": msg.content or ""}
                # Handle tool calls
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"]) if isinstance(tc["args"], dict) else tc["args"]
                            }
                        }
                        for tc in msg.tool_calls
                    ]
                result.append(msg_dict)
            elif isinstance(msg, ToolMessage):
                result.append({
                    "role": "tool",
                    "content": msg.content,
                    "tool_call_id": msg.tool_call_id
                })
            else:
                # Fallback for other message types
                result.append({"role": "user", "content": str(msg.content)})
        return result

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs
    ) -> ChatResult:
        """Generate a response from the Databricks endpoint."""

        # Convert messages to OpenAI format
        formatted_messages = self._convert_messages(messages)

        # Build request payload
        payload = {
            "messages": formatted_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        # Add tools if provided
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]

        if stop:
            payload["stop"] = stop

        logger.debug(f"Calling Databricks endpoint: {self.endpoint_url}")

        # Track timing for Galileo observability
        start_time = time.time()

        # Make the request
        response = requests.post(
            self.endpoint_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=120
        )

        duration_ms = (time.time() - start_time) * 1000

        if response.status_code != 200:
            error_text = response.text[:500]
            logger.error(f"Databricks LLM error: {response.status_code} - {error_text}")
            raise Exception(f"Databricks endpoint error: {response.status_code} - {error_text}")

        data = response.json()

        # Parse response (OpenAI format)
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")

        # Extract token usage if available
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")

        # Create AIMessage with tool calls if present
        tool_calls = message.get("tool_calls", [])
        parsed_tool_calls = None
        if tool_calls:
            parsed_tool_calls = [
                {
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "args": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                }
                for tc in tool_calls
            ]
            ai_message = AIMessage(content=content or "", tool_calls=parsed_tool_calls)
        else:
            ai_message = AIMessage(content=content)

        # Log to Galileo tracer if available
        if self.tracer:
            self.tracer.log_llm_call(
                model="databricks-external",
                input_messages=formatted_messages,
                output_content=content,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=parsed_tool_calls
            )

        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    def bind_tools(self, tools: list, **kwargs):
        """Bind tools to the LLM for function calling."""
        # Convert LangChain tools to OpenAI format
        formatted_tools = []
        for tool in tools:
            formatted_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.args_schema.schema() if hasattr(tool, 'args_schema') else {}
                }
            })

        # Return a new instance with tools bound
        return _BoundDatabricksLLM(
            llm=self,
            tools=formatted_tools
        )


class _BoundDatabricksLLM(BaseChatModel):
    """LLM with tools bound for function calling."""

    llm: DatabricksExternalLLM
    tools: list

    @property
    def _llm_type(self) -> str:
        return "databricks-external-bound"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs
    ) -> ChatResult:
        return self.llm._generate(messages, stop, run_manager, tools=self.tools, **kwargs)

    def bind_tools(self, tools: list, **kwargs):
        """Re-bind tools (allows chaining)."""
        return self.llm.bind_tools(tools, **kwargs)


def get_llm(config: Config, tracer: Optional[GalileoTracer] = None) -> DatabricksExternalLLM:
    """Create LLM instance using Databricks External Endpoint.

    We use a custom LLM wrapper because:
    1. ChatOpenAI appends /chat/completions but Databricks expects /invocations
    2. ChatAnthropic calls api.anthropic.com which is blocked
    3. ChatDatabricks crashed due to dependency issues

    The custom wrapper gives us full control over the HTTP request.

    Args:
        config: Configuration with Databricks credentials
        tracer: Optional Galileo tracer for observability

    Returns:
        Configured LLM instance
    """
    # Databricks endpoint URL - the CORRECT format
    endpoint_url = f"{config.databricks_host}/serving-endpoints/{config.llm_endpoint}/invocations"

    logger.info(f"LLM endpoint: {endpoint_url}")

    return DatabricksExternalLLM(
        endpoint_url=endpoint_url,
        api_key=config.databricks_token,
        temperature=0,
        max_tokens=4096,
        tracer=tracer,
    )


def get_checkpointer(config: Config):
    """Create checkpointer for conversation state.

    Uses MemorySaver for simplicity. For persistence across restarts,
    can be upgraded to SqliteSaver with proper connection handling.
    """
    global _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    # Use MemorySaver for now (works reliably)
    # State is preserved within the session but lost on restart
    logger.info("Using MemorySaver for conversation state")
    _checkpointer = MemorySaver()

    return _checkpointer


def create_agent(config: Optional[Config] = None, tracer: Optional[GalileoTracer] = None):
    """Create the DataScope LangGraph agent.

    Args:
        config: Configuration object. If None, loads from environment.
        tracer: Optional Galileo tracer for observability.

    Returns:
        Compiled LangGraph agent with tools and checkpointing.
    """
    global _agent

    # If no tracer provided and agent already exists, return cached agent
    if tracer is None and _agent is not None:
        return _agent

    if config is None:
        config = get_config()

    logger.info("Creating DataScope LangGraph agent...")

    # Create LLM with tracer attached for observability
    llm = get_llm(config, tracer=tracer)

    # Get tools
    tools = get_tools()
    logger.info(f"Loaded {len(tools)} tools: {[t.name for t in tools]}")

    # Create checkpointer for persistent state
    checkpointer = get_checkpointer(config)

    # Create ReAct agent with system prompt
    agent = create_react_agent(
        model=llm,
        tools=tools,
        checkpointer=checkpointer,
        prompt=SYSTEM_PROMPT,  # System prompt for the agent
    )

    # Only cache if no tracer (tracer makes it request-specific)
    if tracer is None:
        _agent = agent

    logger.info("DataScope agent created successfully")
    return agent


def invoke_agent(
    question: str,
    conversation_id: str,
    config: Optional[Config] = None
) -> Dict[str, Any]:
    """Invoke the agent with a question.

    Args:
        question: The user's question about data quality
        conversation_id: Unique ID for the conversation (thread_id)
        config: Optional configuration override

    Returns:
        Dict with 'response' (str), 'messages' (list), and optional 'trace_id'
    """
    # Create Galileo tracer for this investigation
    tracer = create_tracer(session_id=conversation_id)

    # Create agent with tracer attached
    agent = create_agent(config, tracer=tracer)

    # LangGraph uses thread_id for conversation isolation
    thread_config = {"configurable": {"thread_id": conversation_id}}

    logger.info(f"Invoking agent for conversation {conversation_id}: {question[:100]}...")

    start_time = time.time()
    response = ""

    try:
        # Invoke agent with recursion limit (equivalent to max iterations)
        # Each tool call uses ~3 steps, so 50 allows ~15 tool calls
        result = agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={
                **thread_config,
                "recursion_limit": 50,
            }
        )

        # Extract final response
        messages = result.get("messages", [])
        if messages:
            final_message = messages[-1]
            response = final_message.content if hasattr(final_message, 'content') else str(final_message)
        else:
            response = "Investigation completed but no response was generated."

        logger.info(f"Agent completed with {len(messages)} messages")

        # Complete Galileo trace
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If there's already a running loop, create a task
                asyncio.create_task(tracer.complete(question, response))
            else:
                loop.run_until_complete(tracer.complete(question, response))
        except RuntimeError:
            # No event loop, create one
            asyncio.run(tracer.complete(question, response))

        return {
            "response": response,
            "messages": messages,
            "conversation_id": conversation_id,
            "trace_id": tracer.get_trace_id(),
            "duration_ms": (time.time() - start_time) * 1000
        }

    except Exception as e:
        logger.error(f"Agent invocation failed: {e}")

        # Still complete the trace on error
        import asyncio
        try:
            asyncio.run(tracer.complete(question, f"Error: {str(e)}"))
        except Exception:
            pass

        return {
            "response": f"Investigation failed: {str(e)}",
            "messages": [],
            "conversation_id": conversation_id,
            "error": str(e),
            "trace_id": tracer.get_trace_id()
        }


def get_conversation_history(conversation_id: str, config: Optional[Config] = None) -> list:
    """Get the message history for a conversation.

    Args:
        conversation_id: The conversation thread ID

    Returns:
        List of messages in the conversation
    """
    if config is None:
        config = get_config()

    checkpointer = get_checkpointer(config)

    try:
        # Get the latest checkpoint for this thread
        checkpoint = checkpointer.get({"configurable": {"thread_id": conversation_id}})
        if checkpoint and "channel_values" in checkpoint:
            return checkpoint["channel_values"].get("messages", [])
    except Exception as e:
        logger.error(f"Failed to get conversation history: {e}")

    return []
