"""Scoring and resume-variant recommendation — pure functions, no I/O.

Every score is explainable: it is 100 x (sum of matched skill weights) /
(total skill weight), with multiplicative penalties. The digest shows the
matched keywords so any number can be traced back by hand.
"""

from __future__ import annotations

import re
from functools import lru_cache

from .models import MatchResult


@lru_cache(maxsize=1024)
def _pattern(term: str) -> re.Pattern:
    """Word-boundary-ish match that survives terms like 'c++' or 'c#'."""
    escaped = re.escape(term.lower())
    return re.compile(r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])", re.IGNORECASE)


def _hits(term: str, text: str) -> bool:
    return _pattern(term).search(text) is not None


def _matched_skills(text: str, skills: list[dict]) -> tuple[list[str], float, float]:
    matched: list[str] = []
    points = 0.0
    total = sum(float(s.get("weight", 5)) for s in skills)
    for skill in skills:
        terms = [skill["name"], *skill.get("aliases", [])]
        if any(_hits(t, text) for t in terms):
            matched.append(skill["name"])
            points += float(skill.get("weight", 5))
    return matched, points, total


def _gate_reason(title: str, profile: dict) -> str | None:
    t = title.lower()
    for term in profile.get("seniority_exclude", []):
        if _hits(term, t):
            return f"seniority: {term}"
    for term in profile.get("exclude_keywords", []):
        if term.lower() in t:
            return f"excluded: {term}"
    return None


_YRS_RANGE = re.compile(r"(\d{1,2})\s*(?:[-–]|to)\s*\d{1,2}\s*(?:years?|yrs?)", re.IGNORECASE)
_YRS_PLUS = re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)", re.IGNORECASE)
_YRS_PLAIN = re.compile(r"(\d{1,2})\s*(?:years?|yrs?)", re.IGNORECASE)


def required_min_years(text: str) -> int:
    """Minimum years of experience the text asks for.

    Ranges count as their low end ('1-6 Yrs' -> 1) because the minimum is what
    screens a junior candidate out; '3+ years' -> 3. Returns 0 when nothing
    experience-like is found (e.g. a bare job title).
    """
    candidates: list[int] = []
    for chunk in re.split(r"[.;\n]", text):
        low = chunk.lower()
        if not ("experience" in low or " yrs" in low or "exp" in low.split()):
            continue
        covered = [m.span() for m in _YRS_RANGE.finditer(chunk)]
        for m in _YRS_RANGE.finditer(chunk):
            candidates.append(int(m.group(1)))
        for m in _YRS_PLUS.finditer(chunk):
            candidates.append(int(m.group(1)))
        for m in _YRS_PLAIN.finditer(chunk):
            if not any(s <= m.start() < e for s, e in covered):
                candidates.append(int(m.group(1)))
    return min(candidates) if candidates else 0


def recommend_resume(text: str, resumes: list[dict]) -> str | None:
    """Pick the variant whose emphasized skills match the job text most."""
    if not resumes:
        return None
    best_name, best_hits = resumes[0].get("name", "resume"), -1
    for variant in resumes:
        terms = variant.get("emphasize", [])
        hits = sum(1 for t in terms if _hits(t, text))
        if hits > best_hits:
            best_name, best_hits = variant.get("name", "resume"), hits
    return best_name


def match(job_title: str, text: str, profile: dict, resumes: list[dict],
          text_source: str, penalize_must: bool = True) -> MatchResult:
    """Score one job. `text` is the title alone (stage A) or title + JD (stage B).

    penalize_must=False is used for the title-only stage: titles rarely name
    the stack, so a missing must-have there is noise, not signal.
    """
    reason = _gate_reason(job_title, profile)
    if reason:
        return MatchResult(score=-1, text_source=text_source, excluded=True, exclude_reason=reason)

    # Hard experience gate: a JD whose minimum requirement is above the
    # candidate's bracket is recorded but never emailed.
    asked = required_min_years(text)
    hard_max = profile.get("hard_max_required_years")
    if hard_max is not None and asked > int(hard_max):
        return MatchResult(score=-1, text_source=text_source, excluded=True,
                           exclude_reason=f"experience: asks {asked}+ yrs",
                           asked_years=asked)

    skills = profile.get("skills", [])
    matched, points, total = _matched_skills(text, skills)
    score = 100.0 * points / total if total else 0.0

    missing_must = [
        s["name"] for s in skills
        if s.get("must") and s["name"] not in matched
    ]
    if penalize_must and missing_must:
        score *= 0.5 ** len(missing_must)

    return MatchResult(
        score=round(score),
        matched=matched,
        missing_must_haves=missing_must,
        recommended_resume=recommend_resume(text, resumes),
        text_source=text_source,
        asked_years=asked,
    )
