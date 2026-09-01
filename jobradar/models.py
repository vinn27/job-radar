"""Data models shared across the pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


@dataclass
class JobPosting:
    source: str                       # 'linkedin' | 'adzuna' | 'test'
    external_id: str                  # source-canonical id (LinkedIn jobPosting urn number)
    title: str
    company: str | None
    location: str | None
    posted_text: str | None           # raw "2 days ago" from the card
    listed_date: str | None           # ISO date when the source provides one
    salary: str | None
    apply_url: str | None
    search_query: str                 # keywords that first surfaced this job
    description: str | None = None    # full JD, only fetched for top candidates
    db_id: int | None = None          # row id in the jobs table, set after insert


@dataclass
class MatchResult:
    score: float                      # 0-100, or -1 when excluded by a gate
    matched: list[str] = field(default_factory=list)
    missing_must_haves: list[str] = field(default_factory=list)
    recommended_resume: str | None = None
    text_source: str = "title"        # 'title' | 'title+card' | 'title+jd'
    excluded: bool = False
    exclude_reason: str | None = None
    asked_years: int = 0              # minimum years of experience the text asks for


def posted_age_days(job: "JobPosting") -> int | None:
    """Best-effort age in days from listed_date (ISO) or posted_text ('2 days ago').

    None = unknown (treated as fresh by the pipeline's age filter).
    """
    if job.listed_date:
        try:
            listed = date.fromisoformat(job.listed_date[:10])
            return (date.today() - listed).days
        except ValueError:
            pass
    text = (job.posted_text or "").lower()
    if not text:
        return None
    if any(w in text for w in ("today", "just now", "few hours", "hour", "minute", "second")):
        return 0
    # Naukri writes "3+ weeks ago" — the '+' must not break the parse.
    m = re.search(r"(\d+)\s*\+?\s*days?", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*\+?\s*weeks?", text)
    if m:
        return int(m.group(1)) * 7
    m = re.search(r"(\d+)\s*\+?\s*months?", text)
    if m:
        return int(m.group(1)) * 30
    return None
