"""LinkedIn guest (logged-out) job search — no account involved, reading only.

Endpoints (undocumented, verified working 2026-09-01):
  cards:  /jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=&location=&start=
  detail: /jobs-guest/jobs/api/jobPosting/<id>
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from ..http import SourceError
from ..models import JobPosting
from .base import SourceAdapter

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


def parse_cards(html: str, search_query: str) -> list[JobPosting]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[JobPosting] = []
    for card in soup.select("div.job-search-card[data-entity-urn]"):
        urn = card.get("data-entity-urn", "")
        external_id = urn.rsplit(":", 1)[-1].strip()
        if not external_id.isdigit():
            continue
        title_el = card.select_one("h3.base-search-card__title")
        company_el = (card.select_one("h4.base-search-card__subtitle a")
                      or card.select_one("h4.base-search-card__subtitle"))
        loc_el = card.select_one("span.job-search-card__location")
        time_el = card.select_one("time")
        link_el = card.select_one("a.base-card__full-link")
        salary_el = card.select_one(".job-search-card__salary-info")
        out.append(JobPosting(
            source="linkedin",
            external_id=external_id,
            title=title_el.get_text(strip=True) if title_el else "Untitled",
            company=company_el.get_text(strip=True) if company_el else None,
            location=loc_el.get_text(strip=True) if loc_el else None,
            posted_text=time_el.get_text(strip=True) if time_el else None,
            listed_date=time_el.get("datetime") if time_el else None,
            salary=salary_el.get_text(strip=True) if salary_el else None,
            apply_url=(link_el.get("href") if link_el else None)
                      or f"https://www.linkedin.com/jobs/view/{external_id}",
            search_query=search_query,
        ))
    return out


class LinkedInAdapter(SourceAdapter):
    name = "linkedin"

    def fetch_cards(self, search: dict) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        extra = search.get("params", {})  # e.g. f_E=2 (entry level), f_TPR=r604800 (past week)
        for page in range(int(search.get("pages", 1))):
            resp = self.session.fetch(SEARCH_URL, params={
                "keywords": search["keywords"],
                "location": search.get("location", ""),
                "start": page * 25,
                **extra,
            })
            cards = parse_cards(resp.text, search["keywords"])
            if not cards:
                break  # ran past the last page
            jobs.extend(cards)
        return jobs

    def fetch_detail(self, job: JobPosting) -> str:
        resp = self.session.fetch(DETAIL_URL.format(job_id=job.external_id))
        soup = BeautifulSoup(resp.text, "html.parser")
        el = soup.select_one("div.show-more-less-html__markup")
        if el is None:
            raise SourceError(f"no description markup found for job {job.external_id}")
        return el.get_text("\n", strip=True)
