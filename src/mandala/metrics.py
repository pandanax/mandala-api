"""Лёгкие метрики в YC Monitoring (service=custom) для VM-деплоя.

Дашборд-as-code — ``terraform/monitoring.tf``. Имена метрик здесь и селекторы в
дашборде **обязаны совпадать** (см. константы ниже).

Дизайн:

* Инструментация на границах подсистем (LLM-клиент, Telegram ``bot_api``,
  HTTP-мидлварь) вызывает дешёвые ``record_*``. Если метрики выключены
  (``MANDALA_METRICS_ENABLED`` не задан) — это **no-op**: ничего не отправляется,
  в тестах не создаётся фоновый поток.
* Значения копятся в :class:`MetricsRegistry` (потокобезопасно). Фоновый поток
  :class:`YcMonitoringPusher` раз в интервал снимает snapshot и POST-ит его в YC
  Monitoring write API. Любая ошибка отправки логируется на DEBUG и **не** всплывает
  в приложение.

Типы метрик (YC v2 write):

* ``COUNTER`` — монотонный счётчик (RPS/ошибки YC считает сам через ``rate()``);
* ``DGAUGE`` — вещественный «мгновенный» показатель (латентность за окно: avg/max);
* ``IGAUGE`` — целочисленный gauge (liveness-heartbeat ``mandala.app.up=1``).

Токен для отправки: ``YC_IAM_TOKEN`` (env) или сервисный аккаунт ВМ через metadata
service. Идентификатор каталога — ``YC_MONITORING_FOLDER_ID`` (или ``YC_FOLDER_ID``).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# --- Имена метрик (совпадают с селекторами дашборда terraform/monitoring.tf) ---
HTTP_REQUESTS = "mandala.http.requests"  # COUNTER {route, method, status}
HTTP_LATENCY_MS = "mandala.http.latency_ms"  # DGAUGE  {route, stat=avg|max}
LLM_REQUESTS = "mandala.llm.requests"  # COUNTER {outcome=ok|error|timeout}
LLM_LATENCY_MS = "mandala.llm.latency_ms"  # DGAUGE  {stat=avg|max}
TELEGRAM_DELIVERY = "mandala.telegram.delivery"  # COUNTER {method, outcome=ok|error}
APP_UP = "mandala.app.up"  # IGAUGE  liveness-heartbeat

_TRUE = frozenset({"1", "true", "yes", "on"})
_DEFAULT_ENDPOINT = "https://monitoring.api.cloud.yandex.net/monitoring/v2/data/write"
_METADATA_TOKEN_URL = (
    "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
)

_Key = tuple[str, tuple[tuple[str, str], ...]]


@dataclass(frozen=True)
class MetricPoint:
    """Одна точка для отправки в YC (после снятия snapshot)."""

    name: str
    type: str  # COUNTER | DGAUGE | IGAUGE
    labels: dict[str, str]
    value: float


@dataclass
class _LatAgg:
    """Оконная агрегация латентности (сбрасывается на каждом snapshot)."""

    count: int = 0
    total: float = 0.0
    maximum: float = 0.0


class MetricsRegistry:
    """Потокобезопасное накопление счётчиков и латентностей в процессе."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[_Key, float] = {}
        self._lat: dict[_Key, _LatAgg] = {}

    @staticmethod
    def _key(name: str, labels: Mapping[str, str] | None) -> _Key:
        items = tuple(sorted((labels or {}).items()))
        return (name, items)

    def incr(self, name: str, labels: Mapping[str, str] | None = None, value: float = 1.0) -> None:
        """Прибавить к монотонному счётчику (кумулятивно на весь процесс)."""
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def observe_ms(
        self, name: str, labels: Mapping[str, str] | None = None, value: float = 0.0
    ) -> None:
        """Записать наблюдение латентности (мс) в текущее окно."""
        key = self._key(name, labels)
        with self._lock:
            agg = self._lat.get(key)
            if agg is None:
                agg = _LatAgg()
                self._lat[key] = agg
            agg.count += 1
            agg.total += value
            if value > agg.maximum:
                agg.maximum = value

    def snapshot(self) -> list[MetricPoint]:
        """Снять точки для отправки. Счётчики кумулятивны, латентности сбрасываются."""
        points: list[MetricPoint] = []
        with self._lock:
            for (name, lbls), val in self._counters.items():
                points.append(MetricPoint(name, "COUNTER", dict(lbls), val))
            for (name, lbls), agg in self._lat.items():
                if agg.count == 0:
                    continue
                avg_labels = dict(lbls)
                avg_labels["stat"] = "avg"
                points.append(MetricPoint(name, "DGAUGE", avg_labels, agg.total / agg.count))
                max_labels = dict(lbls)
                max_labels["stat"] = "max"
                points.append(MetricPoint(name, "DGAUGE", max_labels, agg.maximum))
            self._lat.clear()
        return points


