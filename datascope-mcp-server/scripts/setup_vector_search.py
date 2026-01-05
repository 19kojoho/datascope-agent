#!/usr/bin/env python3
"""
DataScope Vector Search Setup Script

This script creates the infrastructure needed for semantic pattern matching:
1. Creates the patterns table in Unity Catalog
2. Loads pattern library data from JSON
3. Creates a Vector Search endpoint (if needed)
4. Creates a Vector Search index for semantic search

Prerequisites:
- Databricks workspace with Unity Catalog enabled
- Vector Search enabled on the workspace
- DATABRICKS_HOST and DATABRICKS_TOKEN environment variables set
- Catalog and schema must exist (e.g., novatech.gold)

Usage:
    python setup_vector_search.py

Or run cells in Databricks notebook.
"""

import os
import json
import time
import requests
from pathlib import Path

# Configuration
CATALOG = "novatech"
SCHEMA = "gold"
TABLE_NAME = "datascope_patterns"
ENDPOINT_NAME = "datascope-vs-endpoint"
INDEX_NAME = f"{CATALOG}.{SCHEMA}.datascope_patterns_index"
EMBEDDING_MODEL = "databricks-bge-large-en"

# Databricks connection
DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
SQL_WAREHOUSE_ID = os.environ.get("SQL_WAREHOUSE_ID", "")

def get_headers():
    """Get authorization headers for Databricks API."""
    return {
        "Authorization": f"Bearer {DATABRICKS_TOKEN}",
        "Content-Type": "application/json"
    }

def execute_sql(query: str) -> dict:
    """Execute SQL via Databricks SQL Statement API."""
    url = f"{DATABRICKS_HOST}/api/2.0/sql/statements"
    payload = {
        "warehouse_id": SQL_WAREHOUSE_ID,
        "statement": query,
        "wait_timeout": "30s"
    }

    resp = requests.post(url, headers=get_headers(), json=payload)
    if resp.status_code != 200:
        raise Exception(f"SQL execution failed: {resp.text}")

    result = resp.json()
    if result.get("status", {}).get("state") == "FAILED":
        error = result.get("status", {}).get("error", {}).get("message", "Unknown error")
        raise Exception(f"SQL failed: {error}")

    return result

def load_pattern_library() -> list:
    """Load patterns from the JSON file."""
    # Try multiple paths (local dev vs Databricks)
    paths = [
        Path(__file__).parent.parent.parent / "config" / "pattern_library.json",
        Path("/Workspace/datascope/config/pattern_library.json"),
        Path("config/pattern_library.json")
    ]

    for path in paths:
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                return data.get("patterns", [])

    raise FileNotFoundError("pattern_library.json not found")

def step1_create_table():
    """Step 1: Create the patterns table in Unity Catalog."""
    print("\n" + "="*60)
    print("Step 1: Creating patterns table")
    print("="*60)

    # Drop if exists for clean setup
    drop_sql = f"DROP TABLE IF EXISTS {CATALOG}.{SCHEMA}.{TABLE_NAME}"
    print(f"  Dropping existing table (if any)...")
    execute_sql(drop_sql)

    # Create table with schema optimized for Vector Search
    # Note: symptoms is stored as STRING (JSON array) for easier embedding
    create_sql = f"""
    CREATE TABLE {CATALOG}.{SCHEMA}.{TABLE_NAME} (
        pattern_id STRING NOT NULL COMMENT 'Unique pattern identifier (e.g., PAT-001)',
        title STRING NOT NULL COMMENT 'Short descriptive title of the pattern',
        category STRING COMMENT 'Pattern category (Data Quality, Business Logic, Pipeline, etc.)',
        symptoms STRING COMMENT 'JSON array of symptom descriptions - used for semantic matching',
        root_cause STRING COMMENT 'Explanation of why this issue occurs',
        resolution STRING COMMENT 'How to fix the issue',
        investigation_sql STRING COMMENT 'Example SQL to investigate this pattern',
        related_bugs STRING COMMENT 'JSON array of related bug IDs',
        databricks_features STRING COMMENT 'JSON array of relevant Databricks features',

        -- Computed column for Vector Search embedding
        -- Combines title + symptoms + root_cause for better semantic matching
        embedding_text STRING GENERATED ALWAYS AS (
            CONCAT(
                'Issue: ', title, '. ',
                'Symptoms: ', COALESCE(symptoms, ''), '. ',
                'Cause: ', COALESCE(root_cause, '')
            )
        ) COMMENT 'Combined text for embedding generation'
    )
    USING DELTA
    COMMENT 'DataScope pattern library for semantic search of data quality issues'
    TBLPROPERTIES (
        'delta.enableChangeDataFeed' = 'true',
        'delta.columnMapping.mode' = 'name'
    )
    """

    print(f"  Creating table {CATALOG}.{SCHEMA}.{TABLE_NAME}...")
    execute_sql(create_sql)
    print("  Table created successfully!")

