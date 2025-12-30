"""MCP (Model Context Protocol) tools for DataScope agent.

This module provides integration with:
- Databricks Managed MCP Servers (SQL, Unity Catalog, Vector Search)
- Custom MCP Server for GitHub (deployed as Databricks App)

Architecture:
- Managed MCPs: Hosted by Databricks, ready to use
- Custom GitHub MCP: Deployed as a Databricks App for code search

Reference: https://docs.databricks.com/aws/en/generative-ai/mcp/
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel


class MCPConfig(BaseModel):
    """Configuration for MCP servers."""

    workspace_host: str
    catalog: str = "novatech"
    schema_name: str = "gold"
    github_repo: str = "19kojoho/novatech-transformations"
    github_mcp_app_url: str | None = None  # URL of GitHub MCP Databricks App

    @classmethod
    def from_env(cls) -> "MCPConfig":
        """Create config from environment variables."""
        host = os.environ.get("DATABRICKS_HOST", "")
        # Remove protocol if present
        if host.startswith("https://"):
            host = host[8:]
        elif host.startswith("http://"):
            host = host[7:]

        return cls(
            workspace_host=host,
            catalog=os.environ.get("DATASCOPE_CATALOG", "novatech"),
            schema_name=os.environ.get("DATASCOPE_SCHEMA_GOLD", "gold"),
            github_repo=os.environ.get("GITHUB_REPO", "19kojoho/novatech-transformations"),
            github_mcp_app_url=os.environ.get("GITHUB_MCP_APP_URL"),
        )


class DatabricksManagedMCPClient:
    """
    Client for Databricks Managed MCP Servers.

    Databricks provides managed MCP servers for:
    - SQL execution (/api/2.0/mcp/sql)
    - Unity Catalog functions (/api/2.0/mcp/functions/{catalog}/{schema})
    - Vector Search (/api/2.0/mcp/vector-search/{catalog}/{schema})
    - Genie spaces (/api/2.0/mcp/genie/{genie_space_id})

    Reference: https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp
    """

    def __init__(self, config: MCPConfig | None = None):
        """Initialize the MCP client."""
        self.config = config or MCPConfig.from_env()
        self._base_url = f"https://{self.config.workspace_host}"

    @property
    def sql_server_url(self) -> str:
        """Get the SQL MCP server URL for executing queries."""
        return f"{self._base_url}/api/2.0/mcp/sql"

    @property
    def unity_catalog_url(self) -> str:
        """Get the Unity Catalog MCP server URL for functions."""
        return (
            f"{self._base_url}/api/2.0/mcp/functions/"
            f"{self.config.catalog}/{self.config.schema_name}"
        )

    @property
    def vector_search_url(self) -> str:
        """Get the Vector Search MCP server URL."""
        return (
            f"{self._base_url}/api/2.0/mcp/vector-search/"
            f"{self.config.catalog}/ml"
        )

    def get_uc_url(self, catalog: str, schema: str) -> str:
        """Get Unity Catalog MCP URL for a specific catalog/schema."""
        return f"{self._base_url}/api/2.0/mcp/functions/{catalog}/{schema}"

    def get_vs_url(self, catalog: str, schema: str) -> str:
        """Get Vector Search MCP URL for a specific catalog/schema."""
        return f"{self._base_url}/api/2.0/mcp/vector-search/{catalog}/{schema}"

    def get_all_server_urls(self) -> dict[str, str]:
        """Get all managed MCP server URLs."""
        return {
            "sql": self.sql_server_url,
            "unity_catalog": self.unity_catalog_url,
            "vector_search": self.vector_search_url,
        }


class GitHubMCPAppClient:
    """
    Client for GitHub MCP Server deployed as a Databricks App.

    This custom MCP server provides:
    - Code search in the novatech-transformations repository
    - File content retrieval
    - Repository browsing

    The server is deployed as a Databricks App and accessed via:
    https://<app-url>/mcp

    Reference: https://docs.databricks.com/aws/en/generative-ai/mcp/custom-mcp
    """

    def __init__(self, config: MCPConfig | None = None):
        """Initialize the GitHub MCP App client."""
        self.config = config or MCPConfig.from_env()
        self.repo = self.config.github_repo

    @property
    def server_url(self) -> str | None:
        """Get the GitHub MCP App server URL."""
        if self.config.github_mcp_app_url:
            url = self.config.github_mcp_app_url
            # Ensure /mcp endpoint
            if not url.endswith("/mcp"):
                url = url.rstrip("/") + "/mcp"
            return url
        return None

    def is_configured(self) -> bool:
        """Check if the GitHub MCP App is configured."""
        return self.server_url is not None

    def get_connection_config(self) -> dict[str, Any]:
        """
        Get configuration for connecting to the GitHub MCP App.

        Use with DatabricksMCPClient:
        ```python
        from databricks_mcp import DatabricksMCPClient
        from databricks.sdk import WorkspaceClient

        config = gh_mcp.get_connection_config()
        workspace_client = WorkspaceClient()
        mcp_client = DatabricksMCPClient(
            server_url=config["server_url"],
            workspace_client=workspace_client
        )
        tools = mcp_client.list_tools()
        ```
        """
        if not self.is_configured():
            raise ValueError(
                "GitHub MCP App URL not configured. "
                "Set GITHUB_MCP_APP_URL environment variable to the Databricks App URL."
            )

        return {
            "server_url": self.server_url,
            "repository": self.repo,
            "transport": "http",
        }


def get_mcp_client_code() -> str:
    """
    Get Python code for using MCP clients with LangGraph.

    This code is designed to run in a Databricks notebook or app.
    """
    return '''
# =============================================================================
# Using Databricks MCP with LangGraph
# =============================================================================

from databricks_mcp import DatabricksMCPClient
from databricks.sdk import WorkspaceClient

# For local development (uses CLI profile)
workspace_client = WorkspaceClient(profile="DEFAULT")

# For Databricks Apps (uses on-behalf-of-user auth)
# from databricks.sdk.credentials_provider import ModelServingUserCredentials
# workspace_client = WorkspaceClient(credentials_strategy=ModelServingUserCredentials())

# =============================================================================
# Connect to Databricks Managed MCPs
# =============================================================================

# SQL MCP - Execute queries
sql_mcp_url = f"{workspace_client.config.host}/api/2.0/mcp/sql"
sql_client = DatabricksMCPClient(server_url=sql_mcp_url, workspace_client=workspace_client)

# Unity Catalog MCP - Get schemas and lineage
uc_mcp_url = f"{workspace_client.config.host}/api/2.0/mcp/functions/novatech/gold"
uc_client = DatabricksMCPClient(server_url=uc_mcp_url, workspace_client=workspace_client)

# Vector Search MCP - Pattern matching
vs_mcp_url = f"{workspace_client.config.host}/api/2.0/mcp/vector-search/novatech/ml"
vs_client = DatabricksMCPClient(server_url=vs_mcp_url, workspace_client=workspace_client)

# List available tools from each MCP
print("SQL Tools:", [t.name for t in sql_client.list_tools()])
print("UC Tools:", [t.name for t in uc_client.list_tools()])
print("VS Tools:", [t.name for t in vs_client.list_tools()])

# =============================================================================
# Connect to Custom GitHub MCP (Databricks App)
# =============================================================================

import os
github_mcp_url = os.environ.get("GITHUB_MCP_APP_URL", "").rstrip("/") + "/mcp"
if github_mcp_url:
    github_client = DatabricksMCPClient(server_url=github_mcp_url, workspace_client=workspace_client)
    print("GitHub Tools:", [t.name for t in github_client.list_tools()])

# =============================================================================
# Use with LangGraph Agent
# =============================================================================

from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic

# Combine all tools from MCPs
all_tools = []
all_tools.extend(sql_client.list_tools())
all_tools.extend(uc_client.list_tools())
# all_tools.extend(vs_client.list_tools())  # Optional
# all_tools.extend(github_client.list_tools())  # If configured

# Create agent with MCP tools
llm = ChatAnthropic(model="claude-sonnet-4-20250514")
agent = create_react_agent(llm, all_tools)

# Run investigation
result = agent.invoke({
    "messages": [{"role": "user", "content": "Why do some customers have NULL churn_risk?"}]
})
'''


def create_github_mcp_app_files() -> dict[str, str]:
    """
    Generate files needed to deploy GitHub MCP as a Databricks App.

    Returns a dict of {filename: content} for the custom MCP server.
    """

    app_yaml = """# Databricks App configuration for GitHub MCP Server
