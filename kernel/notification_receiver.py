"""Manage the one-per-bot I/O receiver with macOS launchd, without an LLM loop."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile

from . import notifications


def definition(root, cfg):
    root = Path(root).resolve()
    launcher = root / "bin/v4"
    if not launcher.is_file():
        raise ValueError("receiver needs the installed adopter launcher")
    return {"Label": "org.vibeproof.telegram." + cfg["bot"],
            "ProgramArguments": [str(launcher), "--repo", str(root), "maintain", "listen"],
            "EnvironmentVariables": {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath},
            "WorkingDirectory": str(root), "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 60,
            "StandardOutPath": str(Path(cfg["storage"]) / "receiver.stdout.log"),
            "StandardErrorPath": str(Path(cfg["storage"]) / "receiver.stderr.log")}


def observe(label):
    target = f"gui/{os.getuid()}/{label}"
    result = subprocess.run(["launchctl", "print", target], text=True, capture_output=True)
    # launchctl output may include inherited environment values. Return only
    # service-state fields, never its raw stdout/stderr.
    fields = {}
    for key in ("state", "pid", "last exit code", "runs"):
        match = re.search(r"^\s*" + re.escape(key) + r"\s*=\s*([^\n]+)", result.stdout, re.M)
        if match:
            fields[key] = match.group(1).strip()
    return {"target": target, "query_exit": result.returncode, "loaded": result.returncode == 0,
            "state": fields, "basis": "launchctl print"}


def _atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".vibeproof-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if Path(temp).exists():
            Path(temp).unlink()


def manage(root, operation):
    if sys.platform != "darwin":
        raise ValueError("managed receiver currently supports macOS launchd; other hosts need their own supervised maintain listen service")
    if operation not in ("start", "stop", "status", "remove"):
        raise ValueError("receiver operation must be start, stop, status or remove")
    cfg = notifications.configuration(root)
    if not cfg:
        raise ValueError("configure Telegram before managing its receiver")
    import fcntl
    with (Path(cfg["storage"]) / "service.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _manage(root, operation, cfg)


def _manage(root, operation, cfg):
    spec = definition(root, cfg)
    label = spec["Label"]
    path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    owner_path = Path(cfg["storage"]) / "service.json"
    current = observe(label)
    if operation == "status":
        return {**current, "installed": path.is_file(), "receiver": notifications.status(root)["receiver"]}
    owner = json.loads(owner_path.read_text()) if owner_path.is_file() else None
    if path.exists() and (not owner or owner.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest()):
        raise ValueError("existing launchd definition is not owned by this receiver; it was left unchanged")
    if current["loaded"] and not owner:
        raise ValueError("a service with this label already exists without receiver ownership")
    if operation in ("stop", "remove"):
        if current["loaded"]:
            result = subprocess.run(["launchctl", "bootout", current["target"]], capture_output=True)
            if result.returncode:
                raise ValueError("launchctl could not stop the receiver; query its status")
        if operation == "remove":
            path.unlink(missing_ok=True); owner_path.unlink(missing_ok=True)
        return {**observe(label), "installed": path.is_file(), "operation": operation}
    content = plistlib.dumps(spec, sort_keys=True)
    if current["loaded"]:
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError("this bot already has a receiver with different coordinates; stop it before explicitly moving it")
        return {**current, "operation": "already started"}
    for key in ("StandardOutPath", "StandardErrorPath"):
        fd = os.open(spec[key], os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(fd)
    _atomic(path, content)
    _atomic(owner_path, (json.dumps({"root": str(Path(root).resolve()), "plist": str(path),
                                  "sha256": hashlib.sha256(content).hexdigest()}) + "\n").encode())
    result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], capture_output=True)
    if result.returncode:
        raise ValueError("launchctl did not start the receiver; definition retained for diagnosis")
    return {**observe(label), "operation": "started", "installed": True}
