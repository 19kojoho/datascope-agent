'use client'

/**
 * Authentication Button Component
 *
 * Displays login/logout button and user authentication status.
 * Uses Databricks OAuth for user authentication.
 */

import { useEffect, useState } from 'react'

interface AuthState {
  authenticated: boolean
  user: {
    hasToken: boolean
    expiresAt?: number
    hasRefreshToken?: boolean
  } | null
  loading: boolean
  error?: string
}

export function AuthButton() {
  const [auth, setAuth] = useState<AuthState>({
    authenticated: false,
    user: null,
    loading: true
  })

  // Check authentication status on mount
  useEffect(() => {
    checkAuth()
  }, [])

  async function checkAuth() {
    try {
      const response = await fetch('/api/auth/me')
      const data = await response.json()
      setAuth({
        authenticated: data.authenticated,
        user: data.user,
        loading: false
      })
    } catch (error) {
      setAuth({
        authenticated: false,
        user: null,
        loading: false,
        error: 'Failed to check authentication'
      })
    }
  }

  function handleLogin() {
    // Redirect to login endpoint
    window.location.href = '/api/auth/login'
  }

  async function handleLogout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' })
      setAuth({
        authenticated: false,
        user: null,
        loading: false
      })
    } catch (error) {
      console.error('Logout failed:', error)
    }
  }

  if (auth.loading) {
    return (
      <div className="flex items-center gap-2 text-gray-400 text-sm">
        <div className="w-2 h-2 bg-gray-400 rounded-full animate-pulse"></div>
        <span>Loading...</span>
      </div>
    )
  }

  if (auth.authenticated && auth.user) {
    // Calculate time until expiry
    const expiresAt = auth.user.expiresAt
    const expiresIn = expiresAt ? Math.max(0, Math.floor((expiresAt - Date.now()) / 60000)) : 0

    return (
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-green-500 rounded-full"></div>
          <span className="text-sm text-gray-300">
            Signed in
            {expiresIn > 0 && (
              <span className="text-gray-500 ml-1">({expiresIn}m left)</span>
            )}
          </span>
        </div>
        <button
          onClick={handleLogout}
          className="px-3 py-1.5 text-sm text-gray-300 hover:text-white border border-gray-600 hover:border-gray-500 rounded-md transition-colors"
        >
          Sign out
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2">
        <div className="w-2 h-2 bg-yellow-500 rounded-full"></div>
        <span className="text-sm text-gray-400">Not signed in</span>
      </div>
      <button
        onClick={handleLogin}
        className="px-3 py-1.5 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-md transition-colors"
      >
        Sign in with Databricks
      </button>
    </div>
  )
}

/**
 * Auth Status Banner
 *
 * Shows a banner when user is not authenticated, explaining that
 * they're using default permissions.
 */
export function AuthStatusBanner() {
  const [auth, setAuth] = useState<AuthState>({
    authenticated: false,
    user: null,
    loading: true
  })

  useEffect(() => {
    async function check() {
      try {
        const response = await fetch('/api/auth/me')
        const data = await response.json()
        setAuth({
          authenticated: data.authenticated,
          user: data.user,
          loading: false
        })
      } catch {
        setAuth({ authenticated: false, user: null, loading: false })
      }
    }
    check()
  }, [])

  if (auth.loading || auth.authenticated) {
    return null
  }

  return (
    <div className="bg-yellow-900/30 border border-yellow-700/50 rounded-lg p-3 mb-4">
      <div className="flex items-start gap-3">
        <div className="text-yellow-500 mt-0.5">
          <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
        </div>
        <div className="flex-1">
          <p className="text-sm text-yellow-200">
            <strong>Not signed in.</strong> You&apos;re using default data access permissions.
            Sign in with Databricks to use your personal access level.
          </p>
        </div>
        <button
          onClick={() => window.location.href = '/api/auth/login'}
          className="px-3 py-1 text-sm text-yellow-200 hover:text-white border border-yellow-700 hover:border-yellow-500 rounded-md transition-colors whitespace-nowrap"
        >
          Sign in
        </button>
      </div>
    </div>
  )
}
