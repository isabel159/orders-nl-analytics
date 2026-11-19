"""Generate a CSV of synthetic orders with intentionally injected errors.

Injected errors (so the cleaning step has real work to do):
  - Exact duplicate rows.
  - Badly written dates: other formats (dd/mm/yyyy, yyyy/mm/dd, dd-mm-yyyy)
    and impossible or empty dates ("2025-02-30", "2025-13-05", "", "N/A").
  - Empty amounts and some amounts with a decimal comma ("12,50").
  - Noisy text: country and status with odd casing, extra spaces or no accents.

Data values (countries, products, statuses) are in Spanish: it is the business
language and the language users will ask questions in.

Generation is deterministic (fixed seed): it always produces the same CSV.

Usage:
    uv run python -m orders_app.generate_data [--n 1200] [--seed 42] [--output data/orders_raw.csv]
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

COLUMNS = ["order_id", "order_date", "customer", "country", "product", "amount", "status"]

# Country -> weight (relative probability of a customer living there)
COUNTRIES = {
    "España": 35,
    "México": 20,
    "Argentina": 12,
    "Colombia": 10,
    "Chile": 8,
    "Francia": 8,
    "Portugal": 7,
}

# Product -> base unit price (EUR)
PRODUCTS = {
    "Portátil": 899.00,
    "Monitor": 229.00,
    "Teclado": 49.90,
    "Ratón": 24.90,
    "Auriculares": 79.00,
    "Webcam": 59.00,
    "Silla ergonómica": 319.00,
    "Escritorio": 249.00,
    "Disco SSD": 109.00,
    "Tablet": 449.00,
}

# Status -> weight
STATUSES = {"completado": 80, "pendiente": 8, "cancelado": 7, "devuelto": 5}

FIRST_NAMES = [
    "Lucía", "Martín", "Sofía", "Mateo", "Valentina", "Hugo", "Camila", "Diego",
    "Paula", "Javier", "Elena", "Andrés", "Carmen", "Pablo", "Isabel", "Tomás",
    "Laura", "Nicolás", "Marta", "Gabriel", "Ana", "Sebastián", "Julia", "Daniel",
]
LAST_NAMES = [
    "García", "Fernández", "López", "Martínez", "Rodríguez", "Pérez", "Gómez",
    "Sánchez", "Díaz", "Romero", "Torres", "Ruiz", "Vargas", "Castro", "Ortega",
    "Silva", "Morales", "Herrera", "Navarro", "Rojas",
]

START_DATE = date(2024, 1, 1)
END_DATE = date(2025, 12, 31)

# Share of rows affected by each injected error
DUPLICATE_RATE = 0.03
BAD_DATE_RATE = 0.05
EMPTY_AMOUNT_RATE = 0.04
COMMA_AMOUNT_RATE = 0.02
TEXT_NOISE_RATE = 0.05

INVALID_DATES = ["2025-02-30", "2025-13-05", "2024-00-10", "", "N/A", "fecha"]


@dataclass
class InjectionReport:
    """How many errors of each kind were injected (printed at the end)."""

    clean_rows: int = 0
    duplicates: int = 0
    reformatted_dates: int = 0
    invalid_dates: int = 0
    empty_amounts: int = 0
    comma_amounts: int = 0
    noisy_texts: int = 0
    examples: dict[str, list[str]] = field(default_factory=dict)


def build_customers(rng: random.Random, n_customers: int = 150) -> list[tuple[str, str]]:
    """Create unique customers (name, country). Each customer lives in a single country."""
    names: set[str] = set()
    while len(names) < n_customers:
        names.add(f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} {rng.choice(LAST_NAMES)}")
    countries = rng.choices(list(COUNTRIES), weights=list(COUNTRIES.values()), k=n_customers)
    return list(zip(sorted(names), countries))


def generate_clean_orders(rng: random.Random, n: int) -> list[dict[str, str]]:
    """Generate n valid orders (before injecting errors)."""
    customers = build_customers(rng)
    # Pareto-like weights: a few customers buy a lot -> the top 10 view is meaningful.
    customer_weights = [1 / (i + 1) ** 0.8 for i in range(len(customers))]
    rng.shuffle(customer_weights)
    n_days = (END_DATE - START_DATE).days

    orders = []
    for i in range(1, n + 1):
        customer, country = rng.choices(customers, weights=customer_weights)[0]
        product = rng.choice(list(PRODUCTS))
        quantity = rng.choices([1, 2, 3], weights=[75, 18, 7])[0]
        # Small price variation (discounts/promos) so amounts are not all identical.
        amount = PRODUCTS[product] * quantity * rng.uniform(0.9, 1.05)
        orders.append(
            {
                "order_id": f"ORD-{i:05d}",
                "order_date": (START_DATE + timedelta(days=rng.randint(0, n_days))).isoformat(),
                "customer": customer,
                "country": country,
                "product": product,
                "amount": f"{amount:.2f}",
                "status": rng.choices(list(STATUSES), weights=list(STATUSES.values()))[0],
            }
        )
    return orders


def _note(report: InjectionReport, kind: str, value: str) -> None:
    examples = report.examples.setdefault(kind, [])
    if len(examples) < 3:
        examples.append(value)


def _bad_date(rng: random.Random, iso: str, report: InjectionReport) -> str:
    """Return the date in a wrong format, or an invalid date."""
    y, m, d = iso.split("-")
    if rng.random() < 0.6:
        report.reformatted_dates += 1
        value = rng.choice([f"{d}/{m}/{y}", f"{y}/{m}/{d}", f"{d}-{m}-{y}"])
        _note(report, "date_other_format", value)
    else:
        report.invalid_dates += 1
        value = rng.choice(INVALID_DATES)
        _note(report, "date_invalid", value)
    return value


def _noisy_text(rng: random.Random, value: str) -> str:
    """Make a text dirty in a recoverable way (casing, spaces, missing accents)."""
    variants = [
        value.upper(),
        value.lower(),
        f"  {value} ",
        value.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ñ", "n"),
    ]
    return rng.choice(variants)


def inject_errors(
    rng: random.Random, orders: list[dict[str, str]]
) -> tuple[list[dict[str, str]], InjectionReport]:
    """Inject errors into a copy of the orders and append duplicates."""
    report = InjectionReport(clean_rows=len(orders))
    rows = [dict(o) for o in orders]
    n = len(rows)

    # Disjoint indices for date and amount errors: each row has at most one "hard" error.
    k_date = int(n * BAD_DATE_RATE)
    k_empty = int(n * EMPTY_AMOUNT_RATE)
    k_comma = int(n * COMMA_AMOUNT_RATE)
    picked = rng.sample(range(n), k_date + k_empty + k_comma)
    date_idx = picked[:k_date]
    empty_idx = picked[k_date : k_date + k_empty]
    comma_idx = picked[k_date + k_empty :]

    for i in date_idx:
        rows[i]["order_date"] = _bad_date(rng, rows[i]["order_date"], report)
    for i in empty_idx:
        rows[i]["amount"] = rng.choice(["", " "])
        report.empty_amounts += 1
    for i in comma_idx:
        rows[i]["amount"] = rows[i]["amount"].replace(".", ",")
        report.comma_amounts += 1
        _note(report, "amount_comma", rows[i]["amount"])

    # Text noise (independent from the errors above).
    for i in rng.sample(range(n), int(n * TEXT_NOISE_RATE)):
        col = rng.choice(["country", "status"])
        rows[i][col] = _noisy_text(rng, rows[i][col])
        report.noisy_texts += 1
        _note(report, "noisy_text", repr(rows[i][col]))

    # Exact duplicates, inserted at random positions.
    for i in rng.sample(range(n), int(n * DUPLICATE_RATE)):
        rows.insert(rng.randint(0, len(rows)), dict(rows[i]))
        report.duplicates += 1

    return rows, report


def generate(n: int = 1200, seed: int = 42) -> tuple[list[dict[str, str]], InjectionReport]:
    """Full generation (clean orders + injected errors). Used by the CLI and the tests."""
    rng = random.Random(seed)
    return inject_errors(rng, generate_clean_orders(rng, n))


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=1200, help="Unique orders to generate (before duplicating)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("data/orders_raw.csv"))
    args = parser.parse_args()

    rows, report = generate(args.n, args.seed)
    write_csv(rows, args.output)

    print(f"CSV written: {args.output} ({len(rows)} rows)")
    print(f"  unique orders           : {report.clean_rows}")
    print(f"  exact duplicates        : {report.duplicates}")
    print(f"  dates in other format   : {report.reformatted_dates}")
    print(f"  invalid/empty dates     : {report.invalid_dates}")
    print(f"  empty amounts           : {report.empty_amounts}")
    print(f"  decimal-comma amounts   : {report.comma_amounts}")
    print(f"  noisy texts             : {report.noisy_texts}")
    for kind, values in report.examples.items():
        print(f"  e.g. {kind}: {', '.join(values)}")


if __name__ == "__main__":
    main()
