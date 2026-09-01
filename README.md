# 📡 Job Radar

A self-running job-hunt assistant that checks **LinkedIn + Naukri every 2 hours**, matches new
openings against my profile, and emails a scored digest with one-click apply links —
with zero manual effort and zero repeats.

No paid SaaS (LazyApply/LoopCV charge ₹2,000+/month for this). Pure Python, ₹0 infrastructure.

## What it does

```
look at job boards → drop junk → check the "seen" diary → score /100 → email digest
     (every 2h)        (old/senior/      (hard + fuzzy dedupe =      (best-first,
                        off-stack)         never the same job twice)   apply links)
```

Each digest email shows, per job:

```
[78] PySpark Data Engineer — Infosys — Pune — Today
     Matched: Data Engineer, SQL, Python, ETL, PySpark
     Recommended resume: DE-General
     Apply: naukri.com/job-listings-...
```

## Why the weird architecture (two runners)

| Runner | Board | Why |
|---|---|---|
| GitHub Actions cron (free) | LinkedIn | Guest endpoints work from datacenter IPs → runs 24/7, even with my PC off |
| Windows Task Scheduler on my PC | Naukri | Naukri's Akamai firewall blocks every headless browser and every cloud IP — only headful Chrome from a residential IP passes |

The two sides own different boards with separate SQLite dedupe databases, so a job can never
be emailed twice. The GitHub runner commits its DB back to the repo each run, so stateless
CI machines still remember everything.

## Features

- **Explainable scoring, no ML**: `100 × (matched skill weights / total weight)`, with
  gates for seniority, minimum experience, and excluded keywords. Every score can be
  traced by hand from the matched-skills chips in the email.
- **Three-layer no-repeat guarantee**: job-id dedupe across searches, fuzzy title+company
  dedupe for reposts, and an `emailed_at` ledger.
- **Freshness + junior filtering**: postings older than N days never emailed; LinkedIn
  `f_E=2` (entry level) + `f_TPR` (posted this week) applied at the source; Naukri
  `experience=1`; hard gate on JDs asking above my experience bracket.
- **Source-adapter pattern**: each board is one adapter class; a source failing is
  logged and skipped, never fatal.
- **Polite crawling**: jittered delays, retry with backoff, hard stop on 429.
- **Resume-variant recommendation**: scores each configured resume variant against the
  JD and picks the best fit.

## Stack

Python 3.12 · requests · BeautifulSoup4 · Playwright (Naukri only) · SQLite · smtplib ·
GitHub Actions · Windows Task Scheduler

## Run it yourself

```bash
git clone https://github.com/vinn27/job-radar.git
cd job-radar
pip install -r requirements.txt
copy config.example.json config.json    # edit searches, skills, weights, your email
python main.py seed-test                # offline check: dedupe + scoring + digest render
python main.py test-email               # needs a Gmail app password in .env (2FA)
python main.py run --dry-run            # full pipeline, no email sent
python main.py run                      # the real thing
```

For Naukri: `pip install playwright && playwright install chromium`.
For scheduling, see the README sections in the workflow file and `Register-ScheduledTask`
(PowerShell) — every 2 h with `-StartWhenAvailable` for catch-up after reboots.

## Project layout

```
main.py                  CLI: run | test-email | backlog | probe | seed-test
jobradar/
  config.py              config + .env loading, __file__-based paths (scheduler-safe)
  models.py              JobPosting / MatchResult, posted-age parsing
  http.py                polite session: throttle per host, retry, backoff
  db.py                  SQLite schema, two-layer dedupe, run log
  matcher.py             pure-function scoring + resume recommendation
  digest.py              HTML + plain-text email rendering (inline CSS for Gmail)
  mailer.py              Gmail SMTP with app-password failure guidance
  pipeline.py            one run, end to end
  sources/
    base.py              adapter contract — per-query failures never kill the run
    linkedin.py          guest search + job-detail endpoints (no login)
    naukri.py            Playwright, headful Chrome, webdriver-fingerprint masking
    adzuna.py            optional secondary source (free API key)
```

## Notes & limits

- LinkedIn endpoints are undocumented and can change; the adapter seam is where fixes go.
  Naukri likewise (selectors were mapped from the live DOM and may drift).
- Naukri fundamentally cannot run in the cloud — that's an Akamai constraint, not a code one.
- Scraping is read-only, logged-out, and rate-limited to stay polite. This tool reads public
  job listings only; it does not automate applications.
