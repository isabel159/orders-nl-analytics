"""Tests for the cleaning rules (pure functions, no database needed)."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from orders_app.cleaning import (
    COLUMNS,
    clean_orders,
    normalize_country,
    normalize_status,
    parse_amount,
    parse_date,
)
from orders_app.generate_data import generate


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2025-03-15", date(2025, 3, 15)),
        ("15/03/2025", date(2025, 3, 15)),
        ("2025/03/15", date(2025, 3, 15)),
        ("15-03-2025", date(2025, 3, 15)),
        (" 2025-03-15 ", date(2025, 3, 15)),
        ("2025-02-30", None),  # impossible day
        ("2025-13-05", None),  # impossible month
        ("", None),
        ("N/A", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("12.50", Decimal("12.50")),
        ("12,50", Decimal("12.50")),  # decimal comma is normalized
        (" 7 ", Decimal("7.00")),
        ("", None),  # empty -> rejected, never imputed
        (" ", None),
        ("abc", None),
        ("-5.00", None),  # negative amounts are not valid sales
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["España", "ESPAÑA", "  España ", "Espana", "españa"])
def test_normalize_country_variants(raw):
    assert normalize_country(raw) == "España"


def test_normalize_unknown_values():
    assert normalize_country("Atlantis") is None
    assert normalize_status("COMPLETADO") == "completado"
    assert normalize_status("enviado") is None


def _df(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def test_clean_orders_handles_each_error_type():
    good = ["ORD-1", "2025-03-01", "Ana López", "España", "Monitor", "229.00", "completado"]
    raw = _df(
        [
            good,
            good,  # exact duplicate -> dropped
            ["ORD-1", "2025-03-02", "Ana López", "España", "Monitor", "100.00", "completado"],  # id conflict
            ["ORD-2", "01/03/2025", "Hugo Ruiz", " MÉXICO ", "Teclado", "49,90", "COMPLETADO"],  # fixable
            ["ORD-3", "2025-02-30", "Lucía Díaz", "Chile", "Ratón", "24.90", "pendiente"],  # bad date
            ["ORD-4", "2025-03-05", "Pablo Gómez", "Chile", "Webcam", "", "completado"],  # empty amount
        ]
    )

    result = clean_orders(raw)

    assert list(result.clean["order_id"]) == ["ORD-1", "ORD-2"]
    fixed = result.clean.set_index("order_id").loc["ORD-2"]
    assert fixed["order_date"] == date(2025, 3, 1)
    assert fixed["amount"] == Decimal("49.90")
    assert fixed["country"] == "México"
    assert fixed["status"] == "completado"

    reasons = dict(zip(result.rejected["order_id"], result.rejected["reject_reason"]))
    assert reasons == {
        "ORD-1": "conflicting_duplicate_order_id",
        "ORD-3": "invalid_date",
        "ORD-4": "missing_amount",
    }
    assert result.stats["exact_duplicates_dropped"] == 1
    # Rejected rows keep their original (raw) values and CSV line number for auditing.
    assert result.rejected.set_index("order_id").loc["ORD-3", "order_date"] == "2025-02-30"
    assert result.rejected.set_index("order_id").loc["ORD-3", "source_row"] == 6


def test_clean_orders_on_generated_dataset_matches_injected_errors():
    """End-to-end check against the generator: every injected error is caught, nothing else."""
    rows, report = generate(n=1200, seed=42)
    result = clean_orders(_df([[r[c] for c in COLUMNS] for r in rows]))

    assert result.stats["exact_duplicates_dropped"] == report.duplicates
    assert result.stats["rejected_invalid_date"] == report.invalid_dates
    assert result.stats["rejected_missing_amount"] == report.empty_amounts
    assert result.stats["dates_reformatted"] == report.reformatted_dates
    assert result.stats["amounts_decimal_comma"] == report.comma_amounts
    assert len(result.clean) == report.clean_rows - report.invalid_dates - report.empty_amounts
    assert result.clean["order_id"].is_unique
    assert set(result.clean["country"]) <= {"España", "México", "Argentina", "Colombia", "Chile", "Francia", "Portugal"}
