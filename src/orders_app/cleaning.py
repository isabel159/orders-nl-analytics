"""Pure cleaning functions: no database or file I/O, so they are easy to test.

Rules (documented in the README):
  - Exact duplicate rows are dropped, keeping the first occurrence.
  - A repeated order_id with *different* content is rejected (we cannot know which one is right).
  - Dates: ISO (yyyy-mm-dd) plus the known alternative formats dd/mm/yyyy, yyyy/mm/dd and
    dd-mm-yyyy (European day-first convention). Impossible or empty dates are rejected.
  - Amounts: a decimal comma is normalized ("12,50" -> 12.50). Empty, non-numeric or
    negative amounts are rejected, never imputed (inventing revenue would distort the metrics).
  - Text: trimmed; country and status are normalized against a known catalog
    (case- and accent-insensitive). Unknown values are rejected.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

COLUMNS = ["order_id", "order_date", "customer", "country", "product", "amount", "status"]

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y")

VALID_COUNTRIES = ["España", "México", "Argentina", "Colombia", "Chile", "Francia", "Portugal"]
VALID_STATUSES = ["completado", "pendiente", "cancelado", "devuelto"]


def _key(value: str) -> str:
    """Comparison key: trimmed, lowercase, without accents ("  ESPAÑA " -> "espana")."""
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(c for c in normalized if not unicodedata.combining(c))


_COUNTRY_BY_KEY = {_key(c): c for c in VALID_COUNTRIES}
_STATUS_BY_KEY = {_key(s): s for s in VALID_STATUSES}


def parse_date(value: str) -> date | None:
    """Parse a date in any accepted format. Returns None if it is empty or impossible."""
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: str) -> Decimal | None:
    """Parse an amount with 2 decimals. Returns None if empty, non-numeric or negative."""
    value = (value or "").strip()
    if not value:
        return None
    if "," in value and "." not in value:
        value = value.replace(",", ".")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount.quantize(Decimal("0.01"))


def normalize_country(value: str) -> str | None:
    return _COUNTRY_BY_KEY.get(_key(value or ""))


def normalize_status(value: str) -> str | None:
    return _STATUS_BY_KEY.get(_key(value or ""))


@dataclass
class CleaningResult:
    clean: pd.DataFrame
    rejected: pd.DataFrame
    stats: dict[str, int] = field(default_factory=dict)


def clean_orders(raw: pd.DataFrame) -> CleaningResult:
    """Clean a raw orders DataFrame (all columns as text).

    Returns the clean rows (typed), the rejected rows (raw values + reason) and counters.
    """
    df = raw[COLUMNS].copy()
    df.insert(0, "source_row", range(2, len(df) + 2))  # CSV line number (header is line 1)
    stats: dict[str, int] = {"rows_read": len(df)}

    # 1. Exact duplicates: same values in every column.
    dup_mask = df.duplicated(subset=COLUMNS, keep="first")
    stats["exact_duplicates_dropped"] = int(dup_mask.sum())
    df = df[~dup_mask]

    clean_rows: list[dict] = []
    rejected_rows: list[dict] = []
    seen_ids: set[str] = set()
    fixed = {"dates_reformatted": 0, "amounts_decimal_comma": 0, "texts_normalized": 0}

    for row in df.to_dict("records"):
        reasons: list[str] = []

        order_id = (row["order_id"] or "").strip()
        if not order_id:
            reasons.append("missing_order_id")
        elif order_id in seen_ids:
            reasons.append("conflicting_duplicate_order_id")

        order_date = parse_date(row["order_date"])
        if order_date is None:
            reasons.append("invalid_date")
        elif order_date.isoformat() != row["order_date"].strip():
            fixed["dates_reformatted"] += 1

        amount = parse_amount(row["amount"])
        if amount is None:
            reasons.append("missing_amount" if not (row["amount"] or "").strip() else "invalid_amount")
        elif "," in row["amount"]:
            fixed["amounts_decimal_comma"] += 1

        country = normalize_country(row["country"])
        if country is None:
            reasons.append("unknown_country")
        status = normalize_status(row["status"])
        if status is None:
            reasons.append("unknown_status")
        if (country and country != row["country"]) or (status and status != row["status"]):
            fixed["texts_normalized"] += 1

        customer = (row["customer"] or "").strip()
        product = (row["product"] or "").strip()
        if not customer:
            reasons.append("missing_customer")
        if not product:
            reasons.append("missing_product")

        if reasons:
            rejected_rows.append({**row, "reject_reason": ";".join(reasons)})
            continue

        seen_ids.add(order_id)
        clean_rows.append(
            {
                "order_id": order_id,
                "order_date": order_date,
                "customer": customer,
                "country": country,
                "product": product,
                "amount": amount,
                "status": status,
            }
        )

    clean = pd.DataFrame(clean_rows, columns=COLUMNS)
    rejected = pd.DataFrame(rejected_rows, columns=["source_row", *COLUMNS, "reject_reason"])

    stats.update(fixed)
    stats["rejected"] = len(rejected)
    for reason, count in rejected["reject_reason"].str.split(";").explode().value_counts().items():
        stats[f"rejected_{reason}"] = int(count)
    stats["clean_rows"] = len(clean)
    return CleaningResult(clean=clean, rejected=rejected, stats=stats)
