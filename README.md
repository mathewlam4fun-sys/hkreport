# hkreport — Auto-published HK biweekly competitive-intel report

Auto-generates the 富途牛牛 HK 行業快報 every 1st and 15th of the month and deploys to https://hkreport.surge.sh.

The site is produced entirely by GitHub Actions — your laptop does not need to be on.

## Architecture

```
hkreport/
├── reports/                 # deployed verbatim to Surge
│   ├── 001.html … NNN.html
│   ├── index.html           # redirect to latest issue
│   └── baseline.json        # rolling state (issue#, period, unresolved signals, FY metrics)
├── snapshots/               # raw collector output, committed for traceability
├── scripts/
│   ├── collect.py           # multi-source data collector (App Store, SFC, HKMA, news, LIHKG, …)
│   ├── generate.py          # 7-phase pipeline: INIT → COLLECT → CLASSIFY → COMPOSE → RENDER → VERIFY → PERSIST
│   ├── verify.py            # 6 quality checks (3 BLOCK + 3 WARN); --json output drives the retry loop
│   └── deploy.py            # non-interactive `npx surge` wrapper
├── config/sources.yaml      # source map for collect.py
├── templates/
│   ├── template.html        # design system (CSS + JS, ~500 lines)
│   └── prompts/
│       ├── classify.md      # system prompt for Claude classify call
│       └── compose.md       # system prompt for Claude compose call
├── .github/workflows/biweekly.yml
└── requirements.txt
```

## Pipeline

`generate.py` runs these phases sequentially:

1. **INIT** — read `reports/baseline.json` → next issue = `prev+1`, period = `prev_end+1d` to today.
2. **COLLECT** — invoke `scripts/collect.py`; aggregate `snapshots/{category}/*_DATE.json` filtered to the period.
3. **CLASSIFY** — one Claude API call (Sonnet 4.6) with `templates/prompts/classify.md`. Output: ordered signal list as JSON.
4. **COMPOSE** — second Claude API call with `templates/prompts/compose.md`. Output: HTML section fragments as JSON.
5. **RENDER** — interpolate into `templates/template.html`.
6. **VERIFY** — run `scripts/verify.py --json`. On BLOCK failure, feed violations back to COMPOSE; max 2 retries.
7. **PERSIST** — write `reports/NNN.html`, rebuild `reports/index.html` redirect, update `reports/baseline.json`.

Then `deploy.py` runs `npx --yes surge reports/ hkreport.surge.sh` using `SURGE_LOGIN` + `SURGE_TOKEN`.

Per-issue API cost ≈ US$0.30–0.50 at Sonnet 4.6 pricing.

## Schedule

Cron `0 1 1,15 * *` — fires 01:00 UTC (09:00 HKT) on the 1st and 15th of each month.

GitHub Actions cron is stateless and can't express "every 14 days from a fixed anchor", so we use fixed monthly anchors instead. Close enough to biweekly.

Manual override: `gh workflow run biweekly.yml` (optionally pass `--field period=2026.07.01-07.15` to force a window).

## Required secrets

Set under repo Settings → Secrets and variables → Actions:

| Secret | Purpose | How to obtain |
|---|---|---|
| `ANTHROPIC_API_KEY` | classify + compose Claude calls | https://console.anthropic.com/settings/keys |
| `TAVILY_API_KEY` | news + social search | https://app.tavily.com/ (free tier OK) |
| `FIRECRAWL_API_KEY` | JS-rendered rate pages | https://firecrawl.dev/ |
| `SURGE_LOGIN` | Surge email | the email you signed up to Surge with |
| `SURGE_TOKEN` | Surge auth token | run `npx surge token` once locally |

`gh secret set ANTHROPIC_API_KEY` from your local terminal is the quickest way; repeat per secret.

## First-time setup

```bash
# 1. Install GitHub CLI if missing
brew install gh && gh auth login

# 2. Create the repo (in this directory)
cd ~/workspace/hkreport
git init -b main
git add .
git commit -m "initial: scaffold auto-publishing pipeline"
gh repo create hkreport --public --source=. --push

# 3. Register secrets
gh secret set ANTHROPIC_API_KEY
gh secret set TAVILY_API_KEY
gh secret set FIRECRAWL_API_KEY
gh secret set SURGE_LOGIN
gh secret set SURGE_TOKEN

# 4. Fire a test run
gh workflow run biweekly.yml
gh run watch
```

## Local development

```bash
# Dry-run (no Claude calls; writes a stub)
python scripts/generate.py --dry-run

# Verify an existing report passes the 6 checks
python scripts/verify.py reports/007.html

# Test the surge command without actually deploying
python scripts/deploy.py --dry-run
```

Set `ANTHROPIC_API_KEY` / `TAVILY_API_KEY` / `FIRECRAWL_API_KEY` in your shell to test the full pipeline locally.

## Recovery / rollback

Every successful run commits the generated `reports/NNN.html` + new snapshots back to `main`. If an issue is bad:

```bash
git revert <bad-commit>
git push
# Then redeploy the prior state
python scripts/deploy.py
```

## What's NOT in this repo

- The interactive `/hk-report` Claude Code skill remains on the author's laptop as a manual fallback.
- The local `~/competitive-intel/tracker/` launchd job is independent and untouched — it's daily personal intel, not the publish pipeline.
