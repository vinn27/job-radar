"""Source adapter contract. A source failing must never kill the run."""

from __future__ import annotations

import logging

from ..models import JobPosting


class SourceAdapter:
    name = "base"

    def __init__(self, settings, session):
        self.settings = settings
        self.session = session

    @classmethod
    def enabled_in(cls, settings) -> bool:
        return bool(settings.sources.get(cls.name, {}).get("enabled", False))

    def fetch_cards(self, search: dict) -> list[JobPosting]:
        raise NotImplementedError

    def fetch_detail(self, job: JobPosting) -> str:
        raise NotImplementedError

    def collect(self, searches: list[dict]) -> tuple[list[JobPosting], list[str]]:
        """Run every search; a single query failing is logged, not fatal."""
        log = logging.getLogger(f"jobradar.source.{self.name}")
        jobs: list[JobPosting] = []
        errors: list[str] = []
        for search in searches:
            label = search.get("keywords", "?")
            try:
                cards = self.fetch_cards(search)
                jobs.extend(cards)
                log.info("query %r: %d cards", label, len(cards))
            except Exception as exc:
                log.warning("query %r failed: %s", label, exc)
                errors.append(f"{self.name}:{label}: {exc}")
        return jobs, errors

    def close(self) -> None:
        """Release heavyweight resources (e.g. a Playwright browser). No-op by default."""
