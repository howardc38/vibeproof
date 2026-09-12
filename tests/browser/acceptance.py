#!/usr/bin/env python3
"""Real browser lane, separate from the Python suite; missing deps fail, never skip.

npm ci --prefix tests/browser
npx --prefix tests/browser playwright install chromium
python3 tests/browser/acceptance.py [--out <private directory>]
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from kernel import runner, redgreen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    directory = (args.out or Path(tempfile.mkdtemp(prefix="v4-browser-acceptance-"))).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    modules = ROOT / "tests/browser/node_modules"
    cli = modules / "@playwright/test/cli.js"
    if not cli.exists():
        sys.exit("Playwright missing: npm ci --prefix tests/browser (this lane cannot skip)")
    repo = directory / "adopter"
    repo.mkdir(exist_ok=True)
    shutil.copytree(ROOT / ".v4/surface", repo / ".v4/surface", dirs_exist_ok=True)
    for name in ("server.py", "client.js", "surface.spec.cjs"):
        shutil.copy2(ROOT / "tests/browser" / name, repo / name)
    if not (repo / "node_modules").exists():
        (repo / "node_modules").symlink_to(modules.resolve(), target_is_directory=True)
    (repo / ".gitignore").write_text("node_modules/\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = shlex.join([sys.executable, "server.py", str(port), str(directory / "state.json")])
    config = """const {surfaceConfig} = require('./.v4/surface/playwright.cjs');
