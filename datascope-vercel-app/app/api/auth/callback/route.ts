/**
 * OAuth Callback Route
 *
 * Handles the callback from Databricks after user authorizes.
 * Exchanges authorization code for tokens and creates session.
 */

import { NextResponse } from 'next/server'
import { exchangeCodeForTokens } from '@/lib/auth/databricks-oauth'
import { getPKCEVerifier, getOAuthState, setUserSession } from '@/lib/auth/session'

export async function GET(request: Request) {
  const url = new URL(request.url)
  const code = url.searchParams.get('code')
  const state = url.searchParams.get('state')
  const error = url.searchParams.get('error')
  const errorDescription = url.searchParams.get('error_description')

  // Handle OAuth errors
  if (error) {
    console.error('OAuth error:', error, errorDescription)
    return NextResponse.redirect(
      `${url.origin}/?error=${encodeURIComponent(errorDescription || error)}`
    )
  }

  // Validate required parameters
  if (!code || !state) {
    return NextResponse.redirect(
      `${url.origin}/?error=${encodeURIComponent('Missing authorization code or state')}`
    )
  }

  try {
    // Verify state matches (CSRF protection)
    const savedState = await getOAuthState()
    if (!savedState || savedState !== state) {
      return NextResponse.redirect(
        `${url.origin}/?error=${encodeURIComponent('Invalid state parameter')}`
      )
    }

    // Get PKCE verifier
    const codeVerifier = await getPKCEVerifier()
    if (!codeVerifier) {
      return NextResponse.redirect(
        `${url.origin}/?error=${encodeURIComponent('Missing PKCE verifier')}`
      )
    }

    // Exchange code for tokens
    const redirectUri = `${url.origin}/api/auth/callback`
    const tokens = await exchangeCodeForTokens(code, redirectUri, codeVerifier)

    // Save session
    await setUserSession({
      accessToken: tokens.accessToken,
      refreshToken: tokens.refreshToken,
      expiresIn: tokens.expiresIn
    })

    // Redirect to home page (success)
    return NextResponse.redirect(`${url.origin}/?login=success`)
  } catch (err) {
    console.error('Token exchange error:', err)
    const message = err instanceof Error ? err.message : 'Failed to exchange code for tokens'
    return NextResponse.redirect(
      `${url.origin}/?error=${encodeURIComponent(message)}`
    )
  }
}
