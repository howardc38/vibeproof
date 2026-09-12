# Try vibeproof on one real task

Start with the [standalone demo](../examples/first-proof/README.md). Then use this guide for **Claude Code in an existing git repository with a working test command**. Python currently has the broadest default test-execution tracing. This guide describes installed behavior, not a promise of complete correctness.

Codex / 雙宿主 / 双宿主: [host setup](CODEX.md). The instructions below retain the default Claude workflow.
## Who does what

| You decide | Your coding agent can execute |
|---|---|
| The requested outcome and files it may change | Installation, task commands, reading findings and repairing code |
| The real test command and environment it needs | Running that command and recording results |
| Whether facts about external writes, auth and UI are correct | Drafting those facts from code for your review |
| Whether an unresolved risk is acceptable | Showing the risk and proposed next step |

You do not need to type every lifecycle command yourself. The installed Claude commands and agents are prompts that help run the workflow. The executable checks and hooks enforce their particular checks; they do not prove that an agent followed every instruction.

## 1. Prepare

Use macOS or Linux with Git and Python 3.12+. Native Windows is not validated. Use a clean branch or another checkout of your project. Keep a restorable commit before installation. Confirm that your real test command works outside vibeproof first.

```sh
git clone https://github.com/howardc38/vibeproof.git ~/vibeproof
~/vibeproof/bin/v4 --repo /absolute/path/to/project init
```

Replace `/absolute/path/to/project` with your actual project path. If you already cloned vibeproof, use that checkout instead of cloning over it.

The initializer creates configuration, registries, acceptance scaffolding and a generated block in `CLAUDE.md`. In the target project's `.v4/config.json`, set `test_command` to the command that actually runs its tests, for example:

```json
"test_command": "python3 -m unittest discover -s tests -v"
```

This is one JSON property, not a complete replacement config. Keep the other fields. The command must produce a recognizable test summary. A quiet command that prints nothing is refused. Runner output formats vary; if a passing suite is rejected, include its sanitized output in an issue. Node's TAP reporter is recognized; the default reporter may not be.

The other initial questions concern the UI suite, runtime read-back, its query command, and acceptance criteria. A real UI or external write needs a real command and evidence. If a capability does not apply, record that decision explicitly using the documented config shape rather than leaving `TODO` or inventing a passing command. Acceptance criteria are needed when using measurement rounds.

For a surface suite, connect its actual results using the [shipped integration
guide](../.v4/surface/INTEGRATION.md). Naming a command or printing a passing
summary is insufficient for surface proof. The optional Playwright helper does
not install browsers; use your host's permissions and a disposable test environment.

## 2. Install

```sh
~/vibeproof/bin/v4 --repo /absolute/path/to/project install
```

Installation runs the checkers' and conditional detectors' own fixtures. Expect minutes rather than an instant setup; one local Python example took about four minutes. This is an example measurement, not an installation SLA.

**What changes:** the installer adds checkers, detectors, fixtures under `.v4/fixtures`, reviewer lenses, optional surface adapters, Claude prompts and a hook-settings template. It writes a launcher and a framework-location file. The kernel continues to live in your vibeproof checkout; keep it available or set `V4_HOME` to its new location. A tiny example received over 1,500 fixture files. Inspect the installation diff before committing it.

Read the complete install result. A reported refusal or missing tool needs attention even if some components installed successfully.

## 3. Confirm facts and connect hooks

The installer drafts `.v4/facts.<repo>.json.draft`. Check the suggested calls against source, correct their kinds, and confirm or replace each `AUTO:` absence in your own words. A wrapper can hide an external write from the draft. Rename the reviewed file by removing only the final `.draft`, then run:

```sh
cd /absolute/path/to/project
./bin/v4 facts validate
```

The complete schema and examples are in [FACTS.md](FACTS.md). Do not copy another project's declarations or simply delete `AUTO:` without checking the claim.

For Claude hooks:

- If `.claude/settings.json` does not exist, copy `.claude/settings.template.json` to it.
- If settings already exist, **merge the template's hook entries** into the existing `hooks` object. Preserve other hooks, permissions and settings. Do not overwrite the whole file.
- Start a fresh Claude Code session in the target repo after configuring the hooks.

```sh
./bin/v4 doctor
```

`doctor` checks wiring, and may still report warnings. Read them. An exit 0 or a settings file does not prove that a hook has fired; the task's ship report records observed hook activity.

## 4. Give the agent one bounded request

After setup, you can paste this into Claude Code. Replace the outcome and allowed-files placeholders with your actual request:

```text
Use the installed vibeproof workflow for one small task.

Outcome: <the exact behavior I want>
Allowed files: <the source and test paths for this task>

Run ./bin/v4 doctor and read any setup problems first. Open one task using my
request verbatim. Derive its checks and read the rules. Write the required
task-specific engagement statements before editing. Implement the change and
real regression tests, run the checks, account for the request, and run ship
through the coordinator. Follow the installed /run instructions for its roles.

If a check cannot run, explain what is missing. Bring me any proposed risk
acceptance before signing it. At the end show what passed, what remains
reported or unsupported, whether hooks fired, and the exact ship result.
```

This prompt is a convenience, not an additional security control. The kernel permits some agent signatures; the instruction above reserves that decision for you in this workflow.

For an operator running the CLI, the main commands are:

```sh
./bin/v4 task --id first-change --request '<your exact request>' --scope 'src/**,tests/**'
./bin/v4 derive --task first-change
./bin/v4 status --task first-change
./bin/v4 engage --claim <actual-claim-id> --text '<what its rule means here>'
# Make the scoped change and its tests. Answer every required engagement.
./bin/v4 check --task first-change
./bin/v4 ship --task first-change
```

Use your project's actual paths; a root-level `app.py` is not inside `src/**`. Replace placeholders rather than pasting them literally. The complete loop, including request coverage and review, is in [USING.md](USING.md).

## 5. Read the result

| Result | Next action |
|---|---|
| Failing check | Read its concrete finding, repair the cause, rerun the check |
| Stale answer | Related inputs changed; re-derive when instructed and recheck |
| Unsupported or missing environment | Supply the required tool, command or proof; do not describe it as a pass |
| Report-only finding | It may not hold this ship; still inspect it and record the follow-up |
| `HELD` | Read the reasons; repeating ship is not a repair |
| `SHIP` | The configured predicate passed; read any remaining reports and degraded-hook notice |

`SHIP` records the framework's decision. It does not deploy the application or intercept every way to commit or publish code. The stop hook can interrupt an unfinished turn once and then lets the second stop through. Your own CI, functional acceptance and review remain useful.

## Tell us whether it earned a second use

After your first real task, use [the feedback issue](https://github.com/howardc38/vibeproof/issues/new?template=first-run.yml). What took longest? Did it catch a real problem? Which finding was wrong or hard to act on? Would you keep it enabled for the next task?

No usage telemetry is added by this launch package. Share only the details you choose; remove private paths and credentials from logs.

Framework maintainers: develop in the private canonical repo; keep an adopter
pointing to a stable released framework checkout. See [SYNC.md](SYNC.md).
