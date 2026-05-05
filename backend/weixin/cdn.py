"""iLink CDN upload helpers for WeChat ClawBot.

The iLink ``sendmessage`` endpoint expects media (image/video/file/voice) to
be referenced by an opaque CDN handle, not embedded inline. Producing that
handle requires a three-step dance:

1. Generate a random 16-byte AES-128 key. Encrypt the file with AES-128-ECB
   (PKCS7 padding); compute the rawsize/md5 of the *plaintext* and the
   ciphertext length.
2. ``POST /ilink/bot/getuploadurl`` with those metadata fields plus the
   target ``to_user_id`` and the AES key (hex). The server replies with a
   pre-signed CDN URL — either as a complete ``upload_full_url`` or as a
   query-param fragment that has to be appended to the CDN base.
3. ``POST`` the ciphertext to that URL with ``Content-Type:
   application/octet-stream``. The CDN echoes back an
   ``x-encrypted-param`` response header — that is the opaque download
   reference the receiver will use to fetch the image.

The reference is then folded into the outbound ``image_item.media`` block
on a regular ``sendmessage`` call (see :class:`WeixinSender.send_photo`).

Reverse-engineered from the public ``nightsailer/wechat-clawbot`` SDK and
the ``hao-ji-xing/openclaw-weixin`` protocol notes — Tencent does not
publish a formal spec for this endpoint.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

logger = logging.getLogger(__name__)

# Tencent's CDN base for personal-account ClawBot media. Hard-coded because
# the iLink ``getconfig`` response that would normally surface it is keyed
# off bot capabilities we don't currently fetch — and this URL has been
# stable across the published SDK releases.
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
# Three retries matches the wechat-clawbot defaults; the CDN occasionally
# 5xx's the first attempt under load.
_UPLOAD_MAX_RETRIES = 3


@dataclass
class UploadedImage:
    """Metadata produced by a successful CDN upload, sufficient to build
    the ``image_item.media`` block in a subsequent sendmessage call."""

    encrypt_query_param: str  # opaque download reference from CDN
    aeskey_hex: str  # 32-char hex of the 16-byte AES key
    plaintext_size: int
    ciphertext_size: int


def aes_ecb_padded_size(plaintext_size: int) -> int:
    """Return the AES-128-ECB ciphertext length for ``plaintext_size`` bytes
    under PKCS7 padding. PKCS7 always adds at least one byte of padding,
    even when the plaintext is already block-aligned, so the formula is
    ``ceil((n+1)/16)*16`` rather than ``ceil(n/16)*16``."""
    return math.ceil((plaintext_size + 1) / 16) * 16


def encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB encrypt ``plaintext`` with PKCS7 padding."""
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _build_upload_url(upload_full_url: str | None, upload_param: str | None,
                      filekey: str) -> str:
    """Pick the actual CDN URL to POST to. Newer iLink builds return a
    fully-formed ``upload_full_url``; older ones return just a query fragment
    we have to splice onto :data:`CDN_BASE_URL`."""
    full = (upload_full_url or "").strip()
    if full:
        return full
    if not upload_param:
        raise RuntimeError(
            "iLink getuploadurl returned neither upload_full_url nor "
            "upload_param — cannot construct CDN target.",
        )
    from urllib.parse import quote
    return (
        f"{CDN_BASE_URL}/upload"
        f"?encrypted_query_param={quote(upload_param)}"
        f"&filekey={quote(filekey)}"
    )


async def upload_ciphertext(
    client: httpx.AsyncClient,
    upload_full_url: str | None,
    upload_param: str | None,
    filekey: str,
    ciphertext: bytes,
) -> str:
    """PUT/POST encrypted bytes to the CDN and return the
    ``x-encrypted-param`` header (the download reference).

    Retries up to :data:`_UPLOAD_MAX_RETRIES` on 5xx; bails immediately on
    4xx since those indicate a malformed pre-signed URL or expired key,
    which a retry won't fix.
    """
    target = _build_upload_url(upload_full_url, upload_param, filekey)
    last_err: Exception | None = None
    for attempt in range(1, _UPLOAD_MAX_RETRIES + 1):
        try:
            resp = await client.post(
                target,
                content=ciphertext,
                headers={"Content-Type": "application/octet-stream"},
            )
            if 400 <= resp.status_code < 500:
                msg = resp.headers.get("x-error-message", resp.text[:200])
                raise RuntimeError(
                    f"CDN upload client error {resp.status_code}: {msg}",
                )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"CDN upload server error {resp.status_code}: "
                    f"{resp.headers.get('x-error-message', '')[:200]}",
                )
            ref = resp.headers.get("x-encrypted-param")
            if not ref:
                raise RuntimeError(
                    "CDN upload succeeded but response is missing the "
                    "x-encrypted-param header — cannot reference the upload.",
                )
            return ref
        except Exception as exc:
            last_err = exc
            # 4xx is non-retriable — propagate immediately.
            if "client error" in str(exc):
                raise
            if attempt < _UPLOAD_MAX_RETRIES:
                logger.warning(
                    "CDN upload attempt %d/%d failed (will retry): %s",
                    attempt, _UPLOAD_MAX_RETRIES, exc,
                )
            else:
                logger.error(
                    "CDN upload failed after %d attempts: %s",
                    _UPLOAD_MAX_RETRIES, exc,
                )
    raise last_err or RuntimeError("CDN upload failed (no error captured)")


def new_filekey() -> str:
    """Random 16-byte hex string used as the per-upload object key."""
    return os.urandom(16).hex()


def new_aeskey() -> bytes:
    """Random 16-byte AES-128 key."""
    return os.urandom(16)
