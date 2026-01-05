/**
 * Chat API Route - Streaming Investigation Endpoint
 *
 * This endpoint handles investigation requests and streams responses
 * using Server-Sent Events (SSE) for real-time updates.
 *
 * Architecture:
 * - Uses Anthropic SDK directly (no Databricks proxy)
 * - Streams text and tool status updates to the client
 * - Tools call Databricks APIs for data access
 *
 * Authentication:
 * - SP OAuth token for app authentication (automatic via MCP client)
 * - User OAuth token from session for per-user data access
 */

import { NextRequest } from 'next/server'
import { createAgent } from '@/lib/agent'
import { getUserToken } from '@/lib/auth/session'

export const runtime = 'nodejs'
export const maxDuration = 60 // 60 second timeout for long investigations

export async function POST(request: NextRequest) {
  try {
    const { message, conversationId } = await request.json()

    if (!message || typeof message !== 'string') {
      return new Response(
        JSON.stringify({ error: 'Message is required' }),
        { status: 400, headers: { 'Content-Type': 'application/json' } }
      )
    }

    // Get user token from session (if authenticated)
    const userToken = await getUserToken()

    // Create agent instance with user token for per-user data access
    const agent = createAgent({
      sessionId: conversationId,
      userToken: userToken || undefined
    })

    // Create readable stream for SSE
    const stream = new ReadableStream({
      async start(controller) {
        const encoder = new TextEncoder()

        try {
          // Stream investigation results
          for await (const chunk of agent.streamInvestigation(message)) {
            controller.enqueue(encoder.encode(chunk))
          }
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Unknown error'
          controller.enqueue(
            encoder.encode(`event: error\ndata: ${JSON.stringify({ error: errorMessage })}\n\n`)
          )
        } finally {
          controller.close()
        }
      }
    })

    return new Response(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive'
      }
    })
  } catch (error) {
    console.error('Chat API error:', error)
    return new Response(
      JSON.stringify({ error: 'Internal server error' }),
      { status: 500, headers: { 'Content-Type': 'application/json' } }
    )
  }
}

// Health check endpoint
export async function GET() {
  return new Response(
    JSON.stringify({
      status: 'ok',
      service: 'datascope-vercel',
      version: '2.0.0',  // Updated for OAuth support
      timestamp: new Date().toISOString()
    }),
    { headers: { 'Content-Type': 'application/json' } }
  )
}
