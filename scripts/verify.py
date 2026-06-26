#!/usr/bin/env python3
"""
Quality checks for a generated HK report HTML.

Six checks, three BLOCK + three WARN. Exits non-zero if any BLOCK fails.

Usage:
    python verify.py path/to/NNN.html
    python verify.py path/to/NNN.html --json     # machine-readable output for retry loop
"""
import argparse
import json
import re
import sys
from pathlib import Path


def section_html(html: str, section_id: str) -> str:
    m = re.search(
        rf'id="{section_id}".*?(?=<div class="section" id="|<div class="disclaimer">)',
        html, re.DOTALL,
    )
    return m.group() if m else ""


def parse_period(html: str):
    m = re.search(r"統計周期：(\d{4})\.(\d{2})\.(\d{2})\s*[—\-]\s*(\d{2})\.(\d{2})", html)
    if not m:
        return None
    y, m1, d1, m2, d2 = map(int, m.groups())
    return (y, m1, d1, m2, d2)


def check_period_compliance(html, violations):
    period = parse_period(html)
    if not period:
        violations.append(("BLOCK", "Check 1", "Period not found in header subtitle"))
        return
    y, m1, d1, m2, d2 = period
    sig_html = section_html(html, "sec-red") + "\n" + section_html(html, "sec-yellow")
    dates = re.findall(r"<strong>(\d{1,2})/(\d{1,2})(?:[–—\-](\d{1,2}))?</strong>", sig_html)
    bad = []
    for tup in dates:
        m, d = int(tup[0]), int(tup[1])
        in_period = False
        if m1 == m2:
            in_period = m == m1 and d1 <= d <= d2
        else:
            in_period = (m == m1 and d >= d1) or (m == m2 and d <= d2)
        if not in_period:
            bad.append(f"{m}/{d}")
    if bad:
        violations.append((
            "BLOCK", "Check 1",
            f"{len(bad)} out-of-period dated facts in signal cards: {', '.join(bad)}",
        ))


def check_source_links(html, violations):
    # RED/YELLOW: 100% required
    cards = re.findall(
        r'<div class="signal-card (?:red|yellow)">.*?(?=<div class="signal-card|<!-- ===== GREEN)',
        html, re.DOTALL,
    )
    total, with_src = 0, 0
    for card in cards:
        fact = re.search(r"fact-content.*?(?=think-label|$)", card, re.DOTALL)
        if not fact:
            continue
        for li in re.findall(r"<li>.*?</li>", fact.group(), re.DOTALL):
            total += 1
            if 'class="src"' in li:
                with_src += 1
    if total and with_src < total:
        violations.append((
            "BLOCK", "Check 2",
            f"RED/YELLOW source coverage {with_src}/{total} ({with_src/total*100:.1f}%) — must be 100%",
        ))

    # GREEN: 90%+
    g = section_html(html, "sec-green")
    tbody = re.search(r"<tbody>(.*?)</tbody>", g, re.DOTALL)
    if tbody:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbody.group(1), re.DOTALL)
        if rows:
            with_src_g = sum(1 for r in rows if 'class="src"' in r)
            pct = with_src_g / len(rows) * 100
            if pct < 90:
                violations.append((
                    "WARN", "Check 2b",
                    f"GREEN source coverage {with_src_g}/{len(rows)} ({pct:.1f}%) — target 90%+",
                ))


def check_branding(html, violations):
    text_only = re.sub(r'<a[^>]*href="[^"]*"[^>]*>[^<]*</a>', "", html)
    text_only = re.sub(r'href="[^"]*"', "", text_only)
    text_only = re.sub(r"<script.*?</script>", "", text_only, flags=re.DOTALL)
    count = text_only.lower().count("moomoo")
    # Allowed in .think-content quote boxes when quoting; we don't try to parse those.
    # Tolerance of 0 in non-link/non-script text.
    if count > 0:
        violations.append((
            "BLOCK", "Check 3",
            f"'moomoo' appears {count} times in visible text — replace with '富途牛牛' or 'Futu'",
        ))


