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
- **Birth time is in the birthplace timezone — never a silent UTC.** `tz_str` comes from the
  geocoder (Nominatim + timezonefinder) and is passed to kerykeion, which converts local→UT.
  `_geocode_city` **raises `ValueError("Timezone …")`** when the tz can't be determined (it used
  to fall back to `"UTC"` silently → local time read as UTC → ascendant/houses shifted by the
  offset; that was Evgeniya's Gemini-instead-of-Pisces ascendant). `scenario_intake` escalates
  that to the user; **do not reintroduce a UTC fallback.** Western houses are set **explicitly to
  Placidus `'P'`** (kerykeion's default today, but defaults drift across versions).
- **The LLM never builds the chart.** The only source is `calculate_natal_chart`. When no computed
  `natal_chart_data` exists, `text_reply.build_natal_prompt_section` injects a *prohibition* (never
  a saved LLM chart text) — better to honestly not build it than fabricate. The old
  `natal_chart_text` LLM-authored path is gone from the prompt/injection (the parser key stays only
  to strip the `---mandala---` marker). "Has a chart" = computed data present, not saved text.
- **Stored derived data (block render).** `calculate_natal_chart` also saves the four main axes
  (`ascendant`/`descendant`/`midheaven`/`imum_coeli`, only when `time_known`) and
  `element_balance` (Fire/Earth/Air/Water counts over the 10 planets, pure sign arithmetic —
  `compute_element_balance`). `house_number` maps kerykeion's `'Tenth_House'`→`10`. `/natal`
  renders these in **reference-style blocks** (`services/chart_render.render_natal_chart_text`:
  axes → luminaries+mask → retrograde → planets-by-sign → planets-by-house → element balance →
  aspects split harmonious/tense → «Снаружи Асц, внутри Солнце» contrast). Render is
  deterministic (never LLM), tolerant of legacy saved data (missing axes/elements skipped or
  recomputed). Deep interpretation still goes through the LLM «Углублённый разбор» button.
  Render uses **only bold `**…**`/emoji/lists** — Telegram's `format_llm_text_for_telegram_html`
  does NOT support single-`_…_` italic (leaks literal underscores); never add `_…_`.
- **No technical latin point names ever reach the user.** kerykeion v5 emits aspect/point
  names like `True_North_Lunar_Node`, `Chiron`, and houses as `First_House`. The single
  translation gate is `natal_chart.translate_point_name` (EN→RU, `None` when untranslatable)
  + `is_display_safe_name` (rejects `_` or a ≥3-latin run; short «MC»/«IC» labels stay). Aspects
  drop when any part is unsafe; `natal_chart_to_system_text` renders houses as **numbers**
  (`house_number`, not raw `First_House`) — that raw-house string was leaking into the LLM
  «Углублённый разбор». Both user paths (`chart_render` **and** `natal_chart_to_system_text`)
  defensively skip unsafe legacy names. `/natal` caption carries date · **time** (when known) ·
  place — never the name (the wheel doesn't depend on it).
- **Birth time is asked as LOCAL time.** `intake_steps.json` `birth_time` prompt explicitly asks
  for local birthplace time; `intake_flow._echo_line` confirms «приму как МЕСТНОЕ время».
- **Chart-wheel image (shipped).** `/natal` (and any saved-chart render) sends a colored
  natal **wheel PNG** before the block text. Authoritative render: `services/chart_wheel.py`
  (`render_natal_wheel_png`) — deterministic, never LLM: kerykeion `KerykeionChartSVG.makeWheelOnlySVG`
  → **resolve CSS `var(--…)` colors** (kerykeion emits all colors as `:root` vars with nested
  refs; cairosvg doesn't resolve `var()` → a black blob — `_resolve_svg_vars` parses `:root`,
  derefs nested vars to `#hex`, substitutes) → `cairosvg` PNG. Needs **system libcairo** +
  a base font: `Containerfile` runtime installs `libcairo2 fonts-dejavu-core` (planet/sign
  glyphs are vector `<path>`; only degree numbers need a font). `cairosvg` is a runtime dep
  (`pyproject.toml`/`uv.lock`). The wheel builds its subject via the shared
  `natal_chart.build_astrological_subject` (same school/houses as the computed chart) using the
  **`geo` coords stored in `natal_chart_data`** — so `/natal` never re-geocodes (offline, fast).
- **Bytes photo upload + file_id cache.** `OutboundMessage.photo_bytes` (`exclude=True` → never
  in web JSON, safe degrade) carries the PNG; `bot_api.send_photo` uploads it **multipart**
  (`call_multipart`), `outbound_send.deliver_outbound_messages` returns `{photo_cache_key → file_id}`.
  First `/natal` uploads bytes with `photo_cache_key=AGENT_CARD_NATAL_WHEEL_FILE_ID`; the delivery
  callers (`webhook_delivery`/`polling`) persist the returned `file_id` to `agent_card` via
  `adapters/telegram/photo_cache.persist_photo_file_ids` — next `/natal` sends by `file_id`
  (instant, no re-render). Cache is invalidated (set to `""`) whenever the chart is recomputed
  (`_try_calculate_and_save_natal_chart`, i.e. profile edit / system switch). Local arm64 note:
  the image builds on **amd64** (`podman build --platform linux/amd64`) — pyswisseph has no
  aarch64 wheel and the slim builder has no gcc.
- **Wheel tests:** `tests/test_natal_wheel_image.py` (PNG signature+size = colored, `var()`
  resolution, no-geocode-with-coords, multipart send, file_id cache reuse, web `exclude` degrade).
- **Regression:** `tests/test_evgenia_natal_regression.py` (two-school accuracy) +
  `tests/test_natal_tz_and_no_fabrication.py` (tz-not-UTC raises, local-time ascendant, western
  Placidus reference, no-fabrication) + `tests/test_natal_block_render.py` (all blocks present,
  axes saved, element balance, local-time prompt/echo). All mock the geocoder and run offline.

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
- `q` (full follow-up query) can't fit Telegram's 64-byte `callback_data`, so `assign_ids`
  stores an `id -> q` map in `agent_card["nav_map"]` (persisted via
  `ProfileRepository.merge_agent_card`; overwritten each nav turn = current step). Nav
  buttons carry `mdl:nav:n<id>`; term buttons carry `mdl:nav:t<id>`.
- **Clickable terms are inline-callback BUTTONS, never in-text links** (captain's call
  after a prod bug). Telegram in-text is only clickable for auto-linked slash-commands, and
  a `t.me/<bot>?start=<payload>` deep-link does **not** deliver the start-payload to an
  already-open chat (returning users) — iOS repeat-tap opens the chat without payload,
  Desktop shows a START button, WebK ignores payloads (bugs.telegram.org/c/8830,
  tdesktop#27064). So a tapped “term link” arrived as a bare `/start` = soft intake restart.
  Fix: the model's `terms` (and the deterministic `/matrix` glossary) become **2–5 key**
  `📖 <term>` callback buttons under the message (`callback_query` always delivers); the rest
  stay plain text. Builder: `nav_protocol.build_term_buttons` (single source for both paths;
  cap `_MAX_TERM_BUTTONS = 5`, 2 per row). `assign_ids` appends them after the nav (`n*`)
  buttons; `NavRender` no longer has `term_links`. The old in-text deep-link machinery is
  **removed**: no `OutboundMessage.term_links`, no `format_llm_text_for_telegram_html`
  term/bot_username params, no `_resolve_bot_username`/`TELEGRAM_BOT_USERNAME` for terms.
- Clicks route in `domain/handler.py` (before intake): `resolve_nav_action` turns
  `mdl:nav:*` (inline button) back into `q` and runs a normal LLM turn (it still also
  resolves legacy `/start mdlnav_*` for harmlessness). Wiring that attaches buttons:
  `services/text_reply.py`.
- Profile/reset/help live in the burger menu (`setMyCommands` in `bot_commands.py`,
  `/profile` handled in `scenario_intake.py`).
- **Deterministic numerology term buttons (`/matrix` render).** The `/matrix` render
  (`chart_render.render_destiny_matrix_*`) never goes through the LLM, so its terms can't rely
  on the model's nav block. `services/term_linkify.numerology_term_buttons(text)` closes that
  gap: it scans a fixed glossary (22 arcana + octagram positions/lines/chakras — source of
  truth `astro.destiny_matrix.ARCANA_NAMES`/`CHAKRAS_TOP_DOWN`), keeps the first 2–5 in text
  order via `build_term_buttons`, and returns `(button_rows, nav_map)` reusing the SAME scheme
  as LLM nav (`nav_map` id→«объясни термин X» query, callback `mdl:nav:t<id>`).
  `scenario_intake._matrix_message_with_clickable_terms` persists the `nav_map` to
  `agent_card` and prepends the term buttons above the render's nav — a tap resolves through
  the existing `resolve_nav_action` → normal LLM turn (explanation + inline nav). Matching is
  **case-sensitive with Cyrillic word boundaries** (so «Император» ≠ inside «Императрица», short
  «Суд» ≠ inside «судьбы»), first-occurrence-only, longest-wins on overlap. Empty result
  degrades safely (plain text). The prompt (`verticals/prompts.py`) tells the model to mark
  only the **2–5 key** terms in `terms`. Tests: `tests/test_numerology_clickable_terms.py`,
  `tests/test_clickable_term_reliable_buttons.py`.
- **Inline-only navigation (no persistent reply keyboard).** Every bot answer ends with an
  inline keyboard — the LLM picks it via the nav block above; when the model emits no valid
  nav (bad JSON, non-astrology vertical), `services/nav_guarantee.py` (`ensure_nav`) attaches
  a contextual fallback to the terminal (last non-invoice) message so a reply is **never**
  left without navigation. The astrology fallback is **2–4 contextual buttons** (Продолжить
  тему `mdl:continue` / Другой аспект `mdl:another` / Прогноз / «⬅️ К темам» last) — not a
  single «К темам» (captain: «почти всегда должны быть нормальные кнопки»); every fallback
  code expands via `quick_actions`. The prompt (`verticals/prompts.py`) hard-forbids listing
  «куда дальше» in prose (bad→good example) so the model puts forks in buttons, not text;
  `ensure_nav` logs `nav fallback fired` (info) when the model gave no valid nav. Every
  domain return path that isn't a bare intake-wizard prompt
  runs through `ensure_nav` (`domain/handler.py`, `services/scenario_intake.py`). The old
  `ASTROLOGY_REPLY_KEYBOARD` is gone; `outbound_send.deliver_outbound_messages` attaches a
  one-time `ReplyKeyboardRemove` to the first button-less message of a batch to clear the
  lingering sticky keyboard for existing users (stateless, no extra bubble). Lingering
  keyboard taps still resolve via `_KEYBOARD_TEXT_TO_CODE` in `quick_actions.py`.
- **«⬅️ К темам» renders a deterministic RICH topics menu (not an LLM prose request).**
  `mdl:topics` expands (in `quick_actions.py`) to the special code `__topics_menu__`
  (`TOPICS_MENU_CODE` / `is_topics_menu`) and is intercepted in `domain/handler.py`
  (`_handle_topics_menu`, next to `_handle_forecast_menu`) **before** the LLM — so a tap
  **always** yields a rich inline button set (short intro + 9 topic buttons), never depends on
  the model emitting a nav block. Buttons reuse existing actions: instant renders `/natal` ·
  `/matrix` · `/numerology`, forecast submenu `mdl:forecast_menu`, and theme deep-dives
  `mdl:th_rel` · `mdl:th_fin` · `mdl:th_career` · `mdl:th_personality` · `mdl:th_health`
  (each already an LLM-query quick action). The menu carries its own buttons so `ensure_nav`
  leaves it alone. The single «⬅️ К темам» fallback button (in `nav_guarantee.py`) still
  points at `mdl:topics` — clicking it now opens this rich menu. Tests:
  `tests/test_keyboard_and_commands.py` (`_handle_topics_menu` shape, all callbacks route,
  `mdl:topics` → menu via `handle_inbound` without touching the LLM).

### Intake: per-field confirm → whole-form confirm → save (draft state machine)

The questionnaire is a **pure state machine** in `services/intake_flow.py` (`step_intake`,
no DB/network) driven by the DB wrapper `services/scenario_intake.py`. Every field is
**echoed for confirmation** ([Верно ✅]/[Исправить ✏️]); confirmed values live in a
**draft** in `scenario_state` (`intake_draft`), NOT in `agent_card`. Only after the
**whole-form summary** is confirmed ([Подтвердить и сохранить ✅]) does the wrapper write
the profile and **synchronously compute + save the natal chart (Swiss Ephemeris) and the
Destiny Matrix**. `agent_card` key for the matrix: `AGENT_CARD_DESTINY_MATRIX_DATA`.

- **Schema v2** (`INTAKE_SCHEMA_VERSION`, phases `input`/`field_confirm`/`form_confirm`/
  `field_pick`). Existing users don't break: completed (`intake_complete` + no phase) →
  wrapper returns `None` → straight to LLM; in-progress v1 → draft seeded from `agent_card`.
  The wrapper gate is: active phase OR not-complete OR an `mdl:intake:*`/`mdl:profile:edit`
  callback; otherwise pass-through.
- **Place validation resolves the city at the step** (geocoder + timezone), not at save —
  the injected `resolve_place` (wraps `astro.natal_chart._geocode_city`) raises
  `PlaceResolveError` and the answer is re-asked before anything is saved. Mock the geocoder
  offline (see `tests/test_intake_ux_wrapper.py`).
- **Editing** (`/profile` → «Редактировать» = `mdl:profile:edit`, or «Изменить» from the
  summary) runs the same flow (`field_pick` → single field → back to summary → re-save +
  recompute). `build_profile_message` carries the edit button.
- **Instant renders (no LLM):** `/natal` and `/matrix` render saved data via
  `services/chart_render.py` (deterministic); they compute+save on the fly if missing.
  `/natal`+`/matrix` are handled as commands in `scenario_intake` (NOT the LLM burger path —
  `_burger_nav_command` only does `/forecast` now). `/matrix` is in the bot menu.
- **Nav on EVERY message** (incl. errors, promo, confirmations): interactive steps set their
  own buttons in `intake_flow`; the wrapper's `_guarantee_all_nav` attaches a fallback to any
  remaining button-less message (stricter than `ensure_nav`, which only touches the terminal).
- **`/start` is a one-time kickoff, not a reset** (`scenario_intake._handle_command`,
  `_SOFT_RESTART_COMMANDS`). On a **completed** profile (`intake_complete`), `/start` and
  `/restart` show the «Анкета уже заполнена…» menu (`_already_completed_menu`, shared with the
  info-command branch) — they do NOT reopen the questionnaire or touch `scenario_state`/
  `agent_card` (the saved natal/matrix stay intact). Only on a new/unfinished profile does
  `/start` (re)start intake from the first question. **`/reset` (`_HARD_RESET_COMMANDS`) is the
  ONLY full wipe** (`reset_session` + delete messages; still preserves `activated_promo`+balance).
  Regression: `tests/test_intake_ux_wrapper.py` (start/restart-on-completed → menu, new user →
  intake, reset → full wipe).
- **Callback codes** (`intake_flow`): `mdl:intake:ok|redo|save|edit|restart|cancel|all`,
  `mdl:intake:f:<field>`, `mdl:profile:edit`. They arrive as `event.text` and are routed
  inside `handle_intake_before_llm` before step validation.
- Tests (offline, geocoder mocked): `tests/test_intake_flow_core.py` (pure core) +
  `tests/test_intake_ux_wrapper.py` (full flow with in-memory repos + real chart/matrix math).

### Local toolchain note

Deps are locked in `uv.lock` (numpy 2.4.4, mypy 1.20.2). A plain `pip install -e ".[dev]"`
into a fresh venv pulls newer numpy whose typeshed uses PEP 695 `type` statements that mypy
(configured `python_version = 3.11`) rejects on numpy stubs. Prefer `uv sync`, or pin
`numpy==2.4.4 mypy==1.20.2` to match the lock before running `scripts/check.sh`.

### Destiny Matrix («Карта судьбы») — second capability in the astrology vertical

Матрица Судьбы (Natalia Ladini, «22 кода судьбы») is a **separate system from astrology**:
pure numerology of the birth **date** (no ephemeris, time, or place — 22 Major Arcana on an
octagram). It is a **capability inside the `astrology` vertical**, not a new vertical: it
reuses the existing intake (DOB already collected), reply path, and LLM-nav protocol — no
second bot/wizard. Authoritative engine: `src/mandala/astro/destiny_matrix.py`.

- **Python computes, LLM interprets** (same house rule as natal chart). `compute_destiny_matrix`
  builds the full chart; `destiny_matrix_to_system_text` injects it as DATA into the astrology
  system prompt — wired in `services/text_reply.py` (computed on the fly whenever `birth_date`
  is present, gated in a try/except). Arcana **values** come from the KB (RAG), not the module.
- **Reduction = digit-sum** (26→8; 22 kept), NOT «subtract 22». Dominant public convention
  (Ladini's own examples + most calculators). Documented in the module + regression test.
- **Accuracy:** `tests/test_destiny_matrix_regression.py` reproduces the CORE octagram of two
  external calculators exactly (07.01.1987, 29.01.1991) and snapshots the derived lines. The
  derived lines (purpose/money/love/chakra/родовые) have **no single public canon** — one
  documented standard construction is implemented; state that, don't claim a canon.
- **KB** lives in `verticals/kb/astrology/destiny_matrix/*.md` (all 22 arcana + positions +
  chakra map + lines). Same vertical as natal-chart KB — retrieval is semantic, they coexist.
  The prompt (`verticals/prompts.py`) tells the model to offer «Карту судьбы» via nav buttons.

### Numerology («Нумерология») — third capability in the astrology vertical

Классическая (пифагорейская) нумерология is another **separate system**, distinct from both
astrology and the Destiny Matrix. Its input is **ИМЯ + дата рождения** (letters of the name →
numbers), where the Matrix uses **date only**. Like the Matrix it's a **capability inside the
`astrology` vertical** (reuses intake, reply path, LLM-nav) — not a new vertical/bot.
Authoritative engine: `src/mandala/astro/numerology.py` (`compute_numerology(full_name,
birth_date)`, `numerology_to_system_text`).

- **Python computes, LLM interprets** (same house rule). Number **meanings** come from the KB
  (RAG), never hardcoded in the engine. Injected as DATA in `services/text_reply.py` (computed
  on the fly whenever `birth_date` present; name optional — engine degrades gracefully). agent_card
  key: `AGENT_CARD_NUMEROLOGY_DATA = "numerology_data"` (saved at profile finalize by
  `_try_compute_and_save_numerology` in `scenario_intake.py`, next to natal + matrix).
- **Core numbers** (one documented standard school — no single public canon; stated in module +
  test + KB): life path, expression/destiny, soul urge (vowels), personality (consonants),
  birthday, maturity. **Reduction = digit-sum; master numbers 11/22/33 are KEPT** (not reduced).
  Life path uses the **Decoz method** (reduce day, month, year separately keeping masters, then
  sum and reduce) — NOT "sum all digits at once".
- **Cyrillic table = dominant 33-letter Pythagorean grid** (letter value = `(pos-1) mod 9 + 1`,
  so `1=А И С Ъ`, … `9=З Р Щ`). **Ё is its own letter (7)**, not «е»(6). Vowels for soul urge:
  `а е ё и о у ы э ю я` (й = consonant). **Ъ/Ь carry no value (skipped)** — so expression = soul
  + personality. Latin fallback = standard `A=1…I=9, J=1…` with vowels `AEIOU` (Y = consonant).
  Case/spaces/hyphens ignored.
- **Instant render (no LLM):** `/numerology` renders saved data via `chart_render.render_numerology_message`
  (deterministic; computes+saves on the fly if missing) — handled as a command in `scenario_intake`
  (`_NUMEROLOGY_COMMANDS`), in the bot menu (`bot_commands.py`), and in `/profile`
  («🔢 Нумерология рассчитана … /numerology» + button). Deep LLM analysis via `mdl:numerology`
  button (expanded in `quick_actions.py`). Bold/emoji/lists only — never `_…_` italic.
- **KB** lives in `verticals/kb/astrology/numerology/*.md` (overview, tables, core-number roles,
  numbers 1–9, master numbers, sources). Same vertical — coexists with natal-chart + matrix KB.
- **Tests (offline):** `tests/test_numerology_regression.py` (hand-verified snapshot «Иван Иванов»
  17.03.1992: LP5/expr5/soul11/pers3/bday8/maturity1, master-number life paths, Ё≠Е, Ъ/Ь skip,
  latin fallback) + `tests/test_numerology_integration.py` (save at finalize, `/numerology`
  render, no-name degrade, profile block) + `tests/test_rag_numerology_smoke.py` (KB indexes,
  retrieval, coexists with matrix KB).

### RAG activation (Матрица Судьбы KB)

RAG is off by default (`MANDALA_RAG_BACKEND=none`). Local dev path and prod requirement are in
`.env.example` (RAG/Qdrant section): `podman compose up -d qdrant` → set `MANDALA_RAG_BACKEND=qdrant`
+ `QDRANT_URL` + `LLM_*` embeddings → `python -m mandala.index_kb --vertical astrology
--recreate-collection`. Offline proof: `tests/test_rag_destiny_matrix_smoke.py` (real KB →
in-memory Qdrant → retrieval feeds prompt, deterministic embedder, no network). Prod needs a
real Qdrant + embedding creds — escalate infra, don't self-provision.

## Утренняя рассылка: proactive daily motivator (scheduler, free, MSK)

The bot proactively sends a short **slogan-motivator** (1–2 lines, «девиз дня», emoji ok — NO
planet breakdown) every morning. **Default ON at 10:00 MSK** for everyone; the user changes the
time or turns it off via `/morning`. **Free — never charges the wallet/quota** (a gift, like the
instant renders); promo/balance untouched. **No migration** — all state lives in `agent_card`.

- **Fixed MSK for all** (`Europe/Moscow`), NOT birthplace tz. Settings keys in `agent_card`
  (`verticals/client_knowledge.py`): `daily_forecast_enabled` (**missing = True**),
  `daily_forecast_time` (`"HH:MM"`, default `"10:00"`), `daily_forecast_last_sent`
  (`"YYYY-MM-DD"` MSK — idempotency, survives restart).
- **Pure decision + content**: `services/daily_forecast.py`. `should_send_daily_forecast(agent_card,
  now)` is a pure function (inject `now` — `now_msk()` default) covering enabled / not-sent-today /
  time-reached / catch-up window (`CATCHUP_WINDOW_MINUTES=180`, so after downtime it won't fire at
  3am). `build_daily_slogan(...)` calls the vertical LLM with a tiny standalone prompt (small
  `max_tokens`), **never** `QuotaService`; degrades to a general slogan when no chart/transits, and
  returns `None` on LLM failure (then we don't send / don't mark sent). Transits reuse the natal
  school (`calculate_current_transits`), positions kept as model «mood», not shown to the user.
- **Scheduler = background asyncio task in the HTTP lifespan** (`http/app.py` →
  `adapters/telegram/daily_forecast_scheduler.py`): `start/stop_daily_forecast_scheduler`. Loop wakes
  ~60s, computes MSK `now`, offloads the sync tick to a worker thread (`anyio.to_thread.run_sync` —
  don't block the loop). `run_daily_forecast_tick` iterates every vertical with a bot token
  (`load_bot_token_map`), reads recipients (`repositories/daily_forecast.py` — join
  `client_profiles`+`channel_links`, only rows with `birth_date`), sends to `external_user_id`
  (= chat_id in a private chat) with the vertical token via `deliver_outbound_messages`, then marks
  `last_sent` in its own txn. Per-user try/except; failure doesn't mark sent (retries within window).
  Global kill-switch `MANDALA_DAILY_FORECAST_ENABLED` (default on) — off = task not started.
- **`/morning` settings (deterministic, no LLM)**: `services/daily_forecast_settings.py`, routed in
  `domain/handler.py` **before** intake (`is_daily_forecast_action`) so it works in any profile
  state. Shows current state + toggle + time presets (07:00–12:00); also `/morning on|off` and
  `/morning HH:MM`; callbacks `mdl:morning`/`:on`/`:off`/`:set:HH:MM`. In the bot menu
  (`bot_commands.py`). The delivered morning message carries «📊 Подробнее» (`mdl:fc_today`) +
  «⚙️ Настроить» (`mdl:morning`).
- **Tests (offline, deterministic)**: `tests/test_daily_forecast.py` (should-send branches, settings
  parse, `/morning` handler, content builder + degrade/fail), `tests/test_daily_forecast_scheduler.py`
  (tick wiring: right chat_id+token, idempotency, quota-not-touched, LLM-fail-no-mark, start/stop),
  `tests/test_keyboard_and_commands.py` (`mdl:morning` routes without touching the LLM).

## Deploy: PORT / EXPOSE / healthcheck contract

Two run paths share one image (`Containerfile`); the app binds `${PORT}`
(`src/mandala/http/__main__.py`).

- **VM + nginx:** `scripts/deploy/restart_app.sh` runs with `-e PORT=8000 -p 8000:8000`.
- **YC Serverless Container:** uses the image default `PORT=8080`; YC routes to the
  `EXPOSE` port, so `EXPOSE` must equal the default `PORT` (both 8080).
- **The Docker `HEALTHCHECK` must probe `${PORT}`, never a hardcoded port** — otherwise
  on the VM (PORT=8000) the probe hits 8080 and the container reports `unhealthy`.
  Verified: OLD hardcoded-8080 probe → `unhealthy` on VM; `${PORT}` probe → `healthy`
  on both paths.

## LLM: single source of truth for per-vertical model

Model for a `vertical_id` is resolved in one place — `LlmConfigProvider.resolve`
(`src/mandala/llm/config.py`) — by strict precedence, highest→lowest: **1)** `LLM_MODEL_<VERTICAL>`
env override (e.g. `LLM_MODEL_ASTROLOGY`) → **2)** `vertical_overrides.json` (bundled in the
package, or the file from `LLM_VERTICAL_OVERRIDES_PATH`) → **3)** global `LLM_MODEL`. Bundled JSON
pins `astrology`/`therapy` = `deepseek-v4-flash`; global `LLM_MODEL` deliberately does **not**
override the JSON (it is always set, so it would silently move those verticals off their default).
To change a vertical's model without editing JSON, set `LLM_MODEL_<VERTICAL>`. Effective model +
source for every vertical is logged at startup (`http/app.py` lifespan →
`llm.factory.log_effective_models`). Details: [docs/agent.md](docs/agent.md) “Выбор модели вертикали”.

## Telegram: multi-tenant token map (several bots, one process)

`vertical_id → bot_token` is resolved in one place — `adapters/telegram/bot_token.py`
(`load_bot_token_map` / `get_bot_token_for_vertical`) — by precedence, highest→lowest:
**1)** `TELEGRAM_BOT_TOKEN_<VERTICAL>` (uppercased slug) → **2)** `TELEGRAM_BOT_TOKENS` JSON
object `{"<vertical>": "<token>"}` → **3)** legacy `TELEGRAM_BOT_TOKEN` + `TELEGRAM_VERTICAL_ID`
(single vertical). Unknown vertical → `None` (caller logs `no_bot_token`, never raises).

