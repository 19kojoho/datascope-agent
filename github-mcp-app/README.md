# GitHub MCP Server for DataScope

This is a custom MCP (Model Context Protocol) server that provides code search
capabilities for the `novatech-transformations` GitHub repository.

## Purpose

The DataScope agent uses this MCP server to:
- Search for transformation SQL code containing specific patterns
- Retrieve full file contents for analysis
- List available transformation files

## Deployment to Databricks Apps

### Prerequisites

1. Databricks CLI installed and authenticated
2. GitHub Personal Access Token with `repo` scope

### Step 1: Create GitHub Token

1. Go to https://github.com/settings/tokens
2. Generate new token (classic) with `repo` scope
3. Copy the token

### Step 2: Deploy the App

```bash
# Navigate to this directory
cd github-mcp-app

# Authenticate with Databricks
databricks auth login --host https://your-workspace.cloud.databricks.com

# Create the app
databricks apps create github-mcp-server

# Get your username
DATABRICKS_USERNAME=$(databricks current-user me | jq -r .userName)

# Sync the files to workspace
databricks sync . "/Users/$DATABRICKS_USERNAME/github-mcp-server"

# Deploy the app
databricks apps deploy github-mcp-server \
    --source-code-path "/Workspace/Users/$DATABRICKS_USERNAME/github-mcp-server"
```

### Step 3: Configure the Secret

```bash
# Set the GitHub token as a secret
databricks apps set-secret github-mcp-server GITHUB_PERSONAL_ACCESS_TOKEN
# (You'll be prompted to enter the token)
```

### Step 4: Get the App URL

```bash
# Get app info
databricks apps get github-mcp-server
```

The MCP endpoint will be available at: `https://<app-name>.<workspace>.databricksapps.com/mcp`

## Configuration

Set these environment variables in your Databricks App:

| Variable | Description | Required |
|----------|-------------|----------|
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub PAT with repo access | Yes |
| `GITHUB_REPO` | Repository to search (default: `19kojoho/novatech-transformations`) | No |

## Available Tools

### `search_code`
Search for code patterns in the repository.

```python
result = search_code(query="churn_risk", file_extension=".sql")
```

### `get_file_contents`
Get the full contents of a file.

```python
result = get_file_contents(file_path="sql/gold/churn_predictions.sql")
```

### `list_sql_files`
List all SQL files in the repository.

```python
result = list_sql_files(directory="sql")
```

## Using with DataScope Agent

After deployment, configure DataScope to use this MCP:

```bash
export GITHUB_MCP_APP_URL="https://github-mcp-server.your-workspace.databricksapps.com"
```

Then in your agent:

```python
from databricks_mcp import DatabricksMCPClient
from databricks.sdk import WorkspaceClient

workspace_client = WorkspaceClient()
github_mcp = DatabricksMCPClient(
    server_url="https://github-mcp-server.your-workspace.databricksapps.com/mcp",
    workspace_client=workspace_client
)

tools = github_mcp.list_tools()
```

## Local Development

```bash
# Install dependencies
pip install -e .

# Set environment variables
export GITHUB_PERSONAL_ACCESS_TOKEN="your-token"
export GITHUB_REPO="19kojoho/novatech-transformations"

# Run locally
uvicorn server.app:app --reload --port 8000

# Test health endpoint
curl http://localhost:8000/health
```

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Server info |
| `GET /health` | Health check |
| `GET /mcp` | MCP SSE endpoint (for MCP clients) |
