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

## 先查現有工作、選 goal，再記原文

每次收到 request，先在同一 repo 跑不帶 goal filter 的 `v4 coordinate status`，讀現有 goals、
原始 inputs、queued/running work 與 assignments；同時核對 active task/worktree 紀錄及當前 code。
`v4 doctor` 的 open tasks 摘要與 `git worktree list --porcelain` 可協助定位；摘要有截斷或未知
歸屬時補讀現有 ledger 的原始 task/request/worktree 紀錄，不能當成完整清單或無關證據。
逐段比較新 request 原文與現有原意、source 及 work：部分重疊或保留舊工作時重用相關 goal；
只有明確記下無關判斷及理由才開新 goal，後續 assessment 保留該理由。不可未查就先建 fresh goal。
關係不清或意圖相衝時保留原文，把未決關係交 owner 判斷，暫停依賴該判斷的 fanout；
不可盲目派工、把全部舊工作改排 queued，或強行納入無關的 legacy task。

選定 goal 後，用 `v4 coordinate input --data <file|->` 保存原文及宿主實際提供的
source identity/message_id，再用 `coordinate assess` 逐段記錄 add、change、cancel 及關係。
先讀 `coordinate schema`；JSON 以 stdin 或 tree 外檔案提供。CLI 不會讀聊天、理解授權、
開 agent 或停止 process。宿主沒有 message_id 就記缺少，不能拿 synthetic ID 當實際訊息證據。
父 session 沒有 agent ID 時保留空值／省略，不能發明一個。

拆工作之前，對帳該 goal 的 queued/running work、依賴及版本；每項 live work 都要有
change、cancel 或 unaffected 的決定。相關 queued work 按 revision 更新，保留 unaffected work；
相關 running work 沿用 stop／宿主對帳協定，不能另開 goal 避開舊 owner。改一部分不等於取消整個舊 request。
有新輸入先記 input 再 assess；pending input 期間不要繼續派工或收貨。
先前無關的 task/claim 不因本次入帳而全部重開。

Orchestrator 管理最新接受的設計文件版本。優先沿用專案現有設計／規格文件，選出實際
受影響段落；缺少 baseline 時先核對 code、tests 與已知原意，標明 observed_baseline，
由宿主建立所選文件後再 register content。觀察到的實作不能冒稱已批准的產品意圖。
`v4 design register` 只記 metadata；missing path 的 needs_initialization 要真的完成文件
才可消除。每個 managed work revision 用 `design disposition` 記 required 及 affected coverage，
或附 source-bound 理由的 not_applicable；不需要藉此全面重審整個 repo 的舊文件。
欄位以 `design schema` 為準，不設固定章節／heading 閘，不另建一份平行 wiki。

## 派工與變更要讀回

先把 settled dependency 的 artifact commits 帶入實際 consumer checkout 並重驗組合；
assign、pre-launch 及 ship 都會核對 consumer HEAD ancestry，另一棵樹的 settled 記錄不能代替。
有實際 open task/worktree 才 `coordinate assign`。`coordinate check-dispatch` 只讀 eligibility，
即使 ready 亦不是 launch 授權。每次宿主 spawn 之前必須先完成 durable `coordinate begin-dispatch`，
只有首次提交回覆的 `launch_authorized=true` 才可 launch 一次；idempotent replay 永遠不能 launch。
欄位以 `coordinate schema` 為準。Claim 已提交但尚無 receipt 時保留 dispatching owner：先向
宿主核對是否已啟動，不可樂觀重試。即使 claim 回覆本身遺失，亦不能靠重送取得第二次授權。
需要重新 launch 時，先取得實際未開始／已停止的 observation，解除舊 owner，再建新 assignment
及取得新 claim；不可重用舊 claim。宿主派工後用 `coordinate receipt` 保存真實 host_ref 與
observation，並把 goal/work/revision、assignment、dependency versions、task/worktree、原文及
文件 coverage 交給 worker。結果未知時先向宿主對帳，不能重複 launch。
準備 future child 時，尚未取得的 worker identity 保留 unknown，不能發明或借 parent identity。
Spawn 後用實際 dispatched receipt 綁定觀察到的 worker identity，再進行獨立 review／收貨；
未知或 conflicting identity 都要 hold。已知既有 worker／parent 可在 assignment 提供實際 tuple；
parent 本身的空 agent ID 是已知 parent，不能與 unknown 混為一談。表示方式以 schema 為準。
`coordinate stop` 只是請求停止；宿主實際
停止／未開始的觀察要另記 receipt，不能把 requested stop 當 observed stopped。
同一 worker 的 dispatched receipt 晚於 observed stopped 到達時保留歷史，不重新開 owner。
每次派工及收結果前重新讀 current version；過期結果仍留作 observation，不當成新版本交付。

Worker 修改 scoped 文件段落後，由另一個 reviewer 獨立讀原始 input、code、tests 及文件，
用 `design review` 記問題、coverage 及證據。不得傳 worker engagement/rationale 引導判斷。
Reviewer 必須異於文件 author 及 assigned worker；receipt 綁 current work/design revision、
文件內容及 task source。後續改 source/content 要重新核實，不能沿用 stale approval。
接受的目標設計可能未實作；review receipt 是有版本的審閱紀錄，不是 identity 認證或語義正確證明。

Worker 回來後記 `coordinate result` 並讀回；orchestrator 才處理 check/ship。
`coordinate settle` 沒有 integration evidence 時只會留下 artifact-ready/waiting-integration，
不解鎖依賴。宿主合併後重驗測試、remerge 及 current versions，再提供實際 target task/worktree/head
的 integrated proof；分支綠燈或一次 task ship 不能冒稱整合後已實作。CLI 不替你 merge。

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
- Start every native role with a fresh context and explicit inputs; inherited-context forks are unavailable in some ephemeral Codex runs. For lens reviewers pass repo, lens and necessary diff coordinates. For managed document review also pass original input/source refs, current work/design versions and affected code/test/document coverage; omit worker engagement/rationale.
- Dispatch up to the available agent capacity, queue the rest, and wait for every requested result. For managed assignments, check-dispatch is read-only; commit coordinate begin-dispatch before native spawn and launch only on its fresh launch_authorized=true response. Never spawn on an idempotent replay. Claimed dispatch without a receipt must reconcile actual host state; a replacement launch needs authoritative release, a new assignment and a new launch claim. Leave future-worker identity unknown until an actual dispatched receipt binds it; never invent it or borrow the parent identity. A known existing parent with an empty agent ID remains distinct from unknown. Review/completion must hold on unknown/conflicting worker identity.
- One worker task per worktree. Bind the task before writing; use explicit --repo and supported --task flags.
- The orchestrator owns ship and risk decisions; never attribute an agent signature to a person.
