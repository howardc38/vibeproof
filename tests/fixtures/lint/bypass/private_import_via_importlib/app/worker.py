import importlib


def go():
    m = importlib.import_module('core._resolver')
    return m.resolve()
