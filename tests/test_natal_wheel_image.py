"""Колесо натальной карты как картинка в Telegram — офлайн, геокодер мокан.

Покрываем:
* рендер колеса → непустые PNG-байты с валидной сигнатурой ``\\x89PNG`` (kerykeion+cairosvg);
* резолвинг CSS-переменных ``var(--…)`` (иначе колесо бесцветное);
* байтовая отправка: ``deliver_outbound_messages`` формирует multipart ``send_photo``
  и возвращает ``{cache_key: file_id}``;
* кэш ``file_id``: второй ``/natal`` НЕ перерисовывает — шлёт фото по ``file_id``;
* деградация не-Telegram канала (web) — байтовое фото игнорируется, текст остаётся.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from mandala.domain.contracts import OutboundMessage
from mandala.repositories.profiles import ClientProfileDTO
from mandala.services import scenario_intake
from mandala.services.chart_wheel import _resolve_svg_vars, render_natal_wheel_png
from mandala.verticals.client_knowledge import (
    AGENT_CARD_NATAL_CHART_DATA,
    AGENT_CARD_NATAL_WHEEL_FILE_ID,
)

_GEO = "mandala.astro.natal_chart._geocode_city"
_NERYUNGRI = (56.66, 124.72, "Asia/Yakutsk")


# --- (A) рендер колеса ---------------------------------------------------------


def test_render_natal_wheel_returns_colorful_png() -> None:
    with patch(_GEO, return_value=_NERYUNGRI):
        png = render_natal_wheel_png("18.02.1988", "11:45", "Нерюнгри", "western")
    assert isinstance(png, bytes)
    assert png[:4] == b"\x89PNG", "невалидная PNG-сигнатура"
    # Цветное колесо заметно тяжелее бесцветного чёрного блоба (~26КБ) — грубая, но
    # надёжная проверка, что var()-цвета зарезолвились и колесо не «схлопнулось».
    assert len(png) > 50_000, f"подозрительно маленький PNG ({len(png)} байт) — цвета?"


def test_render_natal_wheel_uses_coords_without_geocoding() -> None:
    """С готовыми coords геокодер НЕ вызывается (сеть не нужна на /natal)."""
    with patch(_GEO, side_effect=AssertionError("геокодер не должен вызываться")) as geo:
        png = render_natal_wheel_png(
            "18.02.1988", "11:45", "Нерюнгри", "western", coords=_NERYUNGRI
        )
    geo.assert_not_called()
    assert png[:4] == b"\x89PNG"


def test_resolve_svg_vars_dereferences_nested_and_leaves_no_var() -> None:
    svg = (
        "<svg><style>:root{--a: var(--b); --b:#ffbe00; --c:#112233;}</style>"
        "<rect fill='var(--a)'/><line stroke='var(--c)'/><path fill='var(--missing)'/></svg>"
    )
    out = _resolve_svg_vars(svg)
    assert "var(--" not in out
    assert "#ffbe00" in out  # --a → --b → #ffbe00
    assert "#112233" in out  # --c
    assert "#000000" in out  # неизвестная переменная → безопасный дефолт


# --- (B) байтовая отправка + (C) кэш file_id ----------------------------------


def test_deliver_photo_bytes_multipart_and_returns_file_id() -> None:
    from mandala.adapters.telegram.outbound_send import deliver_outbound_messages

    api = MagicMock()
    api.send_photo.return_value = {
        "photo": [
            {"file_id": "small_id", "width": 90, "height": 90},
            {"file_id": "big_id", "width": 900, "height": 900},
        ]
    }
    msg = OutboundMessage(
        text="🪐 Натальная карта",
        photo_bytes=b"\x89PNG_fake",
        photo_filename="natal_wheel.png",
        photo_cache_key=AGENT_CARD_NATAL_WHEEL_FILE_ID,
        buttons=[],
    )
    uploaded = deliver_outbound_messages(api, chat_id=7, messages=[msg])

    assert api.send_photo.call_count == 1
    kwargs = api.send_photo.call_args.kwargs
    assert kwargs["photo_bytes"] == b"\x89PNG_fake"
    assert kwargs["filename"] == "natal_wheel.png"
    assert kwargs["photo"] is None
    # Самый крупный размер идёт в кэш.
    assert uploaded == {AGENT_CARD_NATAL_WHEEL_FILE_ID: "big_id"}


def test_deliver_cached_file_id_sends_by_reference_no_bytes() -> None:
    from mandala.adapters.telegram.outbound_send import deliver_outbound_messages

    api = MagicMock()
    msg = OutboundMessage(text="cap", photo="cached_file_id", buttons=[])
    uploaded = deliver_outbound_messages(api, chat_id=7, messages=[msg])
    kwargs = api.send_photo.call_args.kwargs
    assert kwargs["photo"] == "cached_file_id"
    assert kwargs["photo_bytes"] is None
    assert uploaded == {}  # нечего кэшировать — уже file_id


def test_send_photo_bytes_builds_multipart_call() -> None:
    """`send_photo(photo_bytes=…)` уходит через multipart (data+files), не JSON."""
    from mandala.adapters.telegram.bot_api import TelegramBotApiClient

    client = TelegramBotApiClient("123:ABC")
    mock = MagicMock(return_value={"photo": [{"file_id": "x", "width": 1, "height": 1}]})
    with patch.object(TelegramBotApiClient, "call_multipart", mock):
        client.send_photo(
            chat_id=5,
            photo_bytes=b"PNGDATA",
            filename="natal_wheel.png",
            caption="cap",
            parse_mode="HTML",
        )
    method, data, files = mock.call_args.args
    assert method == "sendPhoto"
    assert data["chat_id"] == "5"
    assert data["caption"] == "cap"
    assert files["photo"] == ("natal_wheel.png", b"PNGDATA", "image/png")


# --- /natal кэш через сценарий (in-memory) ------------------------------------


class _FakeProfiles:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def get_by_user_id(self, uid: UUID) -> ClientProfileDTO | None:
        row = self._store["profiles"].get(uid)
        if row is None:
            return None
        return ClientProfileDTO(
            user_id=uid,
            vertical_id="astrology",
            agent_card=dict(row["agent_card"]),
            scenario_state=dict(row["scenario_state"]),
            display_name=None,
        )

    def merge_agent_card(self, uid: UUID, patch: dict[str, Any]) -> None:
        self._store["profiles"][uid]["agent_card"].update(patch)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    s: dict[str, Any] = {"profiles": {}}
    monkeypatch.setattr(scenario_intake, "ProfileRepository", lambda conn: _FakeProfiles(conn))
    return s


def _seed_with_chart(store: dict[str, Any]) -> UUID:
    uid = uuid4()
    with patch(_GEO, return_value=_NERYUNGRI):
        from mandala.astro.natal_chart import calculate_natal_chart

        chart = calculate_natal_chart("18.02.1988", "11:45", "Нерюнгри", "western")
    store["profiles"][uid] = {
        "agent_card": {
            "birth_date": "18.02.1988",
            "birth_time": "11:45",
            "birth_place": "Нерюнгри",
            AGENT_CARD_NATAL_CHART_DATA: chart,
        },
        "scenario_state": {},
    }
    return uid


def test_first_natal_renders_wheel_second_uses_cached_file_id(store: dict[str, Any]) -> None:
    uid = _seed_with_chart(store)

    # 1-й /natal: колесо рендерится в байты (геокодер НЕ зовём — coords в chart.geo).
    with patch(_GEO, side_effect=AssertionError("no geocode on /natal")):
        first = scenario_intake._instant_natal(store, uid)  # type: ignore[arg-type]
    photo = next((m for m in first if m.photo_bytes), None)
    assert photo is not None, "первый /natal должен нарисовать колесо (байты)"
    assert photo.photo_cache_key == AGENT_CARD_NATAL_WHEEL_FILE_ID

    # Эмулируем ответ Telegram: file_id закеширован в agent_card.
    store["profiles"][uid]["agent_card"][AGENT_CARD_NATAL_WHEEL_FILE_ID] = "cached_wheel_fid"

    # 2-й /natal: НЕ перерисовываем — шлём по file_id (render НЕ вызывается).
    with patch(
        "mandala.services.chart_wheel.render_natal_wheel_png",
        side_effect=AssertionError("не должно перерисовывать при наличии кэша"),
    ):
        second = scenario_intake._instant_natal(store, uid)  # type: ignore[arg-type]
    photo2 = next((m for m in second if m.photo or m.photo_bytes), None)
    assert photo2 is not None
    assert photo2.photo == "cached_wheel_fid"
    assert photo2.photo_bytes is None


# --- (D) деградация не-Telegram канала ----------------------------------------


def test_non_telegram_channel_ignores_photo_bytes() -> None:
    """Web отдаёт OutboundMessage как JSON: photo_bytes исключён (exclude=True), текст цел.

    Telegram-адаптер читает ``photo_bytes`` как атрибут (доступен), а сериализация web-
    ответа его роняет — никакой base64-утечки колеса в JSON.
    """
    msg = OutboundMessage(text="колесо", photo_bytes=b"PNGDATA", buttons=[])
    # Атрибут доступен в Python (для Telegram-загрузки байтов)…
    assert msg.photo_bytes == b"PNGDATA"
    # …но в JSON-представлении (web-канал) его нет.
    dumped = msg.model_dump()
    assert "photo_bytes" not in dumped
    assert dumped.get("text") == "колесо"
    assert "photo_bytes" not in msg.model_dump_json()