command: ['uv', 'run', 'github-mcp-server']
"""

    pyproject_toml = '''[project]
name = "github-mcp-server"
version = "0.1.0"
description = "GitHub MCP Server for DataScope - searches novatech-transformations repo"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn>=0.27.0",
    "fastmcp>=0.1.0",
    "httpx>=0.27.0",
    "databricks-sdk>=0.20.0",
]

[project.scripts]
github-mcp-server = "server.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
'''

    requirements_txt = """# GitHub MCP Server Dependencies
uv
fastapi>=0.110.0
uvicorn>=0.27.0
fastmcp>=0.1.0
httpx>=0.27.0
databricks-sdk>=0.20.0
PyGithub>=2.1.0
"""

    main_py = '''"""Entry point for GitHub MCP Server."""

import uvicorn
from server.app import app


def main():
    """Run the MCP server."""
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
'''

    app_py = '''"""FastAPI application with MCP server for GitHub code search."""

from fastapi import FastAPI
from fastmcp import FastMCP

from server import tools

# Create FastAPI app
app = FastAPI(
    title="GitHub MCP Server",
    description="MCP Server for searching transformation code in novatech-transformations",
)

# Create MCP server
mcp_server = FastMCP("github-mcp")

# Register tools
tools.register_tools(mcp_server)

# Mount MCP server at /mcp
app.mount("/mcp", mcp_server.get_app())


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "github-mcp-server"}
'''

    tools_py = '''"""GitHub MCP tools for code search."""

import os
from typing import Optional
from github import Github
from fastmcp import FastMCP


# GitHub configuration
GITHUB_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
REPO_NAME = os.environ.get("GITHUB_REPO", "19kojoho/novatech-transformations")


def get_github_client() -> Github:
    """Get authenticated GitHub client."""
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_PERSONAL_ACCESS_TOKEN not set")
    return Github(GITHUB_TOKEN)


def register_tools(mcp: FastMCP):
    """Register all GitHub tools with the MCP server."""

    @mcp.tool
    def search_code(query: str, file_extension: str = ".sql") -> dict:
        """
        Search for code in the novatech-transformations repository.

        Args:
            query: Search term (e.g., 'churn_risk', 'CASE WHEN', 'LEFT JOIN')
            file_extension: File extension to filter (default: .sql)

        Returns:
            Matching code snippets with file paths and line numbers
        """
        try:
            g = get_github_client()
            repo = g.get_repo(REPO_NAME)

            # Search in repository
            search_query = f"{query} repo:{REPO_NAME}"
            if file_extension:
                search_query += f" extension:{file_extension.lstrip('.')}"

            results = g.search_code(search_query)

            matches = []
            for item in list(results)[:5]:  # Limit to 5 results
                content = item.decoded_content.decode("utf-8")
                lines = content.split("\\n")

                # Find matching lines
                matching_lines = []
                for i, line in enumerate(lines, 1):
                    if query.lower() in line.lower():
                        # Get context
                        start = max(0, i - 3)
                        end = min(len(lines), i + 2)
                        context = "\\n".join(lines[start:end])
                        matching_lines.append({
                            "line_number": i,
                            "context": context
                        })

                matches.append({
                    "file": item.path,
                    "url": item.html_url,
                    "matches": matching_lines[:3]
                })

            return {
                "query": query,
                "repository": REPO_NAME,
                "total_matches": len(matches),
                "results": matches
            }

        except Exception as e:
            return {"error": str(e)}

    @mcp.tool
    def get_file_contents(file_path: str) -> dict:
        """
        Get the contents of a file from the repository.

        Args:
            file_path: Path to file (e.g., 'sql/gold/churn_predictions.sql')

        Returns:
            File contents with metadata
        """
        try:
            g = get_github_client()
            repo = g.get_repo(REPO_NAME)

            file_content = repo.get_contents(file_path)
            content = file_content.decoded_content.decode("utf-8")

            return {
                "path": file_path,
                "content": content,
                "size": file_content.size,
                "sha": file_content.sha,
                "url": file_content.html_url
            }

        except Exception as e:
            return {"error": str(e)}

    @mcp.tool
    def list_sql_files(directory: str = "sql") -> dict:
        """
        List all SQL files in a directory.

        Args:
            directory: Directory to list (default: 'sql')

        Returns:
            List of SQL file paths
        """
        try:
            g = get_github_client()
            repo = g.get_repo(REPO_NAME)

            contents = repo.get_contents(directory)
            files = []

            def get_files(items, prefix=""):
                for item in items:
                    if item.type == "dir":
                        sub_contents = repo.get_contents(item.path)
                        get_files(sub_contents, item.path + "/")
                    elif item.name.endswith(".sql"):
                        files.append({
                            "path": item.path,
                            "name": item.name,
                            "size": item.size
                        })

            get_files(contents)

            return {
                "directory": directory,
                "repository": REPO_NAME,
                "sql_files": files
            }

        except Exception as e:
            return {"error": str(e)}
'''

    utils_py = '''"""Utility functions for Databricks authentication."""

import os
from databricks.sdk import WorkspaceClient


def get_workspace_client() -> WorkspaceClient:
    """
    Get WorkspaceClient authenticated as the app service principal.

    Use this for operations that don't need user context.
    """
    return WorkspaceClient()


def get_user_authenticated_workspace_client() -> WorkspaceClient:
    """
    Get WorkspaceClient authenticated as the calling user.

    Use this for operations that should respect user permissions.
    Requires on-behalf-of-user OAuth to be configured.
    """
    from databricks.sdk.credentials_provider import ModelServingUserCredentials

    return WorkspaceClient(
        credentials_strategy=ModelServingUserCredentials()
    )
'''

    return {
        "app.yaml": app_yaml,
        "pyproject.toml": pyproject_toml,
        "requirements.txt": requirements_txt,
        "server/__init__.py": "",
        "server/main.py": main_py,
        "server/app.py": app_py,
        "server/tools.py": tools_py,
        "server/utils.py": utils_py,
    }


def print_setup_instructions():
    """Print comprehensive MCP setup instructions."""
    config = MCPConfig.from_env()

    print("""
