# 从短演示到自己的第一个 task

先运行 [独立演示](../examples/first-proof/README.md)，再把完整 framework 用在一个可以还原的 git 项目。最容易开始的环境是 Claude Code、Python 3.12+，以及已经能运行的测试。完整流程使用 macOS 或 Linux；尚未验证原生 Windows。

Codex / 雙宿主 / 双宿主: [host setup](CODEX.md). The instructions below retain the default Claude workflow.
## 你决定，agent 执行

你提供想要的结果、允许修改的文件、真正的测试命令，以及是否接受未解决风险的决定。agent 可以处理安装、流程命令、读取检查结果与修复代码。你不需要每次亲自输入所有命令。

## 先完成安装

保留安装前可以还原的 commit。如果已经下载 vibeproof，使用现有 checkout，不要重复 clone 到同一个目录。

Surface suite 也要接入实际执行结果；可按[接线指引](../.v4/surface/INTEGRATION.md)使用可选 Playwright helper，并确认本机 browser／loopback 权限。

```sh
git clone https://github.com/howardc38/vibeproof.git ~/vibeproof
~/vibeproof/bin/v4 --repo "/absolute/path/to/project" init
```

把路径换成你的项目。然后在项目的 `.v4/config.json` 设置真正的测试命令，例如：

```json
"test_command": "python3 -m unittest discover -s tests -v"
```

这只是一个 JSON 字段，不是整份 config。保留其他设置。先确认测试命令在 framework 外可以运行；完全安静或无法识别测试摘要的命令，可能被拒绝。

```sh
~/vibeproof/bin/v4 --repo "/absolute/path/to/project" install
```

完整安装会复制 checker、detector、fixtures、hooks 和提示文件，并验证 fixtures，通常需要数分钟。它会增加很多文件；一个本地小例子有超过 1,500 个 fixture 文件。核心 kernel 仍留在 vibeproof checkout，不能安装后就把它删掉。

## 核对 facts，接上 hooks

installer 会生成 `.v4/facts.<repo>.json.draft`。逐项对照代码，修正猜错的调用与类型。每个 `AUTO:` 不存在声明，都需要确认后用自己的话说明；不要只是把 `AUTO:` 删掉。

检查完，把文件最后的 `.draft` 去掉，再运行：

```sh
cd "/absolute/path/to/project"
./bin/v4 facts validate
```

如果项目有 UI 或外部写入，就配置真正能运行的 surface/runtime 证明；没有的能力才明确记录不适用。不要用固定输出或空命令制造通过结果。[完整字段与格式](GETTING_STARTED.md)、[facts 格式](FACTS.md)。

- 没有 `.claude/settings.json`：可从 `.claude/settings.template.json` 复制创建。
- 已有 settings：只合并 template 的 hooks，保留原本 hooks、权限与其他设置，不能整份覆盖。
- 在目标 repo 开一个新的 Claude Code session，再运行 `./bin/v4 doctor`。

`doctor` 检查接线；exit 0 不代表每个 hook 已实际触发。最后的 task 报告会显示观察到的 hook 活动。

## 粘贴给 agent 的第一个任务

替换要求和路径后，可以使用以下指令：

```text
请用这个 repo 已安装的 vibeproof 流程完成一个小任务。

要求：<我要的具体行为>
允许修改：<这次的源代码与测试路径>

先运行 ./bin/v4 doctor，读清楚设置问题。用我的要求原文开 task，
生成检查项目并读取相关规则。修改前完成需要的 engagement 说明，
再实现功能和真正的回归测试，运行 checks、交代需求覆盖，
按照已安装 /run 的角色分工完成 ship。

不能运行的检查要说明缺什么。任何风险接受都先把理由交给我决定，
不要自行签署。最后列出通过、仍在报告、unsupported 的项目，
hook 有没有触发，以及实际的 SHIP／HELD 结果。
```

这是一段方便使用的指令，不是新的安全边界；kernel 本身存在 agent 签署路径。

## 怎样知道可以继续？

| 结果 | 怎样处理 |
|---|---|
| FAIL | 看具体原因，修正后重跑 |
| STALE | 相关输入改过了，按提示重新 derive／check |
| UNSUPPORTED | 补环境、命令或证据，不能说成已通过 |
| Report-only | 未必立即阻止 ship，但仍要检查和交代 |
| HELD | 修正列出的原因；重复调用 ship 不会代替修正 |
| SHIP | 配置的判定已通过；仍要看留下的报告和 hook 提示 |

SHIP 不会替你部署，也不保证需求、安全性或所有分支正确。Stop hook 只拦第一次，下一次可以放行。

完整操作细节见 [USING.md](USING.md)（包含更多角色与判断），[技术限制](REFERENCE.md) 也应保留在采用决定中。

## 第一个 task 后

告诉我们：哪一步最麻烦？有发现你确认值得修的问题吗？哪些是误报？下一个任务会不会继续用？[反馈入口](https://github.com/howardc38/vibeproof/issues/new?template=first-run.yml)。只需自愿分享脱敏记录，不需要私人代码或凭证。

Framework maintainers: develop in the private canonical repo; keep an adopter
pointing to a stable released framework checkout. See [SYNC.md](SYNC.md).