def build_write_payload(
    points: list[MetricPoint], common_labels: Mapping[str, str] | None = None
) -> dict[str, object]:
    """Собрать тело запроса YC Monitoring v2 ``/data/write``."""
    metrics: list[dict[str, object]] = [
        {"name": p.name, "type": p.type, "labels": p.labels, "value": p.value} for p in points
    ]
    payload: dict[str, object] = {"metrics": metrics}
    if common_labels:
        payload["labels"] = dict(common_labels)
    return payload


@dataclass(frozen=True)
class MetricsConfig:
    """Конфигурация отправки метрик из окружения."""

    enabled: bool
    folder_id: str
    endpoint: str
    service: str
    push_interval: float
    iam_token: str | None
    common_labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MetricsConfig:
        env = os.environ if environ is None else environ
        enabled = env.get("MANDALA_METRICS_ENABLED", "").strip().lower() in _TRUE
        folder_id = (
            env.get("YC_MONITORING_FOLDER_ID", "").strip() or env.get("YC_FOLDER_ID", "").strip()
        )
        endpoint = env.get("MANDALA_METRICS_ENDPOINT", "").strip() or _DEFAULT_ENDPOINT
        try:
            interval = float(env.get("MANDALA_METRICS_PUSH_INTERVAL", "30"))
        except ValueError:
            interval = 30.0
        interval = max(interval, 5.0)
        iam_token = env.get("YC_IAM_TOKEN", "").strip() or None
        host = env.get("HOSTNAME", "").strip()
        common: dict[str, str] = {"host": host} if host else {}
        return cls(
            enabled=enabled,
            folder_id=folder_id,
            endpoint=endpoint,
            service="custom",
            push_interval=interval,
            iam_token=iam_token,
            common_labels=common,
        )


