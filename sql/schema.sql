-- Star schema for Online Retail II.
--
-- Two fact tables at order-line grain (sales and cancellations) share three
-- conformed dimensions (customer, product, date). Every statement is
-- IF NOT EXISTS, so running this file again is a no-op; loads are made
-- idempotent separately by truncate-and-load inside one transaction.
-- The pipeline sets search_path to the warehouse schema before running it.

CREATE TABLE IF NOT EXISTS dim_date (
    date_key     INTEGER PRIMARY KEY,          -- YYYYMMDD
    full_date    DATE     NOT NULL UNIQUE,
    year         SMALLINT NOT NULL,
    quarter      SMALLINT NOT NULL,
    month        SMALLINT NOT NULL,
    month_name   TEXT     NOT NULL,
    month_start  DATE     NOT NULL,
    day_of_month SMALLINT NOT NULL,
    day_of_week  SMALLINT NOT NULL,            -- ISO: 1 = Monday ... 7 = Sunday
    day_name     TEXT     NOT NULL,
    is_weekend   BOOLEAN  NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_id        INTEGER PRIMARY KEY,
    country            TEXT NOT NULL,          -- most frequent country on the customer's invoices
    first_invoice_date TIMESTAMP,              -- first/last *sale*; NULL if the customer only has cancellations
    last_invoice_date  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dim_product (
    stock_code  TEXT PRIMARY KEY,
    description TEXT,                          -- most common description for the code
    is_product  BOOLEAN NOT NULL               -- FALSE for postage/fee codes seen only on cancellations
);

CREATE TABLE IF NOT EXISTS fact_sales (
    sales_line_id BIGINT PRIMARY KEY,
    invoice       TEXT          NOT NULL,
    invoice_date  TIMESTAMP     NOT NULL,
    date_key      INTEGER       NOT NULL REFERENCES dim_date (date_key),
    customer_id   INTEGER       NOT NULL REFERENCES dim_customer (customer_id),
    stock_code    TEXT          NOT NULL REFERENCES dim_product (stock_code),
    country       TEXT          NOT NULL,      -- country on the invoice (13 customers have two)
    quantity      INTEGER       NOT NULL,
    price         NUMERIC(12,3) NOT NULL,      -- GBP; a few prices have 3 decimals
    -- Computed by Postgres in exact decimal arithmetic, so the revenue check
    -- compares two independent calculations rather than a copied column
    revenue       NUMERIC(14,3) GENERATED ALWAYS AS (quantity * price) STORED
);

CREATE TABLE IF NOT EXISTS fact_cancellations (
    cancellation_line_id BIGINT PRIMARY KEY,
    invoice       TEXT          NOT NULL,      -- starts with 'C'
    invoice_date  TIMESTAMP     NOT NULL,
    date_key      INTEGER       NOT NULL REFERENCES dim_date (date_key),
    customer_id   INTEGER       NOT NULL REFERENCES dim_customer (customer_id),
    stock_code    TEXT          NOT NULL REFERENCES dim_product (stock_code),
    country       TEXT          NOT NULL,
    quantity      INTEGER       NOT NULL,      -- negative: units returned
    price         NUMERIC(12,3) NOT NULL,
    revenue       NUMERIC(14,3) GENERATED ALWAYS AS (quantity * price) STORED  -- negative
);

-- Indexes on the join and filter columns
CREATE INDEX IF NOT EXISTS ix_sales_customer      ON fact_sales (customer_id);
CREATE INDEX IF NOT EXISTS ix_sales_product       ON fact_sales (stock_code);
CREATE INDEX IF NOT EXISTS ix_sales_date_key      ON fact_sales (date_key);
CREATE INDEX IF NOT EXISTS ix_sales_invoice_date  ON fact_sales (invoice_date);
CREATE INDEX IF NOT EXISTS ix_sales_country       ON fact_sales (country);
CREATE INDEX IF NOT EXISTS ix_sales_invoice       ON fact_sales (invoice);
CREATE INDEX IF NOT EXISTS ix_cancel_customer     ON fact_cancellations (customer_id);
CREATE INDEX IF NOT EXISTS ix_cancel_product      ON fact_cancellations (stock_code);
CREATE INDEX IF NOT EXISTS ix_cancel_date_key     ON fact_cancellations (date_key);
CREATE INDEX IF NOT EXISTS ix_cancel_invoice_date ON fact_cancellations (invoice_date);
CREATE INDEX IF NOT EXISTS ix_customer_country    ON dim_customer (country);

-- Append-only audit trail of pipeline runs (not truncated, so it is not part of the idempotency check)
CREATE TABLE IF NOT EXISTS etl_run_log (
    run_id           BIGSERIAL PRIMARY KEY,
    finished_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sales_rows       BIGINT        NOT NULL,
    cancel_rows      BIGINT        NOT NULL,
    sales_revenue    NUMERIC(16,3) NOT NULL,
    max_invoice_date TIMESTAMP
);
-- md5 over the full content of every warehouse table: two runs on the same input must match
ALTER TABLE etl_run_log ADD COLUMN IF NOT EXISTS fingerprint TEXT;
