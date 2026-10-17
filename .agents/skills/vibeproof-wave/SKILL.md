---
name: vibeproof-wave
description: Coordinate multiple requested vibeproof tasks, each in its own Git worktree.
---

# /wave

一次過開 N 刀,每刀一個 worktree、一個 task、一個 worker。**你係 orchestrator。**

Fanout 前先完成 `/run` 的 managed intake：在同 repo 讀不帶 goal filter 的 `coordinate status`，
核對 active task/worktree 紀錄與當前 source，逐段比較新 request 原文和現有工作，先選相關 goal
再保存 input。部分重疊／保留舊工作重用原 goal；只有明確記下 unrelated 理由才開新 goal。
關係不清／意圖衝突時保留原文交 owner 判斷，未定前不 fanout，不把全部工作重排 queued，
不強制 enroll 無關 legacy tasks。逐段 assess add/change/cancel/關係並對帳依賴；相關 queued
work 按 revision 更新，running work 用既有 stop/reconcile 協定，unaffected work 保留。
再定每個 current work revision 的文件 disposition。
沿用已有文件；缺少 baseline 時核對 code/tests/已知原意，保留 observed_baseline 標記，
由宿主初始化文件再 register。Accepted target 可以尚未實作，不拿現有 code 當批准。
`coordinate schema`／`design schema` 擁有 JSON 格式；不要另抄一份 schema。

呢個 command 唔係新機制 —— 佢係把已經手做緊嘅嘢寫低。實測喺 reference adopter:
八個 worktree、五個 task 同時開住,全部人手開,而唯一冇人記得嗰步就係下面第 5 步。

## 次序

```
1  分組       N 組,每組一個 file-level scope
2  worktree   git worktree add ../wt-<id> -b wave/<id>
3  task       v4 --repo ../wt-<id> task --id t-<id> --request '…' --scope '<嗰組嘅檔>'
4  engage     v4 --repo ../wt-<id> engage --task t-<id> --kind <kind> --actor splitter --text '…'
              ↑ 句子要提到一個喺嗰組 scope 入面嘅路徑,唔係就會被拒
5  指派／派工  coordinate assign → check-dispatch → begin-dispatch 首次授權 → 宿主派工 → receipt
6  驗收        scoped code/tests/docs、獨立 design review、coordinate result、accept
7  收          orchestrator check current versions，再 ship --task t-<id>
8  合          宿主 merge、整合後測試/remerge、settle integration 證據；最後才移除 worktree
```

第 1 至 4 步係 `task-splitter` 嘅嘢，一組一次；每個 worker 先 derive，
再跟 `/run` 的 engagement、修改、檢查、request accounting 和 ship 流程，
喺各自嗰棵樹入面行。呢個檔只負責「同時」嗰部分。

## 第 5 步:按宿主容量派工，逐個記 observation

```
每個 worker sub-agent 個 prompt 要帶:
    ./bin/v4 --repo /absolute/wt-<id> status --task t-<id>
    # 收到 host identity 時，從該 worktree 執行 host bind。
    # goal/work/revision、assignment、dependency versions、原文、affected doc coverage。
```

用 host 支援嘅並行 dispatch；唔好等一個 worker 完成先開下一個。
Message 數目本身唔決定並行，亦要遵守 host 的 concurrency 上限。
把 settled dependency 的 artifact commits 帶入實際 consumer checkout 並重驗組合；
assign、pre-launch 及 ship 核對 consumer HEAD ancestry，不只看另一項 work 的 settled label。
超過容量的工作保留 queued；dispatch 前再核對 current versions。`coordinate check-dispatch` 只讀，不能
授權 launch；每次 native spawn 前必須完成 durable `coordinate begin-dispatch`，只接受首次
提交回覆的 `launch_authorized=true`。Idempotent replay 不能 spawn；claim 已提交但 receipt
遺失的 dispatching owner 要向宿主對帳，不可再 launch。若連 claim 回覆都遺失，同樣先對帳；
重送不會再給 launch 授權。重新 launch 要實際未開始／已停止 observation 解除舊 owner，
再建新 assignment 及取得新 claim。`coordinate schema` 擁有格式。每次宿主實際回覆用
`coordinate receipt` 入帳，host_ref/message_id 缺少就講缺少，不造一個當成實際證據。
Future child 的 identity 在 spawn 前保留 unknown，不借 parent 或造 ID；實際 dispatched
receipt 再綁定觀察到的 worker tuple，未知／conflicting identity 不能通過獨立 review／收貨。
已知既有 worker／parent 的實際 tuple 可在 assignment 記錄；已知 parent 空 agent 不等於 unknown。
Outcome unknown 時先向宿主對帳，不能再 launch 同一份工作；stop request 與 observed stopped
分開記。Observed stopped 後同一 worker 的晚到 dispatched receipt 只留歷史，不能重開 owner。
收到部分 change/cancel 先 input/assess，令 obsolete assignment 停止收貨，再依宿主
實際停止觀察決定可否重新指派。不要把晚到的舊 result 晉升為 current revision。

**一個 worker task 對一個 worktree。** Ledger 由 Git common directory 共用，
hook 先按 task_worktree 記錄選出當前 worktree 的 task。同一棵樹有幾個 open
tasks 而沒有明確身份時仍然拒絕猜測。每次 CLI 都傳明確 --repo／--task；
收到 SessionStart/SubagentStart 的 identity 時用 host bind 綁定。
不要靠一個共享的 V4_TASK 環境變數代表同時跑緊的幾個 worker。


## 一刀嘅來源:一個 request,或者幾條講緊同一件事嘅 finding

