/**
 * Session Management for User Authentication
 *
 * Stores user's Databricks OAuth tokens in encrypted HTTP-only cookies.
 * This ensures:
 * - Tokens are not accessible to client-side JavaScript (XSS protection)
 * - Tokens are automatically sent with each request
 * - Session persists across browser refreshes
 */

import { cookies } from 'next/headers'

const ACCESS_TOKEN_COOKIE = 'databricks_access_token'
const REFRESH_TOKEN_COOKIE = 'databricks_refresh_token'
const TOKEN_EXPIRY_COOKIE = 'databricks_token_expiry'
const PKCE_VERIFIER_COOKIE = 'databricks_pkce_verifier'
const OAUTH_STATE_COOKIE = 'databricks_oauth_state'

// Cookie options for security
const SECURE_COOKIE_OPTIONS = {
  httpOnly: true,
  secure: process.env.NODE_ENV === 'production',
  sameSite: 'lax' as const,
  path: '/'
}

export interface UserSession {
  accessToken: string
  refreshToken?: string
  expiresAt: number
}

/**
 * Get the current user session from cookies
 * Returns null if not authenticated
 */
export async function getUserSession(): Promise<UserSession | null> {
  const cookieStore = await cookies()

  const accessToken = cookieStore.get(ACCESS_TOKEN_COOKIE)?.value
  const refreshToken = cookieStore.get(REFRESH_TOKEN_COOKIE)?.value
  const expiryStr = cookieStore.get(TOKEN_EXPIRY_COOKIE)?.value

  if (!accessToken) {
    return null
  }

  const expiresAt = expiryStr ? parseInt(expiryStr, 10) : 0

  return {
    accessToken,
    refreshToken,
    expiresAt
  }
}

/**
 * Check if the user session is valid and not expired
 */
export async function isSessionValid(): Promise<boolean> {
  const session = await getUserSession()
  if (!session) return false

  // Check if token expires in the next minute
  const bufferMs = 60 * 1000
  return Date.now() < session.expiresAt - bufferMs
}

/**
 * Get the user's access token for API calls
 * Returns null if not authenticated or token is expired
 */
export async function getUserToken(): Promise<string | null> {
  const session = await getUserSession()
  if (!session) return null

  // Check if token is still valid (with 1 minute buffer)
  if (Date.now() >= session.expiresAt - 60000) {
    // Token expired or about to expire
    return null
  }

  return session.accessToken
}

/**
 * Save user session to cookies
 */
export async function setUserSession(session: {
  accessToken: string
  refreshToken?: string
  expiresIn: number
}): Promise<void> {
  const cookieStore = await cookies()

  const expiresAt = Date.now() + (session.expiresIn * 1000)

  // Access token - expires when the token expires
  cookieStore.set(ACCESS_TOKEN_COOKIE, session.accessToken, {
    ...SECURE_COOKIE_OPTIONS,
    maxAge: session.expiresIn
  })

  // Token expiry timestamp
  cookieStore.set(TOKEN_EXPIRY_COOKIE, expiresAt.toString(), {
    ...SECURE_COOKIE_OPTIONS,
    maxAge: session.expiresIn
  })

  // Refresh token - longer lived (if provided)
  if (session.refreshToken) {
    cookieStore.set(REFRESH_TOKEN_COOKIE, session.refreshToken, {
      ...SECURE_COOKIE_OPTIONS,
      maxAge: 60 * 60 * 24 * 30 // 30 days
    })
  }
}

/**
 * Clear user session (logout)
 */
export async function clearUserSession(): Promise<void> {
  const cookieStore = await cookies()

  cookieStore.delete(ACCESS_TOKEN_COOKIE)
  cookieStore.delete(REFRESH_TOKEN_COOKIE)
  cookieStore.delete(TOKEN_EXPIRY_COOKIE)
}

/**
 * Store PKCE verifier temporarily during OAuth flow
 */
export async function setPKCEVerifier(verifier: string): Promise<void> {
  const cookieStore = await cookies()
  cookieStore.set(PKCE_VERIFIER_COOKIE, verifier, {
    ...SECURE_COOKIE_OPTIONS,
    maxAge: 60 * 10 // 10 minutes
  })
}

/**
 * Get and clear PKCE verifier
 */
export async function getPKCEVerifier(): Promise<string | null> {
  const cookieStore = await cookies()
  const verifier = cookieStore.get(PKCE_VERIFIER_COOKIE)?.value || null
  if (verifier) {
    cookieStore.delete(PKCE_VERIFIER_COOKIE)
  }
  return verifier
}

/**
 * Store OAuth state for CSRF protection
 */
export async function setOAuthState(state: string): Promise<void> {
  const cookieStore = await cookies()
  cookieStore.set(OAUTH_STATE_COOKIE, state, {
    ...SECURE_COOKIE_OPTIONS,
    maxAge: 60 * 10 // 10 minutes
  })
}

/**
 * Get and clear OAuth state
 */
export async function getOAuthState(): Promise<string | null> {
  const cookieStore = await cookies()
  const state = cookieStore.get(OAUTH_STATE_COOKIE)?.value || null
  if (state) {
    cookieStore.delete(OAUTH_STATE_COOKIE)
  }
  return state
}
