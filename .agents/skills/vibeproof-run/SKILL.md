---
name: vibeproof-run
description: Run one requested development change through the installed vibeproof lifecycle.
---

# /run

由一個 request 行一個完整 task。**你係 orchestrator,唔係 worker。**

已有對應 task 時，先讀返其狀態及已交付證據，沿用 ID，從未完成嗰步續跑。
不要重做已驗過的 setup／runner 研究；只有新失敗才重開相關問題。

單一 task 的 worker 預設用會等待完成的呼叫：Claude Code 的 Agent 設
`run_in_background: false`；Codex 派原生 worker 後用宿主等待工具等該 ID。
若確實使用背景角色，保留返回的 ID，逐個等到完成或明確需要處理，讀回實際結果。
仍有自己派出的角色運行時不要結束 turn；「已派出」「仍在做」不是交付。
被 stopped／中斷的角色按未完成處理，核對實際檔案及 ledger 後續跑，不能用舊摘要當現況。

## 次序

```
1  task-splitter  → v4 task --id <id> --request '...' --scope '...' [--forbid '...']
                                      [--after <上一刀嘅 id>]
2  task-splitter  → v4 engage --task <id> --kind <kind> --actor splitter --text '…'
                   ↑ 句子要提到一個喺 scope 入面嘅路徑,唔係就會被拒
3                   v4 derive --task <id>
4  worker         → v4 engage --claim <id> --text '…'   （寫第一行 code 之前，hook 攔住）
5                   處理 blocking claims；讀取仍然有效的狀態和 report-only 報告
6                   v4 cover --task <id> --quote '…' --symbol …   逐段交代個 request
7                   v4 ship --task <id>
```

**第 2 步係新嘅,而且係 splitter 做唔係 worker 做。** 一個 task 嘅 claim 分兩種:
講緊已經存在嘅 code 嗰啲喺第 3 步就出齊(量過:43 條入面 37 條),支援的 Write/Edit hook 會在初始 gate 未清除、狀態可讀時要求句子；
未 derive、其他寫入路徑或狀態不可讀不構成不可繞過的保證。**講緊呢個 task 即將寫嘅 code 嗰啲,
detector 要有 code 先提得出**,所以永遠冇得事前答。第 2 步就係嗰半:對住規則寫,
唔係對住 claim 寫,subject 係 task 個 scope。

由 splitter 寫,唔係由 worker 寫。`--actor splitter` 唔係裝飾 —— 冇咗佢,ledger
分唔出一句係交落嚟嘅約束,定係佢自己嘅預演,而嗰個分別就係呢一步存在嘅全部理由。

**句子要提到一個喺 task scope 入面嘅路徑,唔係就會被拒**(「mentions nothing this
task is scoped to. Before the code exists, what a rule is about is where it is
going to be written.」)。呢個唔係格式要求:一句講規則而唔講落腳點嘅說話,對住將來
先出現嘅 code 係查唔到真假嘅。實測一個 adopter 一輪 41 句入面 6 句第一次被拒,
全部係補返個檔名就過。

**呢一刀接住上一刀嘅話,第 1 步加 `--after`。** 佢做兩樣嘢:把上一刀嘅 request、
scope 同「邊啲 claim 最後一次 attempt 是 PASS」寫入呢一刀嘅 request(等 worker 唔使靠一份自己
編出嚟嘅摘要開工),同時喺 ledger 記低條邊 —— 冇佢,「邊個 task 接住 X」呢條問題
就冇答案。實測十八刀:每一刀都係人手開,一條鏈都冇記低過,而其中一刀存在嘅唯一
原因就係上一刀撞到嘅嘢。`request_cover` 唔會叫你交代承接落嚟嗰段。

同一個 agent 寫句子又寫 code,次序調轉都只會寫
一句配合佢自己個設計嘅話 —— 量過六刀,worker 事後寫嘅六句全部喺度論證嗰段 code 冇問題,
而 checker 之後全部話有。Splitter 寫出嚟嘅係一個**交落嚟嘅約束**,唔係自我辯護。

之後 worker 喺第 4/5 步寫嘅句子,同第 2 步嗰句擺埋一齊讀 —— **唔一樣嗰度就係佢
開工時冇諗到嘅嘢**。

**後閘唔喺呢條次序入面。** 邊個閘幾時行,由 `docs/SPEC.md` 擁有,唔係由呢個
檔擁有 —— 呢度重述一次,就係第二個真相來源,而佢會飄。佢飄過:呢個檔一度
寫住「4 reviewer × N → 每個 lens 一個」,即係每個 task 跑九個 lens,而 SPEC
§10.1 早兩日已經量到嗰樣嘢係唔跑嘅 **6.6 倍**,所以後閘已經改成定期。一個照
住呢個檔做嘢嘅 orchestrator 會付返嗰 6.6 倍,而嗰 6.6 倍就係搬走佢嘅唯一理由。

