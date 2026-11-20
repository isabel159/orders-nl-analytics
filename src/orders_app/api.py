"""FastAPI app: answer natural-language questions about the orders data.

Flow for POST /ask:
    question -> LLM (SQL + explanation) -> validate_query -> run as app_readonly
             -> LLM (natural-language answer from the rows) -> JSON result

Every failure is returned as a clear JSON error: {"error": <code>, "detail": <message>, ...}

Run:
    uv run uvicorn orders_app.api:app --reload
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from orders_app.db import PostgresExecutor, QueryExecutionError, QueryExecutor
from orders_app.llm import AnswerWriter, AzureOpenAILLM, LLMError, SQLGenerator
from orders_app.validator import QueryValidationError, validate_query

logger = logging.getLogger("orders_app.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Orders NL Analytics",
    description="Preguntas en lenguaje natural sobre los pedidos, traducidas a SQL por un LLM y validadas antes de ejecutarse.",
    version="0.1.0",
)


# ---------- Schemas ----------

class AskRequest(BaseModel):
    question: str = Field(
        min_length=3,
        max_length=500,
        examples=["¿cuánto vendimos en España en marzo?"],
    )
    include_answer: bool = Field(
        default=True,
        description="Write a natural-language answer from the result (one extra LLM call).",
    )


class AskResponse(BaseModel):
    question: str
    sql: str
    explanation: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    answer: str | None = Field(
        description="Natural-language answer built from the rows. null if not requested or if writing it failed "
        "(the data above is still valid)."
    )


class ErrorResponse(BaseModel):
    error: str
    detail: str
    sql: str | None = None


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, detail: str, sql: str | None = None):
        self.status_code, self.error, self.detail, self.sql = status_code, error, detail, sql


# ---------- Dependencies (overridden in tests) ----------

@lru_cache
def get_executor() -> QueryExecutor:
    return PostgresExecutor()


@lru_cache
def _build_llm() -> AzureOpenAILLM:
    min_date, max_date = get_executor().data_range()
    return AzureOpenAILLM.from_env(min_date, max_date)


def _get_llm() -> AzureOpenAILLM:
    try:
        return _build_llm()
    except LLMError as exc:
        raise ApiError(503, "llm_not_configured", str(exc)) from exc
    except QueryExecutionError as exc:
        raise ApiError(exc.status_code, exc.code, str(exc)) from exc


def get_sql_generator() -> SQLGenerator:
    return _get_llm()


def get_answer_writer() -> AnswerWriter:
    return _get_llm()


# ---------- Error handlers: always the same JSON shape ----------

@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    body = ErrorResponse(error=exc.error, detail=exc.detail, sql=exc.sql)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(exclude_none=True))


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    detail = f"{field}: {first.get('msg', 'invalid request')}" if field else first.get("msg", "invalid request")
    return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": detail})


# ---------- Endpoints ----------

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


ERROR_RESPONSES = {
    422: {"model": ErrorResponse, "description": "Invalid request, unanswerable question or unsafe/invalid generated SQL"},
    502: {"model": ErrorResponse, "description": "The LLM failed or returned an unusable response"},
    503: {"model": ErrorResponse, "description": "LLM or database not available"},
    504: {"model": ErrorResponse, "description": "The query took too long"},
}


@app.post("/ask", response_model=AskResponse, responses=ERROR_RESPONSES)
def ask(
    body: AskRequest,
    generator: Annotated[SQLGenerator, Depends(get_sql_generator)],
    executor: Annotated[QueryExecutor, Depends(get_executor)],
    answer_writer: Annotated[AnswerWriter, Depends(get_answer_writer)],
) -> AskResponse:
    question = body.question.strip()
    started = time.perf_counter()

    # 1. LLM: question -> SQL
    try:
        generation = generator.generate_sql(question)
    except LLMError as exc:
        # The user gets a clear message; the log keeps the technical cause for debugging.
        logger.warning("LLM error for question=%r: %s | cause: %r", question, exc, exc.__cause__)
        raise ApiError(502, "llm_error", str(exc)) from exc

    if generation.sql is None:
        raise ApiError(422, "unanswerable_question", generation.explanation or "The question cannot be answered with the available data.")

    # 2. Validate before executing (never trust the LLM output)
    try:
        safe_sql = validate_query(generation.sql)
    except QueryValidationError as exc:
        logger.warning("Rejected SQL for question=%r: %s | sql=%r", question, exc, generation.sql)
        raise ApiError(422, "invalid_query", f"The generated query was rejected: {exc}", sql=generation.sql) from exc

    # 3. Execute with the read-only role
    try:
        result = executor.run(safe_sql)
    except QueryExecutionError as exc:
        logger.warning("Execution error for sql=%r: %s", safe_sql, exc)
        raise ApiError(exc.status_code, exc.code, str(exc), sql=safe_sql) from exc

    rows = jsonable_encoder(result.rows)

    # 4. Natural-language answer from the rows. Graceful degradation: if this step fails, the
    #    (already valid) data is still returned, with answer = null, instead of failing the request.
    answer = None
    if body.include_answer:
        try:
            answer = answer_writer.write_answer(question, generation.explanation, result.columns, rows)
        except LLMError as exc:
            logger.warning("Answer writing failed for question=%r: %s | cause: %r", question, exc, exc.__cause__)

    logger.info("question=%r rows=%d time=%.1fs sql=%r", question, len(rows), time.perf_counter() - started, safe_sql)
    return AskResponse(
        question=question,
        sql=safe_sql,
        explanation=generation.explanation,
        columns=result.columns,
        rows=rows,
        row_count=len(rows),
        answer=answer,
    )