def step2_load_data():
    """Step 2: Load pattern library data into the table."""
    print("\n" + "="*60)
    print("Step 2: Loading pattern data")
    print("="*60)

    patterns = load_pattern_library()
    print(f"  Found {len(patterns)} patterns to load")

    for pattern in patterns:
        # Convert arrays to JSON strings
        symptoms_json = json.dumps(pattern.get("symptoms", []))
        related_bugs_json = json.dumps(pattern.get("related_bugs", []))
        features_json = json.dumps(pattern.get("databricks_features", []))

        # Escape single quotes for SQL
        def escape_sql(s):
            if s is None:
                return "NULL"
            return "'" + str(s).replace("'", "''") + "'"

        insert_sql = f"""
        INSERT INTO {CATALOG}.{SCHEMA}.{TABLE_NAME}
        (pattern_id, title, category, symptoms, root_cause, resolution,
         investigation_sql, related_bugs, databricks_features)
        VALUES (
            {escape_sql(pattern.get('pattern_id'))},
            {escape_sql(pattern.get('title'))},
            {escape_sql(pattern.get('category'))},
            {escape_sql(symptoms_json)},
            {escape_sql(pattern.get('root_cause'))},
            {escape_sql(pattern.get('resolution'))},
            {escape_sql(pattern.get('investigation_sql'))},
            {escape_sql(related_bugs_json)},
            {escape_sql(features_json)}
        )
        """

        execute_sql(insert_sql)
        print(f"    Loaded: {pattern.get('pattern_id')} - {pattern.get('title')[:40]}...")

    print(f"  Loaded {len(patterns)} patterns successfully!")

def step3_create_endpoint():
    """Step 3: Create Vector Search endpoint (if needed)."""
    print("\n" + "="*60)
    print("Step 3: Creating Vector Search endpoint")
    print("="*60)

    # Check if endpoint exists
    url = f"{DATABRICKS_HOST}/api/2.0/vector-search/endpoints/{ENDPOINT_NAME}"
    resp = requests.get(url, headers=get_headers())

    if resp.status_code == 200:
        endpoint = resp.json()
        status = endpoint.get("endpoint_status", {}).get("state", "UNKNOWN")
        print(f"  Endpoint '{ENDPOINT_NAME}' already exists (status: {status})")

        if status == "ONLINE":
            return True
        elif status in ["PROVISIONING", "PENDING"]:
            print("  Waiting for endpoint to come online...")
            return wait_for_endpoint()
        else:
            print(f"  Warning: Endpoint in unexpected state: {status}")
            return False

    # Create new endpoint
    print(f"  Creating new endpoint '{ENDPOINT_NAME}'...")
    url = f"{DATABRICKS_HOST}/api/2.0/vector-search/endpoints"
    payload = {
        "name": ENDPOINT_NAME,
        "endpoint_type": "STANDARD"  # STANDARD is more cost-effective for small workloads
    }

    resp = requests.post(url, headers=get_headers(), json=payload)
    if resp.status_code not in [200, 201]:
        raise Exception(f"Failed to create endpoint: {resp.text}")

    print("  Endpoint creation initiated. Waiting for it to come online...")
    return wait_for_endpoint()

def wait_for_endpoint(max_wait_minutes=15):
    """Wait for endpoint to become online."""
    url = f"{DATABRICKS_HOST}/api/2.0/vector-search/endpoints/{ENDPOINT_NAME}"
    start_time = time.time()
    max_wait_seconds = max_wait_minutes * 60

    while time.time() - start_time < max_wait_seconds:
        resp = requests.get(url, headers=get_headers())
        if resp.status_code == 200:
            status = resp.json().get("endpoint_status", {}).get("state", "UNKNOWN")
            elapsed = int(time.time() - start_time)
            print(f"    Status: {status} ({elapsed}s elapsed)")

            if status == "ONLINE":
                print("  Endpoint is online!")
                return True
            elif status in ["FAILED", "TERMINATED"]:
                raise Exception(f"Endpoint failed to start: {status}")

        time.sleep(30)  # Check every 30 seconds

    raise Exception(f"Endpoint did not come online within {max_wait_minutes} minutes")

def step4_create_index():
    """Step 4: Create Vector Search index."""
    print("\n" + "="*60)
    print("Step 4: Creating Vector Search index")
    print("="*60)

    # Check if index exists
    url = f"{DATABRICKS_HOST}/api/2.0/vector-search/indexes/{INDEX_NAME}"
    resp = requests.get(url, headers=get_headers())

    if resp.status_code == 200:
        index = resp.json()
        status = index.get("status", {}).get("state", "UNKNOWN")
        print(f"  Index '{INDEX_NAME}' already exists (status: {status})")

        if status == "ONLINE":
            return True
        elif status in ["PROVISIONING", "PENDING"]:
            print("  Waiting for index to come online...")
            return wait_for_index()

    # Create new index
    # Using Delta Sync index for automatic updates when table changes
    print(f"  Creating new index '{INDEX_NAME}'...")
    url = f"{DATABRICKS_HOST}/api/2.0/vector-search/indexes"

    payload = {
        "name": INDEX_NAME,
        "endpoint_name": ENDPOINT_NAME,
        "primary_key": "pattern_id",
        "index_type": "DELTA_SYNC",  # Auto-syncs with Delta table
        "delta_sync_index_spec": {
            "source_table": f"{CATALOG}.{SCHEMA}.{TABLE_NAME}",
            "pipeline_type": "TRIGGERED",  # Manual trigger vs CONTINUOUS
            "embedding_source_columns": [
                {
                    "name": "embedding_text",  # Use computed column
                    "embedding_model_endpoint_name": EMBEDDING_MODEL
                }
            ]
        }
    }

    resp = requests.post(url, headers=get_headers(), json=payload)
    if resp.status_code not in [200, 201]:
        error_detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise Exception(f"Failed to create index: {error_detail}")

    print("  Index creation initiated. Waiting for it to come online...")
    return wait_for_index()