```
./bin/v4 status --task repo-review        ← 張單喺 ledger,唔喺任何文件度
```

`repo-review` 係 sweep 提嘅 finding 掛住嗰個常設 task。要修佢哋,一刀 = **幾條講緊
同一個事實嘅 finding**,唔係一條。

**點解唔係一條一刀 —— 量過:**

```
2026-08-19:  224 條 finding → 209 條真 → 26 個真實組
             原紀錄另報平均 7.4 條、最多 13 條；平均值的分母不清，不能由 209/26 推出，勿作目前成效數字
```

一條一刀,即係同一個事實喺 12 個地方修 209 次。呢個 repo 自己嘅 doctrine 就係反面:
**repairing a finding means repairing the fact everywhere it appears。**

## 分組跟語義,唔跟路徑 —— 呢個都量過

```
26 個真實組,平均跨 3.2 個檔
  只喺一個檔入面嘅:                    9 / 26
  一個檔嘅 finding 分散喺多過一組:      19 / 63 個檔
```

所以「按檔分組」兩邊都錯:會把 26 組入面 17 組拆爛,又會把 63 個檔入面 19 個撈埋。
按 lens 分一樣衰 —— 一個 root cause(「一條規則,N 個實作」47 條)本來就跨 lens。

**真實嘅組係:「同一個事實,錯咗喺幾個地方」。冇機械切法接得住,所以呢步係判斷。**

分完之後寫低佢:

```
./bin/v4 review group --name '<一句講嗰個事實>' --claim <id> --claim <id> … --why '…'
```

唔係為咗驗證(驗證已經有,見下),係為咗下次 sweep 見到同一批嘅時候,唔使由零再判一次。

## 分組錯咗會被捉到一半

一個 test 閂一組,而 `v4 check` 係**逐條 claim** 跑 `redgreen.verify` —— 每條都要求
**佢自己個 symbol 真係被執行過**。

實測:同一個 test 檔綁去四條 claim(`engagement.py::before_the_work`、
`review.py::defer`、`lifecycle.py::continues`、`doctor.py::run`),四次獨立 check,
四次 PASS,每次 2.0–2.2 秒。

```
❌ 驗唔到    「呢啲係同一個 root cause」（語義)
✅ 驗到      「一個 test 真係行過呢 N 個 symbol」（機械,已經行緊)
```

第二條唔等於第一條,但佢擋住咗最差嗰種錯分組。

## 每刀收工之前

```
./bin/v4 accept        宣告嘅 test command + 每個 checker + 每個 detector + 四個文件對 code
```

**唔好用本機 `python3 -m unittest` 當驗收。** 實測:1,285 個 test 本機全綠,push 上去
CI 紅 —— `spec-coverage` 捉到兩個新 command 冇入 SPEC。跑 test 唔等於跑閘。

## Scope 重疊與合併後驗證

每個 managed revision 要有 affected document coverage 或 source-bound N/A。Worker 更新受派段落，
orchestrator 管接受版本；獨立 reviewer 核原始 input、code/tests/docs 並記 fresh `design review`。
文件、source 或版本改動會使 receipt 過期。沒有 integration 證據的 settle 只是 artifact ready，
不是 downstream dependency ready；讀回實際整合 target 後才可解鎖。Metadata CLI 不會派 agent、
stop process、寫產品文件或 merge；這些都是宿主的責任。

每棵樹只 diff 自己,所以 A 棵樹嘅寫入喺 B 棵樹嘅 `scope` claim 度睇唔見。合併時仍可能有文字衝突或冇文字衝突的語義錯誤。合併後要重驗測試及
用 `v4 remerge`／status 檢查舊證據，不能把各自綠燈當成整合後已通過。

`task-splitter` 已經量過:**按檔案重疊嚟判要唔要拆,係被否證嘅 —— 89% 乾淨
auto-merge(n=36)。** 所以分組跟住 request 分,唔好為咗避開重疊而砌一個唔自然嘅切法。

## 幾多刀

Kernel 沒有在這份流程定工作數上限；host、資源和可安全合併的範圍仍有限。每刀都要有自己的 request。**一刀一個 request** —— `task-splitter`
嗰句唔係叫你唔好拆,實測兩輪:一個計劃拆咗六刀,一次 61 條 finding 嘅 sweep 拆咗
二十刀,每一刀有自己嘅 request、scope、claim。

## 收唔到就唔好淨低

一棵冇 ship 嘅 worktree 係一個永遠開住嘅 task,而一個開住嘅 task 會:

- 在其仍屬活躍工作且有 blocking claims 時令 `v4 sweep` 等待；只有 report-only 問題不一定算 busy
- 同一 worktree 留低多個 open tasks 時，令 write hook 無法唯一判定歸屬

`v4 doctor` 嗰行 `open tasks` 會逐個名咁報返出嚟,連埋佢喺邊棵樹。收唔到就
`v4 abandon --task <id> --why '…'`,唔好當佢唔存在。

Headless 執行亦要收齊結果先回覆最後答案：驗收指令同步等到 exit code；如用了背景工作，讀回其完成結果後才繼續。單純「等待中」不是完成或阻塞證據。


由維護流程派工時，每組保留原 claim IDs 與 handoff ID。主控先記 prepared；已加入 managed goal 的 assignment 亦必須按上面的 begin-dispatch 首次授權才 spawn，再記真實 host_ref。派工回覆不明就對帳，不能再開一個相同 worker。完成後向原 claims 提供有效 proof，並在合併後重驗。讀取固定 snapshot 的 review 可以與另一棵樹的 dev 並行；修改與合併仍要重新核對授權、目標狀態及相互依賴。

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
