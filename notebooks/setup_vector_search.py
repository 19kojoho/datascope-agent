# Databricks notebook source
# MAGIC %md
# MAGIC # DataScope Vector Search Setup
# MAGIC This notebook sets up Vector Search for finding similar data quality patterns.

# COMMAND ----------

# MAGIC %pip install databricks-vectorsearch
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import json
from databricks.vector_search.client import VectorSearchClient

# Configuration
CATALOG = "novatech"
SCHEMA = "gold"
PATTERNS_TABLE = f"{CATALOG}.{SCHEMA}.datascope_patterns"
VS_ENDPOINT = "datascope-vs-endpoint"
VS_INDEX = f"{CATALOG}.{SCHEMA}.datascope_patterns_index"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: Create the patterns Delta table

# COMMAND ----------

# Load pattern library
patterns_json = """
{
  "patterns": [
    {
      "pattern_id": "PAT-001",
      "title": "Timezone Mismatch Between Source Systems",
      "category": "Data Quality",
      "symptoms": "Records appear to be from the wrong time period. Activity looks delayed by several hours. Time-based filters miss recent data. Regional discrepancies (APAC/EMEA affected differently). Customers marked as inactive despite recent logins.",
      "root_cause": "Different source systems use different timezone conventions (UTC vs local time). When combined without conversion, timestamps are inconsistent.",
      "resolution": "Standardize all timestamps to UTC at ingestion. Use CONVERT_TIMEZONE() or equivalent.",
      "investigation_sql": "SELECT customer_id, usage_timestamp, created_at_utc, created_at_local, TIMESTAMPDIFF(HOUR, created_at_local, created_at_utc) as hour_diff FROM silver.fct_subscriptions WHERE created_at_utc != created_at_local"
    },
    {
      "pattern_id": "PAT-002",
      "title": "Late-Arriving Data Not Reflected in Status",
      "category": "Data Quality",
      "symptoms": "Status shows complete but action hasn't finished. Discrepancy between reported status and actual state. Users complain status is wrong. Reconciliation issues with external systems.",
      "root_cause": "Status calculation uses request/initiation timestamp instead of completion/processing timestamp.",
      "resolution": "Use completion timestamp for status calculations. Handle NULL completion timestamps as pending.",
      "investigation_sql": "SELECT payment_id, payment_date, processed_at, DATEDIFF(processed_at, payment_date) as delay_days FROM silver.fct_payments WHERE processed_at > payment_date"
    },
    {
      "pattern_id": "PAT-003",
      "title": "Aggregation Excludes Relevant Records",
      "category": "Business Logic",
      "symptoms": "Totals don't match between reports. Metrics consistently understated. Finance/business team reports different numbers. Specific segments or products missing from totals.",
      "root_cause": "WHERE clause or JOIN condition filters out records that should be included.",
      "resolution": "Remove incorrect filters. Create separate metrics for filtered vs unfiltered totals.",
      "investigation_sql": "SELECT product_type, SUM(mrr) * 12 as arr FROM silver.fct_subscriptions WHERE status = 'active' GROUP BY product_type"
    },
    {
      "pattern_id": "PAT-004",
      "title": "Duplicate Records Inflating Metrics",
      "category": "Data Quality",
      "symptoms": "Metrics higher than expected. Record counts don't match source system. Reconciliation shows positive variance. Same ID appears multiple times.",
      "root_cause": "Duplicate records from source system not deduplicated during ingestion.",
      "resolution": "Add deduplication using DISTINCT, ROW_NUMBER(), or QUALIFY. Implement idempotent ingestion.",
      "investigation_sql": "SELECT payment_id, COUNT(*) as occurrences FROM silver.fct_payments GROUP BY payment_id HAVING COUNT(*) > 1"
    },
    {
      "pattern_id": "PAT-005",
      "title": "NULL Values Not Handled in Conditional Logic",
      "category": "Data Quality",
      "symptoms": "NULL values in fields that should have values. Records missing from filtered results. CASE statements returning unexpected NULL. Aggregations excluding records silently.",
      "root_cause": "CASE statements or WHERE clauses don't handle NULL cases.",
      "resolution": "Add ELSE clause to CASE statements. Use COALESCE() for default values.",
      "investigation_sql": "SELECT COUNT(*) as null_count FROM gold.churn_predictions WHERE churn_risk IS NULL"
    },
    {
      "pattern_id": "PAT-006",
      "title": "Join Fanout Causing Row Multiplication",
      "category": "Data Modeling",
      "symptoms": "More rows than expected in output. Metrics change when unrelated data changes. Aggregations produce wrong results. Same entity appears multiple times.",
      "root_cause": "1:N or N:M join creates cartesian product effect.",
      "resolution": "Aggregate before joining. Use subqueries to flatten N side first.",
      "investigation_sql": "SELECT customer_id, COUNT(*) as row_count FROM gold.customer_health_scores GROUP BY customer_id HAVING COUNT(*) > 1"
    },
    {
      "pattern_id": "PAT-007",
      "title": "Schema Drift Breaking Downstream",
      "category": "Pipeline",
      "symptoms": "Pipeline failures after source system update. New columns appearing in source data. Changed column types causing errors. Missing columns in downstream tables.",
      "root_cause": "Source system schema changed without coordination.",
      "resolution": "Implement schema validation at ingestion. Add explicit column selection.",
      "investigation_sql": "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'salesforce_accounts_raw' ORDER BY ordinal_position"
    },
    {
      "pattern_id": "PAT-008",
      "title": "Incorrect Customer Classification",
      "category": "Business Logic",
      "symptoms": "Customer shows wrong status (active/churned). Segment assignment incorrect. Risk level doesn't match behavior. Business team disputes classification.",
      "root_cause": "Classification logic doesn't match current business rules.",
      "resolution": "Update classification logic to match business rules. Add documentation for thresholds.",
      "investigation_sql": "SELECT customer_id, churn_risk, avg_logins, last_activity FROM gold.churn_predictions WHERE customer_id = 'CUST-XXXXX'"
    },
    {
      "pattern_id": "PAT-009",
      "title": "Metric Discrepancy Between Reports",
      "category": "Business Logic",
      "symptoms": "Different reports show different values for same metric. Finance and ops teams have different numbers. Historical values don't match current calculations. Stakeholder confusion about source of truth.",
      "root_cause": "Multiple definitions or calculations for the same metric.",
      "resolution": "Establish single source of truth. Document metric definitions.",
      "investigation_sql": "SELECT 'gold' as source, SUM(arr) as total FROM gold.arr_by_customer UNION ALL SELECT 'silver', SUM(mrr)*12 FROM silver.fct_subscriptions WHERE status='active'"
    },
    {
      "pattern_id": "PAT-010",
      "title": "Missing Historical Data",
      "category": "Data Quality",
      "symptoms": "Gaps in time series data. Historical trends look incomplete. YoY comparisons fail. Specific date ranges missing.",
      "root_cause": "Data retention policies, failed backfills, or pipeline outages.",
      "resolution": "Backfill missing data. Add data completeness monitoring.",
      "investigation_sql": "SELECT DATE_TRUNC('day', usage_date) as day, COUNT(*) FROM silver.fct_product_usage GROUP BY 1 ORDER BY 1"
    }
  ]
}
"""

