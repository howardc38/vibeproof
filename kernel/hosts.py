"""Host selection and evidence summaries; verdicts remain in the kernel."""
import json
from pathlib import Path

SUPPORTED = ("claude", "codex")
CONFIG_KEY = "agent_hosts"
HOOKS = ("write_block", "bash_guard", "stop_gate")


def selected(root: Path, override=None) -> tuple[str, ...]:
    if override is not None:
        names = list(SUPPORTED) if override == "both" else [override]
    else:
        p = Path(root) / ".v4/config.json"
        cfg = json.loads(p.read_text()) if p.is_file() else {}
        names = cfg.get(CONFIG_KEY, ["claude"])
    return validate_selection(names)


def validate_selection(names) -> tuple[str, ...]:
    if not isinstance(names, list) or not names or any(n not in SUPPORTED for n in names):
        raise ValueError(f"{CONFIG_KEY} must be a nonempty list drawn from {SUPPORTED}")
    return tuple(dict.fromkeys(names))


def configure(root: Path, choice: str) -> None:
    names = selected(root, choice)
    p = Path(root) / ".v4/config.json"
    cfg = json.loads(p.read_text())
    cfg[CONFIG_KEY] = list(names)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def doctrine_files(root: Path) -> tuple[Path, ...]:
    return tuple(Path(root) / ("CLAUDE.md" if h == "claude" else "AGENTS.md")
                 for h in selected(root))


def evidence(conn, *, task_id=None, host=None, session=None, agent=None) -> dict:
    """Separate observed handlers from checks that could not run.

    Legacy rows remain evidence of activity, never Codex-specific coverage.
    """
    sql = "SELECT task_id,payload,created_at FROM event WHERE kind='hook_seen'"
    args = []
    if task_id is not None:
        sql += " AND task_id=?"
        args.append(task_id)
    out = {"observed": [], "checked": [], "stood_down": [], "legacy": 0, "last": None, "events": [], "tools": []}
    observed, checked, down = set(), set(), set()
    for row in conn.execute(sql, args):
        try:
            p = json.loads(row["payload"])
        except (ValueError, TypeError):
            out["legacy"] += 1
            continue
        if host and p.get("host") != host:
            continue
        if session and p.get("session") != session:
            continue
        if agent and p.get("agent_id") != agent:
            continue
        if not p.get("host"):
            out["legacy"] += 1
        name = p.get("hook")
        if name not in HOOKS:
            continue
        observed.add(name)
        if p.get("hook_event_name"):
            out["events"].append(p["hook_event_name"])
        if p.get("tool_name"):
            out["tools"].append(p["tool_name"])
        if p.get("basis") in ("unreadable", "unparseable", "cannot import"):
            down.add(name)
        elif not p.get("scattered"):
            checked.add(name)
        out["last"] = row["created_at"]
    out["events"] = sorted(set(out["events"]))
    out["tools"] = sorted(set(out["tools"]))
    out.update(observed=sorted(observed), checked=sorted(checked), stood_down=sorted(down))
    return out


def hook_groups(host: str) -> dict:
    """Expected event / matcher groups for an installed host."""
    write = "Write|Edit|MultiEdit|NotebookEdit" if host == "claude" else "^apply_patch$"
    def handler(name):
        return {"type": "command", "command":
                'D=$(git rev-parse --show-toplevel 2>/dev/null); '
                f'if [ -n "$D" ]; then V4_HOST={host} exec python3 "$D/hooks/{name}.py"; '
                f'else echo "v4 {name}: no git root; hook did not run" >&2; echo \'{{}}\'; fi',
                "timeout": 30}
    groups = {
        "PreToolUse": [
            {"matcher": write, "hooks": [handler("write_block")]},
            {"matcher": "^Bash$", "hooks": [handler("bash_guard")]}],
        "Stop": [{"hooks": [handler("stop_gate")]}],
    }
    groups["SessionStart"] = [{"hooks": [handler("session_context")]}]
    prefix = "v4-" if host == "codex" else ""
    groups["SubagentStart"] = [{"matcher": "^" + prefix + "(task-splitter|worker|reviewer|checker-author|monitor)$",
                                "hooks": [handler("session_context")]}]
    groups["SubagentStop"] = [{"matcher": "^" + prefix + "worker$", "hooks": [handler("stop_gate")]}]
    return groups


def _owns_handler(handler, script):
    command = handler.get("command")
    if not isinstance(command, str):
        return False
    return (command.startswith("D=$(git rev-parse --show-toplevel") and
            f'"$D/hooks/{script}.py"' in command)


def activate_hooks(root: Path, host: str) -> Path:
    """Explicitly merge handlers, preserving unrelated settings and hooks."""
    rel = ".claude/settings.json" if host == "claude" else ".codex/hooks.json"
    p = Path(root) / rel
    data = json.loads(p.read_text()) if p.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError(f"{p} must contain an object with an object-valued hooks field")
    target = data.setdefault("hooks", {})
    for event, groups in hook_groups(host).items():
        existing = target.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"{event} must contain a list of matcher groups")
        for group in groups:
            command = group["hooks"][0]["command"]
            # Replace only the same framework handler, leaving other handlers intact.
            script = command.split("/hooks/")[1].split('.py')[0]
            retained = []
            for old in existing:
                hs = [h for h in old.get("hooks", [])
                      if not _owns_handler(h, script)]
                if hs:
                    retained.append({**old, "hooks": hs})
            existing[:] = retained
            existing.append(group)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return p

