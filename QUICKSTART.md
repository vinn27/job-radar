# 🚀 Quickstart — run Job Radar on your own machine

You need: Python 3.12, git, a Gmail account (with 2FA), and 20 minutes.
Nothing personal is stored in this repo — you generate your own config and
passwords locally in the steps below.

## 1. Get the code

```bash
git clone https://github.com/vinn27/job-radar.git
cd job-radar
pip install -r requirements.txt
```

## 2. Make it YOURS — config.json

```bash
copy config.example.json config.json      # (mac/linux: cp)
```

Open `config.json` and change:

- `"email": { "to": ... }` → your Gmail address
- `"searches"` → your keywords and cities (tip: `sql developer` and
  `junior <your role>` are gold for 0–2 yrs experience)
- `"profile": { "skills": ... }` → YOUR tech stack with weights
  (12 = strongest skill ... 4 = nice-to-have; mark `must: true` only for
  skills every job you want absolutely requires)
- `"profile": { "hard_max_required_years" }` → your experience ceiling
- `"freshness": { "max_posted_age_days" }` → how old a posting may be

💡 **Shortcut if you use Claude Code:** paste your resume into Claude and ask:
*"Rewrite the profile and searches sections of this config.json for my resume."*

## 3. Email password — .env

1. Google Account → Security → turn on **2-Step Verification**
2. Security → **App passwords** → create one (16 characters)
3. Copy `.env.example` to `.env` and fill:

```
JOB_RADAR_SMTP_USER=yourgmail@gmail.com
JOB_RADAR_SMTP_PASSWORD=your16charappassword
```

## 4. Test offline first (no scraping, no email)

```bash
python main.py seed-test        # dedupe + scoring + digest render
python main.py test-email       # one test email to your inbox
python main.py run --dry-run    # full real pipeline, sends nothing
python main.py run              # 🎉 first real digest
```

## 5. Naukri support (optional — needs a real browser)

```bash
pip install playwright
python -m playwright install chromium
python main.py probe --source naukri --query "data engineer"
```

A Chrome window will open for ~30 seconds per run — that's normal (Naukri
blocks invisible browsers). If it says `0 cards` or 403, Naukri changed
something — check `jobradar/sources/naukri.py` selectors.

## 6. Schedule it (pick any)

**A) Your PC — both boards:**

```powershell
$action   = New-ScheduledTaskAction -Execute "C:\Windows\py.exe" -Argument '-3.12 "C:\path\to\job-radar\main.py" run' -WorkingDirectory "C:\path\to\job-radar"
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10) -RepetitionInterval (New-TimeSpan -Hours 2)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "Job Radar" -Action $action -Trigger $trigger -Settings $settings
```

**B) Free cloud — LinkedIn 24/7 even with your PC off:**
fork this repo → in your fork: Settings → Secrets and variables → Actions →
add `JOB_RADAR_SMTP_USER` and `JOB_RADAR_SMTP_PASSWORD` → Actions tab →
enable the `jobradar` workflow. It commits its dedupe DB back to your fork
after each run — keep the fork **private** (Settings → General → Danger Zone).

## 7. Where things live

- Your jobs diary: `data/jobradar.db` (SQLite — delete it to reset memory)
- Logs: `logs/jobradar.log`
- See what it has seen: `python main.py backlog --days 7`

Stops: delete the scheduled task (`Unregister-ScheduledTask`) and disable the
workflow in your fork. That's it — enjoy the inbox.