================================================================================
                    DataScope MCP Setup Instructions
================================================================================

## 1. Databricks Managed MCP Servers (Ready to Use)

These are hosted by Databricks and require no additional setup.

### Required Environment Variables:
```bash
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"
export DATABRICKS_TOKEN="your-token"
export DATABRICKS_SQL_WAREHOUSE_ID="your-warehouse-id"
```

### MCP Server URLs:
""")
    print(f"  - SQL:            https://{config.workspace_host}/api/2.0/mcp/sql")
    print(f"  - Unity Catalog:  https://{config.workspace_host}/api/2.0/mcp/functions/{config.catalog}/{config.schema_name}")
    print(f"  - Vector Search:  https://{config.workspace_host}/api/2.0/mcp/vector-search/{config.catalog}/ml")

    print("""

## 2. GitHub MCP Server (Custom - Deploy as Databricks App)

The GitHub MCP server needs to be deployed as a Databricks App.

### Step 1: Generate the app files
```python
from datascope.tools.mcp_tools import create_github_mcp_app_files

files = create_github_mcp_app_files()
for filename, content in files.items():
    # Write files to github-mcp-app directory
    print(f"Creating {filename}")
```

### Step 2: Create GitHub Personal Access Token
1. Go to https://github.com/settings/tokens
2. Generate new token with 'repo' scope
3. Copy the token

