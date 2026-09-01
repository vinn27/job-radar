"""Job Radar CLI.

Commands:
  run          collect jobs, score them, send the email digest (scheduled task)
  test-email   send a tiny test email to verify SMTP settings
  backlog      print jobs seen in the last N days
  probe        dev: print what a source returns, no DB writes
  seed-test    dev: insert fake jobs to test dedupe/scoring/digest without scraping
"""

import argparse
import logging
import sys
from datetime import datetime

# Windows consoles default to cp1252, which chokes on ₹ etc.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from jobradar import db, matcher
from jobradar.config import ensure_dirs, load_settings, setup_logging

log = logging.getLogger("jobradar")


def cmd_run(args, settings):
    from jobradar.pipeline import run

    # Daytime window guard: recruiters post during office hours, so night-time
    # triggers exit quietly instead of scraping (config: "schedule": {"active_hours": [8, 22]}).
    window = settings.raw.get("schedule", {}).get("active_hours")
    if window and args.command == "run" and not (int(window[0]) <= datetime.now().hour < int(window[1])):
        log.info("outside active hours %s (now %02d:xx) — skipping run", window, datetime.now().hour)
        return

    summary = run(
        settings,
        dry_run=args.dry_run,
        only_source=args.source,
        only_query=args.query,
        no_detail=args.no_detail,
    )
    log.info(
        "run finished: %s seen, %s new, %s emailed, status=%s",
        summary["jobs_seen"], summary["new_jobs"], summary["emailed"], summary["status"],
    )
    if summary["status"] == "error":
        raise SystemExit(1)


def cmd_test_email(args, settings):
    from jobradar.mailer import send

    send(
        settings,
        subject="Job Radar: test email",
        text="If you can read this, SMTP is configured correctly.",
        html="<p>If you can read this, <b>SMTP is configured correctly</b>.</p>",
    )
    log.info("test email sent to %s", settings.email["to"])


def cmd_backlog(args, settings):
    conn = db.connect(settings)
    rows = db.backlog(conn, days=args.days)
    if not rows:
        print(f"No jobs in the last {args.days} day(s).")
        return
    print(f"{'score':>5}  {'emailed':>7}  {'first seen':<19}  title — company (location)")
    print("-" * 100)
    for r in rows:
        emailed = "yes" if r["emailed_at"] else "no"
        score = r["score"] if r["score"] is not None else "-"
        print(
            f"{score!s:>5}  {emailed:>7}  {r['first_seen_at'][:19]:<19}  "
            f"{r['title']} — {r['company'] or '?'} ({r['location'] or '?'})"
        )
    print(f"\n{len(rows)} job(s).")


def cmd_probe(args, settings):
    from jobradar.http import PoliteSession
    from jobradar.sources import SOURCES

    if args.source not in SOURCES:
        raise SystemExit(f"unknown source {args.source!r}; known: {', '.join(SOURCES)}")
    adapter = SOURCES[args.source](settings, PoliteSession(**{
        "min_delay": settings.politeness.get("min_delay_sec", 2.0),
        "max_delay": settings.politeness.get("max_delay_sec", 4.0),
        "retries": settings.politeness.get("retries", 3),
        "timeout": settings.politeness.get("timeout_sec", 15),
    }))

    try:
        if args.detail:
            from jobradar.models import JobPosting

            job = JobPosting(
                source=args.source, external_id=args.detail, title="probe", company=None,
                location=None, posted_text=None, listed_date=None, salary=None,
                apply_url=None, search_query=args.query,
            )
            text = adapter.fetch_detail(job)
            print(f"--- detail for {args.detail}: {len(text)} chars ---")
            print(text[:1500])
            return

        jobs = adapter.fetch_cards({"keywords": args.query, "location": "India", "pages": args.pages})
        print(f"--- {len(jobs)} card(s) for {args.query!r} ---")
        for j in jobs[:30]:
            print(f"  [{j.external_id}] {j.title} | {j.company} | {j.location} | "
                  f"{j.posted_text} | {j.salary or '-'}")
            if j.description:
                print(f"      desc: {j.description[:110]}")
        if len(jobs) > 30:
            print(f"  ... and {len(jobs) - 30} more")
    finally:
        adapter.close()


def cmd_seed_test(args, settings):
    from jobradar.seed import seed_test

    seed_test(settings)


def main():
    parser = argparse.ArgumentParser(prog="jobradar", description="Job Radar — matched job digests by email")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="collect + score + email digest")
    p.add_argument("--dry-run", action="store_true", help="do everything except send the email")
    p.add_argument("--source", help="only use this source (e.g. linkedin)")
    p.add_argument("--query", help="only run searches matching these keywords (dev)")
    p.add_argument("--no-detail", action="store_true", help="skip full-JD detail fetches")

    sub.add_parser("test-email", help="send a test email")

    p = sub.add_parser("backlog", help="list jobs from the last N days")
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("probe", help="dev: fetch and print, no DB writes")
    p.add_argument("--source", required=True)
    p.add_argument("--query", default="data engineer")
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--detail", help="fetch full JD for this external job id")

    sub.add_parser("seed-test", help="dev: insert fake jobs incl. a fuzzy duplicate, render digest")

    args = parser.parse_args()

    ensure_dirs()
    setup_logging()
    settings = load_settings()

    handlers = {
        "run": cmd_run,
        "test-email": cmd_test_email,
        "backlog": cmd_backlog,
        "probe": cmd_probe,
        "seed-test": cmd_seed_test,
    }
    try:
        handlers[args.command](args, settings)
    except SystemExit:
        raise
    except Exception:
        log.exception("command %s failed", args.command)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
