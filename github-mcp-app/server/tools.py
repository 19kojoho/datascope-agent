"""GitHub MCP tools for code search.

These tools enable the DataScope agent to search and retrieve
transformation code from the novatech-transformations repository.
"""

import os
from github import Github


# Configuration
GITHUB_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
REPO_NAME = os.environ.get("GITHUB_REPO", "19kojoho/novatech-transformations")


def get_github_client() -> Github:
    """Get authenticated GitHub client."""
    if not GITHUB_TOKEN:
        raise ValueError(
            "GITHUB_PERSONAL_ACCESS_TOKEN not set. "
            "Configure this secret in your Databricks App."
        )
    return Github(GITHUB_TOKEN)


def search_code(query: str, file_extension: str = ".sql") -> dict:
    """
    Search for code in the novatech-transformations repository.

    Use this to find transformation code containing specific patterns,
    column names, or SQL constructs like CASE statements, JOINs, etc.

    Args:
        query: Search term (e.g., 'churn_risk', 'CASE WHEN', 'LEFT JOIN')
        file_extension: File extension to filter (default: .sql)

    Returns:
        Matching code snippets with file paths and line numbers
    """
    try:
        g = get_github_client()
        repo = g.get_repo(REPO_NAME)

        # Build search query
        search_query = f"{query} repo:{REPO_NAME}"
        if file_extension:
            search_query += f" extension:{file_extension.lstrip('.')}"

        results = g.search_code(search_query)

        matches = []
        for item in list(results)[:5]:  # Limit to 5 results
            try:
                content = item.decoded_content.decode("utf-8")
                lines = content.split("\n")

                # Find matching lines with context
                matching_lines = []
                for i, line in enumerate(lines, 1):
                    if query.lower() in line.lower():
                        # Get surrounding context (3 lines before, 2 after)
                        start = max(0, i - 4)
                        end = min(len(lines), i + 3)
                        context_lines = []
                        for j in range(start, end):
                            prefix = ">>> " if j == i - 1 else "    "
                            context_lines.append(f"{j+1:4d} {prefix}{lines[j]}")
                        context = "\n".join(context_lines)

                        matching_lines.append({
                            "line_number": i,
                            "line": line.strip(),
                            "context": context
                        })

                matches.append({
                    "file": item.path,
                    "url": item.html_url,
                    "matches": matching_lines[:3]  # Limit matches per file
                })
            except Exception:
                continue

        return {
            "query": query,
            "repository": REPO_NAME,
            "total_files": len(matches),
            "results": matches
        }

    except Exception as e:
        return {"error": str(e), "query": query}


def get_file_contents(file_path: str) -> dict:
    """
    Get the full contents of a file from the repository.

    Use this after search_code to retrieve the complete transformation
    SQL file for detailed analysis.

    Args:
        file_path: Path to file (e.g., 'sql/gold/churn_predictions.sql')

    Returns:
        Complete file contents with metadata
    """
    try:
        g = get_github_client()
        repo = g.get_repo(REPO_NAME)

        file_content = repo.get_contents(file_path)
        content = file_content.decoded_content.decode("utf-8")

        # Add line numbers for reference
        lines = content.split("\n")
        numbered_content = "\n".join(
            f"{i+1:4d} | {line}" for i, line in enumerate(lines)
        )

        return {
            "path": file_path,
            "content": content,
            "numbered_content": numbered_content,
            "line_count": len(lines),
            "size_bytes": file_content.size,
            "sha": file_content.sha,
            "url": file_content.html_url
        }

    except Exception as e:
        return {"error": str(e), "path": file_path}


def list_sql_files(directory: str = "sql") -> dict:
    """
    List all SQL transformation files in the repository.

    Use this to discover available transformation files before searching.

    Args:
        directory: Directory to list (default: 'sql')

    Returns:
        List of SQL file paths organized by subdirectory
    """
    try:
        g = get_github_client()
        repo = g.get_repo(REPO_NAME)

        files_by_dir = {}

        def collect_files(path: str):
            try:
                contents = repo.get_contents(path)
                for item in contents:
                    if item.type == "dir":
                        collect_files(item.path)
                    elif item.name.endswith(".sql"):
                        dir_name = os.path.dirname(item.path)
                        if dir_name not in files_by_dir:
                            files_by_dir[dir_name] = []
                        files_by_dir[dir_name].append({
                            "name": item.name,
                            "path": item.path,
                            "size": item.size
                        })
            except Exception:
                pass

        collect_files(directory)

        return {
            "repository": REPO_NAME,
            "directory": directory,
            "files_by_directory": files_by_dir,
            "total_files": sum(len(f) for f in files_by_dir.values())
        }

    except Exception as e:
        return {"error": str(e), "directory": directory}
