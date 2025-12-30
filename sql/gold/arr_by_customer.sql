-- =============================================================================
-- Transformation: ARR by Customer
-- Owner: Finance Team
-- Schedule: Daily at 6:00 AM UTC
-- Source: novatech.silver.fct_subscriptions
-- Target: novatech.gold.arr_by_customer
-- 
-- BUG-003: Only includes Core Platform subscriptions!
-- Addon products (Analytics, API, Support, Export) are excluded.
-- This causes ARR to be understated by approximately 24%.
-- =============================================================================

CREATE OR REPLACE TABLE novatech.gold.arr_by_customer AS

SELECT 
    customer_id,
    SUM(mrr) * 12 as arr,
    COUNT(*) as subscription_count,
    MAX(start_date) as latest_subscription_date,
    current_timestamp() as calculated_at
    
FROM novatech.silver.fct_subscriptions

WHERE 
    status = 'active'
    -- BUG-003: This filter excludes addon products!
    -- TODO: Discuss with Finance whether addons should be included
    AND product_type = 'Core Platform'

GROUP BY customer_id;