- **Webhook** resolves the token by `{vertical_id}` from the URL path (`http/app.py`,
  `webhook_delivery.py`); nothing else changed there.
- **Polling** is multi-tenant via `polling.run_polling_multi` (one daemon thread per token,
  shared engine; single-entry map delegates to `run_polling_forever`). `python -m
  mandala.adapters.telegram` polls every configured vertical. Env documented in `.env.example`.

## Request path: sync turn runs off the event-loop (non-blocking)

The reply turn is synchronous by design — sync SQLAlchemy engine, sync repos, sync httpx
LLM client — and one `engine.begin()` transaction is held across the LLM network call
(read-timeout up to 120s). Running that directly in an async handler blocks the FastAPI
event-loop and serializes every concurrent turn (YC `concurrency 16` never materializes).
Fix: **the whole sync turn is offloaded to a worker thread via `anyio.to_thread.run_sync`**
at each async boundary; the sync stack itself is unchanged (least-risky option from the
architecture review).

- **Both entries** offload: web is `_run_inbound_sync` in `http/web_chat.py`; Telegram
  webhook is `process_telegram_webhook_update_async` (wraps the unchanged sync
  `process_telegram_webhook_update`) in `adapters/telegram/webhook_delivery.py`, awaited in
  `http/app.py`. The Stars **billing** update in `http/app.py` is offloaded the same way.
