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
```
_(Pasos de generación, carga y API: pendiente)_

## Tests
```bash
uv run pytest -v
```

## Arquitectura
_Pendiente_

## Datos y limpieza
_Pendiente_

## Modelos SQL
_Pendiente_

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