def count_tbody_rows(section_html_s: str) -> list[int]:
    return [
        len(re.findall(r"<tr[^>]*>", t))
        for t in re.findall(r"<tbody>(.*?)</tbody>", section_html_s, re.DOTALL)
    ]


def check_competitor_coverage(html, violations):
    appdata = section_html(html, "sec-appdata")
    feat = section_html(html, "sec-feature")
    app_rows = sum(count_tbody_rows(appdata))
    feat_rows = sum(count_tbody_rows(feat))
    is_no_change_app = "tag gray" in appdata and "no-change" in appdata
    is_no_change_feat = "tag gray" in feat and "no-change" in feat
    if not is_no_change_app and app_rows < 8:
        violations.append((
            "WARN", "Check 4",
            f"App Store table has {app_rows} rows — minimum 8 of 10 standard competitors",
        ))
    if not is_no_change_feat and feat_rows < 8:
        violations.append((
            "WARN", "Check 4",
            f"Feature Update table has {feat_rows} rows — minimum 8 of 10 standard competitors",
        ))


def check_signal_structure(html, violations):
    cards = re.findall(
        r'<div class="signal-card (?:red|yellow)">.*?(?=<div class="signal-card|<!-- ===== GREEN)',
        html, re.DOTALL,
    )
    nums = []
    for i, c in enumerate(cards):
        for label in ("fact-label", "fact-content", "think-label", "think-content"):
            if c.count(label) != 1:
                violations.append((
                    "BLOCK", "Check 5",
                    f"Card {i+1}: {label} count={c.count(label)} (expected 1)",
                ))
        n = re.search(r'<span class="num (?:red|yellow)">(\d+)</span>', c)
        if n:
            nums.append(int(n.group(1)))
    if nums and nums != list(range(1, len(nums) + 1)):
        violations.append((
            "BLOCK", "Check 5",
            f"Signal numbering not 1..N continuous: got {nums}",
        ))


def check_table_completeness(html, violations):
    thresholds = {
        "sec-rates": 4,         # minimum 4 brokers (Futu + 3 peers)
        "sec-keywords": 6,      # minimum top 6 cross-app keywords
        "sec-social": 8,        # minimum 8 of 11 competitors
        "sec-lihkg": 1,         # summary table at least 1 row
    }
    for sid, threshold in thresholds.items():
        s = section_html(html, sid)
        if "no-change" in s:
            continue
        rows = sum(count_tbody_rows(s))
        if rows < threshold:
            violations.append((
                "WARN", "Check 6",
                f"{sid} has {rows} rows — minimum {threshold}",
            ))


def run_checks(html_path: Path):
    html = html_path.read_text(encoding="utf-8")
    violations = []
    check_period_compliance(html, violations)
    check_source_links(html, violations)
    check_branding(html, violations)
    check_competitor_coverage(html, violations)
    check_signal_structure(html, violations)
    check_table_completeness(html, violations)
    return violations


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html_path", type=Path)
    ap.add_argument("--json", action="store_true", help="emit JSON for retry loop")
    args = ap.parse_args()

    violations = run_checks(args.html_path)
    blockers = [v for v in violations if v[0] == "BLOCK"]
    warns = [v for v in violations if v[0] == "WARN"]

    if args.json:
        print(json.dumps({
            "blockers": [{"check": c, "detail": d} for _, c, d in blockers],
            "warnings": [{"check": c, "detail": d} for _, c, d in warns],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"=== Quality Check Report: {args.html_path.name} ===\n")
        if not violations:
            print("All 6 checks PASS.")
        for sev, check, detail in violations:
            print(f"[{sev}] {check}: {detail}")
        print(f"\n{len(blockers)} BLOCK, {len(warns)} WARN")

    sys.exit(1 if blockers else 0)


if __name__ == "__main__":
    main()
