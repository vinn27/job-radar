"""seed-test: fake jobs to verify dedupe, scoring and digest rendering offline.

Inserts 4 jobs: a strong match, a mid match, a seniority-gated one, and a
fuzzy duplicate of the first (same title+company, different posting id) that
must never reach a digest. Renders the digest to console + logs/digest_preview.html.
"""

from __future__ import annotations

import logging

from . import db, digest, matcher
from .models import JobPosting

log = logging.getLogger("jobradar.seed")

FAKE_JOBS = [
    JobPosting(
        source="test", external_id="seed-1",
        title="Data Engineer (SQL, Python, PySpark)",
        company="Acme Analytics", location="Bengaluru, India",
        posted_text="2 days ago", listed_date=None, salary="₹6–10 LPA",
        apply_url="https://example.com/job/1", search_query="seed",
        description=("We need a data engineer with strong SQL and Python. "
                     "You will build ETL pipelines in PySpark on AWS. "
                     "Requirements: 1-3 years of experience with data pipelines, "
                     "MySQL, and Power BI dashboards."),
    ),
    JobPosting(
        source="test", external_id="seed-2",
        title="Backend Developer — Java",
        company="Beta Systems", location="Pune, India",
        posted_text="1 day ago", listed_date=None, salary=None,
        apply_url="https://example.com/job/2", search_query="seed",
        description="Core Java, Spring Boot, microservices. 4 years of experience required.",
    ),
    JobPosting(
        source="test", external_id="seed-3",
        title="Senior Data Engineer",
        company="Gamma Corp", location="Remote",
        posted_text="3 days ago", listed_date=None, salary=None,
        apply_url="https://example.com/job/3", search_query="seed",
        description="Lead our data platform. SQL, Python, Spark, Airflow.",
    ),
    # Fuzzy duplicate of seed-1: same title+company after normalization, new id.
    JobPosting(
        source="test", external_id="seed-4",
        title="Data Engineer (SQL, Python, PySpark) ",
        company="Acme  Analytics", location="Bengaluru, India",
        posted_text="just now", listed_date=None, salary=None,
        apply_url="https://example.com/job/4", search_query="seed",
    ),
]


def seed_test(settings) -> None:
    conn = db.connect(settings)
    new = db.insert_new_jobs(conn, FAKE_JOBS)
    log.info("seed inserted %d new job(s) (of %d)", len(new), len(FAKE_JOBS))

    for r in new:
        job = r["job"]
        text = job.title + (f"\n{job.description}" if job.description else "")
        mr = matcher.match(job.title, text, settings.profile, settings.resumes,
                           "title+jd" if job.description else "title")
        db.add_score(conn, r["db_id"], mr)
        log.info("  [%s] %s — %s | matched=%s missing_must=%s%s",
                 mr.score, job.title, job.company, ",".join(mr.matched) or "-",
                 ",".join(mr.missing_must_haves) or "-",
                 f" | REPOST of {r['is_repost_of']}" if r["is_repost_of"] else "")

    # Render a digest from whatever is fresh and unemailed, exactly like a real run.
    fresh = [r for r in new if r["is_repost_of"] is None]
    results = {}
    for r in fresh:
        job = r["job"]
        text = job.title + (f"\n{job.description}" if job.description else "")
        results[r["db_id"]] = matcher.match(job.title, text, settings.profile,
                                            settings.resumes,
                                            "title+jd" if job.description else "title")
    min_score = int(settings.scoring.get("min_score_to_email", 40))
    selected = sorted(
        (r for r in fresh if not results[r["db_id"]].excluded
         and results[r["db_id"]].score >= min_score),
        key=lambda r: results[r["db_id"]].score, reverse=True,
    )
    from .pipeline import _entry

    entries = [_entry(r["job"], results[r["db_id"]]) for r in selected]
    stats = {"jobs_seen": len(FAKE_JOBS), "new_jobs": len(fresh), "reposts": 1,
             "excluded": 1, "details_fetched": 0, "errors": []}
    if entries:
        html = digest.render_html(entries, stats)
        text = digest.render_text(entries, stats)
        from .config import LOGS_DIR

        preview_path = LOGS_DIR / "digest_preview.html"
        preview_path.write_text(html, encoding="utf-8")
        print(text)
        print(f"\nHTML preview written to {preview_path}")
        print("Seed jobs are left UNEMAILED (source='test' never reaches a real digest).")
    else:
        print("No qualifying jobs — check your scoring config.")