### Step 3: Deploy to Databricks Apps
```bash
# Authenticate
databricks auth login --host https://your-workspace.cloud.databricks.com

# Create the app
databricks apps create github-mcp-server

# Sync files
DATABRICKS_USERNAME=$(databricks current-user me | jq -r .userName)
databricks sync ./github-mcp-app "/Users/$DATABRICKS_USERNAME/github-mcp-server"

# Deploy
databricks apps deploy github-mcp-server \\
    --source-code-path "/Workspace/Users/$DATABRICKS_USERNAME/github-mcp-server"

# Set secrets
databricks apps set-secret github-mcp-server GITHUB_PERSONAL_ACCESS_TOKEN
```

### Step 4: Configure DataScope to use the GitHub MCP App
```bash
export GITHUB_MCP_APP_URL="https://your-app-name.your-workspace.databricksapps.com"
```

## 3. Using MCPs in Your Agent

```python
from databricks_mcp import DatabricksMCPClient
from databricks.sdk import WorkspaceClient

# Connect to workspace
workspace_client = WorkspaceClient()
host = workspace_client.config.host

# Create MCP clients
sql_client = DatabricksMCPClient(
    server_url=f"{host}/api/2.0/mcp/sql",
    workspace_client=workspace_client
)

github_client = DatabricksMCPClient(
    server_url="https://your-github-mcp-app.databricksapps.com/mcp",
    workspace_client=workspace_client
)

# Get tools
sql_tools = sql_client.list_tools()
github_tools = github_client.list_tools()
```

================================================================================
""")


if __name__ == "__main__":
    print_setup_instructions()
