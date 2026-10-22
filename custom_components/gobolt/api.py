"""GoBolt public tracking API client."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)

_NOT_FOUND_MESSAGE = "tracking number not found"


class GoBoltApiError(Exception):
    """Raised when a GoBolt API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"GoBolt API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class GoBoltApiClient:
    """Client for the public GoBolt tracking endpoint.

    No authentication, cookie or postal code: the endpoint is keyed on the
    tracking code alone. A known code answers HTTP 200 with the parcel object
    itself; an unknown one answers HTTP 404 with a JSON error body.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session
        self._warned_mismatch = False

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the parcel dict for a known parcel, or ``None`` when the
        endpoint reports the code as unknown. Any other failure or non-2xx
        status raises :class:`GoBoltApiError`; network errors propagate as
        ``aiohttp.ClientError``.
        """
        async with self._session.get(
            TRACKING_API_URL,
            params={"tracking_number": tracking_code},
            headers={"Accept": "application/json"},
        ) as response:
            status = response.status
            if status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = (
                        float(retry_after_header) if retry_after_header else None
                    )
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise GoBoltApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if status not in (200, 404):
                raise GoBoltApiError(f"HTTP {status}", status_code=status)
            try:
                # content_type=None: consumer endpoints routinely serve JSON as
                # text/plain, and aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                if status == 404:
                    raise GoBoltApiError("HTTP 404", status_code=404) from err
                raise GoBoltApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise GoBoltApiError(
                "unexpected body (not a JSON object)", status_code=status
            )

        if status == 404:
            message = payload.get("message")
            if (
                isinstance(message, str)
                and message.strip().lower() == _NOT_FOUND_MESSAGE
            ):
                return None
            raise GoBoltApiError("HTTP 404", status_code=404)

        returned = payload.get("tracking_number")
        if not isinstance(returned, str) or not returned:
            raise GoBoltApiError("response without a tracking number")
        if returned.upper() != tracking_code.upper() and not self._warned_mismatch:
            self._warned_mismatch = True
            _LOGGER.warning(
                "GoBolt returned a tracking number that differs from the "
                "requested one; the requested code is used"
            )
        return payload
