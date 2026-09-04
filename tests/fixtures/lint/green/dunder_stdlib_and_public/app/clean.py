import json
import os.path
from core.util import public


class Row:
    def __init__(self, raw):
        self._raw = raw

    def dump(self):
        return json.dumps(self._raw)


def load(path):
    if os.path.exists(path):
        return public([path])
    return []
