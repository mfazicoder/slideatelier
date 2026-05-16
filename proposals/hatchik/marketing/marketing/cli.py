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

from . import budget, config, db, schema, seed as seed_mod, strategy, tenant


AGENTS = {
    "hello": "marketing.agents.hello",
    "persona": "marketing.agents.persona",
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

    # Agents either return one-shot text (hello) or a structured summary
    # (persona) — print whichever shape we got.
    if "text" in result:
        print(result["text"].strip())
    elif args.agent == "persona":
        print(
            f"strategy v{result['version']} saved (id={result['strategy_id']}): "
            f"{result['pillars']} pillars, {result['sub_personas']} sub-personas, "
            f"{result['total_angles']} angles. Use `strategy show` to inspect."
        )
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    cache_blurb = ""
    if result.get("cache_read") or result.get("cache_creation"):
        cache_blurb = (
            f"  cache(r/w)={result.get('cache_read', 0)}/"
            f"{result.get('cache_creation', 0)}"
        )
    print(
        f"\n[run #{result['run_id']}  tokens={result['tokens_in']}→{result['tokens_out']}"
        f"{cache_blurb}  cost=${result['cost_usd']:.6f}]",
        file=sys.stderr,
    )
    return 0


def cmd_strategy_show(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        result = strategy.current(conn, t.id)
        if result is None:
            print(f"no current strategy for tenant {t.slug!r}. run `run persona` first.")
            return 1
        version, strat = result
        if args.json:
            print(strat.model_dump_json(indent=2))
            return 0
        print(f"=== strategy v{version} for tenant {t.slug} ===\n")
        print(f"ICP: {strat.icp.primary}")
        print(f"  type={strat.icp.company_type}  stage={strat.icp.stage}  geo={strat.icp.geo}")
        print("  pain_points:")
        for p in strat.icp.pain_points:
            print(f"    - {p}")
        print("  excludes:")
        for x in strat.icp.excludes:
            print(f"    - {x}")
        print(f"\nVoice: {', '.join(strat.voice.tone_attributes)}")
        print(f"  do:    " + "; ".join(strat.voice.do[:3]) + (" …" if len(strat.voice.do) > 3 else ""))
        print(f"  dont:  " + "; ".join(strat.voice.dont[:3]) + (" …" if len(strat.voice.dont) > 3 else ""))
        print(f"\nSub-personas ({len(strat.sub_personas)}):")
        for sp in strat.sub_personas:
            print(f"  • {sp.name} — {sp.role}")
            print(f"      objection: {sp.objection}")
            print(f"      hook:      {sp.hook}")
        print(f"\nPillars ({len(strat.pillars)}):")
        for p in strat.pillars:
            print(f"  ▸ {p.name} ({len(p.angles)} angles) — {p.description}")
        return 0
    finally:
        conn.close()


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

    strat_p = sub.add_parser("strategy", help="inspect the current strategy")
    strat_sub = strat_p.add_subparsers(dest="strat_cmd", required=True)
    strat_show = strat_sub.add_parser("show", help="print the current strategy")
    strat_show.add_argument("--tenant", default="hatchik")
    strat_show.add_argument("--json", action="store_true", help="emit full JSON instead of a summary")
    strat_show.set_defaults(func=cmd_strategy_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
