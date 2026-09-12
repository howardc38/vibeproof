import json, os, subprocess, sys
from pathlib import Path
r = subprocess.run([sys.executable, "-c", "print('surface reply')"], capture_output=True, text=True)
assert r.returncode == 0 and r.stdout.strip() == 'surface reply'
print('surface reply verified', file=sys.stderr)
Path(os.environ["V4_SURFACE_RESULT"]).write_text(json.dumps({
 "schema":1,"run_id":os.environ["V4_SURFACE_RUN_ID"],"kind":"command",
 "cwd":str(Path.cwd()),"planned":["reply"],"checks":[{"id":"reply","status":"passed"}],"errors":[]}))
