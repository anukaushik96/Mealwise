"""Shared response envelope.

The docs describe a symmetric envelope on both success and failure::

    success: {"success": True,  "data": <payload>, "message": str}
    failure: {"success": False, "error": {"message": str}, "hint": str}

The LIVE server is asymmetric, verified by probing mcp.swiggy.com/im directly:

success
    ``structured_content`` is the BARE payload, with no ``success`` key at all
    (``get_addresses`` returns ``{addresses, total, pagination, imWidgetV2Eligible}``).
failure
    ``structured_content`` IS the documented wrapper, plus a report id:
    ``{"success": False, "error": {"message", "reportId", "reportHint"}}``, and the
    result carries ``is_error: True``.

So ``success: True`` is never actually seen, and requiring a ``success`` key rejects
every successful response. :func:`unwrap` therefore treats "no ``success`` key" as the
success case and keys failure off ``success is False``.

The text content block is human-readable prose, not JSON -- never parse it. Prefer
``structured_content``, which :class:`~swiggy.session.SwiggySession` already does.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SwiggyToolError",
    "SwiggyIndeterminateOrderError",
    "is_failure",
    "unwrap",
]


class SwiggyToolError(Exception):
    """A tool returned ``success: False``, or the transport produced something unusable."""

    def __init__(self, tool: str, detail: str, hint: str | None = None) -> None:
        self.tool = tool
        self.detail = detail
        self.hint = hint
        super().__init__(f"{tool}: {detail} — {hint}" if hint else f"{tool}: {detail}")


class SwiggyIndeterminateOrderError(Exception):
    """A non-idempotent mutation failed in a way that hides whether it landed.

    ``place_food_order`` and ``checkout`` are NOT idempotent: on a 5xx you must
    reconcile via ``get_food_orders`` / ``get_orders`` before retrying, or you risk a
    duplicate order. Callers must never blind-retry on this error.
    """

    def __init__(self, tool: str, reconcile_with: str, cause: BaseException | None = None) -> None:
        self.tool = tool
        self.reconcile_with = reconcile_with
        self.__cause__ = cause
        super().__init__(
            f"{tool} failed indeterminately — it is not idempotent, so check "
            f"{reconcile_with} for an order that may already exist before retrying"
        )


def is_failure(raw: Any) -> bool:
    """True only for the explicit failure wrapper -- not for a bare payload."""
    return isinstance(raw, dict) and raw.get("success") is False


def unwrap(tool: str, raw: Any) -> Any:
    """Narrow a response to its payload, raising :class:`SwiggyToolError` on failure.

    Only ``success is False`` means failure. A payload with no ``success`` key is the
    normal success case on the live server and is returned as-is.
    """
    if not isinstance(raw, dict):
        raise SwiggyToolError(tool, f"unrecognised response: {raw!r:.200}")

    if is_failure(raw):
        error = raw.get("error") or {}
        message = error.get("message") or "unknown error"
        parts = [raw.get("hint") or error.get("reportHint")]
        if error.get("reportId"):
            parts.append(f"report ID: {error['reportId']}")
        hint = " — ".join(p for p in parts if p)
        raise SwiggyToolError(tool, message, hint or None)

    # Documented shape, kept in case the server ever sends it.
    if raw.get("success") is True and "data" in raw:
        return raw["data"]

    # Live shape: the payload itself.
    return raw