- **Correctness is preserved** because the transaction opens and commits inside the *one*
  worker thread — transactionality and quota/payment idempotency are unchanged; quota is
  still deducted after a successful reply, inside the same transaction. Each turn builds its
  own LLM client + Telegram client, so no shared mutable state crosses threads.
- **DB pool is sized for concurrency** in `db/engine.py` (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`/
  `DB_POOL_TIMEOUT`, defaults cover `concurrency 16`); otherwise turns would re-serialize
  waiting for a pooled connection. `anyio`'s default thread limiter (40) is the other bound.
- **Polling** already runs each turn in its own daemon thread — not on the event-loop — so it
  is unchanged. Regression: `tests/test_reqpath_concurrency.py` proves a slow turn for one
  user does not serialize another (both entries), with a negative control in its docstring.

## Voice messages: STT (speech → text) before the normal pipeline

Telegram `voice`/`audio` messages are transcribed to text, then flow through the **existing**
text pipeline (`handle_inbound` → `text_reply`) unchanged — the reply logic never sees audio.

- `inbound_map.py` recognizes `voice`/`audio` → `InboundAttachment(kind=..., file_id=..,
  mime_type in extra)`; the mapper stays pure (no I/O).
- Download + STT orchestration: `adapters/telegram/voice_transcribe.py` (`resolve_voice_to_text`)
  — called in `polling.py` and `webhook_delivery.py` **after** mapping, **before**
  `handle_inbound`. On success it returns an event with `text` filled and
  `voice_transcribed=True` (`domain/contracts.py`); on ANY failure it returns a friendly
  `soft_message` (never raises) — the caller delivers that and skips the turn.
- Audio bytes: `bot_api.py` `get_file` (getFile) + `download_file` (GET
  `/file/bot<token>/<file_path>`, retries like `call`).
- STT provider is OpenAI-compatible `/audio/transcriptions` (Whisper-shaped):
  `services/transcription.py`. Env `STT_*` with fallback to `LLM_*` for URL+key; **Russian is
  the default** (`STT_LANGUAGE=ru`, empty = auto). Not configured (no URL+key) → voice degrades
  softly. Env documented in `.env.example`.

## Monetization: prepaid message wallet + Telegram Stars packs (NO subscription)

Model is a **prepaid message wallet**, not monthly limits/subscription. Each user has a plain
integer **balance** of messages; **every LLM text reply deducts 1**; instant deterministic
renders (`/natal`, `/matrix`, `/numerology`, `/profile`, `/help`) never charge (spend point
unchanged: `text_reply` only). Balance is **not time-based, never resets by time**, and
**survives `/reset`**. New user gets a one-time starting grant (`MANDALA_MESSAGE_WALLET_START`,
default 20). At balance 0 the bot shows **three pack buttons**; buying a pack **adds** messages
(never expires).

- **Balance lives on `users.message_balance`** (column, migration `t20_01_message_wallet`), keyed
  by `(user, vertical)` — one `users` row per vertical, so multi-bot balances stay separate.
  Repo: `repositories/wallet.py` (`WalletRepository`). Atomic spend:
  `UPDATE … SET message_balance = message_balance - 1 WHERE … AND message_balance >= 1 RETURNING …`
  (no negative, no races — the dual condition is in the `UPDATE`). New-user grant is in
  `user_identity.get_or_create_user` (column server_default is 0). `/reset` never touches `users`.
- **Quota = wallet** (`services/quota.py`): `can_consume`/`consume` are resource-aware — `text_reply`
  reads/decrements the wallet; **promo bypasses everything (unlimited)**; **images are NOT charged
  from the wallet** (captain's decision) — `image_generation` is allowed only under promo, else
  denied, wallet untouched (image path stays neutral/working, never tariffed). The consume point
  is still after a successful reply, in the same transaction.
- **Promo = "forever pack" (unlimited)**, unchanged in spirit (`services/promo.py`,
  `is_promo_active`). It lives in `client_profiles.agent_card[activated_promo]` and now
  **survives `/reset`**: `ProfileRepository.reset_session` deliberately preserves that one key
  (rebuilds `agent_card` keeping only `activated_promo`), wiping everything else. Regression:
  `tests/integration/test_wallet_and_reset.py` (reset keeps balance+promo, clears intake/history).
- **Three packs — single source of truth** `services/message_packs.py` (`payload ↔ ⭐-price ↔
  +messages`): `100`/`300`/`1000` → `mandala_pack_{id}`, defaults `1⭐→100 / 2⭐→300 / 5⭐→1000`.
  Prices/grants are env-parametrizable (`MANDALA_PACK_{100,300,1000}_{PRICE,MESSAGES}`); the
  numeric `pack_id`/payload suffix is a **stable** key (do not change on price/grant edits — it's
  the product key in the purchase journal). `pack_by_id`/`pack_by_payload`/`all_packs`.
- **Invoices & picker** (`services/telegram_stars.py`): `build_pack_invoice_message(pack_id)` →
  `OutboundMessage.invoice` (`StarsInvoice`, currency `XTR`, empty `provider_token`, terminal
  message); `build_packs_picker_message(text=…)` → one message with the 3 pack buttons
  (`mdl:pack:<id>`). Delivery still via `outbound_send.deliver_outbound_messages` →
  `bot_api.send_invoice`. Callback helpers/parse in `verticals/quick_actions.py`
  (`PACKS_MENU_CALLBACK=mdl:packs`, `pack_callback`, `parse_pack_callback`, `is_packs_menu` —
  also treats legacy `mdl:premium` as "open picker").
- **Idempotent credit — money, mandatory** (`services/billing.py`
  `PostgresBillingProvider.credit_pack`): `pre_checkout_query` → `successful_payment` resolves the
  pack by payload, then inserts `payment_transactions` (idempotent on `UNIQUE(provider,
  external_id)` = Telegram `telegram_payment_charge_id`) **and** `WalletRepository.add_balance` in
  the same DB transaction. A replayed payment → `duplicate_external_id`, balance unchanged, no
  double credit. `handle_successful_payment` returns a `SuccessfulPaymentOutcome` (credited /
  new balance / duplicate) so `billing_updates.py` shows a balance-aware confirmation.
- **Where the picker is shown** (all replace the old premium invoice): `/topup`
  (`scenario_intake.py`), the `💬 Купить сообщения` button in `verticals/post_intake_offers.py`
  (`mdl:packs`), and the balance-exhausted branch in `services/text_reply.py`. The image branch
  (`image_reply.py`) shows a neutral "images unavailable" message with **no** purchase CTA
  (packs are text credits, don't unlock images). Pack callbacks route in `domain/handler.py`
  (`_route_message_packs`, before quick-action expansion). Profile (`services/profile_view.py`)
  shows «Осталось сообщений: N» (∞ under promo) — balance passed in from the conn-aware callers.
- **Old monthly machinery is retired, not dropped**: `plans`/`plan_limits`/`usage_counters` tables
  and `billing_period` stay (migration chain/downgrade intact) but are unused by the runtime;
  `users.current_plan_id` still points at `free` (NOT NULL FK) — we just no longer read
  `plan_limits`. Migration `t20_01` backfills existing users to the starting grant (pre-existing
  `premium` subscribers → 1000, so no paid user is downgraded); promo unlimited is preserved.
- **Tests**: offline `tests/test_message_packs.py`, `tests/test_telegram_stars_invoice.py`,
  `tests/test_billing_provider.py` (credit_pack idempotency, mocks), `tests/test_quota_promo_bypass.py`
  (wallet consume + promo + image-not-charged); DB-gated `tests/integration/test_quota_service.py`
  (start grant, decrement, **parallel atomicity**), `test_telegram_stars_billing.py` +
  `test_billing_credit_pack.py` (**idempotent credit per pack**), `test_wallet_and_reset.py`.

## Observability: metrics dashboard + logs (YC Monitoring)

Full guide: `docs/monitoring.md`. Dashboard-as-code: `terraform/monitoring.tf`
(`yandex_monitoring_dashboard`, widgets for Telegram / LLM / app + a logs link).

- **Metrics are emitted by the app** into YC Monitoring `service=custom` — authoritative
  code `src/mandala/metrics.py`. Metric names there are the single source of truth; the
  dashboard queries must match them. Off by default; enable with `MANDALA_METRICS_ENABLED=1`
  + `YC_MONITORING_FOLDER_ID` in `/opt/mandala/env`, then recreate the container. Disabled =
  full no-op (no thread, no traffic); IAM token comes from the VM service account (metadata).
- **Instrument only at subsystem boundaries, by wrapping** (never rewrite logic): LLM in
  `llm/openai_compatible.py` (`complete`), Telegram delivery in `adapters/telegram/bot_api.py`
  (`call`), app/webhook via the HTTP middleware in `http/app.py`. Recorders are no-ops unless a
  registry is installed, so deep library code stays safe in tests/workers.
- **Logs** stay stdout `funnel …` lines (`src/mandala/observability.py`); dashboard links to
  the YC Logging group. Tests: `tests/test_metrics.py` (offline, no thread/cloud).
- **Log delivery to YC Logging** (VM-docker stdout → log-group): guide `docs/logging.md`.
  Log-group is `terraform/logging.tf` (`yandex_logging_group`, additive). Delivery is a YC
  **Unified Agent** on the VM (`scripts/deploy/unified-agent/`): a systemd shipper tails
  `docker logs -f --tail 0 mandala-http` into `/var/log/mandala/app.log`, the agent
  (`file_input`→`yc_logs`, IAM via VM metadata SA needing `logging.writer`) pushes it to the
  group. **The deploy path (`restart_app.sh`/`deploy.sh`) is intentionally untouched** — the
  shipper reconnects across container recreation by container name; `docker logs` still works.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
