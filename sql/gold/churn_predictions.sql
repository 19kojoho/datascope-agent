-- =============================================================================
-- Transformation: Churn Predictions
-- Owner: Data Science Team
-- Schedule: Daily at 5:00 AM UTC
-- Source: novatech.silver.dim_customers, novatech.silver.fct_product_usage, 
--         novatech.silver.fct_payments
-- Target: novatech.gold.churn_predictions
-- 
-- BUG-001: Timezone mismatch - some usage timestamps are PST not UTC
-- BUG-005: Missing ELSE clause causes NULL churn_risk
-- =============================================================================

CREATE OR REPLACE TABLE novatech.gold.churn_predictions AS

WITH recent_activity AS (
    SELECT 
        customer_id,
        MAX(usage_timestamp) as last_activity,
        AVG(logins) as avg_logins,
        SUM(api_calls) as total_api_calls,
        AVG(session_duration_minutes) as avg_session_duration
    FROM novatech.silver.fct_product_usage
    WHERE 
        -- BUG-001: This comparison uses potentially wrong timezone data
        -- Some usage_timestamp values are in PST (8 hours behind UTC)
        usage_timestamp > current_timestamp() - INTERVAL 30 DAYS
    GROUP BY customer_id
),

payment_status AS (
    SELECT 
        customer_id,
        MAX(payment_date) as last_payment,
        COUNT(*) as payment_count,
        SUM(amount) as total_paid
    FROM novatech.silver.fct_payments
    WHERE status = 'completed'
    GROUP BY customer_id
)

SELECT 
    c.customer_id,
    c.company_name,
    c.segment,
    c.region,
    
    ra.last_activity,
    ra.avg_logins,
    ra.total_api_calls,
    ra.avg_session_duration,
    
    ps.last_payment,
    ps.payment_count,
    ps.total_paid,
    
    -- BUG-005: Missing ELSE clause!
    -- Customers with NULL avg_logins get NULL risk instead of 'High Risk'
    CASE 
        WHEN ra.avg_logins > 20 THEN 'Low Risk'
        WHEN ra.avg_logins > 5 THEN 'Medium Risk'
        WHEN ra.avg_logins <= 5 THEN 'High Risk'
        -- Missing: ELSE 'High Risk' for NULL cases
    END as churn_risk,
    
    CASE 
        WHEN ra.last_activity IS NULL THEN TRUE
        WHEN ra.avg_logins < 2 THEN TRUE
        ELSE FALSE
    END as is_predicted_churn,
    
    CASE 
        WHEN ra.last_activity IS NOT NULL 
        THEN DATEDIFF(current_timestamp(), ra.last_activity)
        ELSE 999
    END as days_since_activity,
    
    current_timestamp() as prediction_date,
    '30_day_lookback_v2' as model_version

FROM novatech.silver.dim_customers c
LEFT JOIN recent_activity ra ON c.customer_id = ra.customer_id
LEFT JOIN payment_status ps ON c.customer_id = ps.customer_id;
