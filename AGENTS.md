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

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
