"""Centralized configuration for DataScope LangGraph Agent.

Loads all configuration from environment variables with validation.
Supports .env files for local development.
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)


def load_dotenv():
    """Load environment variables from .env file if it exists."""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        logger.info(f"Loading environment from {env_path}")
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value


# Load .env file on module import
load_dotenv()


@dataclass
class Config:
    """Configuration for the DataScope agent.

    All values are loaded from environment variables for security
    and flexibility across environments.
    """

    # Databricks connection
    databricks_host: str
    databricks_token: str

    # LLM endpoint
    llm_endpoint: str

    # SQL Warehouse
    sql_warehouse_id: str

    # GitHub MCP for code search
    github_mcp_url: str

    # Vector Search for pattern matching
    vs_endpoint: str
    vs_index: str

    # Lakebase for analytics
    lakebase_enabled: bool
    lakebase_catalog: str
    lakebase_schema: str

    # State persistence
    checkpoint_dir: str

    # Server settings
    port: int

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables.

        Raises:
            ValueError: If required environment variables are missing.
        """
        # Required variables
        databricks_host = os.environ.get("DATABRICKS_HOST", "")
        if not databricks_host:
            raise ValueError("DATABRICKS_HOST environment variable is required")

        databricks_token = os.environ.get("DATABRICKS_TOKEN", "")
        if not databricks_token:
            logger.warning("DATABRICKS_TOKEN not set - will try OAuth fallback")

        sql_warehouse_id = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "")
        if not sql_warehouse_id:
            raise ValueError("DATABRICKS_SQL_WAREHOUSE_ID environment variable is required")

        # Optional with defaults
        config = cls(
            databricks_host=databricks_host.rstrip("/"),
            databricks_token=databricks_token,
            llm_endpoint=os.environ.get("LLM_ENDPOINT_NAME", "claude-sonnet-endpoint"),
            sql_warehouse_id=sql_warehouse_id,
            github_mcp_url=os.environ.get("GITHUB_MCP_APP_URL", ""),
            vs_endpoint=os.environ.get("VS_ENDPOINT_NAME", "datascope-vs-endpoint"),
            vs_index=os.environ.get("VS_INDEX_NAME", "novatech.gold.datascope_patterns_index"),
            lakebase_enabled=os.environ.get("LAKEBASE_ENABLED", "true").lower() == "true",
            lakebase_catalog=os.environ.get("LAKEBASE_CATALOG", "novatech"),
            lakebase_schema=os.environ.get("LAKEBASE_SCHEMA", "datascope"),
            checkpoint_dir=os.environ.get("CHECKPOINT_DIR", "./checkpoints"),
            port=int(os.environ.get("PORT", "8000")),
        )

        logger.info(f"Configuration loaded: host={config.databricks_host}, endpoint={config.llm_endpoint}")
        return config

    def get_auth_headers(self) -> dict:
        """Get authorization headers for Databricks API calls."""
        if self.databricks_token:
            return {
                "Authorization": f"Bearer {self.databricks_token}",
                "Content-Type": "application/json"
            }

        # Fallback: Try OAuth (for Databricks Apps service principal)
        token = self._get_oauth_token()
        if token:
            return {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

        return {"Content-Type": "application/json"}

    def _get_oauth_token(self) -> Optional[str]:
        """Get OAuth token using service principal credentials."""
        import requests

        client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
        client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            return None

        try:
            token_url = f"{self.databricks_host}/oidc/v1/token"
            resp = requests.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "scope": "all-apis"
                },
                auth=(client_id, client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10
            )

            if resp.status_code == 200:
                return resp.json().get("access_token")
        except Exception as e:
            logger.error(f"OAuth token request failed: {e}")

        return None


# Global config instance (initialized on first use)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance.

    Lazily loads configuration from environment on first call.
    """
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config
