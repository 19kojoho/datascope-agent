'use client'

import { useState, useRef, useEffect } from 'react'
import { ChatMessage } from './ChatMessage'
import { ChatInput } from './ChatInput'
import { AuthButton, AuthStatusBanner } from './AuthButton'

interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  toolCalls?: Array<{
    name: string
    status: 'running' | 'complete' | 'error'
    result?: string
  }>
}

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      role: 'system',
      content: 'Welcome to DataScope! Ask me about data quality issues in your Databricks tables.\n\nExample questions:\n- "Why do some customers have NULL churn_risk?"\n- "Why does ARR show $125M but Finance reports $165M?"\n- "Why is customer XYZ marked as churn when they logged in yesterday?"'
    }
  ])
  const [isLoading, setIsLoading] = useState(false)
  const [currentToolCalls, setCurrentToolCalls] = useState<Message['toolCalls']>([])
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentToolCalls])

  const handleSubmit = async (message: string) => {
    // Add user message
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: message
    }
    setMessages(prev => [...prev, userMessage])
    setIsLoading(true)
    setCurrentToolCalls([])

    // Create placeholder for assistant response
    const assistantId = (Date.now() + 1).toString()
    setMessages(prev => [...prev, {
      id: assistantId,
      role: 'assistant',
      content: '',
      toolCalls: []
    }])

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message })
      })

      if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`)
      }

      const reader = response.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''
      let fullText = ''
      const toolCallsMap = new Map<string, Message['toolCalls'][0]>()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Parse SSE events
        const lines = buffer.split('\n')
        buffer = lines.pop() || '' // Keep incomplete line in buffer

        for (let i = 0; i < lines.length; i++) {
          const line = lines[i]
          if (line.startsWith('event: ')) {
            const eventType = line.slice(7)
            const dataLine = lines[i + 1]
            if (dataLine?.startsWith('data: ')) {
              const data = JSON.parse(dataLine.slice(6))

              switch (eventType) {
                case 'text':
                  fullText += data.text
                  setMessages(prev => prev.map(m =>
                    m.id === assistantId ? { ...m, content: fullText } : m
                  ))
                  break

                case 'tool_start':
                  toolCallsMap.set(data.name, {
                    name: data.name,
                    status: 'running'
                  })
                  setCurrentToolCalls(Array.from(toolCallsMap.values()))
                  setMessages(prev => prev.map(m =>
                    m.id === assistantId ? { ...m, toolCalls: Array.from(toolCallsMap.values()) } : m
                  ))
                  break

                case 'tool_executing':
                  toolCallsMap.set(data.name, {
                    name: data.name,
                    status: 'running'
                  })
                  setCurrentToolCalls(Array.from(toolCallsMap.values()))
                  break

                case 'tool_result':
                  toolCallsMap.set(data.name, {
                    name: data.name,
                    status: 'complete',
                    result: data.result
                  })
                  setCurrentToolCalls(Array.from(toolCallsMap.values()))
                  setMessages(prev => prev.map(m =>
                    m.id === assistantId ? { ...m, toolCalls: Array.from(toolCallsMap.values()) } : m
                  ))
                  break

                case 'error':
                  setMessages(prev => prev.map(m =>
                    m.id === assistantId ? { ...m, content: `Error: ${data.error}` } : m
                  ))
                  break

                case 'done':
                  // Investigation complete
                  break
              }
              i++ // Skip the data line we just processed
            }
          }
        }
      }
    } catch (error) {
      console.error('Chat error:', error)
      setMessages(prev => prev.map(m =>
        m.id === (Date.now() + 1).toString()
          ? { ...m, content: `Error: ${error instanceof Error ? error.message : 'Unknown error'}` }
          : m
      ))
    } finally {
      setIsLoading(false)
      setCurrentToolCalls([])
    }
  }

  return (
    <div className="flex flex-col h-screen bg-gray-950">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
        <div>
          <h1 className="text-xl font-bold text-white">DataScope</h1>
          <p className="text-sm text-gray-400">AI Data Debugging Agent</p>
        </div>
        <AuthButton />
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <AuthStatusBanner />
        {messages.map(message => (
          <ChatMessage
            key={message.id}
            role={message.role}
            content={message.content}
            toolCalls={message.toolCalls}
          />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <ChatInput
        onSubmit={handleSubmit}
        disabled={isLoading}
        placeholder="Ask about a data quality issue..."
      />
    </div>
  )
}
