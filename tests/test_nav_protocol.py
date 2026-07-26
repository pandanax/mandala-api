"""Юнит-тесты структурированной навигации LLM: парсинг, id-карта, резолв кликов."""

from __future__ import annotations

from mandala.services.nav_protocol import (
    NAV_CALLBACK_PREFIX,
    NAV_DEEPLINK_PREFIX,
    NAV_MARKER,
    assign_ids,
    extract_prose_nav,
    resolve_nav_action,
    split_llm_nav_suffix,
)

_VALID_BLOCK = (
    "Луна во Льве даёт яркость и потребность в признании.\n"
    f"{NAV_MARKER}\n"
    '{"buttons":[{"label":"1️⃣ Подробнее","q":"Расскажи подробнее про мою Луну во Льве"},'
    '{"label":"⬅️ Назад","q":"Вернись к общему разбору карты"}],'
    '"terms":[{"term":"Луна во Льве","q":"Что такое Луна во Льве в моей карте?"}]}'
)


# --- split_llm_nav_suffix: валидный вывод ---------------------------------------------


def test_split_valid_extracts_message_and_spec() -> None:
    text, spec = split_llm_nav_suffix(_VALID_BLOCK)
    assert NAV_MARKER not in text
    assert text.strip().startswith("Луна во Льве даёт")
    assert spec is not None
    assert len(spec.buttons) == 2
    assert spec.buttons[0].label == "1️⃣ Подробнее"
    assert spec.buttons[0].query.startswith("Расскажи подробнее")
    assert len(spec.terms) == 1
    assert spec.terms[0].term == "Луна во Льве"


def test_split_accepts_query_alias() -> None:
    raw = f'Привет.\n{NAV_MARKER}\n{{"buttons":[{{"label":"Дальше","query":"иди дальше"}}]}}'
    _, spec = split_llm_nav_suffix(raw)
    assert spec is not None
    assert spec.buttons[0].query == "иди дальше"


# --- split_llm_nav_suffix: безопасная деградация --------------------------------------


def test_split_no_marker_returns_text_and_none() -> None:
    text, spec = split_llm_nav_suffix("Просто ответ без навигации.")
    assert text == "Просто ответ без навигации."
    assert spec is None


def test_split_malformed_json_degrades_and_strips_block() -> None:
    raw = f"Короткое сообщение.\n{NAV_MARKER}\n{{не валидный json,,,}}"
    text, spec = split_llm_nav_suffix(raw)
    # Битый блок не показываем пользователю, но и не падаем.
    assert spec is None
    assert NAV_MARKER not in text
    assert text.strip() == "Короткое сообщение."


def test_split_empty_arrays_yield_none() -> None:
    raw = f'Текст.\n{NAV_MARKER}\n{{"buttons":[],"terms":[]}}'
    text, spec = split_llm_nav_suffix(raw)
    assert spec is None
    assert text.strip() == "Текст."


def test_split_drops_incomplete_entries() -> None:
    raw = (
        f"Текст.\n{NAV_MARKER}\n"
        '{"buttons":[{"label":"нет запроса"},{"label":"ок","q":"поехали"}],'
        '"terms":[{"term":"без q"},{"q":"без term"}]}'
    )
    _, spec = split_llm_nav_suffix(raw)
    assert spec is not None
    assert len(spec.buttons) == 1
    assert spec.buttons[0].label == "ок"
    assert spec.terms == ()


def test_split_carries_agent_card_block_back_to_head() -> None:
    # Если модель ошибочно поставила agent-card блок ПОСЛЕ nav-блока — он должен
    # остаться в head, чтобы его смог разобрать split_llm_agent_card_suffix.
    raw = (
        "Разбор карты.\n"
        f"{NAV_MARKER}\n"
        '{"buttons":[{"label":"Дальше","q":"дальше"}]}\n'
        "---mandala---\n"
        '{"natal_chart_text":"карта"}'
    )
    text, spec = split_llm_nav_suffix(raw)
    assert spec is not None
    assert "---mandala---" in text
    assert '{"natal_chart_text":"карта"}' in text
    assert NAV_MARKER not in text


# --- assign_ids ------------------------------------------------------------------------


def test_assign_ids_builds_map_buttons_and_term_links() -> None:
    _, spec = split_llm_nav_suffix(_VALID_BLOCK)
    assert spec is not None
    render = assign_ids(spec)
    # nav_map: n0/n1 для кнопок, t0 для термина.
    assert render.nav_map["n0"].startswith("Расскажи подробнее")
    assert render.nav_map["n1"].startswith("Вернись")
    assert render.nav_map["t0"].startswith("Что такое")
    # Кнопки по 2 в ряд, callback_data с префиксом.
    flat = [cell for row in render.buttons for cell in row]
    assert flat[0]["callback_data"] == f"{NAV_CALLBACK_PREFIX}n0"
    assert flat[1]["callback_data"] == f"{NAV_CALLBACK_PREFIX}n1"
    # term_links несут payload с deep-link префиксом.
    assert render.term_links == [{"term": "Луна во Льве", "payload": f"{NAV_DEEPLINK_PREFIX}t0"}]


