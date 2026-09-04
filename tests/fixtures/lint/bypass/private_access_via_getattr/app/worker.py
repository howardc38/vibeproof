import core


def go():
    r = getattr(core, '_resolver')
    return r.resolve()
