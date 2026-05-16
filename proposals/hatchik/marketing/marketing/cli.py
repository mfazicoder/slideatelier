"""CLI entry point.

    python -m marketing.cli init                # ensure schema + seed tenant
    python -m marketing.cli run hello --tenant=hatchik
    python -m marketing.cli runs --tenant=hatchik [--limit=10]
    python -m marketing.cli spend --tenant=hatchik

`init` is idempotent and safe to run any time. `run` invokes a named
agent and prints its output + cost.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import budget, config, db, schema, seed as seed_mod, tenant


AGENTS = {
    "hello": "marketing.agents.hello",
}


def cmd_init(_: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        tid = seed_mod.seed_hatchik_tenant(conn)
        print(f"schema ensured. hatchik tenant id={tid}. db={config.DB_PATH}")
    finally:
        conn.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.agent not in AGENTS:
        print(f"unknown agent {args.agent!r}. known: {sorted(AGENTS)}", file=sys.stderr)
        return 2
    module = __import__(AGENTS[args.agent], fromlist=["run"])
    result = module.run(tenant_slug=args.tenant)
    print(result.get("text", "").strip())
    print(
        f"\n[run #{result['run_id']}  tokens={result['tokens_in']}→{result['tokens_out']}  "
        f"cost=${result['cost_usd']:.6f}]",
        file=sys.stderr,
    )
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        rows = conn.execute(
            """
            SELECT id, layer, model, status, tokens_in, tokens_out, cost_usd,
                   started_at, completed_at, error
            FROM marketing_agent_runs
            WHERE tenant_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (t.id, args.limit),
        ).fetchall()
        for r in rows:
            print(json.dumps(dict(r), ensure_ascii=False))
    finally:
        conn.close()
    return 0


def cmd_spend(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        spent = budget.spend_last_24h(conn, t.id)
        print(f"tenant={t.slug} spent_24h=${spent:.4f} cap=${t.spend_cap_daily_usd:.2f}")
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="marketing")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="ensure schema + seed Hatchik tenant").set_defaults(func=cmd_init)

    run_p = sub.add_parser("run", help="invoke a named agent")
    run_p.add_argument("agent", choices=sorted(AGENTS))
    run_p.add_argument("--tenant", default="hatchik")
    run_p.set_defaults(func=cmd_run)

    runs_p = sub.add_parser("runs", help="recent agent runs")
    runs_p.add_argument("--tenant", default="hatchik")
    runs_p.add_argument("--limit", type=int, default=10)
    runs_p.set_defaults(func=cmd_runs)

    spend_p = sub.add_parser("spend", help="rolling 24h spend for a tenant")
    spend_p.add_argument("--tenant", default="hatchik")
    spend_p.set_defaults(func=cmd_spend)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
