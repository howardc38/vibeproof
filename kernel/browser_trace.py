"""Chromium V8 evidence tied to served bytes or verified source-map coordinates."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .surface import server_problem
from .analysis.symbols import ts_mask


def mapped_function(source, line, column, symbol):
    """A unique function/arrow name at the map's exact UTF16 coordinate."""
    if type(line) is not int or type(column) is not int or min(line, column) < 0:
        return None
    lines = source.splitlines(keepends=True)
    if line >= len(lines) or column * 2 >= len(lines[line].encode('utf-16-le')):
        return None
    try:
        offset = sum(map(len, lines[:line])) + len(lines[line].encode('utf-16-le')[:column*2].decode('utf-16-le'))
    except UnicodeDecodeError:
        return None
    masked = ts_mask(source)
    patterns = [r'\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(',
                r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:[\w$]+|\([^;{}]*\))\s*(?::[^=;{}]+)?=>']
    names = [(m.start(1), m.group(1)) for p in patterns for m in re.finditer(p, masked)]
    selected = [name for pos, name in names if pos == offset and (not symbol or name == symbol)]
    if len(selected) != 1 or sum(name == selected[0] for _, name in names) != 1:
        return None
    return selected[0]


def target_sha(root, target):
    path = (Path(root) / target).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observed(directory, root, run_id, target, symbol, expected_sha, coordinate=None):
    """(executed, calls, provenance). Missing/invalid evidence is None, not PASS."""
    root = Path(root).resolve()
    detail = {"source": "chromium-v8", "target": str(target),
              "source_sha256": expected_sha, "run_id": run_id, "matched": [], "valid": False}

    def unknown(reason):
        return None, 0, {**detail, "reason": reason}

    profiles = sorted(Path(directory).glob("*.json"))
    if not profiles:
        return None, 0, {}
    if expected_sha is None or target_sha(root, target) != expected_sha:
        return unknown("target is absent, outside the repo or changed during tracing")
    calls, seen = 0, False
    source = (root / target).read_bytes().decode('utf-8')
    for path in profiles:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return unknown("unreadable browser coverage")
        if (not isinstance(data, dict) or type(data.get("schema")) is not int or
                data["schema"] != 1 or data.get("run_id") != run_id or
                data.get("repo") != str(root) or data.get("browser") != "chromium"):
            return unknown("browser coverage identity does not match this invocation")
        cwd = data.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).resolve().is_relative_to(root):
            return unknown("browser runner cwd is outside the declared repository")
        if (data.get("update_snapshots") != "none" or
                type(data.get("retries")) is not int or data["retries"] != 0 or
                type(data.get("retry")) is not int or data["retry"] != 0 or
                data.get("expected_status") != "passed" or
                data.get("status") not in ("passed", "failed")):
            return unknown("skips, expected failures, snapshot updates or retries cannot establish browser execution proof")
        problem = server_problem(data.get("managed_servers"), root)
        if problem:
            return unknown(problem)
        scripts = data.get("scripts")
        if not isinstance(scripts, list):
            return unknown("browser coverage has no script collection")
        for script in scripts:
            if not isinstance(script, dict):
                continue
            try:
                url = urlsplit(script.get("url", ""))
            except (TypeError, ValueError):
                return unknown("invalid served script URL")
            if url.scheme not in ("http", "https") or not url.hostname:
                continue
            if script.get("source_sha256") != expected_sha:
                mapping = script.get("source_map")
                if not isinstance(mapping, dict) or not re.fullmatch('[a-f0-9]{64}', str(mapping.get('sha256', ''))):
                    continue
                mapped = mapping.get('functions')
                if not isinstance(mapped, list) or coordinate is not None:
                    continue
                for entry in mapped:
                    if not isinstance(entry, dict) or entry.get('source_sha256') != expected_sha:
                        continue
                    name = mapped_function(source, entry.get('original_line'), entry.get('original_column'), symbol)
                    if name is None:
                        continue
                    count = entry.get('count')
                    if type(count) is not int or count < 0:
                        return unknown('invalid mapped V8 entry count')
                    seen = True
                    calls += count
                    detail['matched'].append({'url':script['url'], 'profile':path.name,
                        'source_map_sha256':mapping['sha256'], 'generated_sha256':script.get('source_sha256'),
                        'symbol':name, 'original_line':entry['original_line'], 'original_column':entry['original_column']})
                continue
            functions = script.get("functions")
            if not isinstance(functions, list):
                return unknown("matching script has no V8 function coverage")
            detail["matched"].append({"url": script["url"], "profile": path.name})
            if coordinate is not None:
                return unknown("declaration proof requires actual Node instruction observation; browser function coverage cannot substitute")
            for function in functions:
                if not isinstance(function, dict):
                    return unknown("invalid V8 function coverage")
                if symbol and function.get("functionName") != symbol:
                    continue
                ranges = function.get("ranges") or []
                if not ranges or not isinstance(ranges[0], dict):
                    continue
                count = ranges[0].get("count")
                if type(count) is not int or count < 0:
                    return unknown("invalid V8 entry count")
                seen = True
                calls += count
    if not seen:
        return unknown("browser did not observe the exact target bytes/function")
    return calls > 0, calls, {**detail, "valid": True}
