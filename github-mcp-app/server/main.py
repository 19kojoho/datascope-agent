"""Entry point for GitHub MCP Server."""

import uvicorn
from server.app import app


def main():
    """Run the MCP server."""
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
