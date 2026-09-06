# README 應寫什麼？vibeproof 的差異化在哪裡？

分析日期：2026-09-05。自身功能已於 2026-09-06 對照整合版 code 重核；競品仍是下列固定版本的 2026-09-05 研究。agent/command Markdown 只作產品資產閱讀，不當成已執行的證據。競品以公開 repo 的特定 commit 和實作路徑比較，沒有執行其程式，也沒有做跨產品效能 benchmark。這是有界樣本，不是全市場窮盡調查。

## 結論

**README 應補完整功能地圖。現有三個痛點適合引起共鳴，但不是獨家能力，也不足以解釋為何要採用整套 framework。**

vibeproof 較值得說清楚的是：把任務範圍、機械檢查、review findings、當前證據、風險與出貨判定連在一起，並讓未解決的問題保持可查。這是工作流程的組合價值。不能只因有 ledger、lens、hook 或 red-green，就宣稱全球唯一。

## 三個痛點是否 unique？

| 首頁痛點 | 查到的替代做法 | 對 vibeproof 的判斷 |
|---|---|---|
| 測試綠，但改動沒被執行 | diff-cover 將 coverage 與 Git diff 對照，按改動行计算覆盖並有 fail-under 出口 | 不是獨有。對細粒度覆蓋要求，行級工具還可比我們普通 test checker 更嚴格 |
| AI 刪測試，suite 變綠 | Agentic-SDLC 的 test-lock 記錄測試檔 SHA，之後檢查修改／刪除 | 不是獨有。我們按有效測試數量比較，不能宣稱防止所有測試弱化 |
| 修改超出要求範圍 | agent-scope-guard 比較 diff 路徑與 allowlist；Ship 有 phase-based Write/Edit guard | 不是獨有。我們的價值是前置 hook、事後 scope check 和 scope 變更紀錄一起運作 |

來源（直接實作）：

