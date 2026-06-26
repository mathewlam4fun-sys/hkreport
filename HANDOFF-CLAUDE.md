# HANDOFF-CLAUDE.md — Claude Code 自動安裝指引

> 這份文件給 **Claude Code** 看。
> 如果你是使用者，把以下這段話貼到 Claude Code 對話框：
>
> ```
> 幫我從 https://github.com/<OWNER>/hkreport 拉取並按 HANDOFF-CLAUDE.md 安裝到我的 Mac
> ```
>
> 之後跟著 Claude 的提問逐項回答即可。

---

## 給 Claude 的執行劇本

你正在協助使用者安裝 hkreport 雙週競品快報自動發佈服務。**全程用繁體中文回應**。

### Phase 0 — 預檢

1. 用 Bash 檢查以下命令是否齊全：`python3`、`npx`、`curl`、`launchctl`、`sed`、`git`。
2. 缺 `npx` 通常是缺 Node.js → 引導使用者跑 `brew install node`；缺 `brew` 先裝 Homebrew。
3. 確認 macOS（`uname -s` = Darwin），其他平台暫不支援。

### Phase 1 — 拉取代碼

```bash
mkdir -p ~/workspace
cd ~/workspace
[ -d hkreport ] && { echo "已存在 ~/workspace/hkreport，請與使用者確認是否覆蓋"; exit 1; }
git clone https://github.com/<OWNER>/hkreport.git
cd hkreport
```

把 `<OWNER>` 替換為實際的 GitHub owner（從使用者貼的 URL 中取）。

### Phase 2 — 蒐集 9 個配置值

用 `AskUserQuestion` 一個一個問，**不要一次問完**（避免使用者填錯一項要整批重來）。每個值蒐集到後立刻保存到 shell 環境變數備用。

| # | 變數 | 提問內容 | 取得來源 |
|---|------|---------|---------|
| 1 | `ANTHROPIC_API_KEY` | "請貼上你的 Anthropic API Key" | 公司 AI 平台分配的 user-key（找 IT/AI 平台組要）或 anthropic.com sk-ant- |
| 2 | `ANTHROPIC_BASE_URL` | "Anthropic Base URL？默認走 Futu 代理" | 默認 `https://llm-proxy.futuoa.com/aws`，非 Futu 留空 |
| 3 | `TAVILY_API_KEY` | "請貼上你的 Tavily key" | https://app.tavily.com 免費註冊，30 秒 |
| 4 | `FIRECRAWL_API_KEY` | "請貼上你的 Firecrawl key" | https://firecrawl.dev 免費註冊 |
| 5 | `SURGE_LOGIN` | "Surge 註冊郵箱" | 沒帳號則先跑 `npx surge login` 引導註冊 |
| 6 | `SURGE_TOKEN` | "Surge token" | 跑 `npx surge token` 取得 |
| 7 | `SURGE_DOMAIN` | "Surge 域名（例：你名字-report.surge.sh）" | 任何 *.surge.sh 都可，先到先得 |
| 8 | `ANCHOR_DATE` | "首期錨點日期（YYYY-MM-DD，必須是週五）" | 默認用下一個週五；用 python3 算 |
| 9 | `WEEKDAY/HOUR/MINUTE` | "排期？默認週五 09:00" | 多數情況保持默認 |

**注意**：
- 密鑰值**不要在訊息中重複回顯**（避免聊天記錄洩漏）。Claude 在記住 env 變數時不要 echo。
- Tavily/Firecrawl 沒有帳號的使用者，**先暫停安裝**，引導她到對應網站註冊後再回來。
- Surge 註冊用 `npx surge login`（首次輸入 email + 自設密碼即註冊）。

預計算下一個週五的 Python 片段：
```python
from datetime import date, timedelta
today = date.today()
delta = (5 - today.isoweekday()) % 7
delta = 7 if delta == 0 else delta
print((today + timedelta(days=delta)).isoformat())
```

