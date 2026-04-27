"""twikit-based tweet source. Implemented in Step B."""
from __future__ import annotations

from .base import Tweet


class TwikitSource:
    """Cookie-auth source via the ``twikit`` library. STUB — Step B will implement."""

    def __init__(self, auth_token: str | None) -> None:
        if not auth_token:
            raise RuntimeError(
                "X_AUTH_TOKEN missing — see README section 'Dummy X account setup'"
            )
        self._auth_token = auth_token

    async def fetch_new(self, handle: str, since_id: str | None) -> list[Tweet]:
        raise NotImplementedError("twikit source: Step B")
