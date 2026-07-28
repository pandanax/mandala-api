"""The astrology system prompt must steer the LLM toward short nav labels,
short messages, and «offered topic → button» — checked on prompt TEXT (no LLM)."""

from mandala.verticals.prompts import VERTICAL_SYSTEM_PROMPTS

ASTRO = VERTICAL_SYSTEM_PROMPTS["astrology"]


def test_labels_are_short_icon_plus_one_two_words() -> None:
    # No longer instructs long/full-title labels.
    assert "ПОЛНЫЙ интересный заголовок" not in ASTRO
    assert "живой пункт" not in ASTRO
    # Instructs the icon + 1–2 words rule.
    assert "иконка + 1–2 слова" in ASTRO
    assert "вся конкретика перехода живёт в q" in ASTRO


def test_messages_must_be_short() -> None:
    assert "МАКСИМАЛЬНО короткий (2–3 предложения)" in ASTRO


def test_offered_topic_becomes_a_button() -> None:
    assert "ТЕМА ИЗ ТЕКСТА → КНОПКА" in ASTRO
    assert "Нельзя предлагать тему прозой без кнопки" in ASTRO


def test_nav_schema_and_marker_unchanged() -> None:
    assert "---mandala-nav---" in ASTRO
    assert '{"buttons":[{"label":"…","q":"…"}],"terms":[{"term":"…","q":"…"}]}' in ASTRO


def test_example_labels_are_short() -> None:
    assert '{"label":"🌙 Сон"' in ASTRO
    assert '{"label":"💞 Отношения"' in ASTRO
    assert '"label":"⬅️ Ещё темы"' in ASTRO
