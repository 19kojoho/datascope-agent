# DataScope MCP Server

Databricks-hosted MCP server that exposes data tools to the Vercel-hosted AI agent.

## What This Does

This server implements the Model Context Protocol (MCP) to provide:
- **SQL Execution**: Run queries against Databricks SQL Warehouse
- **Vector Search**: Find similar past issues from pattern library
- **Unity Catalog**: Get table schemas and metadata
- **GitHub Integration**: Search transformation code

## Authentication Model (v3.1 - OAuth)

This server uses **proper OAuth authentication** - the modern, production-ready approach:

```
┌─────────────────────────────────────────────────────────────────────┐
│  VERCEL APP                                                          │
│                                                                      │
│  1. Get OAuth token via M2M flow (client_credentials)               │
│  2. Get user's OAuth token (from their Databricks login)            │
│  3. Call MCP with both tokens                                       │
└─────────────────────────────────────────────────────────────────────┘
         │
         │  Headers:
         │    Authorization: Bearer <sp_oauth_token>    ← App auth
         │    X-User-Token: <user_oauth_token>          ← Data access
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MCP SERVER                                                          │
│                                                                      │
│  1. Validate SP OAuth token (JWT claims + API call)                 │
│  2. Reject if client_id doesn't match allowed SP                    │
│  3. Use user token for all Databricks API calls                     │
│  4. Unity Catalog enforces user's permissions                       │
└─────────────────────────────────────────────────────────────────────┘
```

### Two-Token Model

| Token | Purpose | How to Get | Lifetime |
|-------|---------|------------|----------|
| **SP OAuth Token** | App-to-app authentication | M2M OAuth flow | 1 hour |
| **User OAuth Token** | Per-user data access | User's Databricks login | 1 hour |

### Why This Architecture?

1. **No static secrets** - OAuth tokens instead of hardcoded API keys
2. **Per-user permissions** - Each user sees only their allowed data
3. **Audit trail** - Databricks logs show actual user identity
4. **Secure app auth** - Only authorized apps can call the MCP server

## Service Principal Setup

### 1. Create Service Principal for Vercel App

```bash
# Create the SP
databricks service-principals create --json '{"displayName": "datascope-vercel-app", "active": true}'

# Output:
# {
#   "applicationId": "f2079799-776c-4f40-8057-7ac45d948c6f",  ← CLIENT_ID
#   "id": "211889641289661"
# }
```

### 2. Create OAuth Secret

```bash
# Create OAuth secret (valid for 2 years)
databricks service-principal-secrets-proxy create <SP_ID>

# Output:
# {
#   "secret": "dose54e8e4209fd5f22895111e66f547a719",  ← CLIENT_SECRET
#   "expire_time": "2028-01-03T05:57:06Z"
# }
```

### 3. Store Credentials in Vercel

```bash
# In Vercel environment variables:
DATABRICKS_SP_CLIENT_ID=f2079799-776c-4f40-8057-7ac45d948c6f
DATABRICKS_SP_CLIENT_SECRET=dose54e8e4209fd5f22895111e66f547a719
DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
```

## M2M OAuth Flow (How Vercel Gets Tokens)

### Token Request

```python
import requests

def get_sp_oauth_token():
    """Get OAuth token using M2M (client credentials) flow."""
    response = requests.post(
        f"{DATABRICKS_HOST}/oidc/v1/token",
        data={
            "grant_type": "client_credentials",
            "scope": "all-apis"
        },
        auth=(SP_CLIENT_ID, SP_CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    data = response.json()
    return data["access_token"]  # Valid for 3600 seconds (1 hour)
```

### Token Response

```json
{
  "access_token": "eyJraWQiOiI4ZTYzODQ5NGY0NTdlNzAxOTRiYzUyMDk2YjgzNz...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

## Calling the MCP Server

### Headers Required

```python
headers = {
    "Authorization": f"Bearer {sp_oauth_token}",  # From M2M flow
    "X-User-Token": user_databricks_token,        # From user's login
    "Content-Type": "application/json"
}
```

### Example: Execute SQL

```python
import requests

# Get SP OAuth token (cache this, refresh before expiry)
sp_token = get_sp_oauth_token()

# User's Databricks token (from their OAuth login)
user_token = get_user_token_from_session()

