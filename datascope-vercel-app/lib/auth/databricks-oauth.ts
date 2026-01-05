/**
 * Databricks OAuth Utilities
 *
 * User OAuth authentication using the Databricks App's OAuth client.
 *
 * How it works:
 * 1. User clicks "Sign in with Databricks"
 * 2. Redirected to Databricks login page (PKCE flow)
 * 3. After login, redirected back with authorization code
 * 4. Code exchanged for access token
 * 5. Token used to call MCP server (Databricks App accepts it)
 *
 * The Databricks App's OAuth client ID is auto-created when the app is deployed.
 * This is a public client, so no client secret is needed (PKCE provides security).
 */

interface OAuthTokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  scope?: string
  refresh_token?: string
}

/**
 * Get Databricks host URL from environment
 */
export function getDatabricksHost(): string {
  const host = process.env.DATABRICKS_HOST
  if (!host) {
    throw new Error('DATABRICKS_HOST environment variable not set')
  }
  return host.replace(/\/$/, '')
}

/**
 * Get the OAuth client ID (Databricks App's client ID)
 */
export function getOAuthClientId(): string {
  const clientId = process.env.DATABRICKS_OAUTH_CLIENT_ID
  if (!clientId) {
    throw new Error('DATABRICKS_OAUTH_CLIENT_ID environment variable not set')
  }
  return clientId
}

/**
 * Build the Databricks OAuth authorization URL for user login
 *
 * This initiates the PKCE flow for user authentication.
 * After login, Databricks redirects back with an authorization code.
 *
 * @param redirectUri - The callback URL after login
 * @param state - CSRF protection state parameter
 * @param codeChallenge - PKCE code challenge
 * @returns Authorization URL to redirect user to
 */
export function buildAuthorizationUrl(
  redirectUri: string,
  state: string,
  codeChallenge: string
): string {
  const databricksHost = getDatabricksHost()
  const clientId = getOAuthClientId()

  const params = new URLSearchParams({
    response_type: 'code',
    client_id: clientId,
    redirect_uri: redirectUri,
    scope: 'all-apis offline_access',
    state: state,
    code_challenge: codeChallenge,
    code_challenge_method: 'S256'
  })

  return `${databricksHost}/oidc/v1/authorize?${params.toString()}`
}

/**
 * Exchange authorization code for user OAuth tokens
 *
 * @param code - Authorization code from callback
 * @param redirectUri - Must match the one used in authorization request
 * @param codeVerifier - PKCE code verifier
 * @returns Access token and refresh token
 */
export async function exchangeCodeForTokens(
  code: string,
  redirectUri: string,
  codeVerifier: string
): Promise<{ accessToken: string; refreshToken?: string; expiresIn: number }> {
  const databricksHost = getDatabricksHost()
  const clientId = getOAuthClientId()

  const tokenUrl = `${databricksHost}/oidc/v1/token`

  const response = await fetch(tokenUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded'
    },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: clientId,
      code: code,
      redirect_uri: redirectUri,
      code_verifier: codeVerifier
    })
  })

  if (!response.ok) {
    const error = await response.text()
    throw new Error(`Failed to exchange code for tokens: ${response.status} - ${error}`)
  }

  const data: OAuthTokenResponse = await response.json()

  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresIn: data.expires_in
  }
}

/**
 * Refresh user OAuth token using refresh token
 *
 * @param refreshToken - The refresh token from initial login
 * @returns New access token and optionally new refresh token
 */
export async function refreshUserToken(
  refreshToken: string
): Promise<{ accessToken: string; refreshToken?: string; expiresIn: number }> {
  const databricksHost = getDatabricksHost()
  const clientId = getOAuthClientId()

  const tokenUrl = `${databricksHost}/oidc/v1/token`

  const response = await fetch(tokenUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded'
    },
    body: new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: clientId,
      refresh_token: refreshToken
    })
  })

  if (!response.ok) {
    const error = await response.text()
    throw new Error(`Failed to refresh token: ${response.status} - ${error}`)
  }

  const data: OAuthTokenResponse = await response.json()

  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresIn: data.expires_in
  }
}

/**
 * PKCE utilities for secure OAuth flow
 */
export const pkce = {
  /**
   * Generate a random code verifier for PKCE
   */
  generateCodeVerifier(): string {
    const array = new Uint8Array(32)
    crypto.getRandomValues(array)
    return base64URLEncode(array)
  },

  /**
   * Generate code challenge from verifier (S256 method)
   */
  async generateCodeChallenge(verifier: string): Promise<string> {
    const encoder = new TextEncoder()
    const data = encoder.encode(verifier)
    const hash = await crypto.subtle.digest('SHA-256', data)
    return base64URLEncode(new Uint8Array(hash))
  },

  /**
   * Generate a random state parameter for CSRF protection
   */
  generateState(): string {
    const array = new Uint8Array(16)
    crypto.getRandomValues(array)
    return base64URLEncode(array)
  }
}

/**
 * Base64 URL encode (no padding, URL-safe characters)
 */
function base64URLEncode(buffer: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < buffer.length; i++) {
    binary += String.fromCharCode(buffer[i])
  }
  return btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '')
}
