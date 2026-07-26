"""Гарантия инлайн-навигации: у каждого ответа бота есть кнопки «следующий шаг».

Единственная модель навигации в боте — инлайн-кнопки под сообщением (постоянной
нижней reply-клавиатуры больше нет). Кнопки выбирает LLM через nav-протокол
(:mod:`mandala.services.nav_protocol`). Но модель может не выдать nav-блок
(битый JSON, отвлеклась, не та вертикаль) — тогда сработает контекстный фолбэк
из этого модуля, чтобы ответ **никогда** не остался без навигации.

Фолбэк ведёт к следующему ходу диалога переиспользуя уже существующие
callback-коды быстрых действий (см. :mod:`mandala.verticals.quick_actions`), поэтому
клик по нему проходит обычный путь ``expand_inbound_quick_action`` → ответ бота.

Применяется точечно к терминальному (последнему не-invoice) сообщению каждого
ответа. Счёт на оплату (``invoice``) — терминальное платёжное сообщение со своим UI,
кнопки навигации к нему не крепим. Чистые сообщения-анкеты (шаги сбора данных) не
проходят через этот модуль: там пользователь вводит ответ текстом.
"""

from __future__ import annotations

from mandala.domain.contracts import OutboundMessage


def _btn(label: str, callback_data: str) -> dict[str, str]:
    return {"text": label, "callback_data": callback_data}


def _astrology_fallback_buttons() -> list[list[dict[str, str]]]:
    """Крайний фолбэк astrology, когда модель не дала навигацию.

    НЕ подставляем общие статические кнопки (Натальная/Прогноз/Сферы/Профиль) —
    они переехали в бургер-меню, а под ответами живёт только контекстная навигация
    самой модели. Здесь лишь одна возвратная кнопка «⬅️ К темам»: клик по ней просит
    модель предложить темы разбора — и она снова выдаёт контекстные кнопки. Так
    ответ никогда не остаётся без навигации, но и не несёт чужих пресных кнопок.
    """
    return [[_btn("⬅️ К темам", "mdl:topics")]]


def _therapy_fallback_buttons() -> list[list[dict[str, str]]]:
    """Контентная навигация therapy: выговориться / настроение / тревога."""
    return [
        [_btn("💬 Выговориться", "mdl_th:vent"), _btn("🌤️ Настроение", "mdl_th:mood")],
        [_btn("😟 Тревога", "mdl_th:anx")],
    ]


def fallback_nav_buttons(vertical_id: str) -> list[list[dict[str, str]]] | None:
    """Инлайн-кнопки-фолбэк для вертикали; ``None`` для неизвестной вертикали."""
    v = vertical_id.strip()
    if v == "astrology":
        return _astrology_fallback_buttons()
    if v == "therapy":
        return _therapy_fallback_buttons()
    return None


def ensure_nav(
    messages: list[OutboundMessage],
    vertical_id: str,
) -> list[OutboundMessage]:
    """Гарантировать инлайн-навигацию на терминальном сообщении ответа.

    К последнему сообщению, которое **не** является счётом на оплату, добавляем
    фолбэк-кнопки, если у него ещё нет своих кнопок и есть что показать
    (текст или фото). Уже заданные кнопки (в т.ч. навигация от LLM) не трогаем.

    Ничего не делает, если сообщений нет, у вертикали нет фолбэка, или подходящее
    сообщение не найдено (например, ответ — только счёт на оплату).
    """
    if not messages:
        return messages
    fallback = fallback_nav_buttons(vertical_id)
    if fallback is None:
        return messages
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.invoice is not None:
            continue
        if msg.buttons is None and (msg.text or msg.photo):
            messages[i] = msg.model_copy(update={"buttons": fallback})
        # Терминальное не-invoice сообщение найдено — дальше не идём в любом случае.
        return messages
    return messages
