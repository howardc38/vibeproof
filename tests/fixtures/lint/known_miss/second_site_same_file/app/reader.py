from core.util import _first


def head(items):
    return _first(items)


def also_head(items):
    # Same private, same file, second function. The id carries no
    # enclosing symbol, so this is not a second finding -- the price of
    # a baseline that survives a function being renamed.
    return _first(items)
