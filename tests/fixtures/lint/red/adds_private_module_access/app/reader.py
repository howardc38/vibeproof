import core.resolver as _resolver


def read(brand_id):
    return _resolver._resolve_root(brand_id)
