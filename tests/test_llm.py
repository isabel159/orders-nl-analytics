"""Tests for the LLM client: response parsing and error mapping, with a fake OpenAI client."""

from datetime import date
from types import SimpleNamespace

import httpx
import openai
import pytest

from orders_app.llm import (
    ANSWER_MAX_ROWS,
    AzureOpenAILLM,
    LLMError,
    build_answer_input,
    build_system_prompt,
    parse_llm_content,
)


def test_parse_valid_content():
    result = parse_llm_content('{"sql": " SELECT 1 ", "explanation": "ok"}')
    assert result.sql == "SELECT 1"
    assert result.explanation == "ok"


def test_parse_null_sql_means_unanswerable():
    assert parse_llm_content('{"sql": null, "explanation": "No se puede."}').sql is None


@pytest.mark.parametrize(
    "content",
    [None, "", "SELECT * FROM orders", '{"query": "SELECT 1"}', '{"sql": 42, "explanation": "x"}', "{not json"],
)
def test_parse_invalid_content_raises_llm_error(content):
    with pytest.raises(LLMError):
        parse_llm_content(content)


class FakeCompletions:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.calls = content, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_generator(completions: FakeCompletions) -> AzureOpenAILLM:
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return AzureOpenAILLM(client, "test-deployment", "system prompt")


def test_generator_sends_question_with_strict_json_schema():
    completions = FakeCompletions(content='{"sql": "SELECT 1", "explanation": "ok"}')

    result = make_generator(completions).generate_sql("¿cuánto vendimos?")

    assert result.sql == "SELECT 1"
    call = completions.calls[0]
    assert call["model"] == "test-deployment"
    assert call["messages"][-1] == {"role": "user", "content": "¿cuánto vendimos?"}
    assert call["response_format"]["json_schema"]["strict"] is True


_REQUEST = httpx.Request("POST", "https://example.invalid/openai/v1/chat/completions")


@pytest.mark.parametrize(
    "error, message",
    [
        (openai.APITimeoutError(request=_REQUEST), "in time"),
        (openai.APIConnectionError(request=_REQUEST), "connect"),
        (
            openai.RateLimitError("rate limited", response=httpx.Response(429, request=_REQUEST), body=None),
            "HTTP 429",
        ),
    ],
)
def test_generator_maps_provider_errors_to_llm_error(error, message):
    with pytest.raises(LLMError, match=message):
        make_generator(FakeCompletions(error=error)).generate_sql("pregunta")


def test_write_answer_sends_result_and_returns_text():
    completions = FakeCompletions(content="  En marzo de 2025 vendimos 3.271,59 € en España.  ")

    answer = make_generator(completions).write_answer(
        "¿cuánto vendimos en España en marzo?", "Se asume 2025.", ["total_ventas"], [[3271.59]]
    )

    assert answer == "En marzo de 2025 vendimos 3.271,59 € en España."
    user_message = completions.calls[0]["messages"][-1]["content"]
    assert '"rows": [[3271.59]]' in user_message
    assert "response_format" not in completions.calls[0]  # plain text, not JSON


@pytest.mark.parametrize("content", [None, "", "   "])
def test_write_answer_empty_raises_llm_error(content):
    with pytest.raises(LLMError):
        make_generator(FakeCompletions(content=content)).write_answer("q", "", ["x"], [[1]])


def test_answer_input_is_truncated_for_large_results():
    rows = [[i] for i in range(ANSWER_MAX_ROWS + 10)]
    payload = build_answer_input("q", "note", ["n"], rows)
    assert f'"row_count": {ANSWER_MAX_ROWS + 10}' in payload
    assert '"truncated": true' in payload
    assert f"[{ANSWER_MAX_ROWS + 5}]" not in payload


def test_system_prompt_contains_schema_values_and_data_range():
    prompt = build_system_prompt(date(2024, 1, 1), date(2025, 12, 31))
    for expected in ("v_sales_by_month_country", "v_top_customers", "'España'", "'completado'", "2025-12-31"):
        assert expected in prompt
    assert "rejected_orders" not in prompt  # the LLM is never told about non-allowed tables
