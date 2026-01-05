# DataScope - Vercel + Custom MCP Server Architecture

AI-powered data debugging agent with OAuth authentication and per-user data access.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                           VERCEL                                     │
│                                                                      │
│   Environment Variables:                                             │
│   • ANTHROPIC_API_KEY (direct LLM access)                           │
│   • MCP_SERVER_URL (gateway to all tools)                           │
│   • DATABRICKS_HOST, SP_CLIENT_ID, SP_CLIENT_SECRET (OAuth)         │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Next.js Application                       │   │
│  │                                                              │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │   │
│  │  │   React UI   │  │  API Routes  │  │  Anthropic SDK   │  │   │
│  │  │  + Auth UI   │  │  /api/chat   │  │  Direct Access   │  │   │
│  │  └──────────────┘  │  /api/auth/* │  └──────────────────┘  │   │
│  │                    └──────────────┘                         │   │
│  │                              │                              │   │
│  │                         MCP Client                          │   │
│  │                    (SP OAuth + User Token)                  │   │
│  └──────────────────────────────┼──────────────────────────────┘   │
│                                 │                                    │
└─────────────────────────────────┼────────────────────────────────────┘
                                  │
                     Headers:     │
                     Authorization: Bearer <SP_OAuth_token>
                     X-User-Token: <user_OAuth_token>
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   DATABRICKS (Custom MCP Server)                     │
│                                                                      │
│   Validates:                                                         │
│   • SP OAuth token (app authentication via JWT + API)               │
│   • Uses user token for data queries (per-user permissions)         │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              Custom MCP Server (Databricks App)              │   │
│  │                                                              │   │
│  │  Tools exposed via MCP:                                      │   │
│  │  ├── execute_sql      → Databricks SQL (user permissions)   │   │
│  │  ├── search_patterns  → Vector Search (user permissions)    │   │
│  │  ├── get_table_schema → Unity Catalog (user permissions)    │   │
│  │  ├── search_code      → GitHub API                          │   │
│  │  ├── get_file         → GitHub API                          │   │
│  │  └── list_sql_files   → GitHub API                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│                   Unity Catalog enforces:                            │
│                   • Table grants per user                            │
│                   • Row-level security (RLS)                         │
│                   • Column masks                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Authentication Model (v2.0 - OAuth)

### Two-Token Architecture

| Token | Purpose | How to Get | Lifetime |
|-------|---------|------------|----------|
| **SP OAuth Token** | App-to-app authentication | M2M client_credentials flow | 1 hour (auto-refresh) |
| **User OAuth Token** | Per-user data access | User login via PKCE flow | 1 hour (re-login) |

### Why This Model?

1. **No static secrets** - OAuth tokens instead of hardcoded API keys
2. **Per-user permissions** - Each user sees only their allowed data
3. **Audit trail** - Databricks logs show actual user identity
4. **Secure app auth** - Only authorized apps can call the MCP server

## Project Structure

```
datascope-vercel-app/
├── app/
│   ├── api/
│   │   ├── chat/route.ts         # Streaming chat API
│   │   └── auth/
│   │       ├── login/route.ts    # Initiates OAuth login
│   │       ├── callback/route.ts # OAuth callback handler
│   │       ├── logout/route.ts   # Clears session
│   │       └── me/route.ts       # Current user info
│   ├── page.tsx                  # Main page
│   ├── layout.tsx                # Root layout
│   └── globals.css               # Styles
├── components/
│   ├── Chat.tsx                  # Chat container (SSE handling)
│   ├── ChatInput.tsx             # Input component
│   ├── ChatMessage.tsx           # Message display
│   └── AuthButton.tsx            # Login/logout UI
├── lib/
│   ├── agent/
│   │   ├── index.ts              # Agent using Anthropic SDK
│   │   ├── tools.ts              # Tool definitions + MCP calls
│   │   └── prompts.ts            # System prompts
│   ├── auth/
│   │   ├── databricks-oauth.ts   # OAuth utilities (SP + User)
│   │   └── session.ts            # Session management (cookies)
│   ├── mcp/
│   │   └── client.ts             # MCP client (OAuth-enabled)
│   └── observability/
│       └── galileo.ts            # Tracing (optional)
├── .env.example                  # Environment template
└── README.md
```

## Setup

### 1. Deploy the MCP Server First

See `../datascope-mcp-server/README.md` for MCP server deployment.

After deployment, you'll have:
- MCP Server URL: `https://datascope-mcp.your-workspace.databricks.app`
- Service Principal Application ID (ALLOWED_SP_APP_ID in MCP server)

### 2. Create Service Principal for Vercel App

```bash
# Create the SP
databricks service-principals create --json '{"displayName": "datascope-vercel-app", "active": true}'

# Output:
# {
#   "applicationId": "f2079799-776c-4f40-8057-7ac45d948c6f",  ← CLIENT_ID
#   "id": "211889641289661"
# }

# Create OAuth secret (valid for 2 years)
databricks service-principal-secrets-proxy create <SP_ID>

# Output:
# {
#   "secret": "dose54e8e4209fd5f22895111e66f547a719",  ← CLIENT_SECRET
#   "expire_time": "2028-01-03T05:57:06Z"
# }
```

### 3. Install Dependencies

```bash
cd datascope-vercel-app
npm install
```

### 4. Configure Environment

```bash
cp .env.example .env.local
```

Edit `.env.local`:
```env
# Anthropic API (direct access)
ANTHROPIC_API_KEY=sk-ant-...

# MCP Server
MCP_SERVER_URL=https://datascope-mcp.your-workspace.databricks.app

# Databricks OAuth (required for production)
DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
DATABRICKS_SP_CLIENT_ID=f2079799-776c-4f40-8057-7ac45d948c6f
DATABRICKS_SP_CLIENT_SECRET=dose54e8e4209fd5f22895111e66f547a719
```

### 5. Run Locally

```bash
npm run dev
```

Open http://localhost:3000

### 6. Deploy to Vercel

```bash
vercel
```

Set environment variables in Vercel dashboard:
- `ANTHROPIC_API_KEY`
- `MCP_SERVER_URL`
- `DATABRICKS_HOST`
- `DATABRICKS_SP_CLIENT_ID`
- `DATABRICKS_SP_CLIENT_SECRET`

## Authentication Flow

### App Authentication (SP OAuth)

```
Vercel App                              Databricks
    │                                       │
    │  1. POST /oidc/v1/token               │
    │     grant_type=client_credentials     │
    │     client_id=<SP_CLIENT_ID>          │
    │     client_secret=<SP_CLIENT_SECRET>  │
    │  ─────────────────────────────────────>
    │                                       │
    │  2. { access_token: "eyJ...", ... }   │
    │  <─────────────────────────────────────
    │                                       │
    │  3. Call MCP with SP token            │
    │     Authorization: Bearer <token>      │
    │  ─────────────────────────────────────> MCP Server
    │                                       │
    │  4. MCP validates JWT + API call      │
    │  <───────────────────────────────────── MCP Server
```

### User Authentication (PKCE OAuth)

```
Browser                  Vercel               Databricks
   │                        │                      │
   │  1. Click "Sign in"    │                      │
   │  ──────────────────────>                      │
   │                        │                      │
   │  2. Redirect to Databricks OAuth             │
   │  <───────────────────────────────────────────>
   │                        │                      │
   │  3. User logs in                              │
   │  ────────────────────────────────────────────>
   │                        │                      │
   │  4. Callback with code │                      │
   │  ──────────────────────>                      │
   │                        │  5. Exchange code    │
   │                        │  ────────────────────>
   │                        │                      │
   │                        │  6. User token       │
   │                        │  <────────────────────
   │                        │                      │
   │  7. Set session cookie │                      │
   │  <──────────────────────                      │
```

### MCP Request with Both Tokens

```typescript
// Every MCP request includes both tokens
const headers = {
  'Authorization': `Bearer ${spOAuthToken}`,  // App auth
  'X-User-Token': userOAuthToken,              // User data access
  'Content-Type': 'application/json'
}

// MCP server:
// 1. Validates SP token (is this the Vercel app?)
// 2. Uses user token for Databricks API calls
// 3. Unity Catalog enforces user's permissions
```

## How It Works

### 1. User Signs In (Optional)

Users can sign in with Databricks to use their personal data permissions.
Without signing in, the app uses a fallback token (dev mode).

### 2. User Asks a Question

```
User: "Why do some customers have NULL churn_risk?"
```

### 3. Claude Decides to Use Tools

```typescript
{
  type: 'tool_use',
  name: 'execute_sql',
  input: { query: 'SELECT COUNT(*) FROM novatech.gold.churn_predictions WHERE churn_risk IS NULL' }
}
```

### 4. Vercel Calls MCP Server

The MCP client automatically:
- Gets SP OAuth token (cached, auto-refresh)
- Includes user token from session
- Sends both in headers

### 5. MCP Server Executes with User Permissions

The MCP server:
1. Validates SP OAuth token
2. Uses user token for Databricks SQL API call
3. Unity Catalog enforces that user's permissions
4. Returns results via MCP

### 6. User Sees Only Their Data

Different users see different data based on:
- Table grants (can they access this table?)
- Row-level security (which rows can they see?)
- Column masks (are sensitive columns hidden?)

## Interview-Ready Concepts

### Why Two Tokens?

> "A single token can't solve both problems. App authentication proves the request comes from our Vercel app - this prevents unauthorized clients. Data authorization determines what data the specific user can see - this requires their personal token so Unity Catalog can enforce their permissions."

### Why OAuth over Static Secrets?

> "OAuth tokens are short-lived (1 hour), self-describing (JWT contains identity), and follow industry standards. If an OAuth token leaks, it expires quickly. If an API key leaks, it works until someone notices."

### How Do You Handle Token Refresh?

> "SP tokens are cached in memory with 5-minute buffer before expiry. When a token is about to expire, we get a new one using the client credentials flow. User tokens require re-login after 1 hour - we could implement refresh tokens but chose simplicity for now."

### What If User Isn't Logged In?

> "Without a user token, the MCP server can fall back to a default service account token (dev mode). In production, you'd either require login or use the app's own permissions. The key is the architecture supports both modes."

## Troubleshooting

### "Failed to get SP OAuth token"
- Check `DATABRICKS_HOST`, `DATABRICKS_SP_CLIENT_ID`, `DATABRICKS_SP_CLIENT_SECRET`
- Verify the SP exists and has a valid OAuth secret

### "MCP authentication failed"
- Verify SP Application ID matches `ALLOWED_SP_APP_ID` in MCP server
- Check SP OAuth secret hasn't expired

### "Session expired"
- User needs to sign in again (tokens expire after 1 hour)
- Click "Sign in with Databricks"

### "User not authorized for table"
- The logged-in user doesn't have access to that table
- Check Unity Catalog grants for the user

### "Query returned no results"
- Row-level security may be filtering results for this user
- Different users see different data based on their RLS filters
