/**
 * DataScope Agent - Direct Anthropic API Integration
 *
 * This agent uses the Anthropic SDK directly instead of going through
 * Databricks External Endpoints. This solves the rate limiting and
 * network policy issues we encountered with Databricks Apps.
 *
 * Key Architecture Decisions:
 * 1. Direct Anthropic API access from Vercel (no proxy needed)
 * 2. MCP for Databricks tools (SQL, Vector Search, Unity Catalog)
 * 3. Streaming responses for better UX
 * 4. Tool execution with parallel calls where possible
 *
 * Observability: Galileo AI
 * - All LLM calls and tool calls are traced
 * - Investigations are grouped as traces
 * - Enables debugging and evaluation
 */

import Anthropic from '@anthropic-ai/sdk'
import type { MessageParam, ContentBlock, ToolUseBlock, ToolResultBlockParam, TextBlock } from '@anthropic-ai/sdk/resources/messages'
import { tools, executeTool } from './tools'
import { SYSTEM_PROMPT } from './prompts'
import { createTracer, GalileoTracer } from '../observability/galileo'

// Maximum iterations to prevent infinite loops
const MAX_ITERATIONS = 10

export interface AgentMessage {
  role: 'user' | 'assistant'
  content: string
  toolCalls?: Array<{
    name: string
    input: Record<string, unknown>
    result: string
  }>
}

export interface StreamCallbacks {
  onText?: (text: string) => void
  onToolStart?: (toolName: string, input: Record<string, unknown>) => void
  onToolEnd?: (toolName: string, result: string) => void
  onComplete?: (message: AgentMessage) => void
  onError?: (error: Error) => void
}

export interface AgentOptions {
  sessionId?: string
  /** User's Databricks OAuth token for per-user data access */
  userToken?: string
}

export class DataScopeAgent {
  private client: Anthropic
  private conversationHistory: MessageParam[] = []
  private sessionId: string
  private userToken: string | undefined
  private tracer: GalileoTracer | null = null

  constructor(options?: AgentOptions) {
    const apiKey = process.env.ANTHROPIC_API_KEY
    if (!apiKey) {
      throw new Error('ANTHROPIC_API_KEY environment variable not set')
    }
    this.client = new Anthropic({ apiKey })
    this.sessionId = options?.sessionId || crypto.randomUUID()
    this.userToken = options?.userToken
  }

  /**
   * Run an investigation with streaming output
   */
  async investigate(question: string, callbacks?: StreamCallbacks): Promise<AgentMessage> {
    // Start Galileo trace for this investigation
    this.tracer = createTracer(this.sessionId)

    // Add user message to history
    this.conversationHistory.push({
      role: 'user',
      content: question
    })

    let fullText = ''
    const toolCalls: AgentMessage['toolCalls'] = []
    let iterations = 0

    while (iterations < MAX_ITERATIONS) {
      iterations++

      // Call Claude with tools
      const llmStartTime = Date.now()
      const response = await this.client.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 4096,
        system: SYSTEM_PROMPT,
        tools: tools,
        messages: this.conversationHistory
      })
      const llmDuration = Date.now() - llmStartTime

      // Log LLM call to Galileo
      await this.tracer?.logLLMCall({
        model: 'claude-sonnet-4-20250514',
        input: JSON.stringify(this.conversationHistory.slice(-1)),
        output: JSON.stringify(response.content),
        durationMs: llmDuration,
        inputTokens: response.usage?.input_tokens,
        outputTokens: response.usage?.output_tokens
      })

      // Process response content
      const assistantContent: ContentBlock[] = []
      let hasToolUse = false

      for (const block of response.content) {
        if (block.type === 'text') {
          fullText += block.text
          callbacks?.onText?.(block.text)
          assistantContent.push(block)
        } else if (block.type === 'tool_use') {
          hasToolUse = true
          assistantContent.push(block)
        }
      }

      // Add assistant message to history
      this.conversationHistory.push({
        role: 'assistant',
        content: assistantContent
      })

      // If no tool use, we're done
      if (!hasToolUse || response.stop_reason === 'end_turn') {
        break
      }

      // Execute tools and collect results
      const toolResults: ToolResultBlockParam[] = []

      for (const block of response.content) {
        if (block.type === 'tool_use') {
          const toolBlock = block as ToolUseBlock
          callbacks?.onToolStart?.(toolBlock.name, toolBlock.input as Record<string, unknown>)

          // Execute tool with timing for Galileo
          const toolStartTime = Date.now()
          const result = await executeTool(toolBlock.name, toolBlock.input as Record<string, unknown>, this.userToken)
          const toolDuration = Date.now() - toolStartTime
          result.tool_use_id = toolBlock.id

          const resultContent = typeof result.content === 'string' ? result.content : JSON.stringify(result.content)
          callbacks?.onToolEnd?.(toolBlock.name, resultContent)

          // Log tool call to Galileo
          await this.tracer?.logToolCall({
            name: toolBlock.name,
            input: toolBlock.input as Record<string, unknown>,
            output: { content: resultContent },
            durationMs: toolDuration,
            error: result.is_error ? resultContent : undefined
          })

          toolCalls.push({
            name: toolBlock.name,
            input: toolBlock.input as Record<string, unknown>,
            result: resultContent
          })

          toolResults.push(result)
        }
      }

