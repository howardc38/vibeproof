[English](README.md) · [简体中文](README.zh-CN.md) · **繁體中文**

# vibeproof

### AI 說做好了。用什麼證明？

**真的能用嗎？修好有證據嗎？再改一次，之前的證據還算數嗎？**

vibeproof 把 Claude Code 和 Codex 任務接到可執行的檢查、修復證據與目前仍有效的結果。先看一個故意寫錯的折扣例子：**100 − 20 算出 120，測試卻全過。**

[![三個瞬間：測試全綠但金額錯誤；驗證修正前後與函式執行；再改程式，舊證據變成 STALE。](docs/launch/assets/v4/images/hero-zh-TW.png)](docs/launch/assets/v4/demo-zh-TW.mp4)

[看普通話配音示範](docs/launch/assets/v4/demo-zh-TW.mp4) · [自己跑一次](#自己跑一次) · [用在你的專案](docs/GETTING_STARTED.zh-TW.md)

**最適合先試：** 已有真正 Python 測試的 Git 專案，你已經用 coding agent，也花不少時間檢查它的工作。Claude Code 和 Codex adapters 共用 kernel；原生宿主驗證在 macOS 進行。[宿主設定與限制](docs/CODEX.md)

## 一次改動，三件值得確認的事

### 1. 測試全過，結果卻錯了。

購物車應該是 **80**，實際卻是 **120**。一條無關的 `2 + 2` 測試仍然全綠。一般 Python checker 會指出：

```text
the suite passed and executed none of the 1 changed file(s):
  checkout.py
```

它指出缺少執行證據，不代表每條改動都已被測過：只 import 檔案，也可能通過這項普通檢查。

### 2. 修好，要有對得上的證據。

新回歸測試呼叫 `total(100, 20)`，預期得到 `80`，先在錯誤實作上失敗。修正後，review checker 驗證：**同一條測試修正前失敗、修正後通過，而且執行過目標函式。** 一條無關的綠色測試，不能充當修復證據。

這證明的是示範中的修復。測試仍需要有意義的斷言，不代表所有需求或分支都已正確。

### 3. 程式再改，舊證據過期。

示範把真正的 checker 執行記入暫存 ledger，然後再次改動 `checkout.py`。Kernel 會報告：

```text
ANSWERED → STALE
its subject moved: checkout.py
```

原本成功的執行紀錄仍然保留，但不能繼續當成目前有效的證據。`STALE` 表示需要重新驗證，不代表已找到新 bug。還原同一份已檢查的程式，該份證據便再次適用。

## 自己跑一次

需要 **Git 和 Python 3.12+**，使用 macOS 或 Linux；尚未驗證原生 Windows。示範不用安裝套件、不用 API key，也不用訂閱 coding agent。

```sh
git clone https://github.com/howardc38/vibeproof.git
cd vibeproof
python3 examples/first-proof/run.py
```

腳本會建立並清理暫存 repo。Clone 完即可離線執行，不會把 framework 安裝到你的專案裡。**過程出現 FAIL 是預期的；最後顯示 `DEMO VERIFIED` 才代表示範通過。**

圖片與影片重播 **2026-09-14 刻意建立案例**的實測輸出，包括真正的 checker 執行及 kernel 證據狀態查詢。旁白為合成語音，主持人像是虛構角色；它們不是錄下來的 AI 對話，也沒有展示完整安裝或 ship 流程。[原始碼、執行紀錄與反例](examples/first-proof/README.md)

## 為什麼下一個任務還要用？

| 工作開始變複雜時 | 流程幫你連起什麼 |
|---|---|
| 剛剛檢查通過，agent 又改程式 | 根據目前輸入的 hash，判斷舊證據是否仍適用 |
| Reviewer 找到缺陷 | Finding 可連到可執行的修復測試，追蹤到驗證結果 |
| Worker 說已完成 | Maintenance 可在相連的修復 worktree 執行 checker，讀回原 finding 狀態 |
| 一個問題可以稍後處理 | 只報告的 claims 與嘗試仍留在 ledger；任務政策決定是否阻擋 |
| Review 中斷或受檢程式改變 | Maintenance 區分部分完成、完整與已過期的 review |

定期維護需要宿主排程器內真正存在的工作；修復需要已授權範圍。Telegram 通知送達或確認已讀，都不會關閉 finding。[維護操作](docs/USING.md#periodic-maintenance-and-findings)

## 用在你的專案

[從安裝到第一個任務 →](docs/GETTING_STARTED.zh-TW.md)

你提供想要的結果、允許修改的檔案、真正的測試命令，以及未解決風險的決定。指南附可貼給 agent 的指示。完整安裝會加入 checkers、detectors、fixtures、hooks 和提示檔，驗證 fixtures，並要求確認專案事實，因此比短示範花更多時間。

**選擇宿主：** 預設是 Claude Code。使用 Codex 時選 `--hosts codex` 或 `--hosts both`。`--activate-hooks` 會合併 framework handlers，保留無關的既有設定；Codex hooks 還需要在宿主內審閱及信任。[任務綁定與權限](docs/CODEX.md)

| 要做的工作 | Claude Code | Codex |
|---|---|---|
| 完成一項有明確範圍的改動 | `/run` | `$vibeproof-run` |
| 在獨立 worktrees 協調多個任務 | `/wave` | `$vibeproof-wave` |
| 按適用的 lenses 審查目前程式 | `/sweep` | `$vibeproof-sweep` |
| 查看 findings 並協調維護 | `/maintain` | `$vibeproof-maintain` |

## 還包含什麼？

| 功能 | 幫你做什麼 |
|---|---|
| Scope 檢查 | 在支援的編輯操作提早檢查，也檢查最後的 Git diff |
| 測試改動檢查 | 報告有效測試數量下降及部分 expectation／shape 變化；不等於完整斷言品質分析 |
| Runtime 證明 | 執行你宣告的 trigger，向你宣告的真相來源查詢本次結果 |
| UI 證明 | 要求本次 runner 的新鮮案例結果；可選的 Playwright adapter 支援 browser proof |
| Review lenses | 從需求忠實度、設計、安全及測試充分性提問；仍需要 reviewer 判斷 |
| 結構與憑證檢查 | 部分錯誤處理、外部寫入、secret、signature 及 reference 模式；覆蓋取決於語言和 facts |
| Checker 註冊驗證 | 接受 checker 前，跑 red／green／bypass fixtures 及重跑一致性檢查 |

一般測試的改動檔案執行追蹤只支援 Python。可執行的 review 修復路徑包括 Python、Go 和 Node/V8，取決於 runner。經驗證的同檔案 Python 函式／方法改名可保留原 finding。結構檢查對 Python、Go、TS/JS 的支援深度不同，Rust 較有限。

[完整功能地圖](docs/FEATURES.md) · [技術參考](docs/REFERENCE.md) · [Facts 格式](docs/FACTS.md)

## 看清楚結果代表什麼

- `SHIP` 是按設定作出的任務判定，不會部署程式，也不保證每項需求都已滿足。
- 部分問題先報告而不阻擋；刪測試預設只報告。常設 `repo-review` 的 finding 不會自動阻止另一個任務。
- Hooks 涵蓋支援的宿主輸入，狀態不可讀時可能放行。Stop 檢查在同一次停止流程只攔一次，之後獨立回合可再檢查；hooks 不是 sandbox。
- 本地 ledger 與 hash 不是不可修改的外部信任服務。存在接受風險的路徑，包括 agent 簽署。
- 提示檔、review lenses 與完成紀錄，不證明獨立判斷。業務正確性與安全仍需要合適的測試及人的決定。

[完整限制與 exit codes](docs/REFERENCE.md) · [完整流程](docs/USING.md)

## 試一個真實改動

[告訴我們結果](https://github.com/howardc38/vibeproof/issues/new?template=first-run.yml)：它抓到什麼、誤報什麼，以及下一個任務會不會繼續用。只需分享去除敏感資訊的紀錄，不需要私人程式或憑證。

執行 framework 自己的測試：`python3 tests/run_without_silent_skips.py`。

維護 vibeproof 時，請修改 canonical 開發 repo；見[貢獻方式](CONTRIBUTING.md)與[同步流程](docs/SYNC.md)。

MIT 授權。[授權條款](LICENSE) · [實作規格](docs/SPEC.md)
