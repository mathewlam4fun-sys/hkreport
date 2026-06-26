#!/usr/bin/env python3
"""
Non-interactive Surge deployment wrapper.

Reads SURGE_LOGIN + SURGE_TOKEN + SURGE_DOMAIN from env.
Uploads the reports/ directory to the configured domain.

Usage:
    python scripts/deploy.py                # actually deploy
    python scripts/deploy.py --dry-run      # print command, don't execute
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not REPORTS_DIR.is_dir():
        print(f"reports/ not found at {REPORTS_DIR}", file=sys.stderr)
        sys.exit(2)

    login = os.environ.get("SURGE_LOGIN", "").strip()
    token = os.environ.get("SURGE_TOKEN", "").strip()
    domain = os.environ.get("SURGE_DOMAIN", "").strip()
    if not login or not token:
        print("SURGE_LOGIN and SURGE_TOKEN must be set", file=sys.stderr)
        sys.exit(2)
    if not domain:
        print("SURGE_DOMAIN must be set (e.g. myreport.surge.sh)", file=sys.stderr)
        sys.exit(2)

    npx = shutil.which("npx")
    if not npx:
        print("npx not found on PATH — install Node.js", file=sys.stderr)
        sys.exit(2)

    cmd = [npx, "--yes", "surge", str(REPORTS_DIR), domain]
    print(f"$ {' '.join(cmd)}")

    if args.dry_run:
        print("(dry-run; not executing)")
        return

    env = {**os.environ, "SURGE_LOGIN": login, "SURGE_TOKEN": token}
    result = subprocess.run(cmd, env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