patterns = json.loads(patterns_json)["patterns"]

# Create DataFrame
from pyspark.sql.types import StructType, StructField, StringType

schema = StructType([
    StructField("pattern_id", StringType(), False),
    StructField("title", StringType(), False),
    StructField("category", StringType(), False),
    StructField("symptoms", StringType(), False),
    StructField("root_cause", StringType(), False),
    StructField("resolution", StringType(), False),
    StructField("investigation_sql", StringType(), True)
])

df = spark.createDataFrame(patterns, schema)

# Add a combined text column for embedding
from pyspark.sql.functions import concat_ws

df = df.withColumn(
    "search_text",
    concat_ws(" | ", df.title, df.symptoms, df.root_cause)
)

# Write to Delta table with change data feed enabled (required for Vector Search sync)
df.write.format("delta") \
    .mode("overwrite") \
    .option("overwriteSchema", "true") \
    .saveAsTable(PATTERNS_TABLE)

# Enable change data feed
spark.sql(f"ALTER TABLE {PATTERNS_TABLE} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

print(f"Created table: {PATTERNS_TABLE}")
display(spark.table(PATTERNS_TABLE))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: Create Vector Search Endpoint

# COMMAND ----------

vsc = VectorSearchClient()

# Create endpoint if it doesn't exist
try:
    endpoint = vsc.get_endpoint(VS_ENDPOINT)
    print(f"Endpoint {VS_ENDPOINT} already exists")
except Exception as e:
    print(f"Creating endpoint {VS_ENDPOINT}...")
    vsc.create_endpoint(
        name=VS_ENDPOINT,
        endpoint_type="STANDARD"
    )
    print(f"Created endpoint: {VS_ENDPOINT}")

# Wait for endpoint to be ready
import time
for i in range(30):
    endpoint = vsc.get_endpoint(VS_ENDPOINT)
    status = endpoint.get("endpoint_status", {}).get("state", "UNKNOWN")
    print(f"Endpoint status: {status}")
    if status == "ONLINE":
        break
    time.sleep(10)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: Create Vector Search Index

# COMMAND ----------

# Create the index
try:
    index = vsc.get_index(VS_ENDPOINT, VS_INDEX)
    print(f"Index {VS_INDEX} already exists")
except Exception as e:
    print(f"Creating index {VS_INDEX}...")
    vsc.create_delta_sync_index(
        endpoint_name=VS_ENDPOINT,
        index_name=VS_INDEX,
        source_table_name=PATTERNS_TABLE,
        pipeline_type="TRIGGERED",
        primary_key="pattern_id",
        embedding_source_column="search_text",
        embedding_model_endpoint_name="databricks-bge-large-en"
    )
    print(f"Created index: {VS_INDEX}")

# Wait for index to be ready
for i in range(30):
    index = vsc.get_index(VS_ENDPOINT, VS_INDEX)
    status = index.get("status", {}).get("ready", False)
    print(f"Index ready: {status}")
    if status:
        break
    time.sleep(10)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Test the Vector Search

# COMMAND ----------

# Test search
index = vsc.get_index(VS_ENDPOINT, VS_INDEX)

# Search for a pattern
results = index.similarity_search(
    query_text="Why do some customers have NULL values in their churn risk score?",
    columns=["pattern_id", "title", "symptoms", "root_cause", "resolution", "investigation_sql"],
    num_results=3
)

print("Search Results:")
for row in results.get("result", {}).get("data_array", []):
    print(f"\n--- {row[0]}: {row[1]} ---")
    print(f"Symptoms: {row[2][:200]}...")
    print(f"Root Cause: {row[3]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done!
# MAGIC
# MAGIC Vector Search is now configured. The DataScope agent can use:
# MAGIC - Endpoint: `datascope-vs-endpoint`
# MAGIC - Index: `novatech.gold.datascope_patterns_index`
