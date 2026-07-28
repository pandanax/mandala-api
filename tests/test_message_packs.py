"""Единый источник пакетов сообщений: дефолты, env-параметризация, резолверы."""

from __future__ import annotations

import pytest

from mandala.services.message_packs import (
    PACK_IDS,
    all_packs,
    pack_by_id,
    pack_by_payload,
    starting_balance,
)


def test_three_packs_defaults() -> None:
    packs = all_packs()
    assert [p.pack_id for p in packs] == ["100", "300", "1000"]
    assert [(p.price_stars, p.messages) for p in packs] == [(1, 100), (2, 300), (5, 1000)]
    # Каждый payload стабилен и уникален (ключ товара в журнале покупок).
    payloads = [p.payload for p in packs]
    assert payloads == ["mandala_pack_100", "mandala_pack_300", "mandala_pack_1000"]
    assert len(set(payloads)) == 3


def test_pack_by_id_and_payload_roundtrip() -> None:
    for pid in PACK_IDS:
        by_id = pack_by_id(pid)
        assert by_id is not None
        by_payload = pack_by_payload(by_id.payload)
        assert by_payload == by_id
    assert pack_by_id("999") is None
    assert pack_by_payload("mandala_pack_999") is None
    assert pack_by_payload("") is None


def test_starting_balance_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANDALA_MESSAGE_WALLET_START", raising=False)
    assert starting_balance() == 20
    monkeypatch.setenv("MANDALA_MESSAGE_WALLET_START", "50")
    assert starting_balance() == 50
    # Невалидное значение → дефолт.
    monkeypatch.setenv("MANDALA_MESSAGE_WALLET_START", "0")
    assert starting_balance() == 20
    monkeypatch.setenv("MANDALA_MESSAGE_WALLET_START", "abc")
    assert starting_balance() == 20


def test_button_label_format() -> None:
    p = pack_by_id("300")
    assert p is not None
    assert p.button_label == "2 ⭐ · 300 сообщений"
