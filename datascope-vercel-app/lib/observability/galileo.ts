/**
 * Galileo AI - Observability and Evaluation for DataScope
 *
 * This module provides:
 * 1. LLM call logging - Automatic instrumentation of Anthropic API calls
 * 2. Trace management - Group related calls into investigation traces
 * 3. Evaluation hooks - Track investigation quality metrics
 *
 * Galileo enables:
 * - Debugging agent behavior (why did it choose this tool?)
 * - Measuring latency (which tools are slow?)
 * - Evaluating quality (are investigations accurate?)
 * - Guardrails (prevent hallucinations, enforce policies)
 *
 * Uses the official Galileo SDK: https://github.com/rungalileo/galileo-js
 */

import { GalileoLogger } from 'galileo'

export interface LLMSpan {
  model: string
  input: string
  output: string
  durationMs: number
  inputTokens?: number
  outputTokens?: number
  toolCalls?: Array<{
    name: string
    input: Record<string, unknown>
  }>
}

export interface ToolSpan {
  name: string
  input: Record<string, unknown>
  output: Record<string, unknown>
  durationMs: number
  error?: string
}

// Check if Galileo is enabled
const GALILEO_API_KEY = process.env.GALILEO_API_KEY
const GALILEO_PROJECT = process.env.GALILEO_PROJECT || 'datascope-vercel'
const GALILEO_LOG_STREAM = process.env.GALILEO_LOG_STREAM || 'investigations'

export const isGalileoEnabled = (): boolean => {
  return Boolean(GALILEO_API_KEY)
}

/**
 * GalileoTracer - Manages traces for investigations
 *
 * Each investigation is a trace containing:
 * - User question (input)
 * - LLM calls (reasoning)
 * - Tool calls (data access)
 * - Final answer (output)
 */
export class GalileoTracer {
  private traceId: string
  private sessionId: string
  private spans: Array<LLMSpan | ToolSpan> = []
  private startTime: number
  private logger: GalileoLogger | null = null
  private traceStarted = false

  constructor(sessionId: string) {
    this.traceId = crypto.randomUUID()
    this.sessionId = sessionId
    this.startTime = Date.now()
  }

  /**
   * Initialize the logger and start trace
   */
  private ensureLogger(): GalileoLogger | null {
    if (!isGalileoEnabled()) return null

    if (!this.logger) {
      try {
        this.logger = new GalileoLogger({
          apiKey: GALILEO_API_KEY,
          projectName: GALILEO_PROJECT,
          logStreamName: GALILEO_LOG_STREAM
        })
      } catch (error) {
        console.error('[Galileo] Failed to create logger:', error)
        return null
      }
    }

    return this.logger
  }

  /**
   * Start the trace (only once)
   */
  private startTrace(input: string): void {
    if (this.traceStarted) return

    const logger = this.ensureLogger()
    if (!logger) return

    try {
      logger.startTrace(
        input,                          // input
        undefined,                      // output (set later)
        `Investigation: ${this.sessionId}`, // name
        Date.now() * 1_000_000,         // createdAt in nanoseconds
        undefined,                      // durationNs (set later)
        { sessionId: this.sessionId },  // metadata
        ['investigation', 'datascope'] // tags
      )
      this.traceStarted = true
    } catch (error) {
      console.error('[Galileo] Failed to start trace:', error)
    }
  }

  /**
   * Log an LLM call (Anthropic API)
   */
  async logLLMCall(span: LLMSpan): Promise<void> {
    this.spans.push(span)

    console.log(`[Galileo] LLM call: ${span.model}, ${span.durationMs}ms`)

    if (!isGalileoEnabled()) return

    try {
      this.startTrace(span.input)
      const logger = this.ensureLogger()
      if (!logger) return

      // Add LLM span with correct format
      logger.addLlmSpan({
        input: [{ role: 'user', content: span.input }],
        output: { role: 'assistant', content: span.output },
        model: span.model,
        name: `LLM Call (${span.model})`,
        durationNs: span.durationMs * 1_000_000,
        numInputTokens: span.inputTokens,
        numOutputTokens: span.outputTokens,
        userMetadata: {
          inputTokens: String(span.inputTokens || 0),
          outputTokens: String(span.outputTokens || 0)
        },
        tags: ['llm', 'anthropic']
      })
    } catch (error) {
      console.error('[Galileo] Failed to log LLM call:', error)
    }
  }

  /**
   * Log a tool call (MCP)
   */
  async logToolCall(span: ToolSpan): Promise<void> {
    this.spans.push(span)

    console.log(`[Galileo] Tool call: ${span.name}, ${span.durationMs}ms`)

    if (!isGalileoEnabled()) return

    try {
      const logger = this.ensureLogger()
      if (!logger) return

      // Add tool span as a workflow span (Galileo uses workflow spans for tools)
      logger.addWorkflowSpan(
        JSON.stringify(span.input),     // input
        JSON.stringify(span.output),    // output
        `Tool: ${span.name}`,           // name
        span.durationMs * 1_000_000,    // durationNs
        Date.now() * 1_000_000,         // createdAt
        {
          toolName: span.name,
          error: span.error || ''
        },                              // userMetadata
        ['tool', 'mcp', span.name]      // tags
      )

      // Close the workflow span immediately
      logger.conclude({
        output: JSON.stringify(span.output),
        durationNs: span.durationMs * 1_000_000
      })
    } catch (error) {
      console.error('[Galileo] Failed to log tool call:', error)
    }
  }

  /**
   * Complete the trace and send to Galileo
   */
  async complete(userInput: string, agentOutput: string): Promise<void> {
    const totalDuration = Date.now() - this.startTime

    console.log(`[Galileo] Sending trace: ${this.traceId}`)
    console.log(`[Galileo]   Duration: ${totalDuration}ms`)
    console.log(`[Galileo]   Spans: ${this.spans.length}`)

    if (!isGalileoEnabled()) {
      console.log(`[Galileo] Trace complete (API key not set)`)
      return
    }

    try {
      const logger = this.ensureLogger()
      if (!logger) return

      // If trace wasn't started yet, start it now
      if (!this.traceStarted) {
        this.startTrace(userInput)
      }

      // Conclude the trace
      logger.conclude({
        output: agentOutput,
        durationNs: totalDuration * 1_000_000
      })

      // Flush to ensure data is sent
      const flushedTraces = await logger.flush()
      console.log(`[Galileo] Trace sent successfully: ${this.traceId}, flushed ${flushedTraces?.length || 0} traces`)
    } catch (error) {
      console.error('[Galileo] Failed to complete trace:', error)
    }
  }

  getTraceId(): string {
    return this.traceId
  }
}

/**
 * Create a new tracer for an investigation
 */
export function createTracer(sessionId?: string): GalileoTracer {
  return new GalileoTracer(sessionId || crypto.randomUUID())
}

/**
 * Log evaluation result
 *
 * Use this to track investigation quality:
 * - Did the agent find the root cause?
 * - Was the investigation accurate?
 * - Did it use the right tools?
 */
export async function logEvaluation(
  traceId: string,
  metrics: {
    accuracy?: number  // 0-1: Was the root cause correct?
    completeness?: number  // 0-1: Did it answer all aspects?
    efficiency?: number  // 0-1: Did it use minimal tools?
    userSatisfaction?: number  // 1-5: User rating
  }
): Promise<void> {
  if (!isGalileoEnabled()) return

  console.log(`[Galileo] Evaluation for ${traceId}:`, metrics)

  // Galileo evaluations are typically done via the dashboard or Python SDK
  // For now, we log to console - could be extended with custom metrics API
}
