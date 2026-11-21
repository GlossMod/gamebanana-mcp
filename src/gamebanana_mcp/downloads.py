"""Stream and verify a selected upstream file before publishing it locally."""

import hashlib
import hmac
import os
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from .errors import GameBananaError

_ALLOWED_HOSTS = {"gamebanana.com", "files.gamebanana.com"}
_FILE_CACHE_HOST = re.compile(r"filecache[0-9]+\.gamebanana\.com", re.ASCII)
_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5
_CHUNK_SIZE = 128 * 1024
_DEVICE_NAME = re.compile(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³]", re.ASCII)


async def save_file(http: httpx.AsyncClient, directory: Path, record: dict[str, Any]) -> dict[str, Any]:
    name = record.get("_sFile")
    if not _safe_filename(name):
        raise GameBananaError("The GameBanana file record has no safe filename.")
    url = _download_url(record.get("_sDownloadUrl"))
    expected_size = record.get("_nFilesize")
    if expected_size is not None and (type(expected_size) is not int or expected_size < 0):
        raise GameBananaError("GameBanana supplied an invalid file size.")
    expected_md5 = record.get("_sMd5Checksum")
    if expected_md5 is not None and expected_md5 != "":
        if not isinstance(expected_md5, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", expected_md5):
            raise GameBananaError("GameBanana supplied an invalid MD5 checksum.")
    else:
        expected_md5 = None

    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False) if expected_md5 is not None else None
    count = 0
    partial = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory.resolve() / name
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        async with _stream_file(http, url) as response:
            length = _check_headers(response, expected_size)
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=".gamebanana-", suffix=".part", delete=False
            ) as output:
                partial = Path(output.name)
                async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    count += len(chunk)
                    if length is not None and count > length:
                        raise GameBananaError("Download exceeded the expected size.")
                    output.write(chunk)
                    sha256.update(chunk)
                    if md5 is not None:
                        md5.update(chunk)
        if length is not None and length != count:
            raise GameBananaError(f"Download size mismatch: expected {length} bytes, received {count}.")
        if md5 is not None and not hmac.compare_digest(md5.hexdigest(), expected_md5.lower()):
            raise GameBananaError("Download MD5 did not match the GameBanana file record.")
        # Publish only fully verified bytes, without overwriting even if a competing
        # download or another program created this file since the existence check.
        if os.name == "nt":
            partial.rename(destination)
        else:
            os.link(partial, destination)
    except FileExistsError:
        raise GameBananaError(f"A local file named {name!r} already exists; it was not overwritten.") from None
    except httpx.RequestError:
        raise GameBananaError("GameBanana download failed or was interrupted by the network.") from None
    except GameBananaError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise GameBananaError("Could not create or publish the file in the configured download directory.") from None
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)

    result = {
        "file_id": record["_idRow"],
        "path": str(destination),
        "bytes": count,
        "sha256": sha256.hexdigest(),
    }
    if md5 is not None:
        result["md5"] = md5.hexdigest()
    return result


def _safe_filename(name: Any) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and not name.endswith((".", " "))
        and not any(c in '<>:"/\\|?*' or ord(c) < 32 or ord(c) == 127 for c in name)
        and _DEVICE_NAME.fullmatch(name.split(".", 1)[0].rstrip(" ").upper()) is None
    )


def _download_url(value: Any) -> httpx.URL:
    if not isinstance(value, (str, httpx.URL)) or not value:
        raise GameBananaError("The GameBanana file record has no download address.")
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        raise GameBananaError("GameBanana returned an invalid download address.") from None
    if (
        url.scheme != "https"
        or not (url.host in _ALLOWED_HOSTS or _FILE_CACHE_HOST.fullmatch(url.host))
        or url.port not in {None, 443}
        or url.userinfo
    ):
        raise GameBananaError("Download blocked: the URL is not on an allowed GameBanana HTTPS host.")
    return url


@asynccontextmanager
async def _stream_file(http: httpx.AsyncClient, url: httpx.URL) -> AsyncIterator[httpx.Response]:
    for hop in range(_MAX_REDIRECTS + 1):
        async with http.stream(
            "GET", url, headers={"Accept-Encoding": "identity"}, follow_redirects=False
        ) as response:
            status = response.status_code
            if status in _REDIRECTS:
                location = response.headers.get("Location")
                if not location or hop == _MAX_REDIRECTS:
                    raise GameBananaError("GameBanana download returned an invalid or excessive redirect chain.")
                try:
                    url = _download_url(url.join(location))
                except httpx.InvalidURL:
                    raise GameBananaError("GameBanana returned an invalid download redirect.") from None
                continue
            if status != 200:
                reason = {
                    401: "access not authorized",
                    403: "access denied or link expired",
                    404: "file unavailable or link expired",
                    410: "file removed or link expired",
                    429: "rate limited; try again later",
                }.get(status, "expected a complete file")
                raise GameBananaError(f"GameBanana download: HTTP {status}, {reason}.")
            yield response
            return


def _check_headers(response: httpx.Response, expected_size: int | None) -> int | None:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type in {"text/html", "application/xhtml+xml"}:
        raise GameBananaError("GameBanana returned an HTML page instead of a file.")
    if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
        raise GameBananaError("GameBanana returned an unexpected content encoding instead of raw file bytes.")
    header = response.headers.get("Content-Length")
    if header is not None:
        if not re.fullmatch(r"[0-9]{1,20}", header):
            raise GameBananaError("GameBanana returned an invalid Content-Length.")
        header_size = int(header)
        if expected_size is not None and expected_size != header_size:
            raise GameBananaError("Download Content-Length does not match the GameBanana file size.")
        return header_size
    return expected_size
