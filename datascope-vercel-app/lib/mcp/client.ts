/**
 * MCP Client for DataScope Tools
 *
 * This client communicates with the MCP server deployed on Databricks Apps.
 *
 * Authentication:
 * - User logs in via Databricks OAuth (PKCE flow)
 * - User's token is passed in Authorization header
 * - Databricks Apps validates the token at platform level
 * - MCP server receives authenticated requests
 *
 * MCP Protocol: JSON-RPC 2.0 over HTTP
 * - initialize: Handshake with server
 * - tools/list: Discover available tools
 * - tools/call: Execute a tool
 */

export interface MCPRequest {
  jsonrpc: '2.0'
  id: number
  method: string
  params?: Record<string, unknown>
}

export interface MCPResponse {
  jsonrpc: '2.0'
  id: number
  result?: Record<string, unknown>
  error?: {
    code: number
    message: string
    data?: unknown
  }
}

export interface MCPTool {
  name: string
  description: string
  inputSchema: {
    type: 'object'
    properties: Record<string, unknown>
    required?: string[]
  }
}

export interface MCPToolResult {
  content: Array<{
    type: 'text' | 'image' | 'resource'
    text?: string
    data?: string
    mimeType?: string
  }>
  isError?: boolean
}

let requestId = 0
const getNextId = () => ++requestId

export interface MCPClientOptions {
  /** MCP server URL */
  serverUrl: string
  /** User's Databricks OAuth token (required for authenticated requests) */
  userToken?: string
}

export class MCPClient {
  private serverUrl: string
  private userToken: string | null
  private initialized = false
  private toolsCache: MCPTool[] | null = null

  constructor(options: MCPClientOptions) {
    this.serverUrl = options.serverUrl.replace(/\/$/, '')
    this.userToken = options.userToken || null
  }

  private async sendRequest(method: string, params?: Record<string, unknown>): Promise<MCPResponse['result']> {
    const request: MCPRequest = {
      jsonrpc: '2.0',
      id: getNextId(),
      method,
      params: params || {}
    }

    const headers: Record<string, string> = {
      'Content-Type': 'application/json'
    }

    // User's OAuth token for authentication
    // Databricks Apps validates this at platform level
    if (this.userToken) {
      headers['Authorization'] = `Bearer ${this.userToken}`
    }

    const response = await fetch(`${this.serverUrl}/mcp`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request)
    })

    if (!response.ok) {
      const text = await response.text()

      // Check for auth errors
      if (response.status === 401 || response.status === 302) {
        throw new Error('Authentication required. Please sign in with Databricks.')
      }
      if (response.status === 403) {
        throw new Error('Access denied. You may not have permission to access this resource.')
      }

      throw new Error(`MCP server error: ${response.status} - ${text.slice(0, 200)}`)
    }

    const result: MCPResponse = await response.json()

    if (result.error) {
      throw new Error(`MCP error ${result.error.code}: ${result.error.message}`)
    }

    return result.result || {}
  }

  async initialize(): Promise<void> {
    if (this.initialized) return

    await this.sendRequest('initialize', {
      protocolVersion: '2024-11-05',
      clientInfo: {
        name: 'datascope-vercel',
        version: '2.1.0'  // Updated for Databricks App OAuth
      },
      capabilities: {}
    })

    this.initialized = true

    // Send initialized notification (fire and forget)
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (this.userToken) {
      headers['Authorization'] = `Bearer ${this.userToken}`
    }

    fetch(`${this.serverUrl}/mcp`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        jsonrpc: '2.0',
        method: 'notifications/initialized',
        params: {}
      })
    }).catch(() => {}) // Ignore errors for notifications
  }

  async listTools(): Promise<MCPTool[]> {
    if (this.toolsCache) return this.toolsCache

    await this.initialize()
    const result = await this.sendRequest('tools/list')
    this.toolsCache = (result as { tools?: MCPTool[] }).tools || []
    return this.toolsCache
  }

  async callTool(toolName: string, args?: Record<string, unknown>): Promise<unknown> {
    await this.initialize()

    const result = await this.sendRequest('tools/call', {
      name: toolName,
      arguments: args || {}
    })

    const toolResult = result as MCPToolResult

    // Extract text content from MCP response
    const content = toolResult.content || []
    for (const item of content) {
      if (item.type === 'text' && item.text) {
        try {
          return JSON.parse(item.text)
        } catch {
          return { text: item.text }
        }
      }
    }

    return {}
  }

  /**
   * Check if client has user token
   */
  hasUserToken(): boolean {
    return !!this.userToken
  }
}

/**
 * Create MCP client with environment configuration
 *
 * @param userToken - User's Databricks OAuth token from session
 */
export function createMCPClient(userToken?: string): MCPClient {
  const serverUrl = process.env.MCP_SERVER_URL

  if (!serverUrl) {
    throw new Error('MCP_SERVER_URL environment variable not set')
  }

  // Use provided user token, or fall back to PAT token for testing
  const token = userToken || process.env.DATABRICKS_TOKEN

  if (!token) {
    console.warn('Warning: No token available. User must sign in with Databricks.')
  }

  return new MCPClient({
    serverUrl,
    userToken: token
  })
}
