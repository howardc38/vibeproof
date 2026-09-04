def has_permission(user, resource):
    try:
        return policy.authorize(user, resource)
    except Exception:
        raise AuthorizationUnavailable
