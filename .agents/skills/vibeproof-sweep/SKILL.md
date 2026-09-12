---
name: vibeproof-sweep
description: Run the installed vibeproof lens review when due; use an independent reviewer for every lens.
---

# /sweep

跑一次 lens sweep。**你係 orchestrator,唔係 reviewer —— 你一個 lens 都唔好自己讀。**

## 次序

```
1  v4 sweep                        到期未？
2  唔到期  →  停。呢度冇嘢好諗
3  到期    →  佢一個 lens 印一行 `v4 review lens --lens <名>`
4  每個 lens 派一個 reviewer，按宿主容量並行／排隊，全部收齊   ← 見下
5  收齊    v4 sweep --done --findings <n>
```

**呢度唔寫死幾多個。** `.v4/lenses/` 加一個檔就多一個 reviewer,而呢份文件係四個地方
寫住同一個數字先至過期嘅 —— request-fidelity(2026-08-25 加)同 near-miss(2026-08-26 加)
兩個 lens 開咗一段時間都冇喺呢度出現過。數字由 `v4 sweep` 印,唔由呢度講。

## 第 2 步:唔到期就係唔到期

`v4 sweep` 唔應你嗰陣會講點解,而每個理由都係一個實測逼出嚟嘅守衛:

| 佢講 | 意思 |
|---|---|
| `the last sweep was Nd ago and the interval is 4d` | 未夠鐘。四日係 config 嘅 `lens_sweep` |
| `N task(s) still hold an unanswered claim` | 有人棵樹寫到一半。**呢個唔係叫你去催佢,係叫你唔好而家掃** |
| 讀唔到嘅 task | **讀唔到唔等於乾淨。** 當佢乾淨就係喺睇唔到嘅工作上面掃 |

⚠️ **而家逾期冇任何嘢擋住你,呢個要講清楚。** 曾經有一條 applies_to: always 嘅
sweep-current claim,逾期擋住 `v4 ship`;`a9ae5fb`(2026-08-24)量過佢跑咗 295 次
只 fail 過一次,連同佢個 detector 同 checker 一齊剷咗。
`./bin/v4 --repo . sweep --if-due` 唔到期 exit 1、到期先印 brief；
`v4 doctor`／`v4 maintain status` 可以讀返 review 及排程觀察，但唔係 host job 的即時查詢。
Framework development repo 的 private CI reminder 唔會安裝到 adopter；
其 reminder 綠燈只代表計到 due 狀態，唔代表 model 已 review。

**到期本身唔會啟動 reviewer。** 手動可以叫呢個 workflow；明確配置的 host job
亦可以啟動 maintenance workflow，按其 snapshot／handoff 流程派 reviewer。
安裝唔會自動建立 job，要讀返真實 host job 及執行結果。未配置時，到期而冇人叫
仍不會自動 review；due 狀態本身唔擋 ship，實際 findings 按各自 claim gate 處理。

## 第 4 步:一個 message 一次過開晒

```
每個 reviewer sub-agent 個 prompt 只准帶:
    lens 個 slug（唔係 display name）
    repo 路徑
```

使用 host 支援的並行 dispatch，別等上一個完成才啟動下一個；message 數目本身不是並行證據。歷史記錄：
2026-08-18 嘅 sweep,11 個 lens agent 真並行,214 條 finding。
2026-08-18 同一個框架喺 adopter 度嗰次,ledger 寫住 `11 lens(es) claimed, 1 ran`。
個框架印埋兩個數,就係為咗捉呢個分別。

## 盲讀 —— 呢個 command 存在嘅另一半理由

```
⛔ 唔准傳落 sub-agent:
     worker 嘅 engagement 句
     task 嘅 rationale
     之前 sweep 嘅 finding 清單
     你自己對呢份 code 嘅睇法
```

理由係量過嘅:三個 reviewer 每輪出 3.6 條 finding,**每輪都係新嘅** —— 即係
reviewer 係一個抽樣器,唔係窮舉器。一個讀過解釋嘅抽樣器,會沿住嗰個解釋抽樣,
只會揾返有人已經諗過嘅嘢。

**kernel 攔唔到呢條。** 佢只見到 `v4` 嘅 command 同 ledger,唔知邊個 process 讀過
乜。`reviewer.md` 自己認:「佢係一條紀律,唔係一個邊界。」**而條紀律唯一守得住嘅位
就係呢一步 —— 開 sub-agent 嗰個唔傳落去。**

## 佢哋交返乜

Reviewer 出 claim,唔出 verdict,亦都唔修理:

```
./bin/v4 --repo . review add --lens <slug> --file <path> --symbol <包住嗰個 symbol> \
                             --note '<一句,錯咗乜>'
```

為咗歸屬清楚，提供 `--lens`。省略會用預設分類；同座標不同 note 現時會開編號 variant，
唔會自動修訂舊 note，修訂要用 `review amend`。
`--symbol` 要係一個 stack frame 叫得出名嘅嘢(module-level 常數會即場拒 —— 冇 test
執行得到,即係嗰條 claim 永世閂唔到,唯一出口係簽名)。

## 之後

Finding 落喺 `repo-review` 呢個常設 task,唔係落喺你手上。閂佢係另一件事:
一個適用的 red-green test（含目標執行）、非程式檔案的文字修正證據，或授權的具名風險接受。
文件／JSON finding 不要傳 `--symbol`；延期只記錄決定，不會自動令 claim terminal。
要並行去修,用 `/wave`。

`v4 risk waiting` 會講邊條係「重跑就得」、邊條係「只有簽」。

Headless 執行亦要收齊結果先回覆最後答案：驗收指令同步等到 exit code；如用了背景工作，讀回其完成結果後才繼續。單純「等待中」不是完成或阻塞證據。

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
