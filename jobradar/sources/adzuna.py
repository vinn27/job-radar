"""Adzuna jobs API (free tier) — optional secondary source.

Enable by putting ADZUNA_APP_ID and ADZUNA_APP_KEY in .env (from
developer.adzuna.com) and setting sources.adzuna.enabled = true in config.json.
Adzuna listings already include the JD, so no detail fetches are needed.
"""

from __future__ import annotations

from ..models import JobPosting
from .base import SourceAdapter

SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/in/search/{page}"


class AdzunaAdapter(SourceAdapter):
    name = "adzuna"

    def _credentials(self) -> tuple[str, str] | None:
        app_id = self.settings.env.get("ADZUNA_APP_ID", "").strip()
        app_key = self.settings.env.get("ADZUNA_APP_KEY", "").strip()
        return (app_id, app_key) if app_id and app_key else None

    def fetch_cards(self, search: dict) -> list[JobPosting]:
        creds = self._credentials()
        if creds is None:
            raise RuntimeError("adzuna enabled but ADZUNA_APP_ID/ADZUNA_APP_KEY missing in .env")
        where = search.get("location", "india").replace(", india", "").replace(", India", "").strip() or "india"
        jobs: list[JobPosting] = []
        for page in range(1, int(search.get("pages", 1)) + 1):
            resp = self.session.fetch(SEARCH_URL.format(page=page), params={
                "app_id": creds[0], "app_key": creds[1],
                "what": search["keywords"], "where": where,
                "results_per_page": 20,
            })
            results = resp.json().get("results", [])
            if not results:
                break
            for r in results:
                salary = None
                if r.get("salary_min") and r.get("salary_max"):
                    salary = f"£{r['salary_min']:,.0f}–{r['salary_max']:,.0f}"
                elif r.get("salary_max"):
                    salary = f"up to £{r['salary_max']:,.0f}"
                jobs.append(JobPosting(
                    source="adzuna",
                    external_id=str(r["id"]),
                    title=r.get("title") or "Untitled",
                    company=(r.get("company") or {}).get("display_name"),
                    location=(r.get("location") or {}).get("display_name"),
                    posted_text=None,
                    listed_date=(r.get("created") or "")[:10] or None,
                    salary=salary,
                    apply_url=r.get("redirect_url"),
                    search_query=search["keywords"],
                    description=r.get("description") or None,
                ))
        return jobs

    def fetch_detail(self, job: JobPosting) -> str:
        return job.description or ""
