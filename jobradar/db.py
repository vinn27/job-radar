"""SQLite storage: jobs, scores, runs.

Dedupe has two layers:
  hard  — UNIQUE(source, external_id): the same job surfacing under multiple
          keyword searches is inserted once (LinkedIn ids are canonical).
  fuzzy — dedupe_key = normalize(title)|normalize(company): a repost with a
          fresh posting id is recorded but flagged, and never emailed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from .config import DB_PATH
from .models import JobPosting, MatchResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT,
    location        TEXT,
    posted_text     TEXT,
    listed_date     TEXT,
    salary          TEXT,
    apply_url       TEXT,
    search_query    TEXT,
    dedupe_key      TEXT,
    description     TEXT,
    detail_fetched  INTEGER NOT NULL DEFAULT 0,
    is_repost       INTEGER NOT NULL DEFAULT 0,
    repost_of       INTEGER,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    emailed_at      TEXT,
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key);

CREATE TABLE IF NOT EXISTS scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              INTEGER NOT NULL REFERENCES jobs(id),
    score               REAL NOT NULL,
    matched_keywords    TEXT NOT NULL,
    missing_must_haves  TEXT,
    recommended_resume  TEXT,
    text_source         TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT,
    jobs_seen     INTEGER,
    new_jobs      INTEGER,
    emailed_jobs  INTEGER,
    notes         TEXT
);
"""


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(text: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for fuzzy dedupe keys."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def dedupe_key_for(job: JobPosting) -> str:
    return f"{normalize(job.title)}|{normalize(job.company)}"


def connect(settings=None) -> sqlite3.Connection:
    path = DB_PATH
    if settings is not None and settings.raw.get("db_path"):
        from .config import ROOT

        path = ROOT / settings.raw["db_path"]
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_new_jobs(conn: sqlite3.Connection, jobs: list[JobPosting]) -> list[dict]:
    """Insert jobs, return metadata about the genuinely-new ones.

    Returns [{'db_id', 'job', 'is_repost_of'}] for rows that did not exist
    before this call. Existing jobs just get last_seen_at bumped.
    """
    now = iso_now()
    new: list[dict] = []
    for job in jobs:
        cur = conn.execute(
            """INSERT OR IGNORE INTO jobs
               (source, external_id, title, company, location, posted_text, listed_date,
                salary, apply_url, search_query, dedupe_key, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job.source, job.external_id, job.title, job.company, job.location,
             job.posted_text, job.listed_date, job.salary, job.apply_url,
             job.search_query, dedupe_key_for(job), now, now),
        )
        if cur.rowcount == 1:
            db_id = int(cur.lastrowid)
            job.db_id = db_id
            dup = conn.execute(
                "SELECT id FROM jobs WHERE dedupe_key = ? AND id < ? ORDER BY id LIMIT 1",
                (dedupe_key_for(job), db_id),
            ).fetchone()
            repost_of = int(dup["id"]) if dup else None
            if repost_of is not None:
                conn.execute(
                    "UPDATE jobs SET is_repost = 1, repost_of = ? WHERE id = ?",
                    (repost_of, db_id),
                )
            new.append({"db_id": db_id, "job": job, "is_repost_of": repost_of})
        else:
            row = conn.execute(
                "SELECT id FROM jobs WHERE source = ? AND external_id = ?",
                (job.source, job.external_id),
            ).fetchone()
            conn.execute("UPDATE jobs SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
    conn.commit()
    return new


def set_description(conn: sqlite3.Connection, db_id: int, description: str) -> None:
    conn.execute(
        "UPDATE jobs SET description = ?, detail_fetched = 1 WHERE id = ?",
        (description, db_id),
    )
    conn.commit()


def add_score(conn: sqlite3.Connection, db_id: int, mr: MatchResult) -> None:
    conn.execute(
        """INSERT INTO scores (job_id, score, matched_keywords, missing_must_haves,
                               recommended_resume, text_source, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (db_id, mr.score, json.dumps(mr.matched), json.dumps(mr.missing_must_haves),
         mr.recommended_resume, mr.text_source, iso_now()),
    )
    conn.commit()


def mark_emailed(conn: sqlite3.Connection, db_ids: list[int]) -> None:
    now = iso_now()
    conn.executemany(
        "UPDATE jobs SET emailed_at = ? WHERE id = ?", [(now, i) for i in db_ids]
    )
    conn.commit()


def start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (iso_now(),))
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection, run_id: int, status: str,
    jobs_seen: int, new_jobs: int, emailed_jobs: int, notes: dict,
) -> None:
    conn.execute(
        """UPDATE runs SET finished_at = ?, status = ?, jobs_seen = ?,
           new_jobs = ?, emailed_jobs = ?, notes = ? WHERE id = ?""",
        (iso_now(), status, jobs_seen, new_jobs, emailed_jobs, json.dumps(notes), run_id),
    )
    conn.commit()


def backlog(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return conn.execute(
        """SELECT j.*, s.score, s.matched_keywords
           FROM jobs j
           LEFT JOIN scores s ON s.id = (
               SELECT MAX(id) FROM scores WHERE job_id = j.id
           )
           WHERE j.first_seen_at >= ?
           ORDER BY j.first_seen_at DESC
           LIMIT 500""",
        (cutoff,),
    ).fetchall()
