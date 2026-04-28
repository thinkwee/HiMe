"""One-time QR login flow for the WeChat ClawBot / iLink bot API.

Run as a script::

    python -m backend.weixin.qr_login [--out PATH]

Fetches a QR descriptor from ``ilinkai.weixin.qq.com``, prints the URL (and
an ANSI-rendered QR if the optional ``qrcode`` package is available), then
polls until the user has scanned it via the ClawBot plugin
(WeChat → Settings → Plugins → ClawBot). On success the resulting
``bot_token`` is written to ``--out`` (default: the path in
``WEIXIN_BOT_TOKEN_PATH`` or ``./data/weixin_bot_token.json``).

The token is long-lived; re-run the script only when the iLink ``getupdates``
endpoint starts returning 401.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import random
import struct
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ILINK_BASE = "https://ilinkai.weixin.qq.com"
# Sent in every ``base_info`` block on POST endpoints. iLink rejects calls
# whose channel_version is missing or empty.
CHANNEL_VERSION = "1.0.2"
# ``bot_type=3`` selects the personal-account ClawBot (Tencent uses other
# values for service-account / WeCom variants).
BOT_TYPE = 3
# QR-status polling requires an explicit client-version header on top of
# the standard ``X-WECHAT-UIN`` / ``Authorization`` triplet.
QR_STATUS_HEADERS = {"iLink-App-ClientVersion": "1"}


def wechat_uin_header() -> str:
    """Generate the ``X-WECHAT-UIN`` header value.

    iLink expects a fresh base64-encoded random uint32 per request to defeat
    naive replay attempts. Reusing the same value across calls is enough to
    get the request silently dropped on some endpoints.
    """
    return base64.b64encode(struct.pack("I", random.randint(0, 0xFFFFFFFF))).decode("ascii")


def common_headers(bot_token: str | None = None) -> dict[str, str]:
    """Return the headers shared by every iLink call."""
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": wechat_uin_header(),
    }
    if bot_token:
        h["Authorization"] = f"Bearer {bot_token}"
    return h


async def _get_qr(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.get(
        f"{ILINK_BASE}/ilink/bot/get_bot_qrcode",
        params={"bot_type": BOT_TYPE},
        headers=common_headers(),
    )
    resp.raise_for_status()
    return resp.json()


async def _poll_qr(
    client: httpx.AsyncClient,
    qrcode: str,
    timeout: float = 120.0,
) -> dict[str, Any] | None:
    """Poll the QR-status endpoint until the user confirms the scan.

    The ``status`` field cycles ``wait`` → ``scaned`` → ``confirmed`` (or
    ``expired``); on ``confirmed`` the response also carries the
    ``bot_token`` we need to persist. We poll on the token's presence so
    minor revisions of the status enum don't break the flow.
    """
    headers = {**common_headers(), **QR_STATUS_HEADERS}
    deadline = time.time() + timeout
    last_status = ""
    while time.time() < deadline:
        try:
            resp = await client.get(
                f"{ILINK_BASE}/ilink/bot/get_qrcode_status",
                params={"qrcode": qrcode},
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.debug("qr_status transient error: %s", exc)
            await asyncio.sleep(2.0)
            continue
        if resp.status_code == 200:
            payload = resp.json()
            status = payload.get("status") or ""
            if status and status != last_status:
                print(f"  status: {status}")
                last_status = status
            if payload.get("bot_token") or status == "confirmed":
                return payload
            if status == "expired":
                logger.warning("QR code expired before scan")
                return None
        await asyncio.sleep(2.0)
    return None


def _print_qr(url_or_data: str) -> None:
    """Render a QR in the terminal if ``qrcode`` is installed; else print URL."""
    print(f"\n[WeChat ClawBot QR] {url_or_data}\n")
    try:
        import qrcode  # type: ignore

        qr = qrcode.QRCode(border=1)
        qr.add_data(url_or_data)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        print(
            "(install `qrcode` to render the QR in your terminal — "
            "`pip install qrcode`)"
        )


async def run_qr_login(out_path: Path) -> bool:
    """Execute the full QR login flow. Persists the ``bot_token`` to disk.

    Returns ``True`` on success, ``False`` if the user did not scan in time
    or the iLink response was malformed.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        qr = await _get_qr(client)
        url = qr.get("qrcode_img_content") or ""
        qrcode = qr.get("qrcode") or ""
        if not url or not qrcode:
            logger.error("Unexpected QR response from iLink: %s", qr)
            return False

        print(
            "\nOpen WeChat → Settings → Plugins → ClawBot, then scan this QR:"
        )
        _print_qr(url)
        print("Waiting for scan + confirm (timeout 120s)...")

        result = await _poll_qr(client, qrcode)
        if not result:
            print("Timed out without a scan.")
            return False

        bot_token = result.get("bot_token") or ""
        if not bot_token:
            logger.error("QR confirmed but no bot_token in response: %s", result)
            return False

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Persist auxiliary fields too — ``ilink_user_id`` is the bot's own
        # iLink ID (handy when reading server logs) and ``baseurl`` may
        # differ from the public ILINK_BASE on regional accounts.
        record = {
            "bot_token": bot_token,
            "ilink_bot_id": result.get("ilink_bot_id", ""),
            "ilink_user_id": result.get("ilink_user_id", ""),
            "baseurl": result.get("baseurl", ""),
        }
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2)
        )
        try:
            out_path.chmod(0o600)
        except OSError:
            pass
        print(f"\nbot_token saved to {out_path}")
        if record["ilink_user_id"]:
            print(f"  bot user_id: {record['ilink_user_id']}")
        return True


def load_bot_token(path: Path) -> str | None:
    """Read the cached ``bot_token`` from ``path``; return ``None`` if absent."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get("bot_token") or None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read WeChat bot_token from %s: %s", path, exc)
        return None


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="WeChat ClawBot one-time QR login (iLink bot API)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Path to write the bot_token JSON "
            "(default: WEIXIN_BOT_TOKEN_PATH or ./data/weixin_bot_token.json)"
        ),
    )
    args = parser.parse_args()

    out_path = args.out
    if out_path is None:
        try:
            from ..config import settings

            out_path = Path(settings.WEIXIN_BOT_TOKEN_PATH)
        except Exception:  # pragma: no cover — running without backend env
            out_path = Path("./data/weixin_bot_token.json")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ok = asyncio.run(run_qr_login(out_path))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