### Phase 3 — 跑非互動 install.sh

把蒐集到的 9 個值組成一個 env 字典，跑：

```bash
HKREPORT_INSTALL_NONINTERACTIVE=1 \
ANTHROPIC_API_KEY="..." \
ANTHROPIC_BASE_URL="..." \
TAVILY_API_KEY="..." \
FIRECRAWL_API_KEY="..." \
SURGE_LOGIN="..." \
SURGE_TOKEN="..." \
SURGE_DOMAIN="..." \
WEEKDAY="5" HOUR="9" MINUTE="0" \
ANCHOR_DATE="YYYY-MM-DD" \
MIN_GAP_DAYS="10" \
bash install.sh
```

預期輸出最後一行是 `✓ 安裝完成`，跟著排期 / 域名 / 日誌位置。

### Phase 4 — 驗證 launchd gate

```bash
launchctl kickstart -k gui/$(id -u) com.$(whoami).hkreport
sleep 2
tail -n 20 ~/Library/Logs/hkreport.log
```

預期日誌結尾出現：
```
SKIP: before anchor (<ANCHOR_DATE>); days=-N
```

看到 `SKIP:` 就代表 wrapper、env、路徑都通了。**這一步只證 plumbing OK，不證 API key 對**。

### Phase 5 — 試跑一次完整 pipeline（會花錢 + 真實發佈）

**先問使用者**："要不要現在試跑一次完整 pipeline 驗證 API key 與 Surge 部署？會花約 US$0.5、產出 #001 並發佈到你的域名。"

如果同意：
```bash
source ~/.config/hkreport/env
cd ~/workspace/hkreport
.venv/bin/python scripts/generate.py
.venv/bin/python scripts/deploy.py
```

部署完跑 curl 驗證：
```bash
curl -sI "https://${SURGE_DOMAIN}/" | head -3
curl -s  "https://${SURGE_DOMAIN}/" | grep -o "快報 #[0-9]*"
```

看到 `200 OK` + `快報 #001` 即成功。

### Phase 6 — 回報完成

最終訊息給使用者，列出：
- **域名**：`https://<SURGE_DOMAIN>/`
- **首期自動觸發**：`<ANCHOR_DATE> 09:00`，之後每 14 天
- **日誌**：`~/Library/Logs/hkreport.log`
- **暫停**：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.$(whoami).hkreport.plist`
- **恢復**：`launchctl bootstrap gui/$(id -u) ...`
- **卸載**：`cd ~/workspace/hkreport && bash uninstall.sh`

提醒：**Mac 關機時錯過的觸發，會在下次開機後補跑**（仍受雙週閘門控制）。

---

## 錯誤排查

| 症狀 | 原因 | 修法 |
|------|------|------|
| `FATAL: ANTHROPIC_API_KEY env var not set` | 你直接跑 generate.py 沒 source env | `source ~/.config/hkreport/env` |
| `surge` 命令一直卡住 | npx 首次下載 surge 包 | 等 30 秒，正常現象 |
| `plutil -lint` 失敗 | plist 模板渲染出錯 | 檢查 `templates/launchd.plist.template` 是否完整 |
| `launchctl bootstrap` 報 "already loaded" | 同名 plist 已註冊 | 先 `launchctl bootout` 再 bootstrap |
| Surge 部署成功但網站舊版 | 瀏覽器快取 | curl 驗證；瀏覽器無痕視窗 |
| 跑 generate.py 報 401 | API key 錯 / 走錯端點 | 檢查 `ANTHROPIC_API_KEY` 和 `ANTHROPIC_BASE_URL` 是否匹配 |

## 不要做的事

- 不要把使用者的 API key 寫進任何 commit 到 repo 的檔案
- 不要在訊息中明文重複使用者剛貼的密鑰值
- 不要跳過 Phase 4 直接跑 Phase 5（先確認 plumbing 再花錢）
- 不要主動 `git push` 任何東西（這是 read-only 拉取場景）