def wait_for_index(max_wait_minutes=20):
    """Wait for index to become online."""
    url = f"{DATABRICKS_HOST}/api/2.0/vector-search/indexes/{INDEX_NAME}"
    start_time = time.time()
    max_wait_seconds = max_wait_minutes * 60

    while time.time() - start_time < max_wait_seconds:
        resp = requests.get(url, headers=get_headers())
        if resp.status_code == 200:
            index = resp.json()
            status = index.get("status", {}).get("state", "UNKNOWN")
            ready = index.get("status", {}).get("ready", False)
            elapsed = int(time.time() - start_time)
            print(f"    Status: {status}, Ready: {ready} ({elapsed}s elapsed)")

            if status == "ONLINE" and ready:
                print("  Index is online and ready!")
                return True
            elif status == "FAILED":
                message = index.get("status", {}).get("message", "Unknown error")
                raise Exception(f"Index failed: {message}")

        time.sleep(30)  # Check every 30 seconds

    raise Exception(f"Index did not come online within {max_wait_minutes} minutes")

def step5_test_search():
    """Step 5: Test the Vector Search index."""
    print("\n" + "="*60)
    print("Step 5: Testing Vector Search")
    print("="*60)

    test_queries = [
        "Why do some customers have NULL values in their risk score?",
        "Metrics don't match between Finance and the dashboard",
        "Customer marked as churned but they logged in yesterday"
    ]

    url = f"{DATABRICKS_HOST}/api/2.0/vector-search/indexes/{INDEX_NAME}/query"

    for query in test_queries:
        print(f"\n  Query: \"{query[:50]}...\"")

        payload = {
            "query_text": query,
            "columns": ["pattern_id", "title", "root_cause"],
            "num_results": 2
        }

        resp = requests.post(url, headers=get_headers(), json=payload)
        if resp.status_code != 200:
            print(f"    Error: {resp.text[:100]}")
            continue

        results = resp.json().get("result", {}).get("data_array", [])
        for i, row in enumerate(results, 1):
            if len(row) >= 2:
                print(f"    {i}. {row[0]}: {row[1][:50]}...")

def print_summary():
    """Print setup summary and next steps."""
    print("\n" + "="*60)
    print("SETUP COMPLETE")
    print("="*60)
    print(f"""
Resources Created:
  - Table: {CATALOG}.{SCHEMA}.{TABLE_NAME}
  - Endpoint: {ENDPOINT_NAME}
  - Index: {INDEX_NAME}

Next Steps:
  1. Add this to your MCP server .env file:
     VS_INDEX={INDEX_NAME}

  2. Restart the MCP server to enable pattern search

  3. Test with: "Why do some customers have NULL churn_risk?"
     The agent should find PAT-005 and related investigation steps.

Maintenance:
  - Patterns auto-sync when table is updated (DELTA_SYNC)
  - To add new patterns, INSERT into the table
  - Monitor index health in Databricks UI > Compute > Vector Search
""")

def main():
    """Run the complete setup."""
    print("\n" + "="*60)
    print("DataScope Vector Search Setup")
    print("="*60)

    # Validate environment
    if not DATABRICKS_HOST:
        raise ValueError("DATABRICKS_HOST environment variable not set")
    if not DATABRICKS_TOKEN:
        raise ValueError("DATABRICKS_TOKEN environment variable not set")
    if not SQL_WAREHOUSE_ID:
        raise ValueError("SQL_WAREHOUSE_ID environment variable not set")

    print(f"\nConfiguration:")
    print(f"  Host: {DATABRICKS_HOST[:40]}...")
    print(f"  Catalog: {CATALOG}")
    print(f"  Schema: {SCHEMA}")
    print(f"  Table: {TABLE_NAME}")
    print(f"  Endpoint: {ENDPOINT_NAME}")
    print(f"  Index: {INDEX_NAME}")

    try:
        step1_create_table()
        step2_load_data()
        step3_create_endpoint()
        step4_create_index()
        step5_test_search()
        print_summary()

    except Exception as e:
        print(f"\n ERROR: {e}")
        print("\nTroubleshooting:")
        print("  - Check that Unity Catalog is enabled")
        print("  - Check that Vector Search is enabled on your workspace")
        print("  - Verify your token has sufficient permissions")
        print("  - Check the Databricks workspace logs for details")
        raise

if __name__ == "__main__":
    main()
