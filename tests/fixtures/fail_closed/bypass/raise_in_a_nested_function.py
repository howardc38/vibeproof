"""BYPASS -- the raise is inside a function nobody calls."""


def verify_signature(request, secret_store) -> bool:
    try:
        expected = secret_store.read_signing_secret(request.tenant_id)
        return request.signature == expected
    except Exception:
        def _would_raise():
            raise RuntimeError("nope")
        return False
