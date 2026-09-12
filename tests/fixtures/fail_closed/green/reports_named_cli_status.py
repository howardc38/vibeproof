import sys
from pathlib import Path
def launch():
    status = 0
    if len(sys.argv) > 1:
        try:
            Path(sys.argv[1]).write_text('output')
        except OSError as error:
            report = {'valid': False, 'reason': str(error)}
            status = 5
    print('finished')
    return status
if __name__ == '__main__':
    raise SystemExit(launch())
