#!/usr/bin/env python3
"""Run a native host acceptance prompt against a disposable adopter.

This runner changes no user-level settings. Codex hook trust can be bypassed
only for an explicitly reviewed test invocation; the ordinary untrusted run is
also useful as a negative control.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

FRAMEWORK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRAMEWORK))
from kernel import hosts


def run(host, repo, prompt, out, *, git_operations=False, reviewed_hooks=False,
        timeout=1200, codex_bin=None):
    repo, out = Path(repo).resolve(), Path(out).resolve()
    if repo == FRAMEWORK or (repo / ".public-release.json").exists():
        raise ValueError("native acceptance uses a disposable adopter, not a framework checkout")
    out.mkdir(parents=True, exist_ok=True)
    if host == "codex":
        bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        binary = codex_bin or (str(bundled) if bundled.is_file() else shutil.which("codex"))
        if not binary:
            raise FileNotFoundError("Codex executable is unavailable")
        cmd = [binary, "exec", "--ignore-user-config", "--ephemeral", "--json",
               "-c", "projects={" + json.dumps(str(repo)) + '={trust_level="trusted"}}',
               "-c", 'default_permissions="vibeproof"',
               "-c", "permissions.vibeproof=" + hosts.inline_toml(
                   hosts.permission_profile(repo, git_operations=git_operations)),
               "-C", str(repo), "-"]
        if reviewed_hooks:
            cmd.insert(2, "--dangerously-bypass-hook-trust")
    elif host == "claude":
        binary = shutil.which("claude")
        if not binary:
            raise FileNotFoundError("Claude executable is unavailable")
        cmd = [binary, "-p", "--no-session-persistence", "--output-format", "stream-json",
               "--include-hook-events", "--verbose", "--setting-sources", "project",
               "--allowedTools", "Read,Edit,Write,Bash,Agent,Task,TaskOutput,TaskStop,Skill,Glob,Grep"]
    else:
        raise ValueError("unknown host")
    version = subprocess.check_output([binary, "--version"], text=True).strip()
    (out / "prompt.txt").write_text(prompt)
    started = time.monotonic()
    with (out / "events.jsonl").open("w") as stdout, (out / "stderr.log").open("w") as stderr:
        process = subprocess.Popen(cmd, cwd=repo, stdin=subprocess.PIPE, text=True,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            process.communicate(prompt, timeout=timeout)
            code = process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            code = 124
    result = {"host": host, "version": version, "repo": str(repo), "code": code,
              "seconds": round(time.monotonic() - started, 3),
              "reviewed_test_hook_bypass": reviewed_hooks if host == "codex" else False,
              "git_operations": git_operations,
              "framework_head": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=FRAMEWORK, text=True).strip(),
              "note": "Exit 0 is transport completion. Read back task, source, agent and hook evidence before declaring scenario PASS."}
    (out / "run.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", choices=("claude", "codex"), required=True)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--prompt-file", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--git-operations", action="store_true")
    p.add_argument("--reviewed-test-hooks", action="store_true")
    p.add_argument("--timeout", type=int, default=1200)
    p.add_argument("--codex-bin")
    args = p.parse_args()
    result = run(args.host, args.repo, args.prompt_file.read_text(), args.out,
                 git_operations=args.git_operations, reviewed_hooks=args.reviewed_test_hooks,
                 timeout=args.timeout, codex_bin=args.codex_bin)
    print(json.dumps(result))
    return result["code"]


if __name__ == "__main__":
    sys.exit(main())
