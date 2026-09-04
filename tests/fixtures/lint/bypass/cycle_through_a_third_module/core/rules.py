# The evasion: `rules` does not import `models` back. It imports a third
# module, and that one closes the loop -- so a cycle detector that only
# asks "do these two import each other" sees nothing.
from core.registry import lookup


def validate(obj):
    return lookup(type(obj).__name__) is not None
