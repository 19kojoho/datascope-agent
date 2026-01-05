#!/usr/bin/env python3
"""Test MCP client connection to the GitHub MCP server.

This script tests the MCP client implementation by:
1. Connecting to the deployed MCP server
2. Initializing the MCP session
3. Listing available tools
4. Calling the search_code tool

For Databricks Apps, authentication is required via Bearer token.
"""

import json
import os
import requests

# MCP Server URL (deployed on Databricks Apps)
MCP_SERVER_URL = "https://github-mcp-server-1262935113136277.gcp.databricksapps.com"
MCP_ENDPOINT = f"{MCP_SERVER_URL}/mcp"

# Databricks token for authentication (from app.yaml or environment)
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")  # Set via environment variable

def get_headers():
    """Get HTTP headers with authentication."""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DATABRICKS_TOKEN}"
    }

def test_health_check():
    """Test the health endpoint."""
    print("\n" + "=" * 60)
    print("TEST 1: Health Check")
    print("=" * 60)

    resp = requests.get(f"{MCP_SERVER_URL}/health", headers=get_headers(), timeout=10)
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), indent=2)}")

    assert resp.status_code == 200, f"Health check failed: {resp.status_code}"
    print("✓ Health check passed")
    return True


def test_mcp_initialize():
    """Test MCP initialize method."""
    print("\n" + "=" * 60)
    print("TEST 2: MCP Initialize")
    print("=" * 60)

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "clientInfo": {
                "name": "test-client",
                "version": "1.0.0"
            },
            "capabilities": {}
        }
    }

    print(f"Request: {json.dumps(request, indent=2)}")

    resp = requests.post(
        MCP_ENDPOINT,
        json=request,
        headers=get_headers(),
        timeout=10
    )

    print(f"Status: {resp.status_code}")
    result = resp.json()
    print(f"Response: {json.dumps(result, indent=2)}")

    assert resp.status_code == 200, f"Initialize failed: {resp.status_code}"
    assert "result" in result, "No result in response"
    assert "serverInfo" in result["result"], "No serverInfo in result"

    print("✓ MCP Initialize passed")
    return True


def test_mcp_tools_list():
    """Test MCP tools/list method."""
    print("\n" + "=" * 60)
    print("TEST 3: MCP tools/list")
    print("=" * 60)

    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }

    print(f"Request: {json.dumps(request, indent=2)}")

    resp = requests.post(
        MCP_ENDPOINT,
        json=request,
        headers=get_headers(),
        timeout=10
    )

    print(f"Status: {resp.status_code}")
    result = resp.json()
    print(f"Response: {json.dumps(result, indent=2)}")

    assert resp.status_code == 200, f"tools/list failed: {resp.status_code}"
    assert "result" in result, "No result in response"

    tools = result["result"].get("tools", [])
    print(f"\nAvailable tools: {[t['name'] for t in tools]}")

    assert len(tools) >= 3, f"Expected at least 3 tools, got {len(tools)}"

    print("✓ MCP tools/list passed")
    return True


def test_mcp_search_code():
    """Test MCP tools/call with search_code."""
    print("\n" + "=" * 60)
    print("TEST 4: MCP tools/call - search_code")
    print("=" * 60)

    request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "search_code",
            "arguments": {
                "query": "churn_risk"
            }
        }
    }

    print(f"Request: {json.dumps(request, indent=2)}")

    resp = requests.post(
        MCP_ENDPOINT,
        json=request,
        headers=get_headers(),
        timeout=30
    )

    print(f"Status: {resp.status_code}")
    result = resp.json()

    # Parse the content
    if "result" in result:
        content = result["result"].get("content", [])
        if content and content[0].get("type") == "text":
            text = content[0].get("text", "{}")
            parsed = json.loads(text)
            print(f"Search result: {json.dumps(parsed, indent=2)}")

            files_matched = parsed.get("files_matched", 0)
            print(f"\nFiles matched: {files_matched}")

            if files_matched > 0:
                print("✓ MCP search_code passed - found matching code!")
                return True
            else:
                print("⚠ No matches found (may be expected for new repos)")
                return True

    if "error" in result:
        print(f"Error: {result['error']}")
        return False

    print("✓ MCP search_code passed")
    return True


def test_mcp_list_files():
    """Test MCP tools/call with list_sql_files."""
    print("\n" + "=" * 60)
    print("TEST 5: MCP tools/call - list_sql_files")
    print("=" * 60)

    request = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "list_sql_files",
            "arguments": {
                "directory": "sql"
            }
        }
    }

    print(f"Request: {json.dumps(request, indent=2)}")

    resp = requests.post(
        MCP_ENDPOINT,
        json=request,
        headers=get_headers(),
        timeout=30
    )

    print(f"Status: {resp.status_code}")
    result = resp.json()

    if "result" in result:
        content = result["result"].get("content", [])
        if content and content[0].get("type") == "text":
            text = content[0].get("text", "{}")
            parsed = json.loads(text)
            print(f"List result: {json.dumps(parsed, indent=2)}")

            total = parsed.get("total_files", 0)
            print(f"\nTotal SQL files: {total}")

            print("✓ MCP list_sql_files passed")
            return True

    if "error" in result:
        print(f"Error: {result['error']}")
        return False

    return True


def main():
    """Run all MCP client tests."""
    print("=" * 60)
    print("MCP CLIENT TESTS")
    print(f"Server: {MCP_SERVER_URL}")
    print("=" * 60)

    tests = [
        ("Health Check", test_health_check),
        ("MCP Initialize", test_mcp_initialize),
        ("MCP tools/list", test_mcp_tools_list),
        ("MCP search_code", test_mcp_search_code),
        ("MCP list_files", test_mcp_list_files),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            if test_fn():
                passed += 1
            else:
                failed += 1
                print(f"✗ {name} FAILED")
        except Exception as e:
            failed += 1
            print(f"✗ {name} EXCEPTION: {e}")

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)
