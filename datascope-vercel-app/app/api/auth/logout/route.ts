/**
 * Logout Route
 *
 * Clears the user session and redirects to home page.
 */

import { NextResponse } from 'next/server'
import { clearUserSession } from '@/lib/auth/session'

export async function GET(request: Request) {
  const url = new URL(request.url)

  try {
    await clearUserSession()
    return NextResponse.redirect(`${url.origin}/?logout=success`)
  } catch (error) {
    console.error('Logout error:', error)
    return NextResponse.redirect(`${url.origin}/?error=logout_failed`)
  }
}

export async function POST() {
  try {
    await clearUserSession()
    return NextResponse.json({ success: true })
  } catch (error) {
    console.error('Logout error:', error)
    return NextResponse.json(
      { error: 'Failed to logout' },
      { status: 500 }
    )
  }
}
