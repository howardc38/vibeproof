"""GREEN (mandatory): the two secret-store rollback loops, distilled.

Transcribed from ``core/config/secure_store.py:105`` and
``core/tenants/secret_store.py:125`` in ``adopter_a``.  Both were cited as
flagship fail-open examples and **both are correct code**; a rule that reports
them is wrong, and this file is what stops that regression.

Read the inner handler alone and it looks damning: an ``except Exception`` around
a secret write whose entire body is ``rollback_failures.append(...)``.  Read the
escape paths and there is no way out of the outer handler that is not a
``raise`` -- the inner ``except`` is accumulating which accounts failed to roll
back so the exception it is about to throw can name them.

``exit_kind`` is the function that has to get this right.
"""

import keyring

SERVICE = "example-app"


class SecureStoreError(RuntimeError):
    pass


class SecureStoreRollbackError(SecureStoreError):
    pass


def read_provider_secret(account: str) -> str | None:
    return keyring.get_password(SERVICE, account)


def write_provider_secret(account: str, value: str) -> None:
    keyring.set_password(SERVICE, account, value)


def delete_provider_secret(account: str) -> None:
    keyring.delete_password(SERVICE, account)


def write_provider_secret_updates(updates: dict) -> list:
    ordered = sorted(updates)
    snapshots = {account: read_provider_secret(account) for account in ordered}
    applied: list = []
    try:
        for account in ordered:
            applied.append(account)
            write_provider_secret(account, str(updates[account]))
    except Exception as exc:
        rollback_failures: list = []
        for account in reversed(applied):
            try:
                previous = snapshots[account]
                if previous is None:
                    delete_provider_secret(account)
                else:
                    write_provider_secret(account, previous)
            except Exception:
                rollback_failures.append(account)
        if rollback_failures:
            raise SecureStoreRollbackError(
                "provider secret update failed and rollback could not restore accounts: "
                + ", ".join(sorted(rollback_failures))
            ) from exc
        raise SecureStoreError(
            "provider secret update failed; all applied accounts were rolled back"
        ) from exc
    return ordered


def write_brand_secret_updates(slug: str, updates: dict) -> list:
    ordered_paths = sorted(updates)
    snapshots = {path: read_provider_secret(f"{slug}:{path}") for path in ordered_paths}
    applied: list = []
    try:
        for path in ordered_paths:
            applied.append(path)
            value = updates[path]
            if value:
                write_provider_secret(f"{slug}:{path}", value)
            else:
                delete_provider_secret(f"{slug}:{path}")
    except Exception as exc:
        rollback_failures: list = []
        for path in reversed(applied):
            try:
                previous = snapshots[path]
                if previous is None:
                    delete_provider_secret(f"{slug}:{path}")
                else:
                    write_provider_secret(f"{slug}:{path}", previous)
            except Exception:
                rollback_failures.append(path)
        if rollback_failures:
            raise SecureStoreRollbackError(
                "brand secret update failed and rollback could not restore fields: "
                + ", ".join(sorted(rollback_failures))
            ) from exc
        raise SecureStoreError(
            "brand secret update failed; all applied fields were rolled back"
        ) from exc
    return ordered_paths
