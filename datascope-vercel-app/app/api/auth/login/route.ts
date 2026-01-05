/**
 * OAuth Login Route
 *
 * Initiates the Databricks OAuth flow using PKCE for security.
 * Redirects user to Databricks authorization page.
 */

import { NextResponse } from 'next/server'
import { buildAuthorizationUrl, pkce } from '@/lib/auth/databricks-oauth'
import { setPKCEVerifier, setOAuthState } from '@/lib/auth/session'

export async function GET(request: Request) {
  try {
    // Get the origin for redirect URI
    const url = new URL(request.url)
    const origin = url.origin
    const redirectUri = `${origin}/api/auth/callback`

    // Generate PKCE values
    const codeVerifier = pkce.generateCodeVerifier()
    const codeChallenge = await pkce.generateCodeChallenge(codeVerifier)
    const state = pkce.generateState()

    // Store PKCE verifier and state in cookies (for callback verification)
    await setPKCEVerifier(codeVerifier)
    await setOAuthState(state)

    // Build authorization URL
    const authUrl = buildAuthorizationUrl(redirectUri, state, codeChallenge)

    // Redirect to Databricks OAuth
    return NextResponse.redirect(authUrl)
  } catch (error) {
    console.error('Login error:', error)
    return NextResponse.json(
      { error: 'Failed to initiate login' },
      { status: 500 }
    )
  }
}
