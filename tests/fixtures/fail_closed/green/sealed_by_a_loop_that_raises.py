"""GREEN: the read-back idiom, written with a `for` instead of an `if`.

`trys_sealed_by_a_later_check` looked at `ast.If` only, so the loop below was
not read as sealing anything and this handler was reported with "execution
continues as if the operation had succeeded" -- which the next two lines make
false. A gate whose message is false about the code it points at is worse than
one that says nothing, which is the sentence that function's docstring opens
with.

Same block, same narrowness: the raise still has to be reachable from the
handler. Sealing across a block boundary is a wider question and this is not
it.
"""

import requests


def notify(url, payload):
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass
    for missing in undelivered(url, payload):
        raise RuntimeError(f"notify did not land: {missing}")
