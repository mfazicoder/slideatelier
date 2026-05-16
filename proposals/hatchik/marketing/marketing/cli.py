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

from . import (
    analysis,
    analytics,
    budget,
    config,
    content,
    db,
    distribute as distribute_mod,
    jobs as jobs_mod,
    schema,
    seed as seed_mod,
    strategy,
    tenant,
    worker as worker_mod,
)


AGENTS = {
    "hello": "marketing.agents.hello",
    "persona": "marketing.agents.persona",
    "content": "marketing.agents.content",
    "analyze": "marketing.agents.analyze",
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

    if args.agent == "content":
        from .agents.content import BatchPlan
        plan = BatchPlan(
            x_tweet=args.tweets,
            x_thread=args.threads,
            linkedin=args.linkedin,
            blog=args.blog,
            email=args.email,
        )
        result = module.run(tenant_slug=args.tenant, plan=plan, seed=args.seed)
    else:
        result = module.run(tenant_slug=args.tenant)

    # Agents either return one-shot text (hello) or a structured summary
    # (persona, content) — print whichever shape we got.
    if "text" in result:
        print(result["text"].strip())
    elif args.agent == "persona":
        print(
            f"strategy v{result['version']} saved (id={result['strategy_id']}): "
            f"{result['pillars']} pillars, {result['sub_personas']} sub-personas, "
            f"{result['total_angles']} angles. Use `strategy show` to inspect."
        )
    elif args.agent == "analyze":
        prior = result["prior_strategy_version"]
        new_v = result["new_strategy_version"]
        if new_v is not None:
            print(
                f"analysis run #{result['run_id']}: strategy bumped "
                f"v{prior} → v{new_v}. "
                f"{result['posts_analyzed']} posts, {result['winners']} winners, "
                f"{result['losers']} losers, {result['hypotheses']} hypotheses."
            )
        else:
            print(
                f"analysis run #{result['run_id']} (no auto-promote): "
                f"{result['posts_analyzed']} posts analyzed. "
                f"Use `analysis show` to inspect."
            )
    elif args.agent == "content":
        print(
            f"queued {result['items_queued']}/{result['items_planned']} drafts "
            f"(strategy v{result['strategy_version']}):"
        )
        for it in result["items"]:
            print(f"  #{it['item_id']:>4}  {it['channel']:<10}  {it['pillar']}")
            print(f"          ↳ {it['angle']}")
        if result.get("errors"):
            print(f"\n{len(result['errors'])} error(s):", file=sys.stderr)
            for e in result["errors"]:
                print(f"  - {e}", file=sys.stderr)
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


def cmd_queue_list(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        rows = content.list_queue(
            conn, tenant_id=t.id, status=args.status, limit=args.limit
        )
        if not rows:
            print(f"queue empty (tenant={t.slug}, status={args.status or 'any'})")
            return 0
        for r in rows:
            meta = json.loads(r["metadata_json"])
            angle = meta.get("angle_hook", "")
            preview = r["body"][:80].replace("\n", " ")
            print(
                f"  #{r['id']:>4}  {r['status']:<9}  {r['channel']:<10}  {preview!r}"
            )
            if angle:
                print(f"          ↳ {angle[:100]}")
        return 0
    finally:
        conn.close()


def cmd_queue_show(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        row = content.get_item(conn, tenant_id=t.id, item_id=args.item_id)
        if row is None:
            print(f"no item id={args.item_id} for tenant {t.slug!r}", file=sys.stderr)
            return 1
        meta = json.loads(row["metadata_json"])
        print(f"=== item #{row['id']}  ({row['channel']}, {row['status']}) ===")
        print(f"created_at:   {row['created_at']}")
        if row["posted_at"]:
            print(f"posted_at:    {row['posted_at']}")
        if row["scheduled_for"]:
            print(f"scheduled:    {row['scheduled_for']}")
        if row["rejection_reason"]:
            print(f"rejected:     {row['rejection_reason']}")
        print(f"pillar:       {meta.get('pillar', '?')}")
        print(f"angle:        {meta.get('angle_hook', '?')}")
        print()
        print(row["body"])
        # Print structured metadata that isn't pillar/angle.
        extra = {k: v for k, v in meta.items() if k not in ("pillar", "angle_hook")}
        if extra:
            print("\n--- metadata ---")
            print(json.dumps(extra, indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


def cmd_queue_approve(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        ok = content.approve(conn, tenant_id=t.id, item_id=args.item_id)
        if ok:
            print(f"#{args.item_id} approved.")
            return 0
        print(
            f"#{args.item_id} not transitioned — already non-pending, or wrong tenant.",
            file=sys.stderr,
        )
        return 1
    finally:
        conn.close()


def cmd_queue_reject(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        ok = content.reject(
            conn, tenant_id=t.id, item_id=args.item_id, reason=args.reason
        )
        if ok:
            print(f"#{args.item_id} rejected.")
            return 0
        print(
            f"#{args.item_id} not transitioned — already non-pending, or wrong tenant.",
            file=sys.stderr,
        )
        return 1
    finally:
        conn.close()


def cmd_analytics_refresh_x(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        try:
            stats = analytics.refresh_x_metrics(
                conn, tenant_id=t.id, max_age_hours=args.max_age_hours
            )
        except Exception as exc:
            print(f"refresh failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(
            f"refreshed {stats['refreshed']} distributions, "
            f"{stats['errors']} errors."
        )
        return 0
    finally:
        conn.close()


def cmd_analysis_show(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        result = analysis.latest_report(conn, tenant_id=t.id)
        if result is None:
            print(f"no analysis runs yet for tenant {t.slug!r}.")
            return 1
        run_id, report = result
        if args.json:
            print(report.model_dump_json(indent=2))
            return 0
        print(f"=== analysis run #{run_id}  (tenant={t.slug}) ===\n")
        print("Summary:")
        for k, v in report.summary.items():
            print(f"  {k}: {v}")
        print(f"\nWinners ({len(report.winners)}):")
        for w in report.winners:
            print(f"  ✓ dist={w.distribution_id} ({w.pillar}) — {w.what}")
            print(f"      lesson: {w.lesson}")
        print(f"\nLosers ({len(report.losers)}):")
        for l in report.losers:
            print(f"  ✗ dist={l.distribution_id} ({l.pillar}) — {l.what}")
            print(f"      lesson: {l.lesson}")
        print(f"\nHypotheses ({len(report.hypotheses)}):")
        for h in report.hypotheses:
            print(f"  • {h}")
        print(f"\nStrategy changes:")
        sc = report.strategy_changes
        if sc.voice_do_additions:
            print(f"  voice.do +: {sc.voice_do_additions}")
        if sc.voice_dont_additions:
            print(f"  voice.dont +: {sc.voice_dont_additions}")
        if sc.pillars_to_amplify:
            print(f"  amplify: {sc.pillars_to_amplify}")
        if sc.pillars_to_deprecate:
            print(f"  deprecate: {sc.pillars_to_deprecate}")
        if sc.icp_refinements:
            print(f"  icp_refinements: {sc.icp_refinements}")
        return 0
    finally:
        conn.close()


def cmd_distribute_item(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        try:
            result = distribute_mod.distribute_item(
                conn, tenant_id=t.id, item_id=args.item_id, dry_run=args.dry_run
            )
        except distribute_mod.DistributionError as exc:
            print(f"distribution error: {exc}", file=sys.stderr)
            return 1
        prefix = "[DRY] " if result["dry_run"] else ""
        print(
            f"{prefix}item #{result['item_id']} → distribution #{result['distribution_id']} "
            f"({result['channel']}, {len(result['external_ids'])} part(s))"
        )
        print(f"  primary: {result['primary_url']}")
        return 0
    finally:
        conn.close()


def cmd_distribute_due(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        results = distribute_mod.distribute_due(
            conn, tenant_id=t.id, limit=args.limit, dry_run=args.dry_run
        )
        if not results:
            print(f"no approved items waiting (tenant={t.slug}).")
            return 0
        ok = [r for r in results if "error" not in r]
        err = [r for r in results if "error" in r]
        prefix = "[DRY] " if args.dry_run else ""
        print(f"{prefix}{len(ok)} distributed, {len(err)} failed")
        for r in ok:
            print(
                f"  ✓ #{r['item_id']} → dist #{r['distribution_id']} "
                f"({r['channel']}) {r['primary_url']}"
            )
        for r in err:
            print(f"  ✗ #{r['item_id']}: {r['error']}", file=sys.stderr)
        return 0 if not err else 1
    finally:
        conn.close()


def cmd_jobs_list(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        rows = jobs_mod.list_jobs(conn, status=args.status, limit=args.limit)
        if not rows:
            print(f"no jobs (status={args.status or 'any'})")
            return 0
        for r in rows:
            print(
                f"  #{r['id']:>4}  {r['status']:<8}  {r['kind']:<24}  "
                f"run_at={r['run_at']}  attempts={r['attempts']}/{r['max_attempts']}"
            )
            if r["last_error"]:
                print(f"          ↳ error: {r['last_error']}")
        return 0
    finally:
        conn.close()


def cmd_jobs_stats(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        s = jobs_mod.stats(conn)
        if not s:
            print("no jobs yet.")
            return 0
        for status in ("queued", "running", "done", "failed"):
            if status in s:
                print(f"  {status:<10} {s[status]}")
        return 0
    finally:
        conn.close()


def cmd_scheduler_init(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        enqueued = worker_mod.seed_cron(conn)
        if not enqueued:
            print("cron seeds already present; nothing to enqueue.")
        else:
            print(f"enqueued {len(enqueued)} cron seed job(s): {enqueued}")
        return 0
    finally:
        conn.close()


def cmd_scheduler_start(args: argparse.Namespace) -> int:
    print(
        f"scheduler/worker running. sleep_seconds={args.sleep}; ctrl-c to stop.",
        file=sys.stderr,
    )
    worker_mod.run_forever(sleep_seconds=args.sleep)
    return 0


def cmd_worker_tick(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        job = worker_mod.tick(conn)
        if job is None:
            print("queue empty.")
            return 0
        # Re-fetch to see post-run state.
        row = conn.execute(
            "SELECT status, last_error FROM marketing_jobs WHERE id=?", (job.id,)
        ).fetchone()
        print(f"ran #{job.id} ({job.kind}) → {row['status']}")
        if row["last_error"]:
            print(f"  error: {row['last_error']}")
        return 0
    finally:
        conn.close()


def cmd_distributions_list(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        rows = distribute_mod.list_distributions(conn, tenant_id=t.id, limit=args.limit)
        if not rows:
            print(f"no distributions yet (tenant={t.slug}).")
            return 0
        for r in rows:
            print(
                f"  #{r['id']:>4}  item={r['content_queue_id']:<4}  "
                f"{r['provider']:<6}  posted={r['posted_at']}  "
                f"{r['url']}"
            )
        return 0
    finally:
        conn.close()


def cmd_queue_stats(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        t = tenant.get_by_slug(conn, args.tenant)
        stats = content.queue_stats(conn, tenant_id=t.id)
        total = sum(stats.values())
        if total == 0:
            print(f"queue empty (tenant={t.slug})")
            return 0
        print(f"queue (tenant={t.slug}, total={total}):")
        for s in ("pending", "approved", "rejected", "scheduled", "posted", "failed"):
            if s in stats:
                print(f"  {s:<10} {stats[s]}")
        return 0
    finally:
        conn.close()


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
    # `content` knobs — ignored by other agents
    run_p.add_argument("--tweets", type=int, default=3, help="x_tweet count (content)")
    run_p.add_argument("--threads", type=int, default=1, help="x_thread count (content)")
    run_p.add_argument("--linkedin", type=int, default=1, help="linkedin count (content)")
    run_p.add_argument("--blog", type=int, default=0, help="blog outline count (content)")
    run_p.add_argument("--email", type=int, default=0, help="email count (content)")
    run_p.add_argument("--seed", type=int, default=None, help="rng seed for reproducible angle picks")
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

    queue_p = sub.add_parser("queue", help="content approval queue")
    queue_sub = queue_p.add_subparsers(dest="queue_cmd", required=True)

    q_list = queue_sub.add_parser("list", help="list items in the queue")
    q_list.add_argument("--tenant", default="hatchik")
    q_list.add_argument(
        "--status",
        choices=["pending", "approved", "rejected", "scheduled", "posted", "failed"],
        default=None,
    )
    q_list.add_argument("--limit", type=int, default=20)
    q_list.set_defaults(func=cmd_queue_list)

    q_show = queue_sub.add_parser("show", help="show one item by id")
    q_show.add_argument("item_id", type=int)
    q_show.add_argument("--tenant", default="hatchik")
    q_show.set_defaults(func=cmd_queue_show)

    q_approve = queue_sub.add_parser("approve", help="approve a pending item")
    q_approve.add_argument("item_id", type=int)
    q_approve.add_argument("--tenant", default="hatchik")
    q_approve.set_defaults(func=cmd_queue_approve)

    q_reject = queue_sub.add_parser("reject", help="reject a pending item")
    q_reject.add_argument("item_id", type=int)
    q_reject.add_argument("--reason", required=True)
    q_reject.add_argument("--tenant", default="hatchik")
    q_reject.set_defaults(func=cmd_queue_reject)

    q_stats = queue_sub.add_parser("stats", help="count items by status")
    q_stats.add_argument("--tenant", default="hatchik")
    q_stats.set_defaults(func=cmd_queue_stats)

    dist_p = sub.add_parser("distribute", help="post approved items to their channel")
    dist_sub = dist_p.add_subparsers(dest="dist_cmd", required=True)

    d_item = dist_sub.add_parser("item", help="distribute one approved item by id")
    d_item.add_argument("item_id", type=int)
    d_item.add_argument("--tenant", default="hatchik")
    d_item.add_argument("--dry-run", action="store_true", help="skip the real X call; mark posted with synthetic ids")
    d_item.set_defaults(func=cmd_distribute_item)

    d_due = dist_sub.add_parser("due", help="distribute every approved item (up to --limit)")
    d_due.add_argument("--tenant", default="hatchik")
    d_due.add_argument("--limit", type=int, default=10)
    d_due.add_argument("--dry-run", action="store_true")
    d_due.set_defaults(func=cmd_distribute_due)

    dists_p = sub.add_parser("distributions", help="inspect distribution log")
    dists_sub = dists_p.add_subparsers(dest="dists_cmd", required=True)
    dl = dists_sub.add_parser("list", help="recent distributions")
    dl.add_argument("--tenant", default="hatchik")
    dl.add_argument("--limit", type=int, default=20)
    dl.set_defaults(func=cmd_distributions_list)

    jobs_p = sub.add_parser("jobs", help="inspect the marketing_jobs queue")
    jobs_sub = jobs_p.add_subparsers(dest="jobs_cmd", required=True)
    jl = jobs_sub.add_parser("list", help="list jobs")
    jl.add_argument(
        "--status",
        choices=["queued", "running", "done", "failed"],
        default=None,
    )
    jl.add_argument("--limit", type=int, default=20)
    jl.set_defaults(func=cmd_jobs_list)
    js = jobs_sub.add_parser("stats", help="count jobs by status")
    js.set_defaults(func=cmd_jobs_stats)

    sched_p = sub.add_parser("scheduler", help="cron seeds + foreground worker")
    sched_sub = sched_p.add_subparsers(dest="sched_cmd", required=True)
    si = sched_sub.add_parser("init", help="enqueue the self-rescheduling cron seeds")
    si.set_defaults(func=cmd_scheduler_init)
    ss = sched_sub.add_parser("start", help="run the worker drain loop in the foreground")
    ss.add_argument("--sleep", type=float, default=10.0)
    ss.set_defaults(func=cmd_scheduler_start)

    work_p = sub.add_parser("worker", help="run the worker manually (debug)")
    work_sub = work_p.add_subparsers(dest="worker_cmd", required=True)
    wt = work_sub.add_parser("tick", help="run at most one job and exit")
    wt.set_defaults(func=cmd_worker_tick)

    an_p = sub.add_parser("analytics", help="pull engagement metrics from external sources")
    an_sub = an_p.add_subparsers(dest="analytics_cmd", required=True)
    arx = an_sub.add_parser("refresh-x", help="pull fresh X public_metrics for recent distributions")
    arx.add_argument("--tenant", default="hatchik")
    arx.add_argument("--max-age-hours", type=int, default=24)
    arx.set_defaults(func=cmd_analytics_refresh_x)

    rep_p = sub.add_parser("analysis", help="inspect the latest Layer-4 analysis report")
    rep_sub = rep_p.add_subparsers(dest="analysis_cmd", required=True)
    rs = rep_sub.add_parser("show", help="print a human-readable summary")
    rs.add_argument("--tenant", default="hatchik")
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(func=cmd_analysis_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
