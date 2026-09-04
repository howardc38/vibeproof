<!-- Language switcher. Keep this line identical in all three files. -->
[English](README.md) · **简体中文** · [繁體中文](README.zh-TW.md)

# vibeproof

### 你的 coding agent 说"做好了"。这套东西要它拿出证据。

vibeproof 是一组放在你 git 仓库里、伴着 coding agent 运行的小程序。每次 agent 完成一件工作，它可能出错的每一点都会变成一条**问题**。由一个程序用 exit code 回答这条问题。答案写进一本没人能改的账。**有问题未答就不能出货**：agent 要么修好代码，要么由一个人签名承担这个风险。

它不写代码，不取代你的 CI 或 linter，也不判断设计对不对。它只回答一件更窄的事：**"你说你做了，证据在哪？"**

---

## 这是不是你需要的？

先看左栏。如果有两行以上是你真的中过招的，就值得继续读。

| 你的痛点 | vibeproof 处理得了吗？ | 怎样处理 |
|---|---|---|
| agent 说"加了测试，全绿"，但那条测试**从来没有调用过那个函数**，只是检查源码里有没有某段文字 | **可以。** | 由 Python 解释器自己报告哪些函数跑过（`sys.settrace`）。一条没进入过函数的测试不能关闭 review finding。`test-shape` 更会在测试还没跑之前就标出"这条测试在读源码" |
| agent "重构"了，**顺手删掉两条测试**，套件因为删了而变绿 | **可以。** | `test-weakened` 拿 base commit 时的 test function 同现在的逐个对 |
| 套件全绿，但**根本没执行过 agent 改的那个文件** | **可以。** | `test` checker 在 tracer 下跑你的测试命令，套件绿但没碰过任何改动文件就判 FAIL |
| "加了错误处理"，而那个 `except` **把付款、webhook、授权检查的失败吞掉了** | **可以**，Python、Go、TS/JS。 | `fail-closed` 找出包住对外调用或授权判断的 try/except，而控制流可以在没有异常传出的情况下离开 |
| 一次对外写入（扣款、发帖、发布）**没有 read-back**，或者**重试时会写两次** | **可以**，前提是你先告诉它你的代码库里什么算对外调用。 | `external-write` 读一份你自己维护的小词表（`.v4/facts.<repo>.json`），对每个调用点问这两条问题 |
| agent **改了根本没叫它碰的文件** | **可以**，在写入那一刻。 | 一个 hook 对每次 Write 和 Edit 检查 task 声明的 scope，外面的直接拒绝 |
| 凭证**被 commit 了** | **可以。** | `secret` 扫这个 task 改过的文件，打印之前先遮掉 |
| 某个 scanner 打印了 `PASS`，其实**一个文件都没打开过** | **可以。** | exit code `4` 的意思是"这里我读不到任何东西"。它永远不是 pass，也永远不会结束一个 task |
| agent **没做完就停了**，或者放弃了也不说 | **可以**，拦一次。 | stop hook 列出未答的问题，拒绝第一次停手。修好、签名、或明说"我留下这个 FAIL"，唯一不接受的结局是悄悄走掉 |
| 你想**日后**查得到谁对哪个版本的代码签了什么 | **可以。** | 每次检查和每个签名都是一本 append-only、hash 链住的 SQLite 账里的一行，签名同时是一个必须 commit 的文件 |
| "这个设计对不对？""它真的做了我要的东西吗？" | **不能。** | 这里没有程序读得懂你的意图。reviewer lens 可以提出这条问题，回答的是人 |
| 你想少信一点 model | **不能**，这也不是目标。 | 它让你在机械的部分不需要信任。判断仍然是你的 |
| 你主要写 Rust | **几乎没有。** | 今天只有两个检查读 Rust。见[语言支持](#它读得懂你的语言多少) |
| 你想要一道 agent 绕不过的墙 | **不是。** | 这是有记录的摩擦，不是边界。有 shell 的 agent 可以改一个普通 SQLite 文件或删掉 trigger。你得到的是：篡改会在 audit 里显现，每条逃生路都会留下一行带名字的记录 |

---

## 一个 task 长什么样

以下是 2026-09-02 对一个三文件沙盒仓库的真实执行。你可以重现：代码在下面，命令在 Quickstart。

agent 写了这段，说是"加了错误处理"：

```python
import requests

def charge(card):
    try:
        requests.post("https://api.example/charge", json={"card": card})
    except Exception:
        pass
    return True
```

`v4 derive` 对这个 task 提出七条问题。其中五条拒绝跑 checker，直到 agent 先写一句"这条规则对这段代码意味着什么"。然后 `v4 check`：

```
FAIL fail-closed      src/pay.py:7  in charge  [swallow]
     except Exception guards outbound call `requests.post` (line 6), and control
     can leave this try/except with no exception having propagated

FAIL external-write   src/pay.py:6  in charge  [readback/unobserved-write]
     the outbound write `requests.post` at line 6 discards what it returned, and
     `charge` calls nothing from the outbound-read table.
```

agent 试图停手。stop hook 回答：

```
3 claim(s) on t-e2e are still open:
  OPEN    fad124898937cac4  external-write
  OPEN    3ce1741909dec365  fail-closed
Three ways to end this properly: answer them, sign for one, or say it failed.
```

修好之后（加 `raise_for_status()`，并把 receipt 读回来）两条都 `PASS`。然后 `test` 那条因为另一个原因转红：

```
FAIL test   the suite passed and executed none of the 1 changed file(s):
              src/pay.py
            A green suite that never reached the change proves the suite works.
            It says nothing about this change.
```

整个想法就在这一屏：**"绿"和"真的检查过"是两件事，而第二件一直都是缺的那件。**

---

## Quickstart

你需要一个 git 仓库、Python 3.12、你自己的测试命令，以及（要用 hook 的话）Claude Code。不需要从 PyPI 装任何东西：这个框架零第三方依赖。

**每个仓库做一次。**

```sh
# 1. 获取框架。它留在你的仓库外面；你的仓库只会得到一个 launcher。
git clone https://github.com/howardc38/vibeproof.git ~/vibeproof

# 2. 在你的仓库建立 .v4/。它写好 config，留下四个它拒绝替你猜的问题，
#    第一个就是你的测试命令。
~/vibeproof/bin/v4 --repo /path/to/your/repo init

# 3. 至少回答第一个。
#    在 /path/to/your/repo/.v4/config.json 设置
#      "test_command": "python3 -m pytest -q"      # 或任何能跑你套件的命令

# 4. 安装 checker、detector、hook 和 lens。每个 checker 入场前都要先过
#    自己的 fixture，所以要几分钟。
~/vibeproof/bin/v4 --repo /path/to/your/repo install

# 5. 把 hook 接进 Claude Code，然后检查接线。
cd /path/to/your/repo
cp .claude/settings.template.json .claude/settings.json
./bin/v4 doctor
```

`install` 同时会起草 `.v4/facts.<repo>.json.draft`：它对"你的代码里哪些调用会写到外面世界"的最佳猜测。读一遍，删掉猜错的，改名为 `.v4/facts.<repo>.json`，再跑 `./bin/v4 facts validate`。在你做这件事之前，`external-write` 用一份通用词表运作，而 `ship` 会因为 installer 写下的每一条占位 "absent" 而拦住。[docs/FACTS.md](docs/FACTS.md) 解释这个文件。

**每件工作。** 在仓库里开 session，Claude Code 才会加载 hook。然后：

```sh
./bin/v4 task --id t-1 --request '<请求原文，用提出者自己的话>' --scope 'src/**'
./bin/v4 derive --task t-1                       # 这个 task 引出哪些问题
./bin/v4 engage --claim <id> --text '<这条规则对这段代码意味着什么>'
#   ... 写代码 ...
./bin/v4 check --task t-1                        # 跑 checker，读 exit code
./bin/v4 ship --task t-1                         # SHIP 或 HELD，连理由
git add -A && git commit                         # 签名和账本导出跟代码一起走
```

[docs/USING.md](docs/USING.md) 逐步讲每一步，以及每一步要下的判断。

---

## 它怎样运作

### 词汇

| 词 | 是什么 |
|---|---|
| **task** | 一件工作：提出者的原话、它可以碰的文件、它起步的 commit |
| **scope** | 这个 task 可以改的文件清单。hook 在每次写入之前检查它 |
| **claim** | 关于代码的一条未答问题，例如"这次对外写入，跑两次会怎样？"。它不是判词 |
| **kind** | claim 的类型。21 种。类型决定由哪个程序回答，以及未答会不会拦出货 |
| **detector** | 读 diff、决定哪里值得问的程序。它提出 claim，从不判。20 个 |
| **checker** | 用 exit code 回答一条 claim 的程序。它判，从不提出。21 个 |
| **engagement** | 对 10 种 kind，agent 写第一行代码之前必须先写一句：这条规则对即将写的代码意味着什么。由七条机械判据判这一句，没有人判 |
| **signature** | 程序证明不了的东西可以由人签名承担，附理由。签名会随它看过的 bytes 过期，同 pass 一样 |
| **ship** | 最后一条问题：每条 claim 都答了或签了吗，账本的链完整吗，每条声明的 absence 都确认了吗 |
| **lens** | reviewer subagent 读 diff 时用的一组问题，看不到 worker 的解释。13 份。它提出 finding，从不出判词 |
| **ledger** | `.git/v4/` 里一个 append-only 的 SQLite 数据库。每次检查和签名是一行。逐行 hash 链住；导出文件跟代码一起 commit |

**两个角色，永不合并。** detector 提出、从不判；checker 判、从不提出。你或你的 agent 站在中间：回答那条问题，或为它签名，两样都会留下一行。

### checker 的 exit code 是什么意思

| Exit | 名字 | 意思 |
|---:|---|---|
| `0` | PASS | 看过，没发现。claim 算答了，直到它任何一个输入改变 |
| `1` | FAIL | 看过，有发现。claim 保持 open |
| `4` | UNSUPPORTED | 这里读不到任何东西。**不是 pass，也不是结局** |
| `5` | ERROR | checker 自己崩了 |
| `6` | CHECKER_TAMPERED | 磁盘上的 checker 不是注册的那一份。没有执行 |
| `7` | SUBJECT_MOVED | checker 跑的期间代码变了 |
| `8` | TIMEOUT | 没跑完 |

### 哪些问题会拦出货

八种 kind 未答或未签就拦住 ship：`test`、`scope`、`secret`、`fail-closed`、`external-write`、`review-finding`、`runtime-proof`、`surface-proof`。其余十三种只报告，直到同一种堆积（10 条未答）、放久（14 天）或反复失败（5 次），才同样拦住。

### 一个答案有效多久

每个答案钉住六个输入：它判过的文件的 digest、config、checker 自己的 bytes、detector 的 bytes、facts 词表，以及（对全仓库问题）整棵树。**任何一个移动，答案就变 STALE**，要重问。签名用同一把 key，所以签名永远不会比 pass 便宜。

### 三个 hook

| Hook | 触发于 | 拒绝什么 |
|---|---|---|
| `write_block` | Write、Edit、MultiEdit、NotebookEdit | task scope 以外的路径；保护路径；还欠 engagement 句子时的任何写入 |
| `bash_guard` | Bash | 会写入保护路径的 shell 命令，或提到保护路径而无法证明只是读的命令 |
| `stop_gate` | Stop | 有 claim 未答或 task 未 ship 时结束回合，只拦一次。第二次放行，FAIL 留在账本里 |

三个都是 **fail open**：hook 找不到 kernel 或账本时放行，并在 stderr 说明。一个什么都拦的 hook，是一个会被人关掉的 hook。

### 保护路径

四个路径决定其他一切怎样被判，所以改它们要一个签名，不只是 widen scope：`.v4/**`（config、registry、facts）、`checkers/**`、`detectors/**`、`.github/**`。

### 判官先被判

checker 要过完整套 fixture 才能进 registry：`red/` 必须 exit 1，`green/` 必须 exit 0，`bypass/`（同一缺陷改写成规避的样子）必须 exit 1 且不能是 red 案例的副本，`known_miss/` 明文写下一个盲点。今天 21 个 checker 共 525 个案例。过不了的 checker 永远判不了任何东西。

---

## 21 条问题

每一行是 registry 里的 `question_template` 原文。最后一栏是这种 kind 未答会不会拦 ship。

| Kind | 它回答的问题 | 拦 ship |
|---|---|:---:|
| `test` | Does the repo's declared test command pass?（以及这次执行有没有碰到改动的文件） | ✓ |
| `scope` | Does the diff stay inside the declared scope? | ✓ |
| `secret` | Do the files this task changed contain a committed credential? | ✓ |
| `fail-closed` | Can control leave the try/except at {file}::{symbol} with no exception having propagated? | ✓ |
| `external-write` | After the outbound write at {file}::{symbol}, does anything establish it landed, and does replaying it apply the effect twice? | ✓ |
| `review-finding` | Is the finding at {file}::{symbol} closed by a test that fails without the fix and actually runs the code? | ✓ |
| `runtime-proof` | Did triggering this leave a row in the table that owns the data? | ✓ |
| `surface-proof` | Does the surface suite this repo already maintains still pass? | ✓ |
| `test-shape` | Does {file} prove behaviour, or only that some text is present? | |
| `test-weakened` | Does {file} still hold at least the test functions it held at the base? | |
| `test-expectation` | Did {file}::{symbol} change what it expects while the code it judges did not? | |
| `test-token-shape` | Is there a literal in tests/ that looks like a real credential without saying it is fake? | |
| `signature-change` | Did every caller follow the parameter this change made required? | |
| `dangling-ref` | Does {file} import {symbol} from a module that defines it? | |
| `lint` | Does this task add a structural violation that was not in the base commit? | |
| `layer-boundary` | Does any import cross a layer the declaration does not allow? | |
| `design-pins` | Do the design document's pinned claims about the code still hold? | |
| `control-plane-budget` | Is the control plane still within the ceiling it declared? | |
| `dead-wiring` | Is anything declared here with something on one end and nothing on the other? | |
| `registry-consistency` | Do the registries still describe the checkers, detectors and fixtures on disk? | |
| `spec-coverage` | Does every command, path, kind and mechanism the spec names actually exist? | |

最后四种是关于这个框架自己的，装到别的仓库时会被扣起。`layer-boundary` 要等你的仓库声明了 `.v4/layers.json` 才会装。`runtime-proof` 和 `surface-proof` 要 config 里有命令才有东西可跑。

---

## 它读得懂你的语言多少

十个 checker 不分语言读每个文件（`scope`、`secret`、`test`、`review-finding`、`external-write`，以及五个自我一致性检查）。其余的在 `.v4/checkers.json` 声明自己读哪些扩展名。在你的仓库里没东西可读的 checker 不会被安装，而不是装了然后对从未打开的文件打印 `PASS`。

| 语言 | 读它的 checker | 怎样读 | "测试真的执行过那段代码吗？" |
|---|---:|---|---|
| **Python** | 10 | 标准库 `ast` | **可以。** 经 `PYTHONPATH` 上的 `sitecustomize` 用 `sys.settrace`；pytest、unittest、任何 Python runner 都行。`test` 和 `review-finding` 都用它 |
| **Go** | 7 | Go 自己的 `go/ast`，通过一个用你的 Go toolchain 编译的小程序 | **checker 层仍未接上。** kernel 读得懂 `go test -coverprofile`，但 `review-finding` checker 目前只接受 Python 的关闭测试，其他一律回 `4` |
| **TypeScript / JS** | 8 | 正则表达式遮罩的扫描器，不是 parser；有 `bypass/` fixture 盯着它 | 同 Go：kernel 读得懂 `NODE_V8_COVERAGE`，但没有 checker 走到那里 |
| **Rust** | 2 | 扫描器，没有 parser | **不能。** |

数字是 `reads` 明文写出该语言的 checker 数，截至 2026-09-02。

---

## 它不会做的事

以下每一项都对着代码核过，不是政策宣示。

- **它是摩擦，不是边界。** hook 是 Claude Code 的配置，它们 fail open，有 shell 的 agent 可以改那个 SQLite 文件或删掉 trigger。你得到的是 audit 会显示出来，以及每条逃生路都留下一行带名字的记录。
- **shell guard 只看命令行。** `python3 -c "open(...)"` 或一个打开保护文件的脚本会过。真正作准的是 ship 时的 `scope` checker。
- **签名可以由 agent 落。** `--no-tty-check` 是合法出口；它写的文件会注明 `signed_by: agent`。真正把签名钉住的是：文件要先 commit，`ship` 才接受。
- **engagement 句子是机械判的。** 四十字、提到 subject、不复述规则、跨 task 不重复、不贴凭证。知道规则的人可以刻意满足。目的是让那一刻发生，不是证明它发生过。
- **分析是单文件、词表驱动的。** 另一个文件里的 read-back 对 `external-write` 是看不见的。默认答案是不安全那边的 exception allowlist 对 `fail-closed` 是看不见的。两个盲点都以可执行的 `known_miss/` fixture 保存。
- **review finding 也可以用文字改动关闭**，不只靠测试：一段 24 字以上的标记在 parent commit 有、HEAD 没有。它证明一段字符串移动了，仅此而已。
- **`task` 表不在 hash 链内。** attempt 和 event 在；改写 request 原文不会被 `v4 audit` 抓到。
- **没有人执行 reviewer 盲读。** kernel 不知道哪个进程读过什么。
- **它不判断请求有没有被满足。** `v4 cover` 要你逐句、逐字交代请求，它不下判断。

---

## 数字

以下全部由脚本在 2026-09-02 从这棵树算出，不是记忆。

| | | | |
|---:|---|---:|---|
| **21** | 条问题，每条一个 checker | **20** | 个 detector（11 个条件式、9 个无条件） |
| **8** | 种会拦 ship 的 kind | **13** | 份 reviewer lens，303 条 check |
| **96** | 条生成到 `CLAUDE.md` 的常设规则 | **32** | 个 `v4` 命令 |
| **525** | 个 fixture 案例（196 red · 212 green · 100 bypass · 6 known miss · 11 self-trigger） | **1,027** | 个各语言 fixture 文件（747 Python · 166 Go · 89 TS/JS · 25 Rust） |
| **1,717** | 条框架自己的测试 | **0** | 个第三方依赖 |

这个框架从 2026-08-06 起就是自己的第一个使用者。[docs/EVIDENCE.md](docs/EVIDENCE.md) 背后的测量和 [docs/DOGFOOD_LOG.md](docs/DOGFOOD_LOG.md) 的例子来自那本开发账，它住在 `.git/v4/`，每次 ship 导出到 `.v4/ledger_export.jsonl`。没有带着导出文件的仓库快照重现不了那些数字；它们是一份记录，不是对你正在读的这棵树的宣称。

---

## 接着读什么

| | |
|---|---|
| [docs/USING.md](docs/USING.md) | 一个 task 的七步，以及每步要下的判断 |
| [docs/FACTS.md](docs/FACTS.md) | `external-write` 读的词表文件，以及怎样写你自己的 |
| [docs/SPEC.md](docs/SPEC.md) | 契约：每个机制、命令和约束，钉在代码上 |
| [docs/RATIONALE.md](docs/RATIONALE.md) | 为何这样建，包括试过又撤回的 |
| [docs/EVIDENCE.md](docs/EVIDENCE.md) | 测过什么、何时、有多稳 |
| [CLAUDE.md](CLAUDE.md) | 常设规则，由这个仓库注册了什么生成 |

MIT 许可。
