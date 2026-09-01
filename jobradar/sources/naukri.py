"""Naukri via a real browser (Playwright) — plain requests only get a bot shell.

Search URLs are slug-based: https://www.naukri.com/{keywords}-jobs[-in-{city}].
Browsing is logged-out, read-only. Naukri cards are unusually rich (skill tags,
experience range, salary), so we fold them into `description` — stage-A scoring
sees them without any detail fetches.
"""

from __future__ import annotations

import logging
import random
import re
import time

from ..models import JobPosting
from .base import SourceAdapter

log = logging.getLogger("jobradar.source.naukri")

SEARCH_URL = "https://www.naukri.com/{slug}-jobs"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def parse_cards(html: str, search_query: str) -> list[JobPosting]:
    """Parse Naukri search cards (structure mapped from the live DOM 2026-09-01).

    Cards carry skills tags, experience, salary and a JD snippet — all folded
    into `description` so stage-A scoring sees them without detail fetches.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: list[JobPosting] = []
    for card in soup.select("div.srp-jobtuple-wrapper[data-job-id]"):
        external_id = card.get("data-job-id", "").strip()
        title_el = card.select_one("a.title")
        if not title_el or not external_id:
            continue
        company_el = card.select_one("a.comp-name")
        exp_el = card.select_one(".exp-wrap span[title]") or card.select_one(".expwdth")
        sal_el = card.select_one(".sal-wrap span[title]")
        loc_el = card.select_one(".locWdth") or card.select_one(".loc-wrap span[title]")
        desc_el = card.select_one("span.job-desc")
        posted_el = card.select_one("span.job-post-day")
        tags = [li.get_text(strip=True) for li in card.select("ul.tags-gt li") if li.get_text(strip=True)]

        experience = exp_el.get_text(strip=True) if exp_el else ""
        desc_bits = []
        if desc_el:
            desc_bits.append(desc_el.get_text(strip=True))
        if tags:
            desc_bits.append("Skills: " + ", ".join(tags))
        if experience:
            desc_bits.append(f"Experience required: {experience}")

        out.append(JobPosting(
            source="naukri",
            external_id=external_id,
            title=title_el.get_text(strip=True),
            company=company_el.get_text(strip=True) if company_el else None,
            location=loc_el.get_text(strip=True) if loc_el else None,
            posted_text=posted_el.get_text(strip=True) if posted_el else None,
            listed_date=None,
            salary=sal_el.get_text(strip=True) if sal_el else None,
            apply_url=title_el.get("href"),
            search_query=search_query,
            description="\n".join(desc_bits) or None,
        ))
    return out


class NaukriAdapter(SourceAdapter):
    name = "naukri"

    def __init__(self, settings, session):
        super().__init__(settings, session)
        self._pw = None
        self._browser = None
        self._page = None
        self._last_nav = 0.0

    # -- browser lifecycle ------------------------------------------------------

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        # Prefer an installed real Chrome/Edge (better fingerprint than bundled
        # Chromium); fall back to the bundled build.
        last_exc: Exception | None = None
        # Akamai blocks every headless mode we tested (403) — only headful passes.
        for channel in ("chrome", "msedge", None):
            try:
                kwargs = {
                    "headless": False,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                if channel:
                    kwargs["channel"] = channel
                self._browser = self._pw.chromium.launch(**kwargs)
                log.info("browser up (channel=%s, headful)", channel or "chromium")
                break
            except Exception as exc:
                last_exc = exc
                continue
        if self._browser is None:
            raise RuntimeError(f"could not launch a browser: {last_exc}")
        context = self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-IN",
        )
        self._page = context.new_page()
        # Akamai also fingerprints navigator.webdriver.
        self._page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

    def close(self) -> None:
        for closer in (lambda: self._browser.close(), lambda: self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self._page = self._browser = self._pw = None

    def _navigate(self, url: str, wait_selector: str | None = None):
        self._ensure_browser()
        gap = random.uniform(2.0, 4.0) - (time.monotonic() - self._last_nav)
        if gap > 0:
            time.sleep(gap)
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        self._last_nav = time.monotonic()
        if wait_selector:
            # Deterministic: cards either render or we treat it as a block.
            self._page.wait_for_selector(wait_selector, timeout=20_000)
        self._page.wait_for_timeout(random.randint(1500, 3000))
        # Naukri lazy-loads cards; scroll a couple of screens to trigger it.
        for _ in range(3):
            self._page.mouse.wheel(0, 2500)
            self._page.wait_for_timeout(random.randint(400, 800))
        return resp

    # -- adapter API --------------------------------------------------------

    def fetch_cards(self, search: dict) -> list[JobPosting]:
        slug = slugify(search["keywords"])
        location = (search.get("location") or "").strip()
        city = ""
        if location and location.lower() not in ("india", "remote", "anywhere"):
            city = location.split(",")[0].strip()
        url = SEARCH_URL.format(slug=slug)
        if city:
            url += f"-in-{slugify(city)}"
        if search.get("experience"):
            # 1 = jobs that accept 0-1 yrs starters, 2 = 1-3 yrs, ... (verified live)
            url += f"?experience={int(search['experience'])}"
        resp = self._navigate(url, wait_selector="div.srp-jobtuple-wrapper[data-job-id]")
        if resp is not None and resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} from {url}")
        html = self._page.content()
        cards = parse_cards(html, search["keywords"])
        if not cards:
            raise RuntimeError(f"0 cards parsed from {url} (selector drift or bot wall)")
        return cards

    def fetch_detail(self, job: JobPosting) -> str:
        if not job.apply_url:
            return ""
        self._navigate(job.apply_url)
        html = self._page.content()
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for selector in ("div[class*='JDC'][class*='description']",
                         "div.styles_JDC__dsc-description__", "div.jd", "section[class*='job-desc']"):
            el = soup.select_one(selector)
            if el:
                return el.get_text("\n", strip=True)
        # Cards are rich enough that a missing JD block is not an error —
        # return "" and the pipeline keeps the card-based score.
        return ""
