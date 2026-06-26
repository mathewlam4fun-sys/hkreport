# hkreport 部署指南（接手 / 新環境）

預計 30 分鐘。一杯咖啡的時間。

完成後你會擁有：
- 一個自動每雙週發佈一期競品快報的 macOS 後台任務
- 一個你自己的 Surge 站點（例：`yourname-report.surge.sh`）
- 全部密鑰存於 `~/.config/hkreport/env`（chmod 600），不出機器

---

## 0. 先決條件

在你的 Mac 上需要：

| 工具 | 安裝 |
|---|---|
| macOS | 任何近年版本（Big Sur 以上即可） |
| Python 3.9+ | macOS 內建有 3.9；建議 `brew install python@3.11` |
| Node.js (含 npx) | `brew install node` |
| Homebrew | https://brew.sh |
| curl | macOS 內建 |

驗證：
```bash
python3 --version    # 3.9+
npx --version        # 任何版本
```

---

## 1. 申請 4 個 API 帳號（事前準備，15 分鐘）

> **Futu 同事走捷徑**：你只需要準備自己的「公司 AI 平台 user-key」（向 IT/AI 平台組要）+ 自己的 Surge 帳號 + Tavily/Firecrawl 免費 key。
> Anthropic 代理 URL（`https://llm-proxy.futuoa.com/aws`）install.sh 已內建為默認，直接按 Enter 接受。

依序註冊並把 key 存到記事本：

### 1.1 Anthropic Claude
- **Futu 同事**：用公司 AI 平台分發的 user-key（`user-key-...` 開頭），無需到 anthropic.com 註冊
- **外部用戶**：到 https://console.anthropic.com/settings/keys 申請 API key，**儲值至少 US$10**（每期成本 ~US$0.5），複製 `sk-ant-...` 開頭的 key

> 公司 key **不要走官方端點**（違規）。install.sh 會把 `ANTHROPIC_BASE_URL` 默認設為 Futu 代理，Futu 同事直接 Enter 接受。

### 1.2 Tavily（新聞/社媒搜尋）
- https://app.tavily.com/
- 免費註冊，每月 1000 次查詢，**夠用**
- 複製 `tvly-...` 開頭的 key

### 1.3 Firecrawl（網頁 JS 渲染抓取）
- https://firecrawl.dev/
- 免費註冊，每月 500 頁
- 複製 `fc-...` 開頭的 key

### 1.4 Surge（靜態站點托管）
- https://surge.sh — 官網沒明顯註冊入口，直接執行：
  ```bash
  npx surge login
  ```
- 終端會提示輸入 email + password（**首次就是註冊**）
- 然後取 token：
  ```bash
  npx surge token
  ```
- 複製這串 token 備用

**想好你的域名**：例如 `yourname-report.surge.sh`。任何 `*.surge.sh` 都可，Surge 採先到先得。

---

## 2. 拿到 hkreport repo

從原作者拿到完整目錄（zip / git clone / scp）放到本機，例如：

```bash
mkdir -p ~/workspace
# 視交接方式擇一：
git clone <repo-url> ~/workspace/hkreport
# 或解壓 zip 到 ~/workspace/hkreport
cd ~/workspace/hkreport
```

確認以下檔案都在：
```bash
ls install.sh scripts/run.sh templates/launchd.plist.template
```

---

## 3. 執行安裝向導

```bash
cd ~/workspace/hkreport
bash install.sh
```

向導會依序問你：

1. **API 密鑰**（4 個，貼上步驟 1 準備好的）
2. **Surge 域名 / 登錄 / Token**（步驟 1.4 準備好的）
3. **排期設定**：
   - Weekday：默認 5（週五）；想週一就填 1，週日填 0
   - Hour / Minute：默認 09:00
   - **錨點日期**：向導會自動算出"下一個符合 weekday 的日期"作為預設，按 Enter 接受即可
   - 最小間隔天數：默認 10（防短期重跑）

