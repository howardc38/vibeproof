"""BYPASS -- the raise is there and cannot run.

A rule asking "does the handler body contain a raise" is satisfied. Control
has already left.
"""


def verify_signature(request, secret_store) -> bool:
    try:
        expected = secret_store.read_signing_secret(request.tenant_id)
        return request.signature == expected
    except Exception:
        return False
        raise RuntimeError("unreachable")
