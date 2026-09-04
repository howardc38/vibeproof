"""RED: the same crossing as `analysis_imports_core`, written the short way.

`imported_modules` dropped every relative import -- `and not node.level` -- so
`from ..loader import load` was invisible to the checker whose whole subject is
which direction an import goes. Resolving one needs the importing module's
package, and `layers.scan` knows it: the file's own path.
"""
from ..loader import load


def apply(x):
    return load(x)
