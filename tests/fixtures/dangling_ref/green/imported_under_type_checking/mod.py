"""GREEN: names bound inside `if TYPE_CHECKING:`.

The branch that reads an `if` collected classes, functions and plain
assignments and not imports -- and an import is what that block is *for*. So a
module re-exporting a type this way, and anything naming `mod.Bar`, got "the
code no longer has it".
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from other import Bar
    Payload: dict = {}
