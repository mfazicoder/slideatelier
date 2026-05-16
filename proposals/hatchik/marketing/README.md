# Hatchik marketing agent

Autonomous five-layer marketing system. Markets Hatchik first; the same
code productizes as the Autonomous Growth tier inside Hatchik substrate.

```
Layer 1  Persona & Strategy    Opus 4.7  monthly + on-demand
Layer 2  Content Generation    Sonnet 4.6   daily
Layer 3  Distribution          (X, Resend, blog, draft-only Reddit/Discord)
Layer 4  Listening & Adjust    Opus 4.7  weekly
Layer 5  Self-Improvement      A/B + auto-promote winners, monthly
```

## Status

| Phase | Layer | Done? |
|---|---|---|
| 0 | foundation: schema, tenants, budget gate, runs, prompts, hello agent | ✅ |
| 1 | Layer 1 — persona/strategy agent (Opus 4.7) + `marketing_strategies` versioning | ✅ |
| 2a | Layer 2 — content agent (Sonnet 4.6) + CLI approval queue | ✅ |
| 2b | Mobile-friendly Next.js admin dashboard over the queue | — |
| 3 | Layer 3 — distribution (X API, Resend, PostHog, blog) + scheduler | — |
| 4 | Layer 4 — weekly analysis loop, strategy auto-update | — |
| 5 | Layer 5 — A/B experiments + multi-tenant onboarding | — |

## Stack

| | |
|---|---|
| Language | Python 3.11+ |
| DB       | SQLite — co-tenanted with `signup-service` at `/var/lib/hatchik/signups.db` |
| LLM      | Anthropic SDK direct (Opus 4.7 for reasoning, Sonnet 4.6 for content) |
| Prompts  | Versioned `prompts/<name>/v<N>.md` files (source of truth, mirrored to `marketing_prompt_versions`) |

Tenant identity FKs to substrate's `signups(id)`; NULL for Hatchik's
own tenant. SQLite has no RLS, so tenant scoping is enforced in code
— every query helper takes `tenant_id` explicitly.

## Quick start (local dev)

```bash
cd proposals/hatchik/marketing
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit .env and set HATCHIK_ANTHROPIC_MASTER_KEY (see "Where the API key lives" below)

python -m marketing.cli init                          # schema + seeds 'hatchik' tenant w/ competitors
python -m marketing.cli run hello                     # smoke: one-sentence Hatchik positioning
python -m marketing.cli run persona                   # Layer 1: full strategy (ICP, voice, 5 pillars × 8-10 angles)
python -m marketing.cli strategy show                 # human-readable summary of current strategy
python -m marketing.cli strategy show --json          # full strategy as JSON
python -m marketing.cli run content                   # Layer 2: daily content batch (default 3 tweets + 1 thread + 1 LinkedIn)
python -m marketing.cli run content --blog=1          # also drop a blog outline this run
python -m marketing.cli queue list                    # all queued drafts, newest first
python -m marketing.cli queue list --status=pending   # filter
python -m marketing.cli queue show 7                  # full body + metadata for item #7
python -m marketing.cli queue approve 7               # mark approved (pending → approved)
python -m marketing.cli queue reject 8 --reason="too generic"
python -m marketing.cli queue stats                   # counts by status
python -m marketing.cli runs --limit=10               # last N agent runs (any layer)
python -m marketing.cli spend                         # rolling 24h spend
```

