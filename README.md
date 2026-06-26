# hkreport — 雙週競品快報自動發佈服務

由 macOS launchd 在本機定時觸發，自動生成富途 HK 行業競品快報並部署到 Surge。

## 安裝

- **用 Claude Code 安裝（推薦）**：把以下這行貼到你的 Claude Code 對話框，跟著 Claude 的提問填值即可：
  ```
  幫我從此 repo 拉取並按 HANDOFF-CLAUDE.md 安裝到我的 Mac
  ```
- **手動安裝**：見 [INSTALL.md](INSTALL.md)，30 分鐘走完。

> **給 Claude 看：** 如果使用者要求"安裝 hkreport"，請讀 [HANDOFF-CLAUDE.md](HANDOFF-CLAUDE.md) 並按其中劇本執行。

## 它做什麼

每雙週指定 weekday 的上午（默認週五 09:00），本機自動：

1. 抓取 14 家競品的 App Store / 新聞 / LIHKG / 利率數據
2. 調用 Claude API 分類信號嚴重度（RED/YELLOW/GREEN）+ 撰寫報告
3. 渲染 HTML、執行 6 項質量檢查、失敗自動重試（最多 2 次）
4. 部署到你的 Surge 域名（例：`hkreport.surge.sh`）
5. 失敗會留下完整日誌在 `~/Library/Logs/hkreport.log`

## 架構

```
hkreport/
├── reports/                  # 部署到 Surge 的目錄
│   ├── 001.html … NNN.html
│   ├── index.html            # 重定向到最新一期
│   └── baseline.json         # 滾動狀態（期號、周期、未決信號、FY 指標）
├── snapshots/                # collect.py 的原始輸出
├── scripts/
│   ├── collect.py            # 多源數據收集器
│   ├── generate.py           # 7 階段 pipeline: INIT→COLLECT→CLASSIFY→COMPOSE→RENDER→VERIFY→PERSIST
│   ├── verify.py             # 6 項質量檢查（3 BLOCK + 3 WARN）
│   ├── deploy.py             # 非互動式 surge 包裝
│   └── run.sh                # launchd 入口；做雙週/最小間隔閘門再呼叫上面三支
├── config/sources.yaml       # collect.py 的源映射
├── templates/
│   ├── template.html         # 設計系統（CSS + JS）
│   ├── prompts/{classify,compose}.md
│   └── launchd.plist.template # 由 install.sh 渲染成個人化 plist
├── install.sh                # 互動式安裝向導（中文）
├── uninstall.sh              # 卸載
├── .env.example              # 環境變數範本
├── requirements.txt
├── INSTALL.md                # 接手部署指南
└── README.md
```

## Pipeline 細節

`generate.py` 順序執行：

1. **INIT** — 讀 `reports/baseline.json`，下一期 = `prev+1`，周期 = `prev_end+1d` 到今天
2. **COLLECT** — 呼叫 `scripts/collect.py`；彙整 `snapshots/{category}/*_DATE.json` 中位於周期內的檔案
3. **CLASSIFY** — 一次 Claude API 呼叫（Sonnet 4.6 + `templates/prompts/classify.md`），輸出 JSON 信號清單
4. **COMPOSE** — 第二次 Claude API 呼叫（`templates/prompts/compose.md`），輸出 HTML 區塊片段
5. **RENDER** — 插值到 `templates/template.html`
6. **VERIFY** — 呼叫 `scripts/verify.py --json`；BLOCK 失敗則把違規餵回 COMPOSE，最多重試 2 次
7. **PERSIST** — 寫 `reports/NNN.html`、重建 `reports/index.html`、更新 `reports/baseline.json`

之後 `deploy.py` 執行 `npx --yes surge reports/ $SURGE_DOMAIN`，用 `SURGE_LOGIN` + `SURGE_TOKEN` 非互動部署。

**每期 API 成本** ≈ US$0.30–0.50（Sonnet 4.6 計價）。

## 排期

launchd 在每週指定 weekday 的指定時間觸發 `scripts/run.sh`，由 wrapper 做兩道閘：

1. **雙週閘**：`(today - ANCHOR_DATE) % 14 == 0`，否則 SKIP
2. **最小間隔閘**：距上一期結束 < `MIN_GAP_DAYS` 天則 SKIP

兩道都通過才呼叫 generate + deploy。**Mac 關機時錯過的觸發**：launchd 會在下次開機時補跑（再次經閘判定）。

## 為什麼是 launchd 而非 GitHub Actions

公司 API key 政策禁止託管到第三方 CI/CD；launchd 在本機運行，密鑰不出機器。
代價：Mac 必須開著（或在錨點當天開機過）。

## 必要密鑰

| 環境變數 | 用途 | 取得方式 |
|---|---|---|
| `ANTHROPIC_API_KEY` | classify + compose Claude 呼叫 | https://console.anthropic.com/settings/keys |
| `ANTHROPIC_BASE_URL` | （可選）走自家代理 | 例：`https://llm-proxy.example.com/aws` |
| `TAVILY_API_KEY` | 新聞 + 社媒搜尋 | https://app.tavily.com/（免費額度足夠）|
| `FIRECRAWL_API_KEY` | JS 渲染利率頁 | https://firecrawl.dev/ |
| `SURGE_LOGIN` | Surge 註冊郵箱 | — |
| `SURGE_TOKEN` | Surge 認證 token | 本機 `npx surge token` |
| `SURGE_DOMAIN` | 你的站點域名 | — |
| `ANCHOR_DATE` | 雙週錨點日期 | `install.sh` 會幫你算 |
| `MIN_GAP_DAYS` | 最小間隔（默認 10） | — |

全部走 `~/.config/hkreport/env`（`chmod 600`），不入 repo。

## 本地手動操作

```bash
source ~/.config/hkreport/env

# 不呼 Claude，只寫一個 stub 確認 pipeline 路徑通
.venv/bin/python scripts/generate.py --dry-run

# 強制跑完整 pipeline（會真正花錢、真正發佈）
.venv/bin/python scripts/generate.py
.venv/bin/python scripts/deploy.py

# 試 wrapper 邏輯（包含閘門判斷）
bash scripts/run.sh

# 透過 launchd 觸發一次（仍受閘門控制）
launchctl kickstart -k gui/$(id -u) com.$(whoami).hkreport

# 暫停 / 恢復
launchctl bootout   gui/$(id -u) ~/Library/LaunchAgents/com.$(whoami).hkreport.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.$(whoami).hkreport.plist

# 看日誌
tail -f ~/Library/Logs/hkreport.log
```

## 回滾

每一期報告獨立檔（`reports/NNN.html`），出錯只需：

```bash
rm reports/NNN.html
# 還原 reports/baseline.json 到上一期（手動編輯 issue / period 兩個欄位）
.venv/bin/python scripts/deploy.py    # 重推上一版的 reports/
```

## 不在這份 repo 內

- Claude Code skill `/hk-report`（互動式手動兜底）— 留在原作者本機
- `~/competitive-intel/tracker/` 每日抓取 job — 與本服務獨立
