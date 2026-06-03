"""Tests for APNs device-token storage (single-user device_store).

Points the store at a temp DB (never a real one) and exercises the
upsert/list/revoke/re-own behaviour the push sender relies on.
"""
from __future__ import annotations

import pytest

from backend.ios_gateway import device_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(device_store, "_db_path", lambda: str(tmp_path / "device_tokens.db"))
    return tmp_path


def test_upsert_and_list_multi_device(store):
    device_store.upsert_device_token("LiveUser", "tok-iphone", "com.hime", "production")
    device_store.upsert_device_token("LiveUser", "tok-ipad", "com.hime", "production")
    tokens = {t["device_token"] for t in device_store.list_device_tokens("LiveUser")}
    assert tokens == {"tok-iphone", "tok-ipad"}
    assert device_store.list_device_tokens("other") == []


def test_reown_moves_token(store):
    device_store.upsert_device_token("uA", "shared")
    device_store.upsert_device_token("uB", "shared")  # same device re-registers as uB
    assert device_store.list_device_tokens("uA") == []
    assert len(device_store.list_device_tokens("uB")) == 1


def test_revoke(store):
    device_store.upsert_device_token("LiveUser", "tok")
    assert device_store.revoke_device_token("tok") is True
    assert device_store.list_device_tokens("LiveUser") == []
    assert device_store.revoke_device_token("tok") is False  # already revoked


def test_reactivate_on_reupsert(store):
    device_store.upsert_device_token("LiveUser", "tok")
    device_store.revoke_device_token("tok")
    device_store.upsert_device_token("LiveUser", "tok")  # re-register clears revoked_at
    assert len(device_store.list_device_tokens("LiveUser")) == 1