# Call MCP server
response = requests.post(
    f"{MCP_SERVER_URL}/mcp",
    headers={
        "Authorization": f"Bearer {sp_token}",
        "X-User-Token": user_token,
        "Content-Type": "application/json"
    },
    json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "execute_sql",
            "arguments": {
                "query": "SELECT * FROM novatech.gold.churn_predictions LIMIT 5"
            }
        }
    }
)

result = response.json()
# Result includes: {"using_user_token": true} to confirm user's permissions applied
```

## Deployment

### 1. Deploy to Databricks Apps

```bash
# Create app
databricks apps create datascope-mcp --description "MCP server for DataScope"

# Upload code
databricks workspace import-dir ./datascope-mcp-server "/Workspace/Users/you@example.com/apps/datascope-mcp"

# Deploy
databricks apps deploy datascope-mcp --source-code-path "/Workspace/Users/you@example.com/apps/datascope-mcp"
```

### 2. Configure Environment

Set these in your `.env` or Databricks Apps secrets:

```bash
# Required
DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
SQL_WAREHOUSE_ID=your-warehouse-id

# OAuth Authentication (required for production)
ALLOWED_SP_APP_ID=f2079799-776c-4f40-8057-7ac45d948c6f

# Optional
VS_INDEX=catalog.schema.vector_index
GITHUB_TOKEN=ghp_xxx

# Development only (fallback when no user token provided)
DATABRICKS_TOKEN=dapi...
```

## Available Tools

| Tool | Description | Input | Uses User Token |
|------|-------------|-------|-----------------|
| `execute_sql` | Run SQL query | `query: string` | Yes |
| `search_patterns` | Vector search for similar issues | `query: string` | Yes |
| `get_table_schema` | Get table schema from Unity Catalog | `table_name: string` | Yes |
| `search_code` | Search transformation SQL in GitHub | `query: string` | No |
| `get_file` | Get file contents from GitHub | `file_path: string` | No |
| `list_sql_files` | List SQL files in GitHub | `directory: string` | No |

## Security Features

### OAuth Token Validation

1. **JWT Claims Check**: Validates `sub` (client_id) matches allowed SP
2. **API Verification**: Confirms token is valid via Databricks API call
3. **Caching**: Valid tokens cached for 5 minutes to reduce API calls
4. **Rejection**: Tokens from unauthorized SPs are rejected with 401

### User Token Handling

1. **Pass-through**: User tokens are passed directly to Databricks APIs
2. **No Storage**: User tokens are never stored, only forwarded
3. **Permission Enforcement**: Unity Catalog applies user's grants, RLS, column masks

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run without OAuth (dev mode)
python app.py

# Run with OAuth enabled
ALLOWED_SP_APP_ID=your-sp-app-id python app.py
```

Server runs on http://localhost:8001

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────┐     ┌────────────┐
│   Browser   │────▶│   Vercel    │────▶│  MCP Server     │────▶│ Databricks │
│             │     │   (Agent)   │     │  (Databricks    │     │            │
│  User logs  │     │             │     │   Apps)         │     │  Enforces  │
│  in via     │     │  Gets SP    │     │                 │     │  user's    │
│  Databricks │     │  OAuth      │     │  Validates SP   │     │  Unity     │
│  OAuth      │     │  token      │     │  OAuth token    │     │  Catalog   │
│             │     │             │     │                 │     │  perms     │
│  Token      │     │  Passes     │     │  Uses user      │     │            │
│  stored in  │     │  both       │     │  token for      │     │  RLS,      │
│  session    │     │  tokens     │     │  data queries   │     │  masks     │
└─────────────┘     └─────────────┘     └─────────────────┘     └────────────┘
```

## Interview Justification

> "I implemented a **modern OAuth-based authentication model** for the MCP server:
>
> **For app-to-app authentication**, I use OAuth M2M (client credentials) flow instead of static API keys. The Vercel app has its own Service Principal with OAuth credentials. The MCP server validates incoming tokens by:
> 1. Checking JWT claims to verify the client_id matches the allowed SP
> 2. Calling Databricks API to confirm the token is valid
> 3. Caching valid tokens to avoid repeated validation calls
>
> **For user data access**, I use token pass-through. The user's Databricks OAuth token (obtained when they log in) is forwarded to the MCP server in a separate header. This ensures:
> - Unity Catalog enforces per-user permissions
> - Row-level security filters apply
> - Audit logs show actual user identity
>
> This architecture follows Databricks' recommended patterns for multi-tier applications with per-user access control. The key insight is separating **app authentication** (proving the request is from Vercel) from **data authorization** (enforcing what data each user can see)."