module.exports = surfaceConfig({testDir:'.',testMatch:'surface.spec.cjs',workers:1,
timeout:5000,expect:{timeout:2000},reporter:[['line']],
use:{baseURL:BASE,headless:true},
webServer:[{command:COMMAND+' '+(process.env.PROBE_SERVER || 'good'),
url:BASE,reuseExistingServer:true,timeout:10000}]});
""".replace("BASE", json.dumps(f"http://127.0.0.1:{port}" )).replace("COMMAND", json.dumps(server))
    (repo / "playwright.config.cjs").write_text(config)
    base = shlex.join([shutil.which("node") or "node", str(cli), "test"])
    cases = [("good", "", "", 0), ("lost-write", "", "lost-write", 1),
             ("broken-ui", "", "broken-ui", 1), ("skip", "skip", "", 1),
             ("list", "", "", 1), ("filtered", "", "", 1),
             ("api-only", "api-only", "", 1),
             ("expected-failure", "expected-failure", "", 1),
             ("reporter-override", "", "", 1), ("existing-server", "", "", 1)]
    (directory / "plan.json").write_text(json.dumps(cases, indent=2))
    results = []
    for name, mode, server_mode, expected in cases:
        (directory / "state.json").unlink(missing_ok=True)
        extra = {"list":" --list", "filtered":" --grep arithmetic",
                 "reporter-override":" --reporter=line"}.get(name, "")
        command = f"PROBE_MODE={shlex.quote(mode)} PROBE_SERVER={shlex.quote(server_mode)} " + base + extra
        (repo / ".v4/config.json").write_text(json.dumps({"surface_command":command,
            "surface_kind":"browser", "surface_required":["caption-save"]}))
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        old_server = None
        try:
            if name == "existing-server":
                old_server = subprocess.Popen(shlex.split(server) + ["broken-ui"], cwd=repo,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as response:
                            assert b"Wrong version" in response.read()
                        break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise AssertionError("control server did not start")
            checker = ROOT / "checkers/surface_proof.py"
            result = runner.run_checker(repo_root=repo, checker_path=checker,
                registered_sha=hashlib.sha256(checker.read_bytes()).hexdigest(),
                subject_payload={"repo_root":str(repo),"subject_refs":[]},
                subject_refs=[{"kind":"file","path":"surface.spec.cjs"}], timeout_sec=35)
            row = {"case":name,"expected":expected,"exit":result.exit_code,
                   "stdout":result.stdout,"stderr":result.stderr,"evidence":result.out_payload}
            if old_server:
                assert old_server.poll() is None, "checker must not kill the preexisting server"
            results.append(row)
            (directory / "results.json").write_text(json.dumps(results, indent=2))
            print(f"{name}: expected {expected}, observed {result.exit_code}", flush=True)
            assert result.exit_code == expected, row
        finally:
            if old_server:
                old_server.terminate()
                old_server.wait(timeout=5)
    print(f"10 real browser controls passed; artifacts: {directory}")

    # The managed Python server and Node runner do not execute client.js.
    # Only Chromium's actual V8 evidence can close this frontend finding.
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=V4 browser acceptance",
                    "-c", "user.email=browser-test@localhost", "commit", "-qm",
                    "Record isolated browser closure fixture"], check=True)
    command = ["env", f"NODE_PATH={modules.resolve()}", shutil.which("node") or "node",
               str(cli), "test", "--grep", "save caption"]
    trace_results = []
    for name, mutation, expected in [
        ("behavioral-red", ("client.js", "caption:document.querySelector('input').value",
                            "caption:'wrong persisted bytes'"), True),
        ("harmless-red", ("client.js", "return 'never called'", "return 'still never called'"), False),
        ("startup-is-not-red", ("client.js", "async function saveCaption()", "async function ()"), False),
    ]:
        proof = redgreen.verify(repo, command=command, test_path="surface.spec.cjs",
            target_file="client.js", target_symbol="saveCaption", mutation=mutation, timeout=35)
        row = {"case":name,"expected_ok":expected,"proof":proof.as_dict()}
        trace_results.append(row)
        (directory / "browser-trace-results.json").write_text(json.dumps(trace_results, indent=2))
        assert proof.ok is expected, row
        assert proof.browser_trace and proof.browser_trace["source"] == "chromium-v8", row
        print(f"browser trace {name}: expected {expected}, observed {proof.ok}", flush=True)
    for name, extra, mode, symbol, expected in [
        ("uncalled", [], "", "unusedCaption", False),
        ("list", ["--list"], "", "saveCaption", None),
        ("api-only", [], "api-only", "saveCaption", None),
        ("expected-failure", [], "expected-failure", "saveCaption", None),
    ]:
        details = {}
        rc, executed, calls, output = redgreen._run_traced(repo,
            ["env", f"PROBE_MODE={mode}"] + command + extra,
            "client.js", symbol, timeout=35, trace_details=details)
        row = {"case":name,"expected_executed":expected,"executed":executed,
               "exit":rc,"calls":calls,"detail":details,"output":output}
        trace_results.append(row)
        (directory / "browser-trace-results.json").write_text(json.dumps(trace_results, indent=2))
        assert executed is expected, row
        print(f"browser trace {name}: expected {expected}, observed {executed}", flush=True)
    print("7 real browser execution/closure controls passed")
    mapped_acceptance(repo, directory, modules, cli, config, server)


def mapped_acceptance(repo, directory, modules, cli, config, server):
    """A real minifying TS compiler, actual Chromium, and broken-map controls."""
    source = "type CaptionInput = HTMLInputElement;\n" + (repo / 'client.js').read_text().replace(
        "document.querySelector('input').value", "(document.querySelector('input') as CaptionInput).value")
    source += '\nwindow.__unusedCaption = unusedCaption;\n'
    (repo / 'client.ts').write_text(source)
    shutil.copy2(ROOT / 'tests/browser/build.cjs', repo / 'build.cjs')
    (repo / '.gitignore').write_text('node_modules/\nbundle/\n')
    config = config.replace(json.dumps(server), json.dumps('node build.cjs && ' + server))
    (repo / 'playwright.config.cjs').write_text(config)
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=V4 browser acceptance',
                    '-c','user.email=browser-test@localhost','commit','-qm','Record compiled TypeScript fixture'],check=True)
    command = ['env',f'NODE_PATH={modules.resolve()}','PROBE_BUNDLE=1',shutil.which('node') or 'node',
               str(cli),'test','--grep','save caption']
    results=[]
    for name, mutation, expected in [
        ('mapped-behavioral-red', ('client.ts', "caption:(document.querySelector('input') as CaptionInput).value",
                                 "caption:'wrong persisted bytes'"), True),
        ('mapped-harmless-red', ('client.ts', "return 'never called'", "return 'still never called'"), False),
        ('mapped-startup-is-not-red', ('client.ts', 'async function saveCaption()', 'async function ()'), False),
    ]:
        proof=redgreen.verify(repo, command=command, test_path='surface.spec.cjs',target_file='client.ts',
                             target_symbol='saveCaption',mutation=mutation,timeout=35)
        row={'case':name,'expected_ok':expected,'proof':proof.as_dict()};results.append(row)
        (directory/'sourcemap-results.json').write_text(json.dumps(results,indent=2))
        assert proof.ok is expected, row
        assert proof.browser_trace['matched'][0].get('source_map_sha256'), row
        assert (repo/'client.ts').read_text() == source, 'proof changed original source'
        print(f'{name}: expected {expected}, observed {proof.ok}',flush=True)
    for mode,symbol,expected in [('linked','unusedCaption',False),('inline','saveCaption',True),
                                  ('missing','saveCaption',None),('stale','saveCaption',None),
                                  ('no-content','saveCaption',None),('cross-origin','saveCaption',None)]:
        details={}
        rc,executed,calls,out=redgreen._run_traced(repo,['env',f'PROBE_MAP={mode}']+command,
                'client.ts',symbol,timeout=35,trace_details=details)
        row={'case':mode+'-'+symbol,'expected_executed':expected,'executed':executed,'exit':rc,
             'calls':calls,'detail':details,'output':out};results.append(row)
        (directory/'sourcemap-results.json').write_text(json.dumps(results,indent=2))
        assert rc == 0 and executed is expected,row
        print(f'map {mode} {symbol}: expected {expected}, observed {executed}',flush=True)
    print('9 real compiled TypeScript browser controls passed')


if __name__ == "__main__":
    main()
