"""GREEN: the handler leaves normally, but carries the failure out as a value.

This is the one exemption that is *not* structural, and it is the single largest
source of false positives on real code.  These handlers do not re-raise -- yet no
caller can read their result as success, which is what "fails closed" has to
mean once a codebase has an error-return protocol instead of exceptions.

Three shapes, all measured on ``adopter_a``:

* a constructor that says so           -- ``ToolResult.failure(...)``
* a verdict dict returned              -- ``{"ok": False, "error": ...}``
* a verdict dict assigned then returned -- ``summary["status"] = "failed"``

The cost is real and named in the report: a caller that ignores the return value
turns every one of these back into a fail-open, and this rule cannot see that.
"""

import logging

import requests

logger = logging.getLogger(__name__)


class ToolResult:
    @staticmethod
    def failure(message: str) -> "ToolResult":
        return ToolResult()


def archive(url: str, slug: str) -> ToolResult:
    try:
        requests.post(f"{url}/archive", json={"slug": slug}, timeout=10)
    except requests.exceptions.RequestException as exc:
        logger.warning("archive failed: %s", exc)
        return ToolResult.failure(str(exc))
    return ToolResult()


def update_brand(url: str, slug: str, fields: dict) -> dict:
    try:
        requests.patch(f"{url}/brands/{slug}", json=fields, timeout=10)
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "error": f"write failed: {exc}"}
    return {"ok": True, "slug": slug}


def refresh_token(adapter, brand_id: str) -> dict:
    summary = {"brand_id": brand_id, "status": "ok"}
    try:
        summary["token"] = adapter.refresh_long_lived_token()
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = type(exc).__name__
        return summary
    return summary
