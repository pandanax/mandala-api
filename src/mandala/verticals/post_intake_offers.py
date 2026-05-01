"""Сообщение и кнопки после завершения анкеты (UX: что делать дальше)."""

from __future__ import annotations

from typing import Any

from mandala.domain.contracts import OutboundMessage
from mandala.verticals.client_knowledge import AGENT_CARD_NATAL_CHART_TEXT


def _btn(text_label: str, callback_data: str) -> dict[str, str]:
    return {"text": text_label, "callback_data": callback_data}


def post_intake_completion_message(vertical_id: str, agent_card: dict[str, Any]) -> OutboundMessage:
    """Один исходящий блок: пояснение + inline-кнопки (Telegram/Web)."""
    v = vertical_id.strip()
    if v == "astrology":
        return _astrology_completion(agent_card)
    if v == "therapy":
        return _therapy_completion()
    return OutboundMessage(
        text=(
            "Спасибо, анкета сохранена. Дальше можно писать запросы текстом — "
            "я отвечу в режиме диалога."
        ),
    )


def _astrology_completion(agent_card: dict[str, Any]) -> OutboundMessage:
    has_natal = bool((agent_card or {}).get(AGENT_CARD_NATAL_CHART_TEXT))
    base_intro = (
        "Спасибо, анкета сохранена: имя, дата, место и время рождения учтены.\n\n"
        "Выберите действие кнопкой ниже или напишите свой вопрос текстом."
    )
    if has_natal:
        text = (
            f"{base_intro}\n\n"
            "У меня уже есть сохранённая натальная карта — можно углубиться по темам "
            "или взять прогноз."
        )
        buttons: list[list[dict[str, str]]] = [
            [
                _btn("Финансы и ресурс", "mdl:th_fin"),
                _btn("Отношения", "mdl:th_rel"),
            ],
            [
                _btn("Энергия и режим", "mdl:th_health"),
                _btn("Прогноз на сегодня", "mdl:fc_today"),
            ],
            [
                _btn("Прогноз на неделю", "mdl:fc_week"),
                _btn("Прогноз на месяц", "mdl:fc_month"),
            ],
            [_btn("Прогноз на год", "mdl:fc_year"), _btn("Совместимость", "mdl:syn")],
            [_btn("Обновить натальную карту", "mdl:natal")],
        ]
    else:
        text = base_intro
        buttons = [
            [_btn("Натальная карта", "mdl:natal")],
            [
                _btn("Прогноз на сегодня", "mdl:fc_today"),
                _btn("Прогноз на неделю", "mdl:fc_week"),
            ],
            [
                _btn("Прогноз на месяц", "mdl:fc_month"),
                _btn("Прогноз на год", "mdl:fc_year"),
            ],
            [_btn("Совместимость", "mdl:syn")],
        ]
    return OutboundMessage(text=text, buttons=buttons)


def _therapy_completion() -> OutboundMessage:
    return OutboundMessage(
        text=(
            "Спасибо, вводные сохранены.\n\n"
            "Можете выбрать вариант ниже или написать своими словами, что происходит."
        ),
        buttons=[
            [
                _btn("Выговориться", "mdl_th:vent"),
                _btn("Настроение", "mdl_th:mood"),
            ],
            [_btn("Тревога", "mdl_th:anx")],
        ],
    )
