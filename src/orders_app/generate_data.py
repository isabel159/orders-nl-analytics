"""Genera un CSV de pedidos sintéticos con errores introducidos a propósito.

Errores que se inyectan (para que el paso de limpieza tenga trabajo):
  - Filas duplicadas exactas.
  - Fechas mal escritas: otros formatos (dd/mm/yyyy, yyyy/mm/dd, dd-mm-yyyy)
    y fechas imposibles o vacías ("2025-02-30", "2025-13-05", "", "N/A").
  - Importes vacíos y algunos importes con coma decimal ("12,50").
  - Ruido en textos: país y estado con mayúsculas, espacios o sin tilde.

La generación es determinista (semilla fija): siempre produce el mismo CSV.

Uso:
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

# País -> peso (probabilidad relativa de que un cliente sea de ese país)
COUNTRIES = {
    "España": 35,
    "México": 20,
    "Argentina": 12,
    "Colombia": 10,
    "Chile": 8,
    "Francia": 8,
    "Portugal": 7,
}

# Producto -> precio unitario base (EUR)
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

# Estado -> peso
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

# Proporciones de errores inyectados
DUPLICATE_RATE = 0.03
BAD_DATE_RATE = 0.05
EMPTY_AMOUNT_RATE = 0.04
COMMA_AMOUNT_RATE = 0.02
TEXT_NOISE_RATE = 0.05

INVALID_DATES = ["2025-02-30", "2025-13-05", "2024-00-10", "", "N/A", "fecha"]


@dataclass
class InjectionReport:
    """Cuántos errores de cada tipo se han inyectado (se imprime al final)."""

    clean_rows: int = 0
    duplicates: int = 0
    reformatted_dates: int = 0
    invalid_dates: int = 0
    empty_amounts: int = 0
    comma_amounts: int = 0
    noisy_texts: int = 0
    examples: dict[str, list[str]] = field(default_factory=dict)


def build_customers(rng: random.Random, n_customers: int = 150) -> list[tuple[str, str]]:
    """Crea clientes únicos (nombre, país). Cada cliente vive en un solo país."""
    names: set[str] = set()
    while len(names) < n_customers:
        names.add(f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} {rng.choice(LAST_NAMES)}")
    countries = rng.choices(list(COUNTRIES), weights=list(COUNTRIES.values()), k=n_customers)
    return list(zip(sorted(names), countries))


def generate_clean_orders(rng: random.Random, n: int) -> list[dict[str, str]]:
    """Genera n pedidos correctos (antes de inyectar errores)."""
    customers = build_customers(rng)
    # Pesos tipo Pareto: unos pocos clientes compran mucho -> el top 10 tiene sentido.
    customer_weights = [1 / (i + 1) ** 0.8 for i in range(len(customers))]
    rng.shuffle(customer_weights)
    n_days = (END_DATE - START_DATE).days

    orders = []
    for i in range(1, n + 1):
        customer, country = rng.choices(customers, weights=customer_weights)[0]
        product = rng.choice(list(PRODUCTS))
        quantity = rng.choices([1, 2, 3], weights=[75, 18, 7])[0]
        # Pequeña variación de precio (descuentos/promos) para que no todo sea idéntico.
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
    """Devuelve la fecha en un formato incorrecto o una fecha inválida."""
    y, m, d = iso.split("-")
    if rng.random() < 0.6:
        report.reformatted_dates += 1
        value = rng.choice([f"{d}/{m}/{y}", f"{y}/{m}/{d}", f"{d}-{m}-{y}"])
        _note(report, "fecha_otro_formato", value)
    else:
        report.invalid_dates += 1
        value = rng.choice(INVALID_DATES)
        _note(report, "fecha_invalida", value)
    return value


def _noisy_text(rng: random.Random, value: str) -> str:
    """Ensucia un texto de forma recuperable (mayúsculas, espacios, sin tilde)."""
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
    """Inyecta errores sobre una copia de los pedidos y añade duplicados."""
    report = InjectionReport(clean_rows=len(orders))
    rows = [dict(o) for o in orders]
    n = len(rows)

    # Índices disjuntos para fechas e importes: cada fila tiene como mucho un error "grave".
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
        _note(report, "importe_coma", rows[i]["amount"])

    # Ruido en textos (independiente de lo anterior).
    for i in rng.sample(range(n), int(n * TEXT_NOISE_RATE)):
        col = rng.choice(["country", "status"])
        rows[i][col] = _noisy_text(rng, rows[i][col])
        report.noisy_texts += 1
        _note(report, "texto_sucio", repr(rows[i][col]))

    # Duplicados exactos, insertados en posiciones aleatorias.
    for i in rng.sample(range(n), int(n * DUPLICATE_RATE)):
        rows.insert(rng.randint(0, len(rows)), dict(rows[i]))
        report.duplicates += 1

    return rows, report


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=1200, help="Pedidos únicos a generar (antes de duplicar)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("data/orders_raw.csv"))
    args = parser.parse_args()

    rng = random.Random(args.seed)
    rows, report = inject_errors(rng, generate_clean_orders(rng, args.n))
    write_csv(rows, args.output)

    print(f"CSV generado: {args.output} ({len(rows)} filas)")
    print(f"  pedidos únicos            : {report.clean_rows}")
    print(f"  duplicados exactos        : {report.duplicates}")
    print(f"  fechas en otro formato    : {report.reformatted_dates}")
    print(f"  fechas inválidas/vacías   : {report.invalid_dates}")
    print(f"  importes vacíos           : {report.empty_amounts}")
    print(f"  importes con coma decimal : {report.comma_amounts}")
    print(f"  textos con ruido          : {report.noisy_texts}")
    for kind, values in report.examples.items():
        print(f"  ej. {kind}: {', '.join(values)}")


if __name__ == "__main__":
    main()