def test_assign_ids_one_button_per_row() -> None:
    # Кнопки навигации — полные заголовки перехода: по одной в ряду (вертикальный список).
    raw = (
        f"m\n{NAV_MARKER}\n"
        '{"buttons":[{"label":"a","q":"1"},{"label":"b","q":"2"},{"label":"c","q":"3"}]}'
    )
    _, spec = split_llm_nav_suffix(raw)
    assert spec is not None
    render = assign_ids(spec)
    assert [len(r) for r in render.buttons] == [1, 1, 1]


def test_split_keeps_full_heading_label() -> None:
    # label — полный интересный заголовок (до 64 символов), не «1️⃣ …».
    label = "🌙 Ночное восстановление: что говорит карта о сне и расслаблении"
    raw = f'm\n{NAV_MARKER}\n{{"buttons":[{{"label":"{label}","q":"расскажи про сон"}}]}}'
    _, spec = split_llm_nav_suffix(raw)
    assert spec is not None
    assert spec.buttons[0].label == label


# --- extract_prose_nav: fallback для прозаического списка «куда дальше» ----------------


def test_extract_prose_nav_pulls_trailing_bullets_into_buttons() -> None:
    # Точный кейс капитана: модель написала «куда дальше» прозой без nav-JSON.
    text = (
        "Ваша карта говорит о высокой, но неровной энергии — важен режим.\n\n"
        "• 🌙 Ночное восстановление: что говорит карта о сне и расслаблении\n"
        "• 🏃 Оптимальный тип физической нагрузки по натальной карте\n"
        "• ⬅️ Вернуться к другим темам"
    )
    cleaned, spec = extract_prose_nav(text)
    assert spec is not None
    # Пункты «куда дальше» ушли из видимого текста в кнопки.
    assert "Ночное восстановление" not in cleaned
    assert "Вернуться к другим темам" not in cleaned
    assert cleaned.strip().startswith("Ваша карта говорит")
    labels = [b.label for b in spec.buttons]
    assert labels[0].startswith("🌙 Ночное восстановление")
    assert labels[1].startswith("🏃")
    # Возврат распознан и получил общий запрос «к темам».
    assert "⬅️" in labels[2]
    assert spec.buttons[2].query == "Какие ещё темы можно разобрать по моей натальной карте?"
    # Обычные пункты получают запрос от лица пользователя.
    assert spec.buttons[0].query.startswith("Расскажи подробнее")


def test_extract_prose_nav_no_bullets_returns_text_unchanged() -> None:
    text = "Луна во Льве даёт яркость. Готов углубиться в любую тему."
    cleaned, spec = extract_prose_nav(text)
    assert spec is None
    assert cleaned == text


def test_extract_prose_nav_ignores_single_bullet() -> None:
    text = "Короткий разбор.\n\n• Единственный пункт без блока навигации"
    cleaned, spec = extract_prose_nav(text)
    assert spec is None
    assert cleaned == text


def test_extract_prose_nav_never_empties_message() -> None:
    # Сообщение состоит ТОЛЬКО из буллетов — вырезать нечего, текст не трогаем.
    text = "• Первая тема\n• Вторая тема\n• ⬅️ Назад"
    cleaned, spec = extract_prose_nav(text)
    assert spec is None
    assert cleaned == text


# --- resolve_nav_action ----------------------------------------------------------------


def _nav_map() -> dict[str, str]:
    return {"n0": "углубить тему", "t0": "объясни термин"}


def test_resolve_callback_button() -> None:
    assert resolve_nav_action(f"{NAV_CALLBACK_PREFIX}n0", _nav_map()) == "углубить тему"


def test_resolve_start_deeplink_term() -> None:
    assert resolve_nav_action(f"/start {NAV_DEEPLINK_PREFIX}t0", _nav_map()) == "объясни термин"


def test_resolve_plain_start_is_not_nav() -> None:
    assert resolve_nav_action("/start", _nav_map()) is None


def test_resolve_unknown_id_returns_none() -> None:
    assert resolve_nav_action(f"{NAV_CALLBACK_PREFIX}n9", _nav_map()) is None
    assert resolve_nav_action(f"/start {NAV_DEEPLINK_PREFIX}t9", _nav_map()) is None


def test_resolve_ignores_non_nav_and_empty_map() -> None:
    assert resolve_nav_action("обычный текст", _nav_map()) is None
    assert resolve_nav_action(f"{NAV_CALLBACK_PREFIX}n0", None) is None
    assert resolve_nav_action(None, _nav_map()) is None
