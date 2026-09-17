from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agent_api import access_service


@pytest.mark.asyncio
async def test_created_secret_is_encrypted_at_rest_and_not_exposed_by_authenticated_principal(monkeypatch):
    """Only owner management may recover the encrypted copy; normal auth never does."""
    inserted = []

    async def _insert(row):
        inserted.append(row)

    monkeypatch.setattr(access_service, "_insert_key", _insert)
    monkeypatch.setattr(access_service, "encrypt_secret", lambda value: f"cipher:{value[::-1]}")
    monkeypatch.setattr(
        access_service,
        "decrypt_secret",
        lambda value: value.removeprefix("cipher:")[::-1],
    )

    created = await access_service.create_key("app-a", "publisher-a", "production", None)

    assert created.secret.startswith("qza_")
    assert len(created.secret) >= 40
    assert len(inserted) == 1
    assert inserted[0].secret_hash != created.secret
    assert created.secret not in vars(inserted[0]).values()
    assert inserted[0].secret_ciphertext != created.secret
    assert access_service.recover_key_secret(inserted[0]) == created.secret

    key = SimpleNamespace(
        id=inserted[0].id,
        app_id="app-a",
        owner_user_id="publisher-a",
        key_prefix=created.prefix,
        secret_hash=inserted[0].secret_hash,
        status="active",
        expires_at=None,
    )

    async def _load(secret_hash):
        return key if secret_hash == key.secret_hash else None

    async def _active(*_args):
        return True

    async def _limit(*_args):
        return None

    monkeypatch.setattr(access_service, "_load_key_by_hash", _load)
    monkeypatch.setattr(access_service, "_is_api_release_active", _active)
    monkeypatch.setattr(access_service, "_enforce_fixed_window_limit", _limit)

    principal = await access_service.authenticate_bearer(created.secret)

    assert principal.owner_user_id == "publisher-a"
    assert principal.app_id == "app-a"
    assert not hasattr(principal, "secret")
    with pytest.raises(access_service.ApiAuthError, match="invalid_api_key"):
        await access_service.authenticate_bearer("qza_not-the-created-secret")


@pytest.mark.asyncio
async def test_create_key_does_not_limit_active_key_count(monkeypatch):
    inserted = []

    async def _insert(row):
        inserted.append(row)

    monkeypatch.setattr(access_service, "_insert_key", _insert)
    monkeypatch.setattr(access_service, "encrypt_secret", lambda value: f"cipher:{value}")

    for index in range(3):
        await access_service.create_key("app-a", "publisher-a", f"key-{index}", None)

    assert len(inserted) == 3


@pytest.mark.asyncio
async def test_expired_or_inactive_release_key_is_rejected_before_rate_limit(monkeypatch):
    """A revoked/expired/unpublished API key must never consume a request slot."""
    expired = SimpleNamespace(
        id="key-a",
        app_id="app-a",
        owner_user_id="publisher-a",
        key_prefix="qza_test",
        secret_hash=access_service._hash_secret("qza_expired"),
        status="active",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
    )
    rate_limit_called = False

    async def _load(_secret_hash):
        return expired

    async def _limit(*_args):
        nonlocal rate_limit_called
        rate_limit_called = True

    monkeypatch.setattr(access_service, "_load_key_by_hash", _load)
    monkeypatch.setattr(access_service, "_enforce_fixed_window_limit", _limit)

    with pytest.raises(access_service.ApiAuthError, match="invalid_api_key"):
        await access_service.authenticate_bearer("qza_expired")
    assert rate_limit_called is False


@pytest.mark.asyncio
async def test_valid_key_for_an_inactive_release_is_distinguished_from_an_invalid_key(monkeypatch):
    """A publisher can remediate a disabled release only when clients receive a 403-compatible error."""
    secret = "qza_inactive_release_key_012345678901234567890123456789"
    key = SimpleNamespace(
        id="key-a", app_id="app-a", owner_user_id="publisher-a", key_prefix="qza_inactive",
        secret_hash=access_service._hash_secret(secret), status="active", expires_at=None,
    )
    rate_limit_called = False

    async def _load(_secret_hash):
        return key

    async def _inactive(*_args):
        return False

    async def _limit(*_args):
        nonlocal rate_limit_called
        rate_limit_called = True

    monkeypatch.setattr(access_service, "_load_key_by_hash", _load)
    monkeypatch.setattr(access_service, "_is_api_release_active", _inactive)
    monkeypatch.setattr(access_service, "_enforce_fixed_window_limit", _limit)

    with pytest.raises(access_service.ApiReleaseInactiveError, match="api_release_inactive"):
        await access_service.authenticate_bearer(secret)
    assert rate_limit_called is False


@pytest.mark.asyncio
async def test_revoke_app_access_marks_every_active_key_unusable(monkeypatch):
    """Removing API publishing must invalidate all keys only after the caller commits."""
    revoked = []

    async def _revoke(app_id, reason):
        revoked.append((app_id, reason))
        return 2

    monkeypatch.setattr(access_service, "_revoke_active_keys", _revoke)
    monkeypatch.setattr(access_service, "revoke_external_sessions", AsyncMock(return_value=0))

    assert await access_service.revoke_app_access("app-a", reason="app_unpublished") == 2
    assert revoked == [("app-a", "app_unpublished")]


@pytest.mark.asyncio
async def test_revoke_app_access_also_deactivates_external_workspaces(monkeypatch):
    async def _revoke(*_args):
        return 1

    deactivated = []

    async def _deactivate(app_id, key_kind=None):
        deactivated.append((app_id, key_kind))
        return 3

    monkeypatch.setattr(access_service, "_revoke_active_keys", _revoke)
    monkeypatch.setattr(access_service, "revoke_external_sessions", _deactivate)

    assert await access_service.revoke_app_access("app-a") == 1
    assert deactivated == [("app-a", None)]


@pytest.mark.asyncio
async def test_execution_slots_cap_concurrent_requests_per_key():
    """An iframe-derived short token cannot bypass the publisher's in-flight cap."""
    async with AsyncExitStack() as stack:
        for _ in range(access_service.MAX_CONCURRENT_REQUESTS_PER_KEY):
            await stack.enter_async_context(access_service.execution_slot("key-concurrency-test"))
        with pytest.raises(access_service.ApiConcurrencyLimitError, match="concurrency_limit_exceeded"):
            async with access_service.execution_slot("key-concurrency-test"):
                pass
