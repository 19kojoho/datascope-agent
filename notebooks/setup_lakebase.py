# Databricks notebook source
# MAGIC %md
# MAGIC # DataScope Lakebase Setup
# MAGIC This notebook sets up Lakebase for conversation state management.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Create schema for DataScope agent state
# MAGIC CREATE SCHEMA IF NOT EXISTS novatech.datascope;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Create conversations table for storing chat history
# MAGIC CREATE TABLE IF NOT EXISTS novatech.datascope.conversations (
# MAGIC   conversation_id STRING NOT NULL,
# MAGIC   user_id STRING,
# MAGIC   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
# MAGIC   updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
# MAGIC   title STRING,
# MAGIC   status STRING DEFAULT 'active'
# MAGIC )
# MAGIC USING DELTA
# MAGIC TBLPROPERTIES (delta.enableChangeDataFeed = true);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Create messages table for individual messages
# MAGIC CREATE TABLE IF NOT EXISTS novatech.datascope.messages (
# MAGIC   message_id STRING NOT NULL,
# MAGIC   conversation_id STRING NOT NULL,
# MAGIC   role STRING NOT NULL,  -- 'user', 'assistant', 'system', 'tool'
# MAGIC   content STRING,
# MAGIC   tool_calls STRING,  -- JSON array of tool calls
# MAGIC   tool_call_id STRING,  -- For tool response messages
# MAGIC   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
# MAGIC   tokens_used INT
# MAGIC )
# MAGIC USING DELTA
# MAGIC TBLPROPERTIES (delta.enableChangeDataFeed = true);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Create investigations table for tracking investigation metadata
# MAGIC CREATE TABLE IF NOT EXISTS novatech.datascope.investigations (
# MAGIC   investigation_id STRING NOT NULL,
# MAGIC   conversation_id STRING NOT NULL,
# MAGIC   question STRING NOT NULL,
# MAGIC   status STRING DEFAULT 'in_progress',  -- 'in_progress', 'completed', 'failed'
# MAGIC   started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
# MAGIC   completed_at TIMESTAMP,
# MAGIC   duration_seconds FLOAT,
# MAGIC   tools_used STRING,  -- JSON array
# MAGIC   patterns_matched STRING,  -- JSON array of pattern_ids
# MAGIC   tables_queried STRING,  -- JSON array
# MAGIC   root_cause_found BOOLEAN,
# MAGIC   bug_id STRING,  -- If a known bug was identified
# MAGIC   summary STRING
# MAGIC )
# MAGIC USING DELTA
# MAGIC TBLPROPERTIES (delta.enableChangeDataFeed = true);

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify Tables Created

# COMMAND ----------

# MAGIC %sql
# MAGIC SHOW TABLES IN novatech.datascope;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Insert

# COMMAND ----------

import uuid
from datetime import datetime

# Test conversation
conv_id = str(uuid.uuid4())
spark.sql(f"""
INSERT INTO novatech.datascope.conversations (conversation_id, user_id, title)
VALUES ('{conv_id}', 'test_user', 'Test conversation for NULL churn_risk')
""")

# Test message
msg_id = str(uuid.uuid4())
spark.sql(f"""
INSERT INTO novatech.datascope.messages (message_id, conversation_id, role, content)
VALUES ('{msg_id}', '{conv_id}', 'user', 'Why do some customers have NULL churn_risk?')
""")

print(f"Created test conversation: {conv_id}")

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM novatech.datascope.conversations LIMIT 5;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM novatech.datascope.messages LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cleanup Test Data (Optional)

# COMMAND ----------

# Uncomment to clean up test data
# spark.sql("DELETE FROM novatech.datascope.messages WHERE conversation_id LIKE '%test%'")
# spark.sql("DELETE FROM novatech.datascope.conversations WHERE user_id = 'test_user'")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Analytics Queries
# MAGIC
# MAGIC Run these queries to monitor DataScope usage:

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Daily investigation summary
# MAGIC SELECT
# MAGIC   DATE(started_at) as day,
# MAGIC   COUNT(*) as investigations,
# MAGIC   ROUND(AVG(duration_seconds), 2) as avg_duration_seconds,
# MAGIC   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successful
# MAGIC FROM novatech.datascope.investigations
# MAGIC WHERE started_at >= CURRENT_DATE - INTERVAL 7 DAY
# MAGIC GROUP BY 1
# MAGIC ORDER BY 1 DESC

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Most common tools used
# MAGIC SELECT
# MAGIC   tool,
# MAGIC   COUNT(*) as usage_count
# MAGIC FROM (
# MAGIC   SELECT EXPLODE(FROM_JSON(tools_used, 'ARRAY<STRING>')) as tool
# MAGIC   FROM novatech.datascope.investigations
# MAGIC   WHERE tools_used IS NOT NULL
# MAGIC )
# MAGIC GROUP BY tool
# MAGIC ORDER BY usage_count DESC

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Recent investigations
# MAGIC SELECT
# MAGIC   question,
# MAGIC   status,
# MAGIC   duration_seconds,
# MAGIC   started_at
# MAGIC FROM novatech.datascope.investigations
# MAGIC ORDER BY started_at DESC
# MAGIC LIMIT 10

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done!
# MAGIC
# MAGIC Lakebase tables created:
# MAGIC - `novatech.datascope.conversations` - Conversation sessions
# MAGIC - `novatech.datascope.messages` - Individual messages
# MAGIC - `novatech.datascope.investigations` - Investigation metadata & analytics
# MAGIC
# MAGIC Use the `/stats` endpoint in the UI app to get real-time metrics.
