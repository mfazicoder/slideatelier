# Cohort-funnel metrics dashboard — agent report

Status: feature-complete, smoke-tested, ready for merge.

Goal: replace the assumed numbers in `MARKETING_PLAN.md` §7 with
ground-truth data by signup #50.

## Files added

- `proposals/hatchik/signup-service/cohort_metrics.py` — pure-function
  module holding all cohort SQL + funnel math. Takes a
  `sqlite3.Connection` so tests can pass an in-memory DB.
- `proposals/hatchik/signup-service/test_cohort_metrics.py` — pytest
  smoke suite (6 tests, all green).
- `proposals/hatchik/admin-dashboard.html` — static dashboard, slate-
  dark theme, Chart.js from jsDelivr CDN. Served at
  `https://hatchik.com/admin/dashboard` via a new Caddy rewrite.

## Files modified (additive)

- `proposals/hatchik/signup-service/main.py`
  - `init_db()`: added `tier_transitions` table + index.
  - `create_signup()`: writes initial transition row after signup
    INSERT.
  - New helper `_record_cancellation_transition()` near the existing
    deletion code; called from `admin_delete_account` and
    `confirm_deletion`.
  - New section at the bottom: cohort_metrics import +
    `/api/admin/metrics/{cohorts,funnel,distribution}` endpoints. All
    three reuse `_require_admin`.
  - TODO comment inside `paddle_webhook` at the `subscription.created`
    branch (does **not** modify behaviour — see below).
- `proposals/hatchik/sandbox-orchestrator/host-caddy/Caddyfile`
  - Adds a `/admin/dashboard` handler that rewrites to
    `/admin-dashboard.html`. No other routes touched.
- `proposals/hatchik/FIRST_CUSTOMER_RUNBOOK.md`
  - New "Metrics dashboard" subsection inside the Account-harness
    block, plus three new admin API entries.

No new Python dependencies (still just `fastapi`, `httpx`, `pydantic`,
`email-validator`, `uvicorn`).

## Schema

```sql
CREATE TABLE tier_transitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signup_id       INTEGER NOT NULL,
    from_tier       TEXT,                  -- NULL for the initial signup row
    to_tier         TEXT NOT NULL CHECK(to_tier IN
                       ('sandbox','launch','growth','cancelled','archived')),
    occurred_at     TEXT NOT NULL,
    paddle_event_id TEXT,
    notes           TEXT
);
CREATE INDEX idx_tier_transitions_signup ON tier_transitions(signup_id);
```

Initial transitions are seeded automatically on every new signup; we
inherit the full history of any data already in the table (no
backfill needed for fresh installs — and the prod DB is still empty
pre-launch, so this is a clean slate).

## API endpoints

All admin-token gated via the existing `X-Admin-Token` header:

| Endpoint | Notes |
|---|---|
| `GET /api/admin/metrics/cohorts?granularity=week\|month&since=YYYY-MM-DD` | Default week. Returns `{granularity, since, cohorts:[…]}`. |
| `GET /api/admin/metrics/funnel?since=YYYY-MM-DD` | All-time rates. |
| `GET /api/admin/metrics/distribution?since=YYYY-MM-DD` | Live-tenant tier distribution, cross-referenced with `registry.json`. |

Empty database → returns empty/zero shapes without raising (tested).

## Integration callsites flagged for the merger

Two callsites need wiring once the other agent worktrees merge:

### 1. Lifecycle archiver (Agent B's `lifecycle.py`)

When the reconciler flips a tenant to `archived` (idle-archive
policy), it should append:

```python
conn.execute(
    "INSERT INTO tier_transitions (signup_id, from_tier, to_tier, "
    "occurred_at, paddle_event_id, notes) VALUES (?, ?, 'archived', ?, NULL, "
    "'idle-archive')",
    (signup_id, current_tier, datetime.now(timezone.utc).isoformat()),
)
```

I did **not** touch `lifecycle.py` — it lives in another worktree. The
metrics module already treats `archived` as a churn-equivalent state,
so once this callsite is wired the dashboard will pick up archives in
the next refresh.

### 2. Paddle subscription webhook (parked tech debt)

`signup-service/main.py` `paddle_webhook` has a TODO comment at the
`subscription.created` branch. When the Paddle integration ships:

1. Resolve `customer_email` → `signup_id` via the existing
   `payments`/`signups` join.
2. Decide upgrade target (`launch` vs `growth`) by inspecting the
   subscription's price/item ids.
3. Insert `(signup_id, 'sandbox', 'launch', now, event_id, 'paddle
   webhook')` (or `('launch', 'growth', …)` for the growth tier).

I deliberately did **not** modify the webhook handler — see the
codebase note saying Paddle is parked tech debt to replace post-
approval. The TODO is the only addition.

## Open questions

- **Backfilling current tenants.** If anyone signed up before this
  change shipped, they'll have no initial transition row. The dashboard
  treats their initial tier from the `signups` table as the current
  tier (via `_current_tier` fallback), so the funnel still works — but
  the cohort table's "currently live" count is correct only if the
  `signups.tier` column still reflects reality. For a fresh prod DB
  this is moot; for an existing DB someone should run a one-time:
  ```python
  for s in conn.execute("SELECT id, tier, created_at FROM signups"):
      conn.execute(
          "INSERT INTO tier_transitions (signup_id, from_tier, to_tier, "
          "occurred_at, notes) VALUES (?, NULL, ?, ?, 'backfill')",
          (s["id"], s["tier"], s["created_at"]),
      )
  ```
- **Annual-churn definition.** The dashboard reports 30d / 90d churn
  per cohort, and overall churn. We deferred a proper trailing-365d
  annualised churn rate until we have ≥3 months of data (no point
  computing it on noise).
- **Geo-IP enrichment.** Auto-refresh is disabled on purpose — the
  dashboard makes three parallel API calls per refresh, and the
  signup-service is the only thing that touches the geo-IP quota
  upstream. If we add geo enrichment to the cohort view later, revisit
  the auto-refresh setting.

## Verification

```bash
cd proposals/hatchik/signup-service
python3 -m venv /tmp/v && /tmp/v/bin/pip install -r requirements.txt pytest
/tmp/v/bin/pytest test_cohort_metrics.py -v
```

All 6 tests green locally (Python 3.14.3, pytest 9.0.3):
- empty DB → empty cohort list / zero funnel / zero distribution
- admin-token required (403 without header)
- one seeded signup → cohort row with `total_signups=1`
- month granularity buckets correctly across a month boundary
- a `to='launch'` transition flips `sandbox_to_launch_pct` to 100%
- `?since=YYYY-MM-DD` filter trims cohorts

## Merge guidance

The signup-service `main.py` is shared with five other in-flight
worktrees. To minimise conflicts I:

- added the table definition + index inside `init_db()` immediately
  before the existing `conn.commit()` (one contiguous block)
- added one INSERT statement after the existing signup INSERT (no
  rewrites of nearby code)
- added a single helper + one-line invocation each in the two existing
  deletion handlers
- appended a TODO comment (no logic change) inside `paddle_webhook`
- put **everything else** at the bottom of `main.py` after the
  `healthz` endpoint

If a conflict appears, the resolution should almost always be "keep
both" — there's no overlap of behaviour with what the other agents
are adding.
