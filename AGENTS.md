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
- **Birth time is asked as LOCAL time.** `intake_steps.json` `birth_time` prompt explicitly asks
  for local birthplace time; `intake_flow._echo_line` confirms «приму как МЕСТНОЕ время».
- **Chart-wheel image is deferred (needs-decision).** A kerykeion `KerykeionChartSVG` wheel is
  pure math and generates offline, but every SVG→PNG path (cairosvg; svglib→reportlab5 whose PNG
  backend is `rlPyCairo`) needs **system libcairo** in the prod image, and `OutboundMessage.photo`
  is a URL/`file_id` `str` with no bytes-upload plumbing. Shipped text-only; do not add libcairo
  to the image without a deploy decision.
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
  `/profile` handled in `scenario_intake.py`). The channel-agnostic
  `OutboundMessage.term_links` field carries `{term, payload}`.
- **Inline-only navigation (no persistent reply keyboard).** Every bot answer ends with an
  inline keyboard — the LLM picks it via the nav block above; when the model emits no valid
  nav (bad JSON, non-astrology vertical), `services/nav_guarantee.py` (`ensure_nav`) attaches
  a contextual fallback to the terminal (last non-invoice) message so a reply is **never**
  left without navigation. Every domain return path that isn't a bare intake-wizard prompt
  runs through `ensure_nav` (`domain/handler.py`, `services/scenario_intake.py`). The old
  `ASTROLOGY_REPLY_KEYBOARD` is gone; `outbound_send.deliver_outbound_messages` attaches a
  one-time `ReplyKeyboardRemove` to the first button-less message of a batch to clear the
  lingering sticky keyboard for existing users (stateless, no extra bubble). Lingering
  keyboard taps still resolve via `_KEYBOARD_TEXT_TO_CODE` in `quick_actions.py`.

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

### RAG activation (Матрица Судьбы KB)

RAG is off by default (`MANDALA_RAG_BACKEND=none`). Local dev path and prod requirement are in
`.env.example` (RAG/Qdrant section): `podman compose up -d qdrant` → set `MANDALA_RAG_BACKEND=qdrant`
+ `QDRANT_URL` + `LLM_*` embeddings → `python -m mandala.index_kb --vertical astrology
--recreate-collection`. Offline proof: `tests/test_rag_destiny_matrix_smoke.py` (real KB →
in-memory Qdrant → retrieval feeds prompt, deterministic embedder, no network). Prod needs a
real Qdrant + embedding creds — escalate infra, don't self-provision.

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

## Telegram Stars: purchasing premium (invoice → activation)

Full round-trip: invoice → `pre_checkout_query` → `successful_payment` → `activate_plan`.
The receive half (pre_checkout/successful_payment, idempotent activation) is
`services/telegram_stars.py` + `adapters/telegram/billing_updates.py`; the **send** half
(invoice creation) is the piece added here.

- **Single source of truth for the invoice** is `telegram_stars.build_premium_invoice_message()`:
  it fills `OutboundMessage.invoice` (`StarsInvoice` in `domain/contracts.py`) with
  `payload=STARS_INVOICE_PAYLOAD_PREMIUM` (= `plans.external_product_id`, migration
  `t19_01`) and `amount_stars=premium_price_stars()`. Reuse it everywhere so payload↔plan and
  price never diverge. Price is a product param: env `MANDALA_STARS_PREMIUM_PRICE` (default 250).
- **Delivery**: `outbound_send.deliver_outbound_messages` sees `msg.invoice` and calls
  `bot_api.send_invoice` (currency `XTR`, empty `provider_token` = Stars). An invoice message
  is **terminal** — its text/photo/buttons are ignored (the invoice carries title/desc/price).
  Channels without Stars ignore the field (safe degrade).
- **Initiation points** (all just show the invoice, no reply-logic changes): `/topup`
  (`scenario_intake.py`), the `⭐ Premium` upsell button in `verticals/post_intake_offers.py`
  (callback `mdl:premium` → quick-action code `__premium_topup__` → routed in `domain/handler.py`
  next to `is_show_profile`), and the quota-exceeded branches in
  `services/text_reply.py` / `image_reply.py` (append the invoice after the limit message).

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