      // Add tool results to history
      this.conversationHistory.push({
        role: 'user',
        content: toolResults
      })
    }

    const message: AgentMessage = {
      role: 'assistant',
      content: fullText,
      toolCalls: toolCalls.length > 0 ? toolCalls : undefined
    }

    // Complete Galileo trace
    await this.tracer?.complete(question, fullText)

    callbacks?.onComplete?.(message)
    return message
  }

  /**
   * Stream investigation with Server-Sent Events format
   */
  async *streamInvestigation(question: string): AsyncGenerator<string> {
    // Start Galileo trace for this investigation
    const tracer = createTracer(this.sessionId)
    let fullText = ''

    // Add user message to history
    this.conversationHistory.push({
      role: 'user',
      content: question
    })

    let iterations = 0

    while (iterations < MAX_ITERATIONS) {
      iterations++

      // Stream response from Claude
      const stream = await this.client.messages.stream({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 4096,
        system: SYSTEM_PROMPT,
        tools: tools,
        messages: this.conversationHistory
      })

      let hasToolUse = false

      // Process streaming events for UI updates
      const streamStartTime = Date.now()
      for await (const event of stream) {
        if (event.type === 'content_block_start') {
          if (event.content_block.type === 'tool_use') {
            hasToolUse = true
            yield `event: tool_start\ndata: ${JSON.stringify({ name: event.content_block.name })}\n\n`
          }
        } else if (event.type === 'content_block_delta') {
          if (event.delta.type === 'text_delta') {
            fullText += event.delta.text
            yield `event: text\ndata: ${JSON.stringify({ text: event.delta.text })}\n\n`
          }
        }
      }
      const streamDuration = Date.now() - streamStartTime

      // Get final message for history (has complete content blocks)
      const finalMessage = await stream.finalMessage()

      // Log LLM call to Galileo
      await tracer.logLLMCall({
        model: 'claude-sonnet-4-20250514',
        input: JSON.stringify(this.conversationHistory.slice(-1)),
        output: JSON.stringify(finalMessage.content),
        durationMs: streamDuration,
        inputTokens: finalMessage.usage?.input_tokens,
        outputTokens: finalMessage.usage?.output_tokens
      })

      // Add assistant message to history using complete blocks from finalMessage
      this.conversationHistory.push({
        role: 'assistant',
        content: finalMessage.content
      })

      // If no tool use, we're done
      // Note: We must execute tools even if stop_reason is 'end_turn' to avoid
      // tool_use blocks without corresponding tool_result blocks
      if (!hasToolUse) {
        // Complete Galileo trace before finishing
        await tracer.complete(question, fullText)
        yield `event: done\ndata: {}\n\n`
        return // Use return instead of break to avoid duplicate done
      }

      // Execute tools
      const toolResults: ToolResultBlockParam[] = []

      for (const block of finalMessage.content) {
        if (block.type === 'tool_use') {
          const toolBlock = block as ToolUseBlock

          yield `event: tool_executing\ndata: ${JSON.stringify({ name: toolBlock.name, input: toolBlock.input })}\n\n`

          const toolStartTime = Date.now()
          const result = await executeTool(toolBlock.name, toolBlock.input as Record<string, unknown>, this.userToken)
          const toolDuration = Date.now() - toolStartTime
          result.tool_use_id = toolBlock.id

          const resultContent = typeof result.content === 'string' ? result.content : JSON.stringify(result.content)

          // Log tool call to Galileo
          await tracer.logToolCall({
            name: toolBlock.name,
            input: toolBlock.input as Record<string, unknown>,
            output: { content: resultContent },
            durationMs: toolDuration,
            error: result.is_error ? resultContent : undefined
          })

          yield `event: tool_result\ndata: ${JSON.stringify({ name: toolBlock.name, result: resultContent.slice(0, 500) })}\n\n`

          toolResults.push(result)
        }
      }

      // Add tool results to history
      this.conversationHistory.push({
        role: 'user',
        content: toolResults
      })

      // Continue looping - the next iteration will get Claude's response to tool results
      // The loop will break when hasToolUse is false (Claude responds with just text)
    }

    // Complete Galileo trace
    await tracer.complete(question, fullText)

    // If we hit MAX_ITERATIONS, still send done
    yield `event: done\ndata: {}\n\n`
  }

  /**
   * Clear conversation history
   */
  clearHistory(): void {
    this.conversationHistory = []
  }
}

// Factory function
export function createAgent(options?: AgentOptions): DataScopeAgent {
  return new DataScopeAgent(options)
}
