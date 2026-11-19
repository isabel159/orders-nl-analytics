# Orders NL Analytics

Pipeline de datos de pedidos con Python y PostgreSQL: generación y limpieza de datos,
modelos SQL analíticos y una API FastAPI que responde preguntas en lenguaje natural
usando un LLM de Azure OpenAI (Azure AI Foundry), validando cada consulta antes de ejecutarla.

> 🚧 En construcción: este README se completa a medida que avanza el proyecto.

## Requisitos
- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker (para PostgreSQL 16)
- Un deployment de Azure OpenAI en Azure AI Foundry (probado con `gpt-5-mini`)

## Cómo ejecutarlo
```bash
cp .env.example .env      # rellenar contraseñas y credenciales de Azure OpenAI
uv sync
docker compose up -d
uv run python -m orders_app.generate_data   # genera data/orders_raw.csv (determinista, seed 42)
uv run python -m orders_app.load            # limpia el CSV y lo carga en PostgreSQL
```
_(API: pendiente)_

## Tests
```bash
uv run pytest -v
```

## Arquitectura
_Pendiente_

## Datos y limpieza

**Generación** (`orders_app.generate_data`): 1.200 pedidos de 2024-2025 (150 clientes, 7 países,
10 productos) más errores inyectados a propósito. Los valores están en español (idioma del negocio
y de las preguntas); el esquema, en inglés.

**Limpieza** (`orders_app.cleaning`, funciones puras) y **carga** (`orders_app.load`, una sola
transacción, recarga completa e idempotente):

| Error | Regla aplicada | Filas |
|---|---|---|
| Duplicados exactos | Se eliminan, se conserva la primera aparición | 36 eliminadas |
| Mismo `order_id` con distinto contenido | Se rechaza (no se puede saber cuál es correcta) | 0 |
| Fecha en otro formato (`15/03/2025`, `2025/03/15`, `15-03-2025`) | Se corrige (convención europea día/mes) | 36 corregidas |
| Fecha imposible o vacía (`2025-02-30`, `N/A`) | Se rechaza | 24 rechazadas |
| Importe con coma decimal (`306,87`) | Se corrige | 24 corregidas |
| Importe vacío, no numérico o negativo | Se rechaza, **no se imputa** (inventar ventas falsea las métricas) | 48 rechazadas |
| País/estado con mayúsculas, espacios o sin tilde | Se normaliza contra un catálogo; desconocido → rechazo | 38 normalizadas |

Resultado: **1.236 filas leídas → 1.128 en `orders`, 72 en `rejected_orders`**.

Las filas rechazadas se guardan en `rejected_orders` con sus valores originales, el número de
línea del CSV y el motivo: nada se descarta en silencio y la limpieza es auditable.

## Modelos SQL

Definidos en [`sql/02_views.sql`](sql/02_views.sql) y creados en cada carga.

**Regla de negocio:** una *venta* es un pedido con estado `completado`. Los pendientes aún no son
ingreso y los cancelados/devueltos no lo son.

| Vista | Granularidad | Columnas |
|---|---|---|
| `v_sales_by_month_country` | mes × país | `month` (primer día del mes), `country`, `n_orders`, `total_sales`, `avg_order_value` |
| `v_top_customers` | cliente (top 10) | `ranking`, `customer`, `country`, `n_orders`, `total_spent`, `first_order`, `last_order` |

### Acceso de solo lectura
La API no usa el usuario administrador, sino el rol `app_readonly` (creado en la carga), con
mínimo privilegio:

- `SELECT` **solo** sobre `orders`, `v_sales_by_month_country` y `v_top_customers`
  (lista única en `config.ALLOWED_TABLES`). Sin acceso a `rejected_orders`.
- Todas sus transacciones son de solo lectura por defecto (`default_transaction_read_only`)
  y tienen `statement_timeout = 5s`.

Es la última capa de seguridad: aunque una consulta maliciosa superase la validación de la API,
PostgreSQL la rechazaría (`tests/test_db_permissions.py` lo comprueba).

## API
_Pendiente_

## Decisiones tomadas
_Pendiente_

## Qué haría con más tiempo
_Pendiente_

## Cómo usé la IA
_Pendiente_

## Tiempo invertido
_Pendiente_
