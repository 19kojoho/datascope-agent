/**
 * DataScope Agent Tools
 *
 * All tools are executed via the MCP gateway server.
 * The Vercel app only needs the MCP server URL and auth token.
 * All other secrets (Databricks, GitHub) stay in the MCP server.
 */

import type { Tool, ToolResultBlockParam } from '@anthropic-ai/sdk/resources/messages'
import { createMCPClient } from '../mcp/client'

// Tool definitions for Claude (matches MCP server tool schemas)
export const tools: Tool[] = [
  {
    name: 'search_patterns',
    description: `Search for similar past data quality issues using Vector Search.
Use this FIRST before investigating to get context on common patterns and suggested SQL queries.

Args:
  query: Description of the data issue (e.g., "NULL churn_risk values")

Returns:
  Similar patterns with symptoms, root causes, and suggested SQL`,
    input_schema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Description of the data issue to find similar patterns for'
        }
      },
      required: ['query']
    }
  },
  {
    name: 'execute_sql',
    description: `Execute a SQL query against Databricks SQL Warehouse.

Use this to:
- Count affected records: SELECT COUNT(*) FROM table WHERE condition
- Sample data: SELECT * FROM table WHERE condition LIMIT 10
- Compare values between tables or layers (bronze/silver/gold)
- Check for NULL values or duplicates
- Get table schema: DESCRIBE novatech.gold.table_name
- List tables: SHOW TABLES IN novatech.gold

Only SELECT, DESCRIBE, and SHOW queries are allowed (read-only).`,
    input_schema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'SQL query to execute'
        }
      },
      required: ['query']
    }
  },
  {
    name: 'get_table_schema',
    description: `Get the schema of a table from Unity Catalog.

Returns column names, types, and comments for the specified table.`,
    input_schema: {
      type: 'object',
      properties: {
        table_name: {
          type: 'string',
          description: 'Fully qualified table name (e.g., novatech.gold.churn_predictions)'
        }
      },
      required: ['table_name']
    }
  },
  {
    name: 'search_code',
    description: `Search SQL transformation code in the GitHub repository.

Use this to find the transformation logic that creates a specific column
or table. This helps identify WHERE the bug is in the code.

Searches .sql files in the repository for the given term.`,
    input_schema: {
      type: 'object',
      properties: {
        term: {
          type: 'string',
          description: 'Search term (e.g., column name like "churn_risk")'
        }
      },
      required: ['term']
    }
  },
  {
    name: 'get_file',
    description: `Get the full contents of a file from the GitHub repository.

Use this after search_code to see the full transformation logic.
Returns file contents with line numbers.`,
    input_schema: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: "Path to file (e.g., 'sql/gold/churn_predictions.sql')"
        }
      },
      required: ['file_path']
    }
  },
  {
    name: 'list_sql_files',
    description: `List SQL transformation files in the repository.

Use this to discover what transformation files exist before searching.`,
    input_schema: {
      type: 'object',
      properties: {
        directory: {
          type: 'string',
          description: "Directory to list (default: 'sql')"
        }
      },
      required: []
    }
  }
]

/**
 * Execute a tool via the MCP gateway server
 *
 * @param toolName - Name of the tool to execute
 * @param input - Tool input parameters
 * @param userToken - User's Databricks OAuth token for per-user data access
 */
export async function executeTool(
  toolName: string,
  input: Record<string, unknown>,
  userToken?: string
): Promise<ToolResultBlockParam> {
  try {
    const mcpClient = createMCPClient(userToken)

    // Map tool names to MCP tool names (handle any naming differences)
    const mcpToolName = toolName === 'search_code' ? 'search_code' : toolName

    // Map input parameter names if needed
    let mcpArgs = input
    if (toolName === 'search_code' && 'term' in input) {
      mcpArgs = { query: input.term }
    }

    // Call the MCP server
    const result = await mcpClient.callTool(mcpToolName, mcpArgs)

    // Format result for display
    const formattedResult = formatToolResult(toolName, result as Record<string, unknown>)

    return {
      type: 'tool_result',
      tool_use_id: '', // Will be set by caller
      content: formattedResult
    }
  } catch (error) {
    return {
      type: 'tool_result',
      tool_use_id: '',
      content: `Error: ${String(error)}`,
      is_error: true
    }
  }
}

