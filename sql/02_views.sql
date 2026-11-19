-- Analytical models on top of the cleaned `orders` table.
-- Business rule: a "sale" is an order with status 'completado'. Pending orders are not
-- revenue yet, and cancelled/returned orders are not revenue at all.

-- Sales per month and country.
CREATE OR REPLACE VIEW v_sales_by_month_country AS
SELECT
    date_trunc('month', order_date)::date        AS month,        -- first day of the month
    country,
    count(*)                                     AS n_orders,
    sum(amount)                                  AS total_sales,
    round(avg(amount), 2)                        AS avg_order_value
FROM orders
WHERE status = 'completado'
GROUP BY 1, 2;

COMMENT ON VIEW v_sales_by_month_country IS
    'Completed sales per month (first day of month) and country.';

-- Top 10 customers by total spent (completed orders only).
CREATE OR REPLACE VIEW v_top_customers AS
SELECT
    rank() OVER (ORDER BY sum(amount) DESC)      AS ranking,
    customer,
    country,
    count(*)                                     AS n_orders,
    sum(amount)                                  AS total_spent,
    min(order_date)                              AS first_order,
    max(order_date)                              AS last_order
FROM orders
WHERE status = 'completado'
GROUP BY customer, country
ORDER BY total_spent DESC, customer
LIMIT 10;

COMMENT ON VIEW v_top_customers IS
    'Top 10 customers by total spent on completed orders.';
