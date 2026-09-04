def _resolve_context(name):
    return {"name": name}


def public_context(name):
    return _resolve_context(name)
