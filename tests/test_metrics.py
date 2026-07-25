"""Тесты лёгких метрик YC Monitoring: реестр, конфиг, payload и инструментация.

Всё офлайн: фоновый поток и реальный YC не задействуются — активный реестр
устанавливается напрямую через :func:`mandala.metrics.install_registry`.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from mandala import metrics
from mandala.adapters.telegram.bot_api import TelegramApiError, TelegramBotApiClient
from mandala.http.app import create_app
from mandala.llm import ChatMessage, LlmProviderError, OpenAICompatibleTextClient
from mandala.metrics import (
    APP_UP,
    HTTP_LATENCY_MS,
    HTTP_REQUESTS,
    LLM_LATENCY_MS,
    LLM_REQUESTS,
    TELEGRAM_DELIVERY,
    MetricPoint,
    MetricsConfig,
    MetricsRegistry,
    build_write_payload,
    normalize_route,
)


@pytest.fixture
def registry() -> Iterator[MetricsRegistry]:
    """Установить свежий активный реестр на время теста и снять по завершении."""
    reg = MetricsRegistry()
    prev = metrics.install_registry(reg)
    try:
        yield reg
    finally:
        metrics.install_registry(prev)


def _counter(points: list[MetricPoint], name: str, labels: dict[str, str]) -> float | None:
    for p in points:
        if p.name == name and p.type == "COUNTER" and p.labels == labels:
            return p.value
    return None


def _latency(points: list[MetricPoint], name: str, stat: str) -> float | None:
    """Значение DGAUGE-латентности c меткой ``stat`` (avg|max), метки роута игнор."""
    for p in points:
        if p.name == name and p.type == "DGAUGE" and p.labels.get("stat") == stat:
            return p.value
    return None


# --- Реестр -----------------------------------------------------------------------


def test_registry_counter_accumulates() -> None:
    reg = MetricsRegistry()
    reg.incr("m", {"a": "1"})
    reg.incr("m", {"a": "1"}, value=2.0)
    reg.incr("m", {"a": "2"})
    points = reg.snapshot()
    assert _counter(points, "m", {"a": "1"}) == 3.0
    assert _counter(points, "m", {"a": "2"}) == 1.0


def test_registry_counter_is_cumulative_across_snapshots() -> None:
    reg = MetricsRegistry()
    reg.incr("m")
    reg.snapshot()
    reg.incr("m")
    # Счётчик монотонный: второй snapshot видит суммарное значение.
    assert _counter(reg.snapshot(), "m", {}) == 2.0


def test_registry_latency_avg_max_and_window_reset() -> None:
    reg = MetricsRegistry()
    reg.observe_ms("lat", {"route": "/x"}, 10.0)
    reg.observe_ms("lat", {"route": "/x"}, 30.0)
    points = reg.snapshot()
    by_stat = {p.labels["stat"]: p.value for p in points if p.name == "lat"}
    assert by_stat == {"avg": 20.0, "max": 30.0}
    # Латентность оконная: после snapshot окно очищено.
    assert [p for p in reg.snapshot() if p.name == "lat"] == []


# --- payload ----------------------------------------------------------------------


def test_build_write_payload_shape() -> None:
    points = [MetricPoint("m", "COUNTER", {"a": "1"}, 5.0)]
    payload = build_write_payload(points, {"host": "vm-1"})
    assert payload["labels"] == {"host": "vm-1"}
    metrics_list = payload["metrics"]
    assert isinstance(metrics_list, list)
    assert metrics_list[0] == {
        "name": "m",
        "type": "COUNTER",
        "labels": {"a": "1"},
        "value": 5.0,
    }


def test_build_write_payload_without_common_labels() -> None:
    payload = build_write_payload([MetricPoint("m", "IGAUGE", {}, 1.0)])
    assert "labels" not in payload


# --- конфиг -----------------------------------------------------------------------


def test_config_disabled_by_default() -> None:
    cfg = MetricsConfig.from_env({})
    assert cfg.enabled is False


def test_config_enabled_parses_folder_and_interval() -> None:
    cfg = MetricsConfig.from_env(
        {
            "MANDALA_METRICS_ENABLED": "TRUE",
            "YC_MONITORING_FOLDER_ID": "b1gxxx",
            "MANDALA_METRICS_PUSH_INTERVAL": "45",
            "YC_IAM_TOKEN": "t0ken",
        }
    )
    assert cfg.enabled is True
    assert cfg.folder_id == "b1gxxx"
    assert cfg.push_interval == 45.0
    assert cfg.iam_token == "t0ken"


def test_config_interval_has_floor() -> None:
    cfg = MetricsConfig.from_env(
        {"MANDALA_METRICS_ENABLED": "1", "MANDALA_METRICS_PUSH_INTERVAL": "1"}
    )
    assert cfg.push_interval == 5.0


# --- no-op при выключенных метриках -----------------------------------------------


def test_record_helpers_are_noop_when_disabled() -> None:
    # Реестр не установлен: вызовы не должны падать и ничего не пишут.
    assert metrics.get_registry() is None
    metrics.record_http_request(route="/health", method="GET", status=200, elapsed_ms=1.0)
    metrics.record_llm(outcome="ok", elapsed_ms=1.0)
    metrics.record_telegram_delivery(method="sendMessage", outcome="ok")
    assert metrics.get_registry() is None


# --- нормализация роутов ----------------------------------------------------------


def test_normalize_route() -> None:
    assert normalize_route("/webhooks/telegram/astrology") == "/webhooks/telegram/{vertical_id}"
    assert normalize_route("/health") == "/health"
    assert normalize_route("/webhooks/web") == "/webhooks/web"
    assert normalize_route("/unknown/deep/path") == "/unknown"
    assert normalize_route("/") == "/"


# --- инструментация LLM -----------------------------------------------------------


def _llm_client(handler: object) -> OpenAICompatibleTextClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return OpenAICompatibleTextClient(
        base_url="https://example.test/v1",
        api_key="sk-test",
        default_model="m",
        client=httpx.Client(transport=transport),
    )


def test_llm_metric_ok(registry: MetricsRegistry) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
        )

    with _llm_client(handler) as client:
        client.complete([ChatMessage(role="user", content="x")])
    points = registry.snapshot()
    assert _counter(points, LLM_REQUESTS, {"outcome": "ok"}) == 1.0
    # Латентность LLM эмитится DGAUGE avg/max — иначе виджет «llm-latency» без данных.
    assert _latency(points, LLM_LATENCY_MS, "avg") is not None
    assert _latency(points, LLM_LATENCY_MS, "max") is not None


def test_llm_metric_error(registry: MetricsRegistry) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    with _llm_client(handler) as client, pytest.raises(LlmProviderError):
        client.complete([ChatMessage(role="user", content="x")])
    assert _counter(registry.snapshot(), LLM_REQUESTS, {"outcome": "error"}) == 1.0


def test_llm_metric_timeout(registry: MetricsRegistry) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with _llm_client(handler) as client, pytest.raises(LlmProviderError):
        client.complete([ChatMessage(role="user", content="x")])
    assert _counter(registry.snapshot(), LLM_REQUESTS, {"outcome": "timeout"}) == 1.0


# --- инструментация доставки Telegram ---------------------------------------------


def _tg_client(handler: object) -> TelegramBotApiClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return TelegramBotApiClient("token", client=httpx.Client(transport=transport))


def test_telegram_delivery_metric_ok(registry: MetricsRegistry) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    with _tg_client(handler) as api:
        api.send_message(chat_id=1, text="hi")
    labels = {"method": "sendMessage", "outcome": "ok"}
    assert _counter(registry.snapshot(), TELEGRAM_DELIVERY, labels) == 1.0


def test_telegram_delivery_metric_error(registry: MetricsRegistry) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "blocked"})

    with _tg_client(handler) as api, pytest.raises(TelegramApiError):
        api.send_message(chat_id=1, text="hi")
    labels = {"method": "sendMessage", "outcome": "error"}
    assert _counter(registry.snapshot(), TELEGRAM_DELIVERY, labels) == 1.0


# --- инструментация HTTP (мидлварь) -----------------------------------------------


def test_http_middleware_records_request(registry: MetricsRegistry) -> None:
    app = create_app()
    client = TestClient(app)
    with patch("mandala.http.app.get_engine") as mock_get_engine:
        mock_engine = Mock()
        mock_conn = Mock()
        mock_result = Mock()
        mock_result.fetchone.return_value = (1,)
        mock_conn.execute.return_value = mock_result
        mock_engine.begin.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = Mock(return_value=None)
        mock_get_engine.return_value = mock_engine
        resp = client.get("/health")
    assert resp.status_code == 200
    points = registry.snapshot()
    value = _counter(
        points,
        HTTP_REQUESTS,
        {"route": "/health", "method": "GET", "status": "200"},
    )
    assert value == 1.0
    # Латентность ответа эмитится DGAUGE avg/max с меткой роута — иначе виджеты
    # «app-latency» / «tg-latency» показывают «Нет данных» даже под трафиком.
    assert _latency(points, HTTP_LATENCY_MS, "avg") is not None
    assert _latency(points, HTTP_LATENCY_MS, "max") is not None
    assert any(p.name == HTTP_LATENCY_MS and p.labels.get("route") == "/health" for p in points)


# --- pusher: heartbeat и офлайн-отправка ------------------------------------------


def test_pusher_flush_includes_heartbeat_and_posts() -> None:
    reg = MetricsRegistry()
    reg.incr("m", {"a": "1"})
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={})

    cfg = MetricsConfig.from_env(
        {"MANDALA_METRICS_ENABLED": "1", "YC_MONITORING_FOLDER_ID": "b1gxxx"}
    )
    pusher = metrics.YcMonitoringPusher(
        reg,
        cfg,
        token_provider=lambda: "tok",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    pusher.flush_once()
    body = captured["body"]
    assert isinstance(body, str)
    assert APP_UP in body
    assert "folderId=b1gxxx" in str(captured["url"])
    assert "service=custom" in str(captured["url"])


def test_pusher_swallows_errors() -> None:
    reg = MetricsRegistry()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    cfg = MetricsConfig.from_env(
        {"MANDALA_METRICS_ENABLED": "1", "YC_MONITORING_FOLDER_ID": "b1gxxx"}
    )
    pusher = metrics.YcMonitoringPusher(
        reg,
        cfg,
        token_provider=lambda: "tok",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    # Не должно бросать наружу.
    pusher.flush_once()
