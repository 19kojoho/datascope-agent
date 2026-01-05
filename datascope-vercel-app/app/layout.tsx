import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'DataScope - AI Data Debugging Agent',
  description: 'Investigate data quality issues with AI-powered debugging',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