The persona agent reads `proposals/hatchik/PRODUCT_OFFERING.md` and
`MARKETING_PLAN.md` as the product brief (paths configurable via the
tenant's `settings_json.product_docs`). Competitors live in
`settings_json.competitors`. Both are seeded by `init`.

Prompt caching is on by default — the system prompt + product/competitor
blob get `cache_control: ephemeral` markers, so re-running `persona`
within ~5 minutes pays only the variable-block + output cost
(roughly 1/10th of a cold call).

## Where the API key lives

On the production VPS, the Anthropic key is in `/etc/hatchik/signup.env`
under the canonical Hatchik name **`HATCHIK_ANTHROPIC_MASTER_KEY`** —
the same env var that `signup-service/ai_proxy.py` consumes for the AI
passthrough proxy. The marketing config reads that var first, then
falls back to the SDK-standard `ANTHROPIC_API_KEY` if unset.

The runbook (`proposals/hatchik/ALPHA_TEST_RUNBOOK.md`) documents env
var **names**, not values; the actual secret lives only in the systemd
EnvironmentFile on the host.

For local dev: paste the value into `proposals/hatchik/marketing/.env`
(gitignored) or export `HATCHIK_ANTHROPIC_MASTER_KEY` in your shell.

## Where the DB lives

`HATCHIK_SIGNUP_DB` env var. Defaults:

- **prod:** `/var/lib/hatchik/signups.db`  (set in signup-service's systemd unit)
- **local dev:** `./marketing.dev.db`  (per `.env.example`)

The marketing system creates only `marketing_*` tables. It never touches
signup-service's tables. The `signup_id` FK on `marketing_tenants` is
nullable so Hatchik's own tenant row works without a corresponding
`signups` row.

## Layout

```
marketing/
├── pyproject.toml
├── requirements.txt
├── .env.example
├── marketing/                  # the Python package
│   ├── config.py               # env loading, model IDs, pricing table
│   ├── db.py                   # sqlite3 connection (WAL, FK on, busy_timeout)
│   ├── schema.py               # PRAGMA-gated CREATE TABLE IF NOT EXISTS
│   ├── tenant.py               # slug → Tenant lookup
│   ├── budget.py               # rolling-24h spend cap circuit breaker
│   ├── prompts.py              # load v<N>.md + mirror to DB
│   ├── runs.py                 # marketing_agent_runs CRUD
│   ├── anthropic_client.py     # SDK wrapper: budget gate → call → log
│   ├── seed.py                 # idempotent Hatchik tenant insert (+ competitors)
│   ├── strategy.py             # Pydantic Strategy schema + marketing_strategies CRUD
│   ├── content.py              # ContentDraft schemas + marketing_content_queue CRUD + state machine
│   ├── cli.py                  # `python -m marketing.cli …`
│   └── agents/
│       ├── hello.py            # Phase-0 smoke agent
│       ├── persona.py          # Phase-1 Layer-1 strategy agent (Opus 4.7, cached)
│       └── content.py          # Phase-2 Layer-2 content agent (Sonnet 4.6, cached batch)
├── prompts/
│   ├── hello/v1.md
│   ├── persona/v1.md
│   └── content/v1.md
└── tests/
    ├── test_phase0.py          #  6 tests
    ├── test_phase1.py          #  8 tests
    └── test_phase2.py          # 12 tests (content schemas, angle picker, queue state machine, mocked agent)
```

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

The tests don't require an Anthropic key or a network call — they
exercise schema, seed, budget gate, prompt loader, and the
`MissingAPIKey` path of the hello agent.

## What ships in later phases

- **Phase 2b** — mobile-friendly Next.js admin dashboard sitting over the same queue (swipe approve/reject); for now the CLI does the same job
- **Phase 3** — `integrations/x.py`, `integrations/resend.py`, `integrations/posthog.py`; scheduler/worker draining `marketing_jobs`; approved items auto-flow to distribution
- **Phase 4** — `agents/analyze.py` (Layer 4); auto-updates the strategy from outcomes (signups attributed via PostHog, engagement from X API, etc.)
- **Phase 5** — multi-tenant onboarding flow; encrypted per-tenant keys in `marketing_tenant_api_keys`; optional Notion mirror of strategy doc

## Non-negotiables

- **Prompts are files**, not strings in code. Edit a prompt → bump the
  file's version → loader mirrors it to DB on next run.
- **Every LLM call logs to `marketing_agent_runs`** (input, output,
  model, tokens, cost, status).
- **No auto-posting to Reddit/Discord** — drafts only, founder pastes.
- **Tenant isolation enforced in code** — every helper takes `tenant_id`.
- **$5/tenant/day spend cap** — checked before every call.