def _default_token_provider(config: MetricsConfig) -> Callable[[], str | None]:
    """Статический токен из env или ленивое получение из metadata service ВМ."""
    if config.iam_token:
        static_token = config.iam_token
        return lambda: static_token

    cache: dict[str, tuple[float, str]] = {}

    def provider() -> str | None:
        now = time.monotonic()
        cached = cache.get("token")
        if cached is not None and cached[0] > now:
            return cached[1]
        try:
            with httpx.Client(timeout=httpx.Timeout(3.0)) as client:
                resp = client.get(_METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            return None
        cache["token"] = (now + 600.0, token)
        return token

    return provider


class YcMonitoringPusher:
    """Фоновый поток: раз в интервал POST-ит snapshot в YC Monitoring write API."""

    def __init__(
        self,
        registry: MetricsRegistry,
        config: MetricsConfig,
        *,
        token_provider: Callable[[], str | None] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self._token_provider = token_provider or _default_token_provider(config)
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        thread = threading.Thread(target=self._run, name="yc-metrics-pusher", daemon=True)
        self._thread = thread
        thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._config.push_interval):
            self.flush_once()

    def flush_once(self) -> None:
        """Один цикл отправки. Никогда не бросает исключений наружу."""
        try:
            points = self._registry.snapshot()
            points.append(MetricPoint(APP_UP, "IGAUGE", {}, 1.0))
            token = self._token_provider()
            if not token:
                logger.debug("metrics: нет IAM-токена, пропускаю отправку")
                return
            payload = build_write_payload(points, self._config.common_labels)
            resp = self._client.post(
                self._config.endpoint,
                params={"folderId": self._config.folder_id, "service": self._config.service},
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            if resp.status_code >= 400:
                logger.debug("metrics: write API HTTP %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:  # noqa: BLE001 — метрики не должны ронять приложение
            logger.debug("metrics: отправка не удалась: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.flush_once()
        self._client.close()


# --- Глобальный активный реестр и жизненный цикл ---------------------------------

_registry: MetricsRegistry | None = None
_pusher: YcMonitoringPusher | None = None
_init_lock = threading.Lock()


def get_registry() -> MetricsRegistry | None:
    """Текущий активный реестр (или ``None``, если метрики выключены)."""
    return _registry


def install_registry(registry: MetricsRegistry | None) -> MetricsRegistry | None:
    """Установить активный реестр напрямую (для тестов). Возвращает предыдущий."""
    global _registry
    prev = _registry
    _registry = registry
    return prev


def init_from_env(environ: Mapping[str, str] | None = None, *, start_pusher: bool = True) -> bool:
    """Включить метрики по env (идемпотентно). Возвращает ``True``, если активны."""
    global _registry, _pusher
    with _init_lock:
        if _registry is not None:
            return True
        config = MetricsConfig.from_env(environ)
        if not config.enabled:
            return False
        if not config.folder_id:
            logger.warning(
                "metrics: MANDALA_METRICS_ENABLED задан, но YC_MONITORING_FOLDER_ID пуст"
                " — метрики выключены"
            )
            return False
        registry = MetricsRegistry()
        _registry = registry
        if start_pusher:
            pusher = YcMonitoringPusher(registry, config)
            pusher.start()
            _pusher = pusher
        logger.info(
            "metrics: включены (folder=%s interval=%.0fs)", config.folder_id, config.push_interval
        )
        return True


def shutdown() -> None:
    """Остановить фоновую отправку и снять активный реестр."""
    global _registry, _pusher
    with _init_lock:
        if _pusher is not None:
            _pusher.stop()
            _pusher = None
        _registry = None


# --- Инструментация на границах подсистем ----------------------------------------


def normalize_route(path: str) -> str:
    """Нормализовать путь в шаблон роута (ограничение кардинальности меток)."""
    if path.startswith("/webhooks/telegram/"):
        return "/webhooks/telegram/{vertical_id}"
    if path in ("/health", "/webhooks/web"):
        return path
    segment = path.strip("/").split("/", 1)[0]
    return f"/{segment}" if segment else "/"


def record_http_request(*, route: str, method: str, status: int, elapsed_ms: float) -> None:
    """Метрики приложения: счётчик запросов (route/method/status) + латентность."""
    registry = _registry
    if registry is None:
        return
    registry.incr(HTTP_REQUESTS, {"route": route, "method": method, "status": str(status)})
    registry.observe_ms(HTTP_LATENCY_MS, {"route": route}, elapsed_ms)


def record_llm(*, outcome: str, elapsed_ms: float) -> None:
    """Метрики LLM: счётчик запросов по исходу (ok/error/timeout) + латентность."""
    registry = _registry
    if registry is None:
        return
    registry.incr(LLM_REQUESTS, {"outcome": outcome})
    registry.observe_ms(LLM_LATENCY_MS, {}, elapsed_ms)


def record_telegram_delivery(*, method: str, outcome: str) -> None:
    """Метрики доставки Telegram: счётчик вызовов Bot API по методу и исходу."""
    registry = _registry
    if registry is None:
        return
    registry.incr(TELEGRAM_DELIVERY, {"method": method, "outcome": outcome})