/**
 * Format tool results for display
 */
function formatToolResult(toolName: string, result: Record<string, unknown>): string {
  // Handle errors
  if (result.error) {
    return `Error: ${result.error}`
  }

  switch (toolName) {
    case 'execute_sql':
    case 'get_table_schema':
      return formatSQLResult(result)

    case 'search_patterns':
      return formatPatternResult(result)

    case 'search_code':
      return formatCodeSearchResult(result)

    case 'get_file':
      return formatFileResult(result)

    case 'list_sql_files':
      return formatFileListResult(result)

    default:
      return JSON.stringify(result, null, 2)
  }
}

function formatSQLResult(result: Record<string, unknown>): string {
  const columns = result.columns as string[] || []
  const rows = result.rows as unknown[][] || []
  const rowCount = result.row_count as number || rows.length

  if (rows.length === 0) {
    return 'Query executed successfully but returned no results.'
  }

  // Format as markdown table
  const header = '| ' + columns.join(' | ') + ' |'
  const separator = '| ' + columns.map(() => '---').join(' | ') + ' |'
  const body = rows.map(row =>
    '| ' + row.map(v => v === null ? 'NULL' : String(v)).join(' | ') + ' |'
  ).join('\n')

  const footer = result.truncated
    ? `\n*Showing ${rows.length} of ${rowCount} rows*`
    : ''

  return `\`\`\`\n${header}\n${separator}\n${body}\n\`\`\`${footer}`
}

function formatPatternResult(result: Record<string, unknown>): string {
  const patterns = result.patterns as Array<Record<string, string>> || []

  if (patterns.length === 0) {
    return 'No similar patterns found in history. This may be a new type of issue.'
  }

  const lines = ['**Similar Past Issues Found:**\n']

  for (const pattern of patterns) {
    lines.push(`### ${pattern.pattern_id}: ${pattern.title}`)
    lines.push(`**Symptoms:** ${(pattern.symptoms || '').slice(0, 300)}...`)
    lines.push(`**Root Cause:** ${pattern.root_cause}`)
    lines.push(`**Resolution:** ${pattern.resolution}`)
    if (pattern.investigation_sql) {
      lines.push(`**Suggested SQL:** \`${pattern.investigation_sql.slice(0, 150)}...\``)
    }
    lines.push('')
  }

  return lines.join('\n')
}

function formatCodeSearchResult(result: Record<string, unknown>): string {
  const filesMatched = result.files_matched as number || 0
  const results = result.results as Array<Record<string, unknown>> || []

  if (filesMatched === 0) {
    return `No code found matching the search term. Try different search terms.`
  }

  const lines = [`**Code Search Results:**`]
  lines.push(`*Found matches in ${filesMatched} files*\n`)

  for (const r of results.slice(0, 3)) {
    lines.push(`### File: \`${r.file}\``)
    const matches = r.matches as Array<Record<string, string>> || []
    for (const m of matches.slice(0, 2)) {
      lines.push(`\`\`\`sql\n${m.fragment}\n\`\`\``)
    }
    lines.push('')
  }

  return lines.join('\n')
}

function formatFileResult(result: Record<string, unknown>): string {
  const filePath = result.file_path as string || ''
  const content = result.content as string || ''
  const lineCount = result.line_count as number || 0
  const htmlUrl = result.html_url as string || ''

  // Add line numbers
  const numbered = content.split('\n').map((line, i) =>
    `${String(i + 1).padStart(4)} | ${line}`
  ).join('\n')

  const lines = [`**File: \`${filePath}\`** (${lineCount} lines)`]
  if (htmlUrl) {
    lines.push(`*GitHub: ${htmlUrl}*`)
  }
  lines.push('')
  lines.push(`\`\`\`sql\n${numbered}\n\`\`\``)

  return lines.join('\n')
}

function formatFileListResult(result: Record<string, unknown>): string {
  const total = result.total_files as number || 0
  const filesByDir = result.files_by_directory as Record<string, string[]> || {}

  const lines = [`**SQL Transformation Files** (${total} files)\n`]

  for (const [dirName, files] of Object.entries(filesByDir).sort()) {
    lines.push(`### \`${dirName}/\``)
    for (const f of files) {
      lines.push(`- ${f}`)
    }
    lines.push('')
  }

  return lines.join('\n')
}
