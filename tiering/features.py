"""Customer features for the tiering model, computed in SQL from the warehouse.

Leakage guard: every feature only sees transactions strictly before `cutoff`,
and the outcome (future revenue) only sees [cutoff, outcome_end). Validation
therefore asks the honest question "given what we knew on 1 June 2011, did the
score rank customers by what they went on to spend?"
"""
import pandas as pd

from pipeline.db import SCHEMA, read_sql

CUTOFF = "2011-06-01"
OUTCOME_END = "2011-12-01"  # exclusive: outcomes cover 2011-06-01 .. 2011-11-30

FEATURES = ["recency", "frequency", "monetary", "tenure", "avg_order_value", "breadth",
            "cancel_rate", "regularity"]

FEATURE_SQL = """
WITH orders AS (          -- one row per invoice
    SELECT customer_id, invoice, min(invoice_date) AS order_ts, sum(revenue) AS order_value
    FROM fact_sales
    WHERE invoice_date < :cutoff
    GROUP BY customer_id, invoice
),
gaps AS (                 -- days between consecutive orders
    SELECT customer_id,
           order_ts::date - lag(order_ts::date) OVER (PARTITION BY customer_id ORDER BY order_ts) AS gap_days
    FROM orders
),
customer AS (
    SELECT customer_id, min(order_ts) AS first_ts, max(order_ts) AS last_ts,
           count(*) AS frequency, sum(order_value) AS monetary
    FROM orders
    GROUP BY customer_id
),
breadth AS (
    SELECT customer_id, count(DISTINCT stock_code) AS breadth
    FROM fact_sales
    WHERE invoice_date < :cutoff
    GROUP BY customer_id
),
cancels AS (
    SELECT customer_id, count(DISTINCT invoice) AS cancelled_orders
    FROM fact_cancellations
    WHERE invoice_date < :cutoff
    GROUP BY customer_id
),
regularity AS (           -- needs at least 3 orders (2 gaps) for a standard deviation
    SELECT customer_id, stddev_samp(gap_days) AS regularity
    FROM gaps
    GROUP BY customer_id
)
SELECT
    c.customer_id,
    (CAST(:cutoff AS date) - c.last_ts::date)                          AS recency,
    c.frequency,
    c.monetary::float                                                  AS monetary,
    (CAST(:cutoff AS date) - c.first_ts::date)                         AS tenure,
    (c.monetary / c.frequency)::float                                  AS avg_order_value,
    b.breadth,
    coalesce(x.cancelled_orders, 0)::float
        / (c.frequency + coalesce(x.cancelled_orders, 0))              AS cancel_rate,
    r.regularity::float                                                AS regularity
FROM customer c
JOIN breadth b USING (customer_id)
LEFT JOIN cancels x USING (customer_id)
LEFT JOIN regularity r USING (customer_id)
ORDER BY c.customer_id
"""

OUTCOME_SQL = """
SELECT customer_id, sum(revenue)::float AS future_revenue
FROM fact_sales
WHERE invoice_date >= :start AND invoice_date < :end
GROUP BY customer_id
"""


def build_features(cutoff: str = CUTOFF, schema: str = SCHEMA) -> pd.DataFrame:
    """One row per customer with at least one purchase before `cutoff`."""
    df = read_sql(FEATURE_SQL, schema=schema, cutoff=cutoff).set_index("customer_id")
    return df[FEATURES].astype(float)


def future_revenue(start: str = CUTOFF, end: str = OUTCOME_END, schema: str = SCHEMA) -> pd.Series:
    return read_sql(OUTCOME_SQL, schema=schema, start=start, end=end).set_index("customer_id")["future_revenue"]


def build_dataset(cutoff: str = CUTOFF, outcome_end: str = OUTCOME_END, schema: str = SCHEMA) -> pd.DataFrame:
    """Features at `cutoff` joined to revenue in [cutoff, outcome_end); customers who buy nothing get 0."""
    X = build_features(cutoff, schema)
    y = future_revenue(cutoff, outcome_end, schema)
    return X.assign(future_revenue=y.reindex(X.index).fillna(0.0))
