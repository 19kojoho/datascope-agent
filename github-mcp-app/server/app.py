"""FastAPI application for GitHub code search.

This server provides REST API endpoints for searching transformation code
in the novatech-transformations GitHub repository.

Deployed as a Databricks App, it enables the DataScope agent to:
- Search for SQL code containing specific patterns
- Retrieve full file contents
- List available transformation files
"""

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

from server.tools import (
    search_code,
    get_file_contents,
    list_sql_files,
)


# Create FastAPI app
app = FastAPI(
    title="GitHub Code Search Server",
    description="REST API for searching transformation code in novatech-transformations",
    version="0.1.0",
)


class SearchRequest(BaseModel):
    query: str
    file_extension: Optional[str] = ".sql"


class FileRequest(BaseModel):
    file_path: str


class ListRequest(BaseModel):
    directory: Optional[str] = "sql"


@app.get("/")
def root():
    """Root endpoint with server info."""
    return {
        "name": "github-code-search",
        "description": "REST API for GitHub code search",
        "repository": "19kojoho/novatech-transformations",
        "endpoints": {
            "search": "/search",
            "file": "/file",
            "list": "/list",
            "health": "/health"
        }
    }


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "github-code-search"}


@app.post("/search")
def search(request: SearchRequest):
    """
    Search for code in the repository.

    Args:
        query: Search term (e.g., 'churn_risk', 'CASE WHEN')
        file_extension: File extension to filter (default: .sql)
    """
    return search_code(request.query, request.file_extension)


@app.post("/file")
def get_file(request: FileRequest):
    """
    Get the full contents of a file.

    Args:
        file_path: Path to file (e.g., 'sql/gold/churn_predictions.sql')
    """
    return get_file_contents(request.file_path)


@app.post("/list")
def list_files(request: ListRequest):
    """
    List all SQL files in the repository.

    Args:
        directory: Directory to list (default: 'sql')
    """
    return list_sql_files(request.directory)
