# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Astrology: two-school computation model

Positions are computed in Python (Swiss Ephemeris via kerykeion) and injected into the LLM
prompt as data — the model interprets, it never computes or invents positions. Authoritative
code: `src/mandala/astro/natal_chart.py`.

- **Two schools, never mixed.** `western` = Tropical zodiac + Placidus houses; `vedic` =
  Sidereal zodiac, **Lahiri** ayanamsa, **whole-sign** houses (each sign = one Bhava from the
  Lagna). Both must match standard reference software for the chosen school. The active school
  is `natal_chart_data["chart_system_key"]` (falls back to agent_card `astro_system`).
- **Transits honor the natal school.** `calculate_current_transits(..., system=...)` must be
  called with the same system as the natal chart; otherwise a forecast overlays a tropical grid
  on a sidereal chart and the answer looks "in between" (real user feedback). Wired in
  `services/text_reply.py`.
- **Birth time is in the birthplace timezone.** `tz_str` comes from the geocoder
  (Nominatim + timezonefinder) and is passed to kerykeion, which converts local→UT — not UTC.
- **Regression:** `tests/test_evgenia_natal_regression.py` reproduces a real user's western +
  vedic reference charts from reverse-engineered birth data (geocoder mocked, runs offline) and
  asserts the two schools differ by exactly the Lahiri ayanamsa (not a blend).

### LLM navigation protocol (astrology «robot navigator» UX)

The astrology bot answers as a navigator, not a chat partner: a short message plus
LLM-generated navigation. The model appends a machine block at the very end of its reply
(after the optional `---mandala---` agent-card block):

```
<short message>
---mandala-nav---
{"buttons":[{"label":"…","q":"…"}],"terms":[{"term":"…","q":"…"}]}
```

- Parser + id assignment + click resolution: `src/mandala/services/nav_protocol.py`
  (`split_llm_nav_suffix`, `assign_ids`, `resolve_nav_action`). Invalid/missing JSON
  degrades to plain text — never raises.
- `q` (full follow-up query) can't fit Telegram's 64-byte `callback_data` / 64-char
  start-payload, so `assign_ids` stores an `id -> q` map in `agent_card["nav_map"]`
  (persisted via `ProfileRepository.merge_agent_card`; overwritten each nav turn = current
  step). Buttons carry only `mdl:nav:<id>`; clickable terms carry `mdlnav_<id>`.
- Clicks route in `domain/handler.py` (before intake): `resolve_nav_action` turns
  `mdl:nav:*` (inline button) or `/start mdlnav_*` (term deep-link) back into `q` and runs a
  normal LLM turn. Wiring that attaches buttons/term_links: `services/text_reply.py`.
- Terms render as inline `t.me/<bot>?start=<payload>` links in
  `adapters/telegram/text_format.py` (`format_llm_text_for_telegram_html`); bot username via
  env `TELEGRAM_BOT_USERNAME` or cached `getMe` in `outbound_send.py`. No username → terms
  stay plain text (safe degrade).
- Profile/reset/help live in the burger menu (`setMyCommands` in `bot_commands.py`,
  `/profile` handled in `scenario_intake.py`); the persistent reply keyboard is content-nav
  only. The channel-agnostic `OutboundMessage.term_links` field carries `{term, payload}`.

### Local toolchain note

Deps are locked in `uv.lock` (numpy 2.4.4, mypy 1.20.2). A plain `pip install -e ".[dev]"`
into a fresh venv pulls newer numpy whose typeshed uses PEP 695 `type` statements that mypy
(configured `python_version = 3.11`) rejects on numpy stubs. Prefer `uv sync`, or pin
`numpy==2.4.4 mypy==1.20.2` to match the lock before running `scripts/check.sh`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