def codex_assets(src: Path) -> dict[str, str]:
    """Generate Codex roles from the maintained role bodies; no second doctrine."""
    assets = {}
    def prompt(name, directory):
        text = (Path(src) / ".claude" / directory / (name + ".md")).read_text()
        if text.startswith("---\n"):
            _, meta, text = text.split("---", 2)
        return text.strip()
    shared = (
        "\n\nCodex host contract:\n"
        "- Run CLI commands with explicit --repo; supply --task only on commands that accept it. Use --help for action-specific flags. A shell export does not bind hooks.\n"
        "- Bind the assigned V4 task using the session/agent identity supplied by SessionStart/SubagentStart.\n"
        "- For another worktree use an explicit exec working directory. Patch tools may restrict writes to the host project; dispatch the worker in its authorized worktree or use a permitted tool there.\n"
        "- Never infer a worker identity from the parent session alone.\n"
        "- Reviewer source access is read-only in intent; review add/done still write evidence through v4.\n"
    )
    for role in ("task-splitter", "worker", "reviewer", "checker-author"):
        instructions = prompt(role, "agents") + shared
        name = "v4-" + role
        assets[f".codex/agents/{name}.toml"] = (
            "name = " + json.dumps(name) + "\n"
            "description = " + json.dumps("vibeproof " + role) + "\n"
            "developer_instructions = " + json.dumps(instructions, ensure_ascii=False) + "\n")
    monitor = "\n\n".join((Path(src) / ".github/monitor" / name).read_text() for name in ("PROMPT.md", "SCOPE.md"))
    assets[".codex/agents/v4-monitor.toml"] = (
        'name = "v4-monitor"\ndescription = "Independent vibeproof monitor: findings and evidence, no source edits"\n'
        "developer_instructions = " + json.dumps(monitor + shared, ensure_ascii=False) + "\n")
    contracts = {
        "run": "Run one requested development change through the installed vibeproof lifecycle.",
        "wave": "Coordinate multiple requested vibeproof tasks, each in its own Git worktree.",
        "sweep": "Run the installed vibeproof lens review when due; use an independent reviewer for every lens.",
        "maintain": "Configure or run vibeproof maintenance, review cadence, authorized repairs and actionable notifications.",
    }
    for name, description in contracts.items():
        body = prompt(name, "commands")
        adaptation = (
            "\n\nCodex orchestration:\n"
            "- Use the native v4-task-splitter, v4-worker, v4-reviewer and v4-checker-author agents.\n"
            "- Start every native role with a fresh context and explicit inputs; inherited-context forks are unavailable in some ephemeral Codex runs. For reviewers pass only repo, lens and necessary diff coordinates.\n"
            "- Dispatch up to the available agent capacity, queue the rest, and wait for every requested result. "
            "This replaces the Claude source's all-at-once scheduling and single-shell export instructions.\n"
            "- One worker task per worktree. Bind the task before writing; use explicit --repo and supported --task flags.\n"
            "- The orchestrator owns ship and risk decisions; never attribute an agent signature to a person.\n"
        )
        assets[f".agents/skills/vibeproof-{name}/SKILL.md"] = (
            "---\nname: vibeproof-" + name + "\ndescription: " + description + "\n---\n\n"
            + body + shared + adaptation)
    assets[".codex/hooks.template.json"] = json.dumps(
        {"description": "vibeproof Codex hooks; merge explicitly, then review host trust.",
         "hooks": hook_groups("codex")}, ensure_ascii=False, indent=2) + "\n"
    assets[".codex/config.template.toml"] = (
        '# Merge into project config only after reviewing required paths.\n'
        '# Select this profile in a host that supports permission profiles.\n'
        '# Git snapshot/worktree operations need separate authorization.\n'
        'default_permissions = "vibeproof"\n\n'
        '[permissions.vibeproof]\nextends = ":workspace"\n\n'
        '[permissions.vibeproof.filesystem.":workspace_roots"]\n'
        '"." = "write"\n".git" = "read"\n".git/v4" = "write"\n'
        '".codex" = "read"\n".agents" = "read"\n\n'
        '[permissions.vibeproof.network]\nenabled = false\n')
    return assets

def permission_profile(root: Path, *, git_operations=False) -> dict:
    """Local workspace permissions with an explicit shared ledger allowance.

    Git operations are a separate choice; this never grants full machine access.
    """
    import subprocess
    root = Path(root).resolve()
    value = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root,
                           capture_output=True, text=True, check=True).stdout.strip()
    common = (root / value).resolve()
    fs = {
        ":root": "read", ":tmpdir": "write", ":slash_tmp": "write",
        ":workspace_roots": {".": "write", ".git": "read", ".codex": "read", ".agents": "read"},
        str(common / "v4"): "write",
    }
    if git_operations:
        for name in ("objects", "refs", "logs", "worktrees"):
            fs[str(common / name)] = "write"
        for name in ("index", "HEAD", "COMMIT_EDITMSG", "ORIG_HEAD", "MERGE_HEAD",
                     "MERGE_MSG", "MERGE_MODE", "AUTO_MERGE", "SQUASH_MSG", "packed-refs"):
            fs[str(common / name)] = "write"
            fs[str(common / (name + ".lock"))] = "write"
    return {"filesystem": fs, "network": {"enabled": False}}


def inline_toml(value) -> str:
    """Serialize only the small scalar/table shape of a permission profile."""
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(k) + "=" + inline_toml(v) for k, v in value.items()) + "}"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    raise ValueError("unsupported permission-profile value")
