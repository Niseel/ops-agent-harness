"""HTTP routes under /api (spec: API). Shared pieces: the runner dependency and masked JSON responses."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.log import secret_forms


def get_runner(request: Request):
    """The runner the lifespan opened. Tests override this dependency."""
    return request.app.state.runner


class MaskedJSONResponse(JSONResponse):
    """Every response body with configured secret values replaced by `***`.

    The run row and the checkpoint keep an objective as typed, so a secret pasted into one would come back.
    """

    def render(self, content: Any) -> bytes:
        body = super().render(content).decode("utf-8")
        for secret in secret_forms(settings.secrets()):
            body = body.replace(secret, "***")
        return body.encode("utf-8")
