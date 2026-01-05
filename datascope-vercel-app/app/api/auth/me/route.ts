/**
 * Current User Route
 *
 * Returns information about the currently logged-in user.
 * Used by the frontend to check authentication status.
 */

import { NextResponse } from 'next/server'
import { getUserSession, isSessionValid } from '@/lib/auth/session'

export async function GET() {
  try {
    const session = await getUserSession()

    if (!session) {
      return NextResponse.json({
        authenticated: false,
        user: null
      })
    }

    const valid = await isSessionValid()

    if (!valid) {
      return NextResponse.json({
        authenticated: false,
        user: null,
        reason: 'session_expired'
      })
    }

    // We could call Databricks API to get user info, but for now
    // just return that they're authenticated
    return NextResponse.json({
      authenticated: true,
      user: {
        // Token is present and valid
        hasToken: true,
        expiresAt: session.expiresAt,
        hasRefreshToken: !!session.refreshToken
      }
    })
  } catch (error) {
    console.error('Get user error:', error)
    return NextResponse.json(
      { error: 'Failed to get user info' },
      { status: 500 }
    )
  }
}
