"""Shared helpers for the bdpan_* scripts — pure stdlib, Python >= 3.9.

Everything network-related goes through :class:`Session`, a thin wrapper over
urllib that keeps an explicit cookie dict (so ``Set-Cookie`` values like
``BDCLND`` survive across calls) and never requests compressed bodies.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# The legacy PCS download endpoint works with this UA + a BDUSS cookie.
DOWNLOAD_UA = "pan.baidu.com"

BASE_PARAMS = {"channel": "chunlei", "web": "1", "clienttype": "0"}

REQUIRED_COOKIES = ["BDUSS", "STOKEN", "BAIDUID"]

# Cookies actually needed by the pan web APIs. Sending the browser's full
# baidu.com jar (100+ cookies) trips nginx's "400 Request Header Or Cookie
# Too Large" on pan.baidu.com — always filter to this set.
SEND_COOKIES = [
    "BDUSS", "BDUSS_BFESS", "STOKEN", "PTOKEN", "BAIDUID", "BAIDUID_BFESS",
    "PSTM", "BIDUPSID", "H_PS_PSSID", "PSINO", "ndut_fmt", "PANWEB", "PANPSC",
]


class BaiduError(RuntimeError):
    """API returned a non-zero errno (or an unexpected HTTP status)."""

    def __init__(self, errno: Any, msg: str = ""):
        self.errno = errno
        super().__init__(f"[errno {errno}] {msg}".strip())


def parse_share_url(url: str) -> tuple[str, str | None]:
    """Extract ``(surl, pwd)`` from a Baidu share URL.

    ``surl`` is the token after ``/s/`` **without** the leading ``1`` (the
    form the ``share/verify`` API expects); the public page URL is
    ``https://pan.baidu.com/s/1{surl}``. ``pwd`` is the 提取码 when present in
    the query string, else ``None``.
    """
    m = re.search(r"pan\.baidu\.com/s/1([\w-]+)", url)
    if not m:
        raise ValueError(f"not a Baidu share URL: {url!r}")
    surl = m.group(1)
    pwd = None
    q = re.search(r"[?&]pwd=([\w-]+)", url)
    if q:
        pwd = q.group(1)
    return surl, pwd


def parse_locals_mset(html: str) -> dict | None:
    """Parse the ``locals.mset({...});`` JSON blob embedded in a share page.

    Contains ``shareid``, ``share_uk``, ``file_list`` (with ``fs_id``,
    ``server_filename``, ``size``, ``path``, ``md5``, ``duration``), etc.
    """
    m = re.search(r"locals\.mset\((\{.*?\})\);", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def load_cookies(path: str | Path) -> dict[str, str]:
    """Load a cookies.json produced by bdpan_cookies.py.

    Keeps only the SEND_COOKIES whitelist with ASCII values: the full browser
    jar (100+ cookies) overflows nginx's header buffer on pan.baidu.com
    ("400 Request Header Or Cookie Too Large"), and non-ASCII values would
    crash HTTP header encoding.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        k: v for k, v in raw.items()
        if k in SEND_COOKIES and isinstance(v, str) and v.isascii()
    }


def check_cookies(cookies: dict[str, str]) -> list[str]:
    """Return the list of required cookies missing from *cookies*."""
    return [k for k in REQUIRED_COOKIES if not cookies.get(k)]


class Session:
    """Minimal cookie-aware HTTP session on urllib (no third-party deps)."""

    def __init__(self, cookies: dict[str, str], ua: str = BROWSER_UA, timeout: int = 30):
        self.cookies = dict(cookies)
        self.ua = ua
        self.timeout = timeout

    # -- internals ---------------------------------------------------------

    def _cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def _absorb_set_cookie(self, resp: Any) -> None:
        for header in resp.headers.get_all("Set-Cookie") or []:
            pair = header.split(";", 1)[0]
            if "=" in pair:
                k, v = pair.split("=", 1)
                self.cookies[k.strip()] = v.strip()

    def request(
        self,
        url: str,
        params: dict | None = None,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> bytes:
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
        req.add_header("User-Agent", self.ua)
        req.add_header("Cookie", self._cookie_header())
        req.add_header("Accept-Encoding", "identity")
        req.add_header("Referer", "https://pan.baidu.com/disk/home")
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self._absorb_set_cookie(resp)
                return resp.read()
        except urllib.error.HTTPError as e:
            self._absorb_set_cookie(e)
            payload = e.read()
            raise BaiduError(f"http-{e.code}", payload[:200].decode("utf-8", "replace")) from e

    def get_json(self, url: str, params: dict | None = None,
                 data: dict | None = None) -> dict:
        return json.loads(self.request(url, params=params, data=data).decode("utf-8"))

    # -- baidu pan specifics -------------------------------------------------

    def verify_share(self, surl: str, pwd: str) -> None:
        """POST share/verify so the session gains the BDCLND cookie."""
        params = {**BASE_PARAMS, "surl": surl, "t": int(time.time() * 1000)}
        out = self.get_json("https://pan.baidu.com/share/verify",
                            params=params, data={"pwd": pwd, "vcode": "", "vcode_str": ""})
        if out.get("errno") != 0:
            raise BaiduError(out.get("errno"), "share verify failed (wrong pwd? captcha?)")
        if not self.cookies.get("BDCLND"):
            raise BaiduError("no-bdclnd", "verify ok but BDCLND cookie missing")

    def bdstoken(self) -> str:
        out = self.get_json("https://pan.baidu.com/api/gettemplatevariable",
                            params={**BASE_PARAMS, "fields": '["bdstoken","username","loginstate"]'})
        if out.get("errno") != 0:
            raise BaiduError(out.get("errno"), "gettemplatevariable failed (cookies expired?)")
        result = out.get("result", {})
        if result.get("loginstate") != 1:
            raise BaiduError("not-logged-in", "cookies present but not logged in — re-extract")
        return result["bdstoken"]

    def fetch_share_page(self, surl: str, pwd: str) -> dict:
        """Verify + fetch the share page, returning the parsed locals.mset blob."""
        self.verify_share(surl, pwd)
        html = self.request(f"https://pan.baidu.com/s/1{surl}",
                            params={"pwd": pwd}).decode("utf-8", "replace")
        data = parse_locals_mset(html)
        if not data:
            if "wappass" in html or "验证码" in html:
                raise BaiduError("captcha", "Baidu is demanding a captcha — open the link in a "
                                            "browser once, then retry")
            raise BaiduError("parse", "locals.mset not found in share page (layout change?)")
        return data

    def list_dir(self, path: str, bdstoken: str) -> list[dict]:
        out = self.get_json("https://pan.baidu.com/api/list",
                            params={**BASE_PARAMS, "dir": path, "order": "name",
                                    "bdstoken": bdstoken})
        if out.get("errno") != 0:
            raise BaiduError(out.get("errno"), f"list {path} failed")
        return out.get("list", [])


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"
