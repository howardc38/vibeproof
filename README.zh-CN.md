[English](README.md) · **简体中文** · [繁體中文](README.zh-TW.md)

# vibeproof

### AI 说做好了。用什么证明？

**真的能用吗？修好有证据吗？再改一次，之前的证据还算数吗？**

vibeproof 把 Claude Code 和 Codex 任务接到可执行的检查、修复证据与当前仍有效的结果。先看一个故意写错的折扣例子：**100 − 20 算出 120，测试却全过。**

[![三个瞬间：测试全绿但金额错误；验证修正前后与函数执行；再改代码，旧证据变成 STALE。](docs/launch/assets/v4/images/hero-zh-CN.png)](docs/launch/assets/v4/demo-zh-CN.mp4)

[看普通话配音演示](docs/launch/assets/v4/demo-zh-CN.mp4) · [自己运行一次](#自己运行一次) · [用在你的项目](docs/GETTING_STARTED.zh-CN.md)

**最适合先试：** 已有真正 Python 测试的 Git 项目，你已经用 coding agent，也花不少时间检查它的工作。Claude Code 和 Codex adapters 共用 kernel；原生宿主验证在 macOS 进行。[宿主配置与限制](docs/CODEX.md)

## 一次改动，三件值得确认的事

### 1. 测试全过，结果却错了。

购物车应该是 **80**，实际却是 **120**。一条无关的 `2 + 2` 测试仍然全绿。一般 Python checker 会指出：

```text
the suite passed and executed none of the 1 changed file(s):
  checkout.py
```

它指出缺少执行证据，不代表每条改动都已被测过：只 import 文件，也可能通过这项普通检查。

### 2. 修好，要有对得上的证据。

新回归测试调用 `total(100, 20)`，预期得到 `80`，先在错误实现上失败。修正后，review checker 验证：**同一条测试修正前失败、修正后通过，而且执行过目标函数。** 一条无关的绿色测试，不能充当修复证据。

这证明的是演示中的修复。测试仍需要有意义的断言，不代表所有需求或分支都已正确。

### 3. 代码再改，旧证据过期。

演示把真正的 checker 执行记入临时 ledger，然后再次改动 `checkout.py`。Kernel 会报告：

```text
ANSWERED → STALE
its subject moved: checkout.py
```

原本成功的执行记录仍然保留，但不能继续当成当前有效的证据。`STALE` 表示需要重新验证，不代表已找到新 bug。还原同一份已检查的代码，该份证据便再次适用。

## 自己运行一次

需要 **Git 和 Python 3.12+**，使用 macOS 或 Linux；尚未验证原生 Windows。演示不用安装软件包、不用 API key，也不用订阅 coding agent。

```sh
git clone https://github.com/howardc38/vibeproof.git
cd vibeproof
python3 examples/first-proof/run.py
```

脚本会建立并清理临时 repo。Clone 完即可离线执行，不会把 framework 安装到你的项目里。**过程出现 FAIL 是预期的；最后显示 `DEMO VERIFIED` 才代表演示通过。**

图片与影片重播 **2026-09-14 刻意建立案例**的实测输出，包括真正的 checker 执行及 kernel 证据状态查询。旁白为合成语音，主持人像是虚构角色；它们不是录下来的 AI 对话，也没有展示完整安装或 ship 流程。[源代码、执行记录与反例](examples/first-proof/README.md)

## 为什么下一个任务还要用？

| 工作开始变复杂时 | 流程帮你连起什么 |
|---|---|
| 刚刚检查通过，agent 又改代码 | 根据当前输入的 hash，判断旧证据是否仍适用 |
| Reviewer 找到缺陷 | Finding 可连到可执行的修复测试，跟踪到验证结果 |
| Worker 说已完成 | Maintenance 可在相连的修复 worktree 执行 checker，读回原 finding 状态 |
| 一个问题可以稍后处理 | 只报告的 claims 与尝试仍留在 ledger；任务政策决定是否阻挡 |
| Review 中断或受检代码改变 | Maintenance 区分部分完成、完整与已过期的 review |

定期维护需要宿主调度器内真正存在的工作；修复需要已授权范围。Telegram 通知送达或确认已读，都不会关闭 finding。[维护操作](docs/USING.md#periodic-maintenance-and-findings)

## 用在你的项目

[从安装到第一个任务 →](docs/GETTING_STARTED.zh-CN.md)

你提供想要的结果、允许修改的文件、真正的测试命令，以及未解决风险的决定。指南附可粘贴给 agent 的指示。完整安装会加入 checkers、detectors、fixtures、hooks 和提示文件，验证 fixtures，并要求确认项目事实，因此比短演示花更多时间。

**选择宿主：** 默认是 Claude Code。使用 Codex 时选 `--hosts codex` 或 `--hosts both`。`--activate-hooks` 会合并 framework handlers，保留无关的既有配置；Codex hooks 还需要在宿主内审阅及信任。[任务绑定与权限](docs/CODEX.md)

| 要做的工作 | Claude Code | Codex |
|---|---|---|
| 完成一项有明确范围的改动 | `/run` | `$vibeproof-run` |
| 在独立 worktrees 协调多个任务 | `/wave` | `$vibeproof-wave` |
| 按适用的 lenses 审查当前代码 | `/sweep` | `$vibeproof-sweep` |
| 查看 findings 并协调维护 | `/maintain` | `$vibeproof-maintain` |

## 还包含什么？

| 功能 | 帮你做什么 |
|---|---|
| Scope 检查 | 在支持的编辑操作提早检查，也检查最后的 Git diff |
| 测试改动检查 | 报告有效测试数量下降及部分 expectation／shape 变化；不等于完整断言质量分析 |
| Runtime 证明 | 执行你宣告的 trigger，向你宣告的真相来源查询本次结果 |
| UI 证明 | 要求本次 runner 的新鲜案例结果；可选的 Playwright adapter 支持 browser proof |
| Review lenses | 从需求忠实度、设计、安全及测试充分性提问；仍需要 reviewer 判断 |
| 结构与凭证检查 | 部分错误处理、外部写入、secret、signature 及 reference 模式；覆盖取决于语言和 facts |
| Checker 注册验证 | 接受 checker 前，跑 red／green／bypass fixtures 及重跑一致性检查 |

一般测试的改动文件执行跟踪只支持 Python。可执行的 review 修复路径包括 Python、Go 和 Node/V8，取决于 runner。经验证的同文件 Python 函数／方法改名可保留原 finding。结构检查对 Python、Go、TS/JS 的支持深度不同，Rust 较有限。

[完整功能地图](docs/FEATURES.md) · [技术参考](docs/REFERENCE.md) · [Facts 格式](docs/FACTS.md)

## 看清楚结果代表什么

- `SHIP` 是按配置作出的任务判定，不会部署代码，也不保证每项需求都已满足。
- 部分问题先报告而不阻挡；删测试默认只报告。常设 `repo-review` 的 finding 不会自动阻止另一个任务。
- Hooks 涵盖支持的宿主输入，状态不可读时可能放行。Stop 检查在同一次停止流程只拦一次，之后独立回合可再检查；hooks 不是 sandbox。
- 本地 ledger 与 hash 不是不可修改的外部信任服务。存在接受风险的路径，包括 agent 签署。
- 提示文件、review lenses 与完成记录，不证明独立判断。业务正确性与安全仍需要合适的测试及人的决定。

[完整限制与 exit codes](docs/REFERENCE.md) · [完整流程](docs/USING.md)

## 试一个真实改动

[告诉我们结果](https://github.com/howardc38/vibeproof/issues/new?template=first-run.yml)：它抓到什么、误报什么，以及下一个任务会不会继续用。只需分享去除敏感信息的记录，不需要私人代码或凭证。

执行 framework 自己的测试：`python3 tests/run_without_silent_skips.py`。

维护 vibeproof 时，请修改 canonical 开发 repo；见[贡献方式](CONTRIBUTING.md)与[同步流程](docs/SYNC.md)。

MIT 授权。[授权条款](LICENSE) · [实现规格](docs/SPEC.md)
