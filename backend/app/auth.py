"""Who is acting, and the approver token (spec: Approval; ADR 0009).

There are no user accounts in this version (docs/DESIGN.md, Limitations): every
caller is `anonymous`. When `APPROVER_TOKEN` is set, deciding an approval over
HTTP needs the same value in `X-Approver-Token`. The CLI runs locally and calls
the runner directly, so it needs no token.
"""

import hmac
from typing import Annotated

from fastapi import Header, HTTPException

from app.config import settings


def current_user() -> str:
    return "anonymous"


def require_approver(x_approver_token: Annotated[str | None, Header()] = None) -> None:
    """FastAPI dependency on the decision route. Constant-time compare; bytes, since non-ASCII text is refused."""
    token = settings.approver_token
    if not token:
        return
    if x_approver_token is None or not hmac.compare_digest(x_approver_token.encode(), token.encode()):
        raise HTTPException(401, "missing or wrong X-Approver-Token")
