"""Tests for the GoBolt API client."""

import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.gobolt.api import GoBoltApiClient, GoBoltApiError
from custom_components.gobolt.const import TRACKING_API_URL

from .payloads import delivered_sample

CODE = "EXAMPLE123456"
NOT_FOUND = {
    "type": "error",
    "message": "Tracking number not found",
    "status": 404,
    "ok": False,
}


def _session_returning(
    status: int, body: object = None, headers: dict | None = None
) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_the_parcel_object():
    payload = delivered_sample(CODE)
    session = _session_returning(200, payload)

    parcel = await GoBoltApiClient(session).async_get_parcel(CODE)

    assert parcel is payload


async def test_request_is_keyless_and_encodes_the_code_as_a_query_param():
    session = _session_returning(200, delivered_sample("A B&C"))

    await GoBoltApiClient(session).async_get_parcel("A B&C")

    session.get.assert_called_once_with(
        TRACKING_API_URL,
        params={"tracking_number": "A B&C"},
        headers={"Accept": "application/json"},
    )


async def test_get_parcel_returns_none_on_the_semantic_404():
    """An unknown code is a normal state, not an error."""
    client = GoBoltApiClient(_session_returning(404, NOT_FOUND))
    assert await client.async_get_parcel("EXAMPLE000000") is None


async def test_404_with_another_message_is_an_error():
    client = GoBoltApiClient(_session_returning(404, {"message": "Not Found"}))
    with pytest.raises(GoBoltApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 404


async def test_404_without_json_is_an_error():
    client = GoBoltApiClient(_session_returning(404, "<html>"))
    with pytest.raises(GoBoltApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 404


async def test_404_with_non_object_body_is_an_error():
    client = GoBoltApiClient(_session_returning(404, ["x"]))
    with pytest.raises(GoBoltApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_error_status():
    client = GoBoltApiClient(_session_returning(500, {}))
    with pytest.raises(GoBoltApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 500


async def test_429_carries_retry_after_seconds():
    client = GoBoltApiClient(_session_returning(429, {}, {"Retry-After": "120"}))
    with pytest.raises(GoBoltApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 120.0


@pytest.mark.parametrize(
    "headers", [{}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}]
)
async def test_429_without_seconds_has_no_retry_after(headers):
    client = GoBoltApiClient(_session_returning(429, {}, headers))
    with pytest.raises(GoBoltApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.retry_after is None


async def test_get_parcel_raises_on_unparseable_body():
    client = GoBoltApiClient(_session_returning(200, "not json"))
    with pytest.raises(GoBoltApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = GoBoltApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(GoBoltApiError):
        await client.async_get_parcel(CODE)


@pytest.mark.parametrize("identity", [None, "", 12345])
async def test_missing_identity_is_a_refresh_failure(identity):
    payload = delivered_sample(CODE)
    payload["tracking_number"] = identity
    client = GoBoltApiClient(_session_returning(200, payload))
    with pytest.raises(GoBoltApiError):
        await client.async_get_parcel(CODE)


async def test_case_difference_in_the_echo_is_not_a_mismatch(caplog):
    client = GoBoltApiClient(_session_returning(200, delivered_sample(CODE.lower())))
    assert await client.async_get_parcel(CODE) is not None
    assert "differs" not in caplog.text


async def test_mismatching_echo_warns_once_without_logging_codes(caplog):
    client = GoBoltApiClient(_session_returning(200, delivered_sample("OTHER99")))
    await client.async_get_parcel(CODE)
    await client.async_get_parcel(CODE)
    assert caplog.text.count("differs") == 1
    assert CODE not in caplog.text
    assert "OTHER99" not in caplog.text


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = GoBoltApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