向導會自動完成：
- 寫入 `~/.config/hkreport/env`（chmod 600）
- 建立 `.venv/` 並裝依賴
- 封存原作者的 7 份歷史報告到 `reports/.archive_*/`
- 寫入空白的 `reports/baseline.json`（issue=0）
- 生成 `~/Library/LaunchAgents/com.<你用戶名>.hkreport.plist`
- `launchctl bootstrap` 註冊任務

完成畫面會列出排期、域名、日誌位置、下一步指令。

---

## 4. 兩個驗證步驟

### 4.1 驗證 launchd wrapper 邏輯

```bash
launchctl kickstart -k gui/$(id -u) com.$(whoami).hkreport
tail -n 20 ~/Library/Logs/hkreport.log
```

預期看到：
```
SKIP: before anchor (YYYY-MM-DD); days=-N
```

或（恰好今天就是錨點）：
```
SKIP: off-cycle; days_since_anchor=...
```

只要看到 `SKIP:` 開頭就代表 wrapper、env、路徑都通了。

### 4.2 跑一次完整 pipeline（會真實花錢 + 真實發佈）

```bash
source ~/.config/hkreport/env
.venv/bin/python scripts/generate.py
.venv/bin/python scripts/deploy.py
```

預期 5-10 分鐘後完成。然後：

```bash
curl -s "https://${SURGE_DOMAIN}/" | head -20
open "https://${SURGE_DOMAIN}/"
```

看到 `富途 HK 行業快報 #001` 字樣 → 成功。

---

## 5. 等首期自動觸發

到了你設定的錨點當天 weekday + 時間，Mac 開機狀態下會自動跑。
之後每 14 天再跑一次。

**Mac 關機怎麼辦**：launchd 會在下次開機後補跑（仍受閘門控制 — 不是錨點當天則 SKIP）。

---

## 常見問題

### Q: 我想換時間 / 換錨點
重跑 `bash install.sh`，向導會用備份覆寫 env 與 plist。

### Q: 我想換域名
編輯 `~/.config/hkreport/env` 改 `SURGE_DOMAIN`；舊域名需手動 `npx surge teardown old.surge.sh`。

### Q: 暫停一段時間
```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.$(whoami).hkreport.plist
```
恢復用 `bootstrap`。

### Q: 看到 `FATAL: ANTHROPIC_API_KEY env var not set`
你直接跑了 `scripts/generate.py` 而沒先 `source ~/.config/hkreport/env`。
launchd 會自動 source（因為 wrapper 做了），手動跑要自己來。

### Q: Surge 部署成功但網站還是舊版
99% 是瀏覽器快取。試：
```bash
curl -s "https://${SURGE_DOMAIN}/" | grep -o "快報 #[0-9]*"
```
若 curl 顯示新版號 → 純瀏覽器問題，無痕視窗刷新即可。
若 curl 也顯示舊版號 → 跑 `.venv/bin/python scripts/deploy.py` 重推。

### Q: 想停用整套服務
```bash
bash uninstall.sh
```
會問是否刪 plist / env / venv，可逐項選。`reports/` 不會自動刪。

### Q: 想完全換成自己公司的競品 / 視角
- 競品清單：`config/sources.yaml`
- 信號分級邏輯：`templates/prompts/classify.md`
- 報告口徑 / 品牌：`templates/prompts/compose.md` + `templates/template.html`
- 9 條硬規則：原作者文件，這份 repo 沒帶 — 想改成自己的需自行重寫 prompts

> 這份服務的編輯框架是"富途 HK PM 視角追蹤 14 家券商"。
> 想做完全不同主題的報告，等於是 fork 後重寫 prompts，所需工程量另算。

---

## 接手後的最小心智負擔

正常狀態下你**什麼都不用做**。Mac 開著、錨點到了，站點就自動更新。

每兩週開一次站點檢查當期內容即可；如果有質量問題，看 `~/Library/Logs/hkreport.log` 對應期次的 VERIFY 段落，通常會說明哪條規則被違反。
