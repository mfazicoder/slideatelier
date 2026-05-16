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

## Phase 0 status

Smoke pipeline only — schema, tenant seed, budget gate, prompt loader,
hello-world agent + CLI. No scheduler, no distribution, no dashboard
yet (those are Phases 2–5).

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
pip install -r requirements.txt

cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY (see "Where the API key lives" below)

python -m marketing.cli init                  # creates schema + seeds 'hatchik' tenant
python -m marketing.cli run hello             # one-sentence Hatchik positioning, logged to DB
python -m marketing.cli runs --limit=5        # last 5 agent runs
python -m marketing.cli spend                 # rolling 24h spend
```

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
│   ├── seed.py                 # idempotent Hatchik tenant insert
│   ├── cli.py                  # `python -m marketing.cli …`
│   └── agents/
│       └── hello.py            # Phase-0 smoke agent
├── prompts/
│   └── hello/v1.md
└── tests/
    └── test_phase0.py          # no-network unit tests
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

- **Phase 1** — `agents/persona.py` (Layer 1) + Notion mirror of strategy doc
- **Phase 2** — `agents/content.py` (Layer 2) + Next.js approval dashboard
- **Phase 3** — `integrations/x.py`, `integrations/resend.py`, `integrations/posthog.py`; scheduler/worker on `marketing_jobs`
- **Phase 4** — `agents/analyze.py` (Layer 4); auto-updates strategy from outcomes
- **Phase 5** — multi-tenant onboarding flow; encrypted per-tenant keys in `marketing_tenant_api_keys`

## Non-negotiables

- **Prompts are files**, not strings in code. Edit a prompt → bump the
  file's version → loader mirrors it to DB on next run.
- **Every LLM call logs to `marketing_agent_runs`** (input, output,
  model, tokens, cost, status).
- **No auto-posting to Reddit/Discord** — drafts only, founder pastes.
- **Tenant isolation enforced in code** — every helper takes `tenant_id`.
- **$5/tenant/day spend cap** — checked before every call.
