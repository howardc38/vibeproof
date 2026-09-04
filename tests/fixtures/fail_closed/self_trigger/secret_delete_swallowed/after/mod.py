def revoke(name):
    try:
        keyring.delete_password(SVC, name)
    except Exception:
        raise
    return True
