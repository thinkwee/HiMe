"""APNs: each device token is pushed through its own environment."""
import sys
import types
from types import SimpleNamespace

import pytest

from backend.ios_gateway import apns as apns_mod


class _Resp:
    def __init__(self, ok: bool, desc: str = "") -> None:
        self.is_successful = ok
        self.description = desc
        self.status = "200" if ok else "400"


@pytest.fixture
def fake_aioapns(monkeypatch, tmp_path):
    created: list[dict] = []
    sent: list[tuple[bool, str]] = []

    class APNs:
        def __init__(self, **kw) -> None:
            self.sandbox = kw["use_sandbox"]
            created.append(kw)

        async def send_notification(self, req):
            sent.append((self.sandbox, req.device_token))
            # A token only works in the environment it was minted for.
            ok = req.device_token.startswith("sbx") == self.sandbox
            return _Resp(ok, "" if ok else "BadDeviceToken")

    mod = types.ModuleType("aioapns")
    mod.APNs = APNs
    mod.NotificationRequest = lambda **kw: SimpleNamespace(**kw)
    mod.PushType = SimpleNamespace(ALERT="alert")
    monkeypatch.setitem(sys.modules, "aioapns", mod)
    key = tmp_path / "k.p8"
    key.write_text("PEM")
    revoked: list[str] = []
    monkeypatch.setattr(apns_mod.device_store, "revoke_device_token", lambda t: revoked.append(t) or True)
    return SimpleNamespace(created=created, sent=sent, revoked=revoked, key=str(key))


def _settings(key: str, env: str = "production") -> SimpleNamespace:
    return SimpleNamespace(
        APNS_ENABLED=True, APNS_KEY_PATH=key, APNS_KEY_ID="K", APNS_TEAM_ID="T",
        APNS_BUNDLE_ID="b", APNS_ENV=env,
    )


async def test_each_token_uses_its_own_environment(fake_aioapns, monkeypatch) -> None:
    tokens = [
        {"device_token": "sbx-1", "environment": "sandbox"},
        {"device_token": "prod-1", "environment": "production"},
        {"device_token": "prod-2", "environment": None},  # old build: APNS_ENV fallback
    ]
    monkeypatch.setattr(apns_mod.device_store, "list_device_tokens", lambda uid: tokens)
    sender = apns_mod.APNSSender(_settings(fake_aioapns.key))
    assert await sender.send("u", "t", "b") == 3
    assert fake_aioapns.sent == [(True, "sbx-1"), (False, "prod-1"), (False, "prod-2")]
    assert len(fake_aioapns.created) == 2  # one client per environment, reused
    assert fake_aioapns.revoked == []


async def test_dead_token_is_revoked(fake_aioapns, monkeypatch) -> None:
    # A token whose recorded env is wrong/stale fails in that env and is retired.
    tokens = [{"device_token": "prod-stale", "environment": "sandbox"}]
    monkeypatch.setattr(apns_mod.device_store, "list_device_tokens", lambda uid: tokens)
    sender = apns_mod.APNSSender(_settings(fake_aioapns.key))
    assert await sender.send("u", "t", "b") == 0
    assert fake_aioapns.revoked == ["prod-stale"]
