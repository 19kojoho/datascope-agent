'use client'

import { ReactNode } from 'react'

interface ChatMessageProps {
  role: 'user' | 'assistant' | 'system'
  content: string | ReactNode
  toolCalls?: Array<{
    name: string
    status: 'running' | 'complete' | 'error'
    result?: string
  }>
}

export function ChatMessage({ role, content, toolCalls }: ChatMessageProps) {
  const isUser = role === 'user'
  const isSystem = role === 'system'

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div
        className={`max-w-[85%] rounded-lg px-4 py-3 ${
          isUser
            ? 'bg-blue-600 text-white'
            : isSystem
            ? 'bg-yellow-600/20 text-yellow-200 border border-yellow-600/30'
            : 'bg-gray-800 text-gray-100'
        }`}
      >
        {/* Role indicator */}
        <div className={`text-xs mb-1 ${isUser ? 'text-blue-200' : 'text-gray-400'}`}>
          {isUser ? 'You' : isSystem ? 'System' : 'DataScope'}
        </div>

        {/* Tool calls indicator */}
        {toolCalls && toolCalls.length > 0 && (
          <div className="mb-2 space-y-1">
            {toolCalls.map((tool, idx) => (
              <div
                key={idx}
                className={`text-xs px-2 py-1 rounded flex items-center gap-2 ${
                  tool.status === 'running'
                    ? 'bg-blue-500/20 text-blue-300'
                    : tool.status === 'error'
                    ? 'bg-red-500/20 text-red-300'
                    : 'bg-green-500/20 text-green-300'
                }`}
              >
                {tool.status === 'running' && (
                  <span className="inline-block w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
                )}
                {tool.status === 'complete' && <span>&#10003;</span>}
                {tool.status === 'error' && <span>&#10007;</span>}
                <span className="font-mono">{tool.name}</span>
              </div>
            ))}
          </div>
        )}

        {/* Message content */}
        <div className="prose prose-invert prose-sm max-w-none">
          {typeof content === 'string' ? (
            <div className="whitespace-pre-wrap">{content}</div>
          ) : (
            content
          )}
        </div>
      </div>
    </div>
  )
}
