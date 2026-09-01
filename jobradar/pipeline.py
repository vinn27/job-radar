"""One run, end to end:

fetch -> hard dedupe -> fuzzy dedupe -> score on title -> fetch JD for top K ->
rescore on title+JD -> filter/sort/cap -> render -> send -> mark emailed.
"""

from __future__ import annotations

import logging
from datetime import datetime

from . import db, digest, mailer, matcher
from .http import PoliteSession, SourceError
from .models import JobPosting, MatchResult, posted_age_days
from .sources import SOURCES

log = logging.getLogger("jobradar.pipeline")


def _polite_session(settings):
    p = settings.politeness
    return PoliteSession(
        min_delay=float(p.get("min_delay_sec", 2.0)),
        max_delay=float(p.get("max_delay_sec", 4.0)),
        retries=int(p.get("retries", 3)),
        timeout=int(p.get("timeout_sec", 15)),
    )


def _entry(job: JobPosting, mr: MatchResult) -> dict:
    return {
        "title": job.title, "company": job.company, "location": job.location,
        "posted_text": job.posted_text, "salary": job.salary,
        "url": job.apply_url, "score": int(mr.score),
        "matched_skills": mr.matched, "missing_must_haves": mr.missing_must_haves,
        "recommended_resume": mr.recommended_resume,
    }


def run(settings, dry_run: bool = False, only_source: str | None = None,
        only_query: str | None = None, no_detail: bool = False) -> dict:
    conn = db.connect(settings)
    run_id = db.start_run(conn)

    searches = settings.searches
    if only_query:
        matches = [s for s in searches if s["keywords"] == only_query]
        searches = matches or [{"keywords": only_query, "location": "India", "pages": 1}]

    # -- collect -------------------------------------------------------------
    jobs: list[JobPosting] = []
    errors: list[str] = []
    session = _polite_session(settings)
    adapters = []
    for name, cls in SOURCES.items():
        if only_source and name != only_source:
            continue
        if cls.enabled_in(settings):
            adapters.append(cls(settings, session))
        else:
            log.info("source %s disabled, skipping", name)

    for adapter in adapters:
        # A source can override the global searches list (sources.<name>.searches).
        own = settings.sources.get(adapter.name, {}).get("searches")
        collected, errs = adapter.collect(own or searches)
        jobs.extend(collected)
        errors.extend(errs)

    if adapters and not jobs and errors and len(errors) >= len(adapters) * max(1, len(searches)):
        db.finish_run(conn, run_id, "error", 0, 0, 0, {"errors": errors})
        log.error("every query failed: %s", errors)
        return {"jobs_seen": 0, "new_jobs": 0, "emailed": 0, "status": "error", "errors": errors}

    # Naukri (or any Playwright source) opens a real browser — always close it.
    try:
        return _run_after_collect(settings, conn, run_id, jobs, errors, adapters,
                                  dry_run, no_detail)
    finally:
        for adapter in adapters:
            try:
                adapter.close()
            except Exception:
                pass


def _run_after_collect(settings, conn, run_id, jobs, errors, adapters,
                       dry_run, no_detail):

    # -- dedupe ---------------------------------------------------------------
    new_rows = db.insert_new_jobs(conn, jobs)
    fresh = [r for r in new_rows if r["is_repost_of"] is None]
    reposts = [r for r in new_rows if r["is_repost_of"] is not None]

    # -- freshness: only postings inside the age window reach scoring/email ----
    max_age = int(settings.raw.get("freshness", {}).get("max_posted_age_days", 4))
    fresh_all = fresh
    fresh = [r for r in fresh
             if (d := posted_age_days(r["job"])) is None or d <= max_age]
    stale = len(fresh_all) - len(fresh)
    log.info("seen=%d new=%d reposts=%d stale=%d(>%dd)", len(jobs), len(fresh),
             len(reposts), stale, max_age)

    # -- stage A: score every fresh job on its title (+card text if the source
    # provides any — Naukri/Adzuna cards carry skills/experience/salary) -------
    results: dict[int, MatchResult] = {}
    for r in fresh:
        job = r["job"]
        text = job.title + (f"\n{job.description}" if job.description else "")
        mr = matcher.match(job.title, text, settings.profile, settings.resumes,
                           "title+card" if job.description else "title",
                           penalize_must=False)
        db.add_score(conn, r["db_id"], mr)
        results[r["db_id"]] = mr

    # -- stage B: fetch JDs for the most promising few -------------------------
    threshold = int(settings.scoring.get("detail_fetch_threshold", 20))
    max_details = int(settings.scoring.get("max_details_per_run", 12))
    details_fetched = 0
    if not no_detail:
        by_source = {a.name: a for a in adapters}
        candidates = sorted(
            (r for r in fresh if not results[r["db_id"]].excluded
             and results[r["db_id"]].score >= threshold),
            key=lambda r: results[r["db_id"]].score, reverse=True,
        )[:max_details]
        for r in candidates:
            job = r["job"]
            adapter = by_source.get(job.source)
            if adapter is None:
                continue
            try:
                desc = adapter.fetch_detail(job)
            except Exception as exc:
                log.warning("detail fetch failed for %s %s: %s", job.source, job.external_id, exc)
                errors.append(f"detail:{job.source}:{job.external_id}: {exc}")
                continue
            if not desc:
                continue
            db.set_description(conn, r["db_id"], desc)
            job.description = desc
            mr = matcher.match(job.title, f"{job.title}\n{desc}", settings.profile,
                               settings.resumes, "title+jd")
            db.add_score(conn, r["db_id"], mr)
            results[r["db_id"]] = mr
            details_fetched += 1

    # -- select, render, send ---------------------------------------------------
    excluded = sum(1 for mr in results.values() if mr.excluded)
    min_score = int(settings.scoring.get("min_score_to_email", 40))
    max_jobs = int(settings.digest.get("max_jobs", 25))
    selected = sorted(
        (r for r in fresh if not results[r["db_id"]].excluded
         and results[r["db_id"]].score >= min_score),
        key=lambda r: results[r["db_id"]].score, reverse=True,
    )[:max_jobs]

    entries = [_entry(r["job"], results[r["db_id"]]) for r in selected]
    stats = {
        "jobs_seen": len(jobs), "new_jobs": len(fresh), "reposts": len(reposts),
        "stale": stale, "excluded": excluded, "details_fetched": details_fetched,
        "errors": errors,
    }

    emailed = 0
    if entries:
        html = digest.render_html(entries, stats)
        text = digest.render_text(entries, stats)
        if dry_run:
            from .config import LOGS_DIR
            (LOGS_DIR / "digest_preview.html").write_text(html, encoding="utf-8")
            log.info("dry-run: %d jobs would be emailed; preview at %s",
                     len(entries), LOGS_DIR / "digest_preview.html")
        else:
            subject = (f"{settings.email.get('subject_prefix', 'Job Radar')}: "
                       f"{len(entries)} new match{'es' if len(entries) != 1 else ''} — "
                       f"{datetime.now():%d %b %H:%M}")
            mailer.send(settings, subject, text, html)
            db.mark_emailed(conn, [r["db_id"] for r in selected])
            emailed = len(entries)
    else:
        log.info("no qualifying new jobs — no email this run")

    status = "error" if not adapters else ("partial" if errors else "ok")
    db.finish_run(conn, run_id, status, len(jobs), len(fresh), emailed,
                  {"errors": errors, "details_fetched": details_fetched})
    return {**stats, "emailed": emailed, "status": status}
