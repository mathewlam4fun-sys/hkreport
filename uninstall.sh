#!/usr/bin/env bash
# hkreport — 卸載腳本：移除 launchd 任務，可選擇刪除 env / venv / 已部署站點。
# 不會自動刪除 reports/ 內容。

set -uo pipefail

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'

USER_NAME="$(whoami)"
PLIST_LABEL="com.${USER_NAME}.hkreport"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
ENV_FILE="$HOME/.config/hkreport/env"
REPO_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

confirm() {
  local prompt="$1" reply
  read -rp "  $prompt [y/N]: " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

echo "${BOLD}hkreport 卸載${RESET}"
echo

# 1. launchd
if [ -f "$PLIST_PATH" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
  if confirm "刪除 $PLIST_PATH？"; then
    rm "$PLIST_PATH"
    echo "  ${GREEN}✓${RESET} 已刪除 plist"
  else
    echo "  ${YELLOW}!${RESET} 保留 plist（已 bootout，不會再觸發）"
  fi
else
  echo "  ${DIM}沒找到 $PLIST_PATH，跳過${RESET}"
fi

# 2. env
if [ -f "$ENV_FILE" ]; then
  if confirm "刪除 $ENV_FILE（含所有 API 密鑰）？"; then
    rm "$ENV_FILE"
    echo "  ${GREEN}✓${RESET} 已刪除 env"
  fi
fi

# 3. venv
if [ -d "${REPO_ROOT}/.venv" ]; then
  if confirm "刪除 ${REPO_ROOT}/.venv？"; then
    rm -rf "${REPO_ROOT}/.venv"
    echo "  ${GREEN}✓${RESET} 已刪除 venv"
  fi
fi

# 4. Surge 站點
SURGE_DOMAIN="${SURGE_DOMAIN:-}"
if [ -z "$SURGE_DOMAIN" ] && [ -f "${REPO_ROOT}/reports/index.html" ]; then
  # 嘗試從歷史 env 備份猜
  echo
  echo "${DIM}  若要 teardown 已部署的 Surge 站點，請手動執行：${RESET}"
  echo "${DIM}    npx surge teardown YOUR_DOMAIN.surge.sh${RESET}"
fi

echo
echo "${BOLD}${GREEN}卸載完成${RESET}"
echo "${DIM}reports/ 與 snapshots/ 內容未動，可手動 rm -rf 清除${RESET}"
