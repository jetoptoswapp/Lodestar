"""結構化錯誤格式（沿用 ver2）。

所有結構化錯誤回應 = {"detail": {"category", "message", ...extra}}。
用法：raise HTTPException(status_code=..., detail=error_detail("missing_prd", "PRD must exist first."))
"""
from __future__ import annotations


def error_detail(category: str, message: str, **extra: object) -> dict[str, object]:
    detail: dict[str, object] = {"category": category, "message": message}
    detail.update(extra)
    return detail
