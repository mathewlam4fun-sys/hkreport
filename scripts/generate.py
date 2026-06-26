#!/usr/bin/env python3
"""
End-to-end generator for one issue of the HK biweekly report.

Pipeline (mirrors the hk-report skill, headless):
  INIT     -> derive issue number + period from reports/baseline.json
  COLLECT  -> subprocess into scripts/collect.py
  CLASSIFY -> single Claude API call -> JSON list of signals
  COMPOSE  -> single Claude API call -> HTML section fragments
  RENDER   -> string-interpolate fragments into templates/template.html
  VERIFY   -> scripts/verify.py; on BLOCK failure, retry COMPOSE with feedback (max 2)
  PERSIST  -> write reports/NNN.html + reports/index.html + reports/baseline.json

Usage:
    python scripts/generate.py                       # produces next issue ending today
    python scripts/generate.py --period 2026.06.24-07.07
    python scripts/generate.py --dry-run             # no Claude calls; writes a stub
    python scripts/generate.py --skip-collect        # reuse most recent snapshots/
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"
SNAPSHOTS_DIR = REPO_ROOT / "snapshots"
TEMPLATES_DIR = REPO_ROOT / "templates"
PROMPTS_DIR = TEMPLATES_DIR / "prompts"

MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 2


# ---------- INIT ----------

def load_baseline() -> dict:
    return json.loads((REPORTS_DIR / "baseline.json").read_text(encoding="utf-8"))


def parse_period_end(period_str: str) -> tuple[date, date]:
    """Accept formats like '2026.06.24-07.07' or '2026.06.24-2026.07.07'."""
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})-(?:(\d{4})\.)?(\d{2})\.(\d{2})", period_str)
    if not m:
        raise ValueError(f"Bad period format: {period_str}")
    y1, m1, d1, y2, m2, d2 = m.groups()
    start = date(int(y1), int(m1), int(d1))
    end = date(int(y2 or y1), int(m2), int(d2))
    return start, end


def derive_next_period(baseline: dict) -> tuple[date, date]:
    """Start = last issue end + 1 day; end = today."""
    prev = baseline["period"]  # e.g., "2026.06.03-06.23"
    _, prev_end = parse_period_end(prev)
    start = prev_end + timedelta(days=1)
    end = date.today()
    return start, end


# ---------- COLLECT ----------

def run_collect(skip: bool):
    if skip:
        print("[COLLECT] --skip-collect; using existing snapshots/")
        return
    collect_script = REPO_ROOT / "scripts" / "collect.py"
    if not collect_script.exists():
        print("[COLLECT] scripts/collect.py missing; skipping", file=sys.stderr)
        return
    print(f"[COLLECT] invoking {collect_script}")
    subprocess.run(
        [sys.executable, str(collect_script)],
        check=False,  # never abort the workflow on collector hiccups
        cwd=REPO_ROOT,
    )


def aggregate_snapshots(start: date, end: date) -> dict:
    """Walk snapshots/{category}/{source}_DATE.json for files within the period."""
    bundle = {}
    if not SNAPSHOTS_DIR.exists():
        return bundle
    for cat_dir in SNAPSHOTS_DIR.iterdir():
        if not cat_dir.is_dir():
            continue
        bundle[cat_dir.name] = []
        for f in sorted(cat_dir.glob("*.json")):
            m = re.search(r"_(\d{4}-\d{2}-\d{2})", f.name)
            if not m:
                continue
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            if start <= d <= end:
                try:
                    bundle[cat_dir.name].append(json.loads(f.read_text(encoding="utf-8")))
                except Exception as e:
                    print(f"[COLLECT] skip {f.name}: {e}", file=sys.stderr)
    return bundle


# ---------- CLASSIFY + COMPOSE (Claude API) ----------

def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def claude_call(system: str, user: str, json_mode: bool = False) -> str:
    """Single Claude API call. Imports anthropic lazily so --dry-run works without it."""
    try:
        import anthropic
    except ImportError:
        print("anthropic SDK not installed; pip install -r requirements.txt", file=sys.stderr)
        sys.exit(2)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ANTHROPIC_API_KEY env var not set", file=sys.stderr)
        sys.exit(2)

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip() or None
    client = anthropic.Anthropic(api_key=api_key, base_url=base_url) if base_url \
        else anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text
    if json_mode:
        # Best-effort strip of ```json fences
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    return text


def classify_signals(bundle: dict, baseline: dict, start: date, end: date) -> list[dict]:
    system = load_prompt("classify.md")
    user = json.dumps({
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "prev_issue": baseline.get("issue"),
        "carry_forward_candidates": baseline.get("unresolved_signals", []),
        "baseline_issues": baseline.get("baseline_issues", []),
        "appstore_baseline": baseline.get("appstore_snapshot", {}),
        "rates_baseline": baseline.get("rates", {}),
        "bundle": bundle,
    }, ensure_ascii=False, indent=2)
    raw = claude_call(system, user, json_mode=True)
    return json.loads(raw)


def compose_sections(signals: list[dict], baseline: dict, bundle: dict,
                     start: date, end: date, prev_html: str,
                     fixup_feedback: str | None = None) -> dict:
    system = load_prompt("compose.md")
    payload = {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "issue_number": baseline["issue"] + 1,
        "signals": signals,
        "baseline": baseline,
        "bundle_summary": {
            k: f"{len(v)} snapshot(s)" if isinstance(v, list) else "n/a"
            for k, v in bundle.items()
        },
    }
    user = json.dumps(payload, ensure_ascii=False, indent=2)
    if fixup_feedback:
        user += f"\n\nPREVIOUS ATTEMPT FAILED QUALITY CHECKS. Fix these violations and re-emit the full JSON:\n{fixup_feedback}"
    raw = claude_call(system, user, json_mode=True)
    return json.loads(raw)


# ---------- RENDER ----------

def build_issue_switcher(current: int) -> str:
    issues = sorted(int(f.stem) for f in REPORTS_DIR.glob("[0-9][0-9][0-9].html"))
    if current not in issues:
        issues.append(current)
        issues.sort()
    parts = ['<div class="issue-switcher">', '  <span class="label">Issues</span>']
    for n in issues:
        active = ' class="active"' if n == current else ""
        # Period strings live inside each file's header; for the switcher we use a short stub.
        parts.append(f'  <a href="{n:03d}.html"{active}>#{n:03d}</a>')
    parts.append('  <a href="setup.html" class="setup-cta">🛠 自己跑一份 →</a>')
    parts.append("</div>")
    return "\n".join(parts)


def render_html(sections: dict, signals: list[dict], baseline: dict,
                issue_number: int, start: date, end: date) -> str:
    template = (TEMPLATES_DIR / "template.html").read_text(encoding="utf-8")
    red_n = sum(1 for s in signals if s["severity"] == "RED")
    yellow_n = sum(1 for s in signals if s["severity"] == "YELLOW")
    green_n = sum(1 for s in signals if s["severity"] == "GREEN")

    # The Claude compose call returns ready-to-paste HTML fragments keyed by section.
    # We substitute them into the template's commented placeholders.
    substitutions = {
        "{{TITLE}}": f"富途HK行業快報 #{issue_number:03d} | {start.strftime('%Y.%m.%d')} — {end.strftime('%m.%d')}",
        "{{REPORT_TITLE}}": f"富途 HK 行業快報 #{issue_number:03d}",
        "{{SUBTITLE}}": f"統計周期：{start.strftime('%Y.%m.%d')} — {end.strftime('%m.%d')} | 基於周期內客觀事實 + 業務思考",
        "{{META_SCOPE}}": sections.get("meta_scope", "競品範圍：14 家"),
        "{{META_SOURCES}}": sections.get("meta_sources", "數據源：SFC / HKMA / HKEX / 公司公告 / App Store / LIHKG / Tavily"),
        "{{META_DELTA}}": sections.get("meta_delta", f"vs #{baseline['issue']:03d} 變動：見下方信號"),
        "{{RED_COUNT}}": str(red_n),
        "{{YELLOW_COUNT}}": str(yellow_n),
        "{{GREEN_COUNT}}": str(green_n),
        "{{ISSUE_SWITCHER}}": build_issue_switcher(issue_number),
        "{{NAV_TITLE}}": f"#{issue_number:03d} 導覽",
        "{{DESKTOP_NAV_LINKS}}": sections.get("nav_html", ""),
        "{{MOBILE_NAV_LINKS}}": sections.get("nav_html_mobile", ""),
        "{{RED_SIGNAL_CARDS}}": sections.get("red_cards_html", ""),
        "{{YELLOW_SIGNAL_CARDS}}": sections.get("yellow_cards_html", ""),
        "{{GREEN_ROWS}}": sections.get("green_rows_html", ""),
        "{{DISCLAIMER}}": f"試刊 #{issue_number:03d} | 統計周期 {start.strftime('%Y.%m.%d')} — {end.strftime('%m.%d')} | 信號分級基於周期內公開信息<br>業務影響評估為 PM 視角初判，建議結合內部數據交叉驗證後做行動決策",
    }
    for placeholder, content in substitutions.items():
        template = template.replace(placeholder, content)

    # Inject full pre-composed sections (App Store / Feature / Review / etc.) verbatim
    for key in ("appdata", "feature", "review", "keywords", "social", "lihkg",
                "futu", "rates", "preview"):
        html_block = sections.get(f"{key}_section_html")
        if html_block:
            # Replace the entire <div class="section" id="sec-KEY">…</div>
            template = re.sub(
                rf'<div class="section" id="sec-{key}">.*?</div>\s*(?=<!--|<div class="section"|<div class="disclaimer")',
                html_block + "\n",
                template,
                count=1,
                flags=re.DOTALL,
            )

    # Replace the issue switcher block (the template has a commented stub)
    template = re.sub(
        r'<div class="issue-switcher">.*?</div>',
        substitutions["{{ISSUE_SWITCHER}}"],
        template,
        count=1,
        flags=re.DOTALL,
    )

    return template


# ---------- VERIFY ----------

def run_verify(html_path: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify.py"),
         str(html_path), "--json"],
        capture_output=True, text=True,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"blockers": [{"check": "?", "detail": result.stdout or result.stderr}], "warnings": []}


# ---------- PERSIST ----------

def update_index(latest: int):
    (REPORTS_DIR / "index.html").write_text(
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        f'<meta http-equiv="refresh" content="0;url={latest:03d}.html">'
        '<title>Redirecting...</title></head><body>'
        f'<p>Redirecting to <a href="{latest:03d}.html">#{latest:03d}</a>...</p>'
        '</body></html>\n',
        encoding="utf-8",
    )


def update_baseline(baseline: dict, signals: list[dict], start: date, end: date,
                    issue_number: int):
    baseline["issue"] = issue_number
    baseline["prev_period"] = baseline["period"]
    baseline["period"] = f"{start.strftime('%Y.%m.%d')}-{end.strftime('%m.%d')}"
    baseline["mode"] = "auto"
    # Re-derive unresolved signals from this issue's RED/YELLOW
    baseline["unresolved_signals"] = [
        {
            "id": s.get("id") or f"S{i+1}",
            "title": s["title"],
            "severity": s["severity"],
            "status": s.get("status", "monitoring"),
            "first_reported": s.get("carry_from") or f"#{issue_number:03d}",
            "next_check": s.get("next_check", ""),
        }
        for i, s in enumerate(signals)
        if s["severity"] in ("RED", "YELLOW")
    ]
    baseline["new_this_issue"] = [s["title"] for s in signals if not s.get("carry_from")]
    (REPORTS_DIR / "baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------- MAIN ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", help="YYYY.MM.DD-MM.DD; otherwise prev_end+1d to today")
    ap.add_argument("--dry-run", action="store_true", help="no Claude calls; writes stub")
    ap.add_argument("--skip-collect", action="store_true")
    args = ap.parse_args()

    baseline = load_baseline()
    if args.period:
        start, end = parse_period_end(args.period)
    else:
        start, end = derive_next_period(baseline)
    issue_number = baseline["issue"] + 1
    print(f"[INIT] Issue #{issue_number:03d} | {start} -> {end}")

    run_collect(skip=args.skip_collect)
    bundle = aggregate_snapshots(start, end)
    print(f"[COLLECT] bundle: {sum(len(v) for v in bundle.values())} snapshots across "
          f"{len(bundle)} categories")

    if args.dry_run:
        stub_path = REPORTS_DIR / f"{issue_number:03d}-draft.html"
        stub_path.write_text(
            f"<!DOCTYPE html><html><body><h1>DRY RUN #{issue_number:03d}</h1>"
            f"<p>Period {start} – {end}; {sum(len(v) for v in bundle.values())} snapshots.</p>"
            "</body></html>",
            encoding="utf-8",
        )
        print(f"[DRY-RUN] wrote {stub_path}")
        return

    prev_html_path = REPORTS_DIR / f"{baseline['issue']:03d}.html"
    prev_html = prev_html_path.read_text(encoding="utf-8") if prev_html_path.exists() else ""

    print("[CLASSIFY] calling Claude API")
    signals = classify_signals(bundle, baseline, start, end)
    print(f"[CLASSIFY] {sum(1 for s in signals if s['severity']=='RED')} RED, "
          f"{sum(1 for s in signals if s['severity']=='YELLOW')} YELLOW, "
          f"{sum(1 for s in signals if s['severity']=='GREEN')} GREEN")

    print("[COMPOSE] calling Claude API")
    fixup = None
    final_html_path = REPORTS_DIR / f"{issue_number:03d}.html"
    for attempt in range(MAX_RETRIES + 1):
        sections = compose_sections(signals, baseline, bundle, start, end, prev_html, fixup)
        html = render_html(sections, signals, baseline, issue_number, start, end)
        final_html_path.write_text(html, encoding="utf-8")
        print(f"[RENDER] wrote {final_html_path}")

        verify_result = run_verify(final_html_path)
        if not verify_result["blockers"]:
            print(f"[VERIFY] PASS ({len(verify_result['warnings'])} warnings)")
            break
        print(f"[VERIFY] {len(verify_result['blockers'])} blockers on attempt {attempt+1}")
        if attempt == MAX_RETRIES:
            print("[VERIFY] giving up; HTML left in place for manual review", file=sys.stderr)
            sys.exit(3)
        fixup = "\n".join(f"- [{b['check']}] {b['detail']}" for b in verify_result["blockers"])

    update_index(issue_number)
    update_baseline(baseline, signals, start, end, issue_number)
    print(f"[PERSIST] index + baseline updated. Issue #{issue_number:03d} ready for deploy.")


if __name__ == "__main__":
    main()
