# Databricks notebook source
# MAGIC %md
# MAGIC # DataScope MLflow Monitoring Setup
# MAGIC This notebook sets up MLflow tracking for DataScope investigations.

# COMMAND ----------

import mlflow
from mlflow.tracking import MlflowClient

# Set experiment
EXPERIMENT_NAME = "/Shared/datascope-investigations"

try:
    experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
    if experiment is None:
        experiment_id = mlflow.create_experiment(EXPERIMENT_NAME)
        print(f"Created experiment: {EXPERIMENT_NAME}")
    else:
        experiment_id = experiment.experiment_id
        print(f"Using existing experiment: {EXPERIMENT_NAME}")

    mlflow.set_experiment(EXPERIMENT_NAME)
except Exception as e:
    print(f"Error setting up experiment: {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create a sample investigation run

# COMMAND ----------

# Example: Log an investigation
with mlflow.start_run(run_name="sample-investigation"):
    # Log parameters
    mlflow.log_param("question", "Why do some customers have NULL churn_risk?")
    mlflow.log_param("user_id", "sample_user")

    # Log metrics
    mlflow.log_metric("duration_seconds", 5.2)
    mlflow.log_metric("tools_called", 3)
    mlflow.log_metric("sql_queries_executed", 2)

    # Log tags
    mlflow.set_tag("bug_id", "BUG-005")
    mlflow.set_tag("pattern_matched", "PAT-005")
    mlflow.set_tag("root_cause_found", "true")

    # Log the response as an artifact
    response = """
    **What I Found:** 16 customers have NULL churn_risk scores.

    **The Problem:** Missing ELSE clause in CASE statement.

    **Why It Happened:** The churn model uses a CASE statement that doesn't handle customers with no activity data.

    **How Many Records:** 16 out of 500 customers (3.2%)

    **How to Fix It:** Add ELSE 'unknown' to the CASE statement in gold.churn_predictions.
    """

    # Save response to file and log
    with open("/tmp/investigation_response.txt", "w") as f:
        f.write(response)
    mlflow.log_artifact("/tmp/investigation_response.txt")

print("Sample run logged successfully!")

# COMMAND ----------

# MAGIC %md
# MAGIC ## View recent investigations

# COMMAND ----------

client = MlflowClient()

# Get recent runs
runs = client.search_runs(
    experiment_ids=[experiment_id],
    max_results=10,
    order_by=["start_time DESC"]
)

print(f"Found {len(runs)} recent investigations:\n")
for run in runs:
    print(f"Run: {run.info.run_name}")
    print(f"  Duration: {run.data.metrics.get('duration_seconds', 'N/A')}s")
    print(f"  Bug ID: {run.data.tags.get('bug_id', 'N/A')}")
    print(f"  Root Cause Found: {run.data.tags.get('root_cause_found', 'N/A')}")
    print()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Analytics Dashboard Queries
# MAGIC
# MAGIC Use these SQL queries to analyze investigation patterns:

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Investigation success rate
# MAGIC SELECT
# MAGIC   COUNT(*) as total_investigations,
# MAGIC   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successful,
# MAGIC   AVG(duration_seconds) as avg_duration_seconds
# MAGIC FROM novatech.datascope.investigations
# MAGIC WHERE started_at >= CURRENT_DATE - INTERVAL 7 DAY

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Most common patterns found
# MAGIC SELECT
# MAGIC   patterns_matched,
# MAGIC   COUNT(*) as occurrences
# MAGIC FROM novatech.datascope.investigations
# MAGIC WHERE patterns_matched IS NOT NULL
# MAGIC GROUP BY patterns_matched
# MAGIC ORDER BY occurrences DESC
# MAGIC LIMIT 10

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Tools usage frequency
# MAGIC SELECT
# MAGIC   EXPLODE(FROM_JSON(tools_used, 'ARRAY<STRING>')) as tool,
# MAGIC   COUNT(*) as usage_count
# MAGIC FROM novatech.datascope.investigations
# MAGIC WHERE tools_used IS NOT NULL
# MAGIC GROUP BY tool
# MAGIC ORDER BY usage_count DESC

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done!
# MAGIC
# MAGIC MLflow monitoring is configured:
# MAGIC - Experiment: `/Shared/datascope-investigations`
# MAGIC - Tracks: duration, tools used, patterns matched, success rate
# MAGIC - Query Lakebase tables for analytics