- diff-cover：[行級計算](https://github.com/Bachmann1234/diff_cover/blob/bd73e2d09b3b56edff27f8da7b62ca472720d2de/diff_cover/report_generator.py#L111)、[閾值返回碼](https://github.com/Bachmann1234/diff_cover/blob/bd73e2d09b3b56edff27f8da7b62ca472720d2de/diff_cover/diff_cover_tool.py#L437)。它依賴你供應合適的 coverage report，不能省略此前提。
- Agentic-SDLC：[test-lock 比較與判定](https://github.com/Sweet-Papa-Technologies/Agentic-SDLC/blob/50d9220b6a1ebbf4be5ab11329bb4264683403b3/plugins/fofo/skills/sdlc/scripts/test-lock#L58)。沒有 lock 時會 skip，不能把它寫成永遠開啟的保證。
- agent-scope-guard：[allowlist 判定](https://github.com/manuelsampedro1/agent-scope-guard/blob/8effd0d653db1d6d01277eb4601bea0a7040a7f6/src/agent_scope_guard/cli.py#L63)、[CLI 出口](https://github.com/manuelsampedro1/agent-scope-guard/blob/8effd0d653db1d6d01277eb4601bea0a7040a7f6/src/agent_scope_guard/cli.py#L269)。這是 diff/list 檢查，不等於前置 Write hook。
- Ship：[階段性的 Read/Write/Edit 限制](https://github.com/heliohq/ship/blob/40da17bd7c1447660efd40064178ba09357fadce/scripts/phase-guardrail.sh#L20)。它只在指定 workflow 與 subagent 條件下觸發。

因此，這三個應命名為「你可能遇過的問題」，不能當成「只有我們能解決」。影片仍可用其中一個做入口：易懂、可重現，不需要假裝獨家才有价值。

## 連 evidence / ledger / fresh proof 也不是獨有

agent-done-or-not 的 `done-gate.sh` 會真正執行命令，保存返回碼與 log digest 到 JSONL receipt，並在 assert 時檢查新鮮度及可選的 Git state binding。它另有 Stop hook。來源：[capture](https://github.com/mohamedzhioua/agent-done-or-not/blob/2d7ae9331cf8d0272834bb9db31ae863f042bc88/done-gate.sh#L261)、[state drift](https://github.com/mohamedzhioua/agent-done-or-not/blob/2d7ae9331cf8d0272834bb9db31ae863f042bc88/done-gate.sh#L176)、[assert](https://github.com/mohamedzhioua/agent-done-or-not/blob/2d7ae9331cf8d0272834bb9db31ae863f042bc88/done-gate.sh#L406)。

所以「不信 agent 自報成功」「要先拿證據」「證據會過期」「有 ledger」都已是這類工具的共同方向。

Superpowers 的 verification-before-completion skill 也明確要求 fresh verification 和 regression red-green。這個具體檔案是操作規則；不能因而把整個 Superpowers repo 說成沒有程式或只是 prompt。來源：[該 skill](https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/skills/verification-before-completion/SKILL.md#L21)。

Ship 亦有機械 Stop gate 和階段狀態管理，故 agents、commands、分工或「不准未做完就停」本身也不足以構成 unique。來源：[Stop gate](https://github.com/heliohq/ship/blob/40da17bd7c1447660efd40064178ba09357fadce/scripts/stop-gate.sh#L50)。

## 更值得主打的四個問題

這些是**有辨識度的切入點**，不是已證明全球獨有。

### 1. 「你驗過的那份，還是現在這份嗎？」

vibeproof 在讀取 claim 狀態時，核對相關 subject、config、checker、facts、detector，以及適用時的 worktree digest。風險接受也綁狀態。這比單純記住某個時間的 PASS 更具體。來源：[stale_reason](../../kernel/state.py)、[risk state](../../kernel/state.py)。

具體比較：agent-done-or-not 這次讀到的 Bash state-drift 函式比較 commit 與 clean→dirty 狀態，不逐一 hash 目前未提交內容。因此它不能只靠那個函式區分同一 HEAD 下 dirty→dirty 的再次修改。這是可指向 code 的相對差別，不是對其整個產品的全面結論。

適合下一個 demo：同一個未提交任務，先取得 PASS，再修改被判的程式或 facts，顯示原結果變 STALE。要實際跑出來後才製成宣傳素材。

### 2. 「review 說修好了，究竟用什麼把 finding 關掉？」

reviewer 經 `review add` 建立固定類型的 finding，不能隨意換成一個容易 PASS 的 kind。修正測試路徑可以把同一測試放到舊 commit 與現在執行，並觀察目標函式。來源：[raise_finding](../../kernel/review.py)、[redgreen.verify](../../kernel/redgreen.py)。

TDD 與 red-green 不獨有；Agentic-SDLC 也有 [redgreen-gate](https://github.com/Sweet-Papa-Technologies/Agentic-SDLC/blob/50d9220b6a1ebbf4be5ab11329bb4264683403b3/plugins/fofo/skills/sdlc/scripts/redgreen-gate#L36)。差別要說在「具體 finding 的關閉如何接到實際測試與狀態」，而不是把 red-green 說成新發明。

限制：文字關閉、風險接受仍是其他出口；沒有指定 task 的 finding 會進 `repo-review`，不自動阻止別的 feature task。

### 3. 「暫時不擋的問題，是保留了，還是被忘掉？」

report-only 問題仍有 claim 和 attempt，ship 會把它列出。評估任務時，數量、年齡或反覆失敗門檻可令 report 升級為 blocker。來源：[split_open](../../kernel/state.py)、[thresholds](../../kernel/state.py)。

「warning 與 error 分級」並不新；比較有用的是把它與持續狀態、明確原因及判定接在一起。也必須說清楚：目前數量是在該 task 報告中計算，並非所有舊 task 的 debt 都會自動阻止下一個 task。

適合內容：同一個 task 的 ship 報告同時有已通過、暫不阻擋、unsupported 和 hook 未觸發的資訊，展示使用者怎樣作決定。

### 4. 「AI 連 checker 都能寫，那誰檢查 checker？」

註冊前執行 red/green/bypass fixtures，拒絕把 red 的原封副本當 bypass，並重跑比較返回碼及 stdout。這比把任意 script 路徑加入設定更有可說明的保障。來源：[verify_checker](../../kernel/register.py)、[repeatability](../../kernel/register.py)、[register](../../kernel/register.py)。

驗證工具本身、mutation testing、插件自測都不是新概念。vibeproof 的特色是把這一步放在自己的註冊／安裝入口。這個價值較適合會擴充規則的使用者，不一定是完全新手的第一個痛點。

## README 要有功能區，但不要放成百科全書

建議首頁次序：

1. 一句定位、最佳使用對象。
2. 容易理解的三個痛點與短 demo。
3. 一小段「为什么不只是一个 scanner」。
4. 完整工作流程圖：任務、detectors、review lenses、claims、checkers、ledger、ship。
5. 八個左右的功能群：每群寫使用者得到什麼，而不只是內部名詞。
6. 安裝入口、支援範圍與重要限制。
7. 完整功能／命令目錄的連結。

**13 個 lens 名稱、21 個 kinds、32 個 CLI commands，不應全部堆在首頁第一屏。** 首頁應讓人知道全貌，完整清單放 [FEATURES.md](../FEATURES.md)。

## 每個你問到的項目應放哪裡

| 項目 | 首頁需要嗎？ | 建議寫法 |
|---|---|---|
| Lens | 需要 | 補機械規則看不到的設計、需求、測試充分性等 review；輸出 finding，非自動正確性證明 |
| Hooks | 需要 | 哪些操作可提早被攔；明示需要接線及 fail-open／Stop 一次的邊界 |
| Agents / commands | 需要 | 提供操作整個流程的提示模板；分清 Claude slash commands 與 v4 CLI |
| Detector → checker | 需要 | 一個決定問什麼，一個執行檢查；不要以為 detector 自己下 PASS |
| Report vs block | 必須 | 不阻擋不等於消失；也不等於全都不須處理 |
| Ledger | 必須 | 所有已建立的 claims／attempts 留在同一份紀錄；不是只有 report 才進去 |
| Staleness | 必須 | 舊證據是否仍適用是核心使用價值 |
| Registry fixtures | 簡述即可 | 告訴進階使用者自訂 checker 不是只貼一個 script |
| 全部 commands／kinds | 放完整目錄 | 首頁放功能群和連結 |
| 長篇歷史數字 | 不宜作主推理由 | 不能代替外部採用、收益與留用證據 |

## 建議定位

英文方向：**A check-and-review workflow that keeps open questions and current evidence attached to each coding task.**

中文方向：**把任務、檢查、review 和仍然有效的證據接在一起，讓「做完」有一份可追查的依據。**

不用把原來三個痛點刪掉；把它們放回「容易理解的入口」。真正要解釋的採用理由是：當你的工作同時涉及多個檢查、review、修改與取捨時，這套流程能否減少你自行拼接和追蹤它們的負擔。

如果使用者只缺 coverage，一個 coverage 工具可能更直接。若他需要跨多輪 agent 工作追蹤待答問題與有效證據，vibeproof 才更有理由被長期留下。這需要第一批使用者實際驗證，不能由 repo 代碼獨自證明產品市場契合。

## 研究範圍

已讀取的公開版本：

| Repo | Commit | 本次重點 |
|---|---|---|
| Bachmann1234/diff_cover | bd73e2d09b3b56edff27f8da7b62ca472720d2de | 改動行 coverage 計算與 threshold |
| Sweet-Papa-Technologies/Agentic-SDLC | 50d9220b6a1ebbf4be5ab11329bb4264683403b3 | test-lock、redgreen-gate、gate-runner |
| mohamedzhioua/agent-done-or-not | 2d7ae9331cf8d0272834bb9db31ae863f042bc88 | capture、receipt、state drift、assert、Stop |
| manuelsampedro1/agent-scope-guard | 8effd0d653db1d6d01277eb4601bea0a7040a7f6 | allowlist、diff 輸入、CLI verdict |
| obra/superpowers | b36e0829c6d0140e93cfef2ca599b1b07d4a7797 | verification skill、相關 review/TDD 資產及 hook 宣告 |
| heliohq/ship | 40da17bd7c1447660efd40064178ba09357fadce | phase guardrail、Stop gate、orchestrator 部分狀態檢查 |

沒有把 competitor README 的自我宣稱當成已驗證能力；也沒有因單一路徑缺一個功能，就宣稱整個產品完全沒有該能力。
