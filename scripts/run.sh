#!/bin/bash
# hkreport launchd wrapper — fires every Friday at the configured hour via launchd,
# but only proceeds if today is on the biweekly anchor AND the gap since the
# previous issue is >= MIN_GAP_DAYS.
#
# All device/account-specific values come from ~/.config/hkreport/env.
# This script self-locates its repo via $BASH_SOURCE — no hardcoded paths.

set -uo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO="$( cd "${SCRIPT_DIR}/.." && pwd )"
ENV_FILE="${HOME}/.config/hkreport/env"
LOG_DIR="${HOME}/Library/Logs"
LOG_FILE="${LOG_DIR}/hkreport.log"

mkdir -p "${LOG_DIR}"
exec >> "${LOG_FILE}" 2>&1

echo
echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
echo "REPO=${REPO}"

if [ ! -f "${ENV_FILE}" ]; then
  echo "FATAL: ${ENV_FILE} missing — run ./install.sh first"; exit 1
fi
# shellcheck disable=SC1090
source "${ENV_FILE}"

: "${ANCHOR_DATE:?ANCHOR_DATE missing from env}"
: "${SURGE_DOMAIN:?SURGE_DOMAIN missing from env}"
MIN_GAP_DAYS="${MIN_GAP_DAYS:-10}"

cd "${REPO}" || { echo "FATAL: cannot cd ${REPO}"; exit 1; }

# --- Gate 1: biweekly anchor ---
today=$(date '+%Y-%m-%d')
anchor_epoch=$(date -j -f '%Y-%m-%d' "${ANCHOR_DATE}" '+%s')
today_epoch=$(date -j -f '%Y-%m-%d' "${today}"        '+%s')
days_since_anchor=$(( (today_epoch - anchor_epoch) / 86400 ))

if (( days_since_anchor < 0 )); then
  echo "SKIP: before anchor (${ANCHOR_DATE}); days=${days_since_anchor}"
  exit 0
fi
if (( days_since_anchor % 14 != 0 )); then
  echo "SKIP: off-cycle; days_since_anchor=${days_since_anchor} (not multiple of 14)"
  exit 0
fi

# --- Gate 2: min-gap since last issue ---
PY="${REPO}/.venv/bin/python"
[ -x "${PY}" ] || PY=/usr/bin/python3

if [ -f "reports/baseline.json" ]; then
  prev_end=$("${PY}" -c '
import json, re
b = json.load(open("reports/baseline.json"))
m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})-(?:(\d{4})\.)?(\d{2})\.(\d{2})", b["period"])
y1,m1,d1,y2,m2,d2 = m.groups()
print(f"{y2 or y1}-{m2}-{d2}")
')
  prev_epoch=$(date -j -f '%Y-%m-%d' "${prev_end}" '+%s')
  gap_days=$(( (today_epoch - prev_epoch) / 86400 ))
  echo "Prev issue ended ${prev_end}; gap=${gap_days} days"
  if (( gap_days < MIN_GAP_DAYS )); then
    echo "SKIP: gap too small (${gap_days} < ${MIN_GAP_DAYS})"
    exit 0
  fi
fi

# --- Run pipeline ---
echo "RUN: generate.py"
"${PY}" scripts/generate.py
gen_rc=$?
if (( gen_rc != 0 )); then
  echo "FATAL: generate.py exit ${gen_rc}"; exit ${gen_rc}
fi

echo "RUN: deploy.py"
"${PY}" scripts/deploy.py
dep_rc=$?
if (( dep_rc != 0 )); then
  echo "FATAL: deploy.py exit ${dep_rc}"; exit ${dep_rc}
fi

echo "DONE: $(date '+%Y-%m-%d %H:%M:%S %Z')"