所以:

```
./bin/v4 sweep                # 到期未？未到期就冇你事，行返上面第 5 步
./bin/v4 sweep --if-due       # 一句就答完，畀 cron 用
```

到期之後點做,喺 `/sweep`,唔喺呢度 —— 呢個檔一度寫住「4 reviewer × N」而
SPEC 兩日前已經量到嗰樣嘢係唔跑嘅 6.6 倍,即係第二個真相來源一定會飄。佢飄過一次。

點解係定期而唔係每 task —— 睇 `docs/SPEC.md` §10.1,唔好喺呢度搵。

同時開幾刀,喺 `/wave`。

## 你保住嘅四條邊界

**Worker 唔可以自己叫 ship。** 出貨係一個判詞,而 worker 就係被判嗰個。
你叫,唔係佢叫。

**Worker 唔可以自己簽名 —— 接受風險要符合用戶授權。** 同一個理由,而且更硬:ship 只係記錄
工作做完,簽名係記錄 signer 接受未證明嘅事，唔能夠認證 signer 必然係人。實際發生過:一個 worker
用 `--no-tty-check` 簽走咗判佢自己嗰條 claim,留低嘅檔案寫住 repo 擁有者個名、
一個字冇提係 agent 執行。佢個論證後來核實係啱嘅 —— **而論證啱唔係簽名嘅資格**。

Worker 交返理由畀你,你決定簽唔簽。你自己代人簽嗰陣,`.v4/risks/*.json` 會寫住
`signed_by: agent`,咁樣至少讀得返。

**Reviewer 盲讀。** 唔好把 worker 嘅 engagement 句或者 task rationale 傳落去 ——
一個讀過解釋嘅抽樣器會沿住嗰個解釋抽樣。

**Ship 唔收斂就停。** `ship` 有一個喺同一 task 內累積嘅「產生新 claims」重掃額度；收斂嘅零新增輪次唔扣額度。用晒就停,
唔好重新派 —— 每次重新派等於冇上限,而每輪都會向一個 append-only ledger 加 claim。

## 開工之前

本次掂到 Web UI 時，先按 `.v4/surface/INTEGRATION.md` 核對產品旅程與現有 suite。
欠必要案例或接線就派現有 worker 補建，reviewer 獨立核對，再由 checker 驗證。
唔好把缺少 suite、全 skip 或只印成功當成 surface proof；唔需要新增 agent 角色。
Browser finding 要關閉時沿用該 suite 的紅綠測試；編譯過的 JS/TS 依 integration contract
提供同次 build 的 source map。缺少映射／宿主不容許啟動瀏覽器，都保留未證明狀態。

```
./bin/v4 --repo . doctor      # 呢個 repo 係真係接好咗,定係得個樣
```

`BAD` 嗰幾行喺正常使用之下係靜嘅。


開工時判斷本次已授權 request 是否有可獨立驗收的工作。若有，按依賴與共享狀態選用 `/wave`；不要只因檔案多而拆，亦不要為並行發明額外工作。維護派來的修復保留原 claim／handoff 關聯，收尾要讀回原 claim，不能只交一個新的綠 task。

Codex host contract:
- Run CLI commands with explicit --repo; supply --task only on commands that accept it. Use --help for action-specific flags. A shell export does not bind hooks.
- Bind the assigned V4 task using the session/agent identity supplied by SessionStart/SubagentStart.
- For another worktree use an explicit exec working directory. Patch tools may restrict writes to the host project; dispatch the worker in its authorized worktree or use a permitted tool there.
- Never infer a worker identity from the parent session alone.
- Reviewer source access is read-only in intent; review add/done still write evidence through v4.


Codex orchestration:
- Use the native v4-task-splitter, v4-worker, v4-reviewer and v4-checker-author agents.
- Start every native role with a fresh context and explicit inputs; inherited-context forks are unavailable in some ephemeral Codex runs. For reviewers pass only repo, lens and necessary diff coordinates.
- Dispatch up to the available agent capacity, queue the rest, and wait for every requested result. This replaces the Claude source's all-at-once scheduling and single-shell export instructions.
- One worker task per worktree. Bind the task before writing; use explicit --repo and supported --task flags.
- The orchestrator owns ship and risk decisions; never attribute an agent signature to a person.
