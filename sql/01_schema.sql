-- Schema for the cleaned orders and the rows rejected during cleaning.
-- The load is a full refresh: tables are dropped and recreated on every run (idempotent).

DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS rejected_orders CASCADE;

CREATE TABLE orders (
    order_id    TEXT          PRIMARY KEY,
    order_date  DATE          NOT NULL,
    customer    TEXT          NOT NULL,
    country     TEXT          NOT NULL,
    product     TEXT          NOT NULL,
    amount      NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    status      TEXT          NOT NULL
                CHECK (status IN ('completado', 'pendiente', 'cancelado', 'devuelto'))
);

CREATE INDEX idx_orders_date    ON orders (order_date);
CREATE INDEX idx_orders_country ON orders (country);

-- Raw values (as text) of every row that could not be loaded, with the reason.
-- Keeps the cleaning auditable: nothing is silently dropped.
CREATE TABLE rejected_orders (
    source_row    INTEGER PRIMARY KEY,   -- line number in the CSV (header = line 1)
    order_id      TEXT,
    order_date    TEXT,
    customer      TEXT,
    country       TEXT,
    product       TEXT,
    amount        TEXT,
    status        TEXT,
    reject_reason TEXT    NOT NULL,
    rejected_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
