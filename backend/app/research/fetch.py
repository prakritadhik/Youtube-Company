"""Fetch a public web page for research, safely.

This is the one place the company reaches out to arbitrary URLs, so it is deliberately strict:

* only ``http``/``https`` on ports 80/443, no credentials in the URL;
* every address the host resolves to must be a public, globally routable address (no loopback, private, link-local,
  multicast, reserved or cloud-metadata ranges), and the connection is made to the address that was checked, so a
  host that changes its DNS answer between the check and the connection cannot redirect the request inward;
* every redirect is checked again, at most four of them;
* ``robots.txt`` is honoured (a missing file allows, a server error or an unreadable file forbids);
* response size and time are capped, and only HTML and plain text are accepted;
* hidden elements (``display:none``, ``hidden``, ``aria-hidden``) and scripts are not extracted, because they are the
  usual hiding place for planted instructions.

The extracted text is untrusted data (see ``untrusted``). The fetcher never follows anything written in it.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib import robotparser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.timeutil import utcnow

USER_AGENT = "YouTubeCompanyResearch/1.0 (local research tool; public pages only; honours robots.txt)"
ALLOWED_PORTS = {80, 443}
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
MAX_REDIRECTS = 4
ROBOTS_LIMIT = 200_000


class FetchError(Exception):
    """A fetch was refused or failed. ``code`` is stable; ``retryable`` says whether trying again could help."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


@dataclass
class Fetched:
    url: str
    final_url: str
    status: int
    content_type: str
    title: str
    text: str  # extracted visible text (not yet cleaned for storage)
    sha256: str  # of the raw bytes received
    raw_bytes: int
    truncated: bool
    retrieved_at: datetime
    robots: str  # allowed | no_robots_file


Resolver = Callable[[str, int], list[str]]


def system_resolver(host: str, port: int) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as e:
        raise FetchError("dns_failed", f"the host '{host}' could not be resolved", retryable=True) from e


def is_public_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and (ip.sixtofour is not None or ip.teredo is not None):
        return False  # tunnelled forms can hide a private IPv4 address
    return ip.is_global and not ip.is_multicast


def validate_url(url: str) -> tuple[str, str, int]:
    """Return ``(scheme, host, port)`` or raise ``FetchError``."""
    if not url or len(url) > 2000 or any(c in url for c in "\r\n\t\x00 "):
        raise FetchError("bad_url", "the URL is empty, too long or contains whitespace")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("bad_url", "only http and https URLs can be fetched")
    if parts.username or parts.password:
        raise FetchError("bad_url", "URLs with embedded credentials are refused")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchError("bad_url", "the URL has no host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise FetchError("bad_url", "the URL has an invalid port") from e
    if port not in ALLOWED_PORTS:
        raise FetchError("blocked_host", f"port {port} is not allowed (only 80 and 443)")
    return parts.scheme, host, port


class _Text(HTMLParser):
    """Visible-text extractor that skips scripts, styles and anything hidden."""

    _VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr", "param"}
    _SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "object", "canvas"}
    _BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}
    _HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|opacity\s*:\s*0(\.0+)?\s*(;|$)", re.I)

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._stack: list[tuple[str, bool]] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        if tag in self._VOID:
            if tag == "br" and not self._skip_depth:
                self.parts.append("\n")
            return
        hidden = (tag in self._SKIP and tag != "head") or "hidden" in a or a.get("aria-hidden", "").lower() == "true" \
            or bool(self._HIDDEN_STYLE.search(a.get("style", "")))
        if tag == "head":
            hidden = False  # the <title> lives here; other head content is not text
        self._stack.append((tag, hidden))
        if hidden:
            self._skip_depth += 1
        elif tag in self._BLOCK and not self._skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._VOID:
            return
        for i in range(len(self._stack) - 1, -1, -1):  # tolerate unclosed tags
            if self._stack[i][0] == tag:
                for _, h in self._stack[i:]:
                    if h:
                        self._skip_depth -= 1
                del self._stack[i:]
                break
        if tag in self._BLOCK and not self._skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth and not any(t == "head" for t, _ in self._stack):
            self.parts.append(data)


def extract_text(html: str) -> tuple[str, str]:
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup must never break a research task
        pass
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return re.sub(r"\s+", " ", p.title).strip(), text


class SafeFetcher:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, resolver: Resolver | None = None, max_bytes: int = 1_000_000,
                 timeout: float = 15.0) -> None:
        self._transport = transport
        self._resolve = resolver or system_resolver
        self.max_bytes = max_bytes
        self.timeout = timeout

    # --- low level: one request pinned to a checked address ------------------------------------------------------
    def _addresses(self, host: str, port: int) -> list[str]:
        try:
            literal = ipaddress.ip_address(host)
            addrs = [str(literal)]
        except ValueError:
            addrs = self._resolve(host, port)
        if not addrs:
            raise FetchError("dns_failed", f"the host '{host}' has no address", retryable=True)
        bad = [a for a in addrs if not is_public_address(a)]
        if bad:
            raise FetchError("blocked_host", f"'{host}' resolves to a non-public address ({bad[0]}); internal addresses are never fetched")
        return addrs

    def _request(self, client: httpx.Client, scheme: str, host: str, port: int, path_query: str, *, limit: int) -> tuple[httpx.Response, bytes, bool]:
        addr = self._addresses(host, port)[0]
        ip_host = f"[{addr}]" if ":" in addr else addr
        default_port = 443 if scheme == "https" else 80
        netloc = ip_host if port == default_port else f"{ip_host}:{port}"
        url = f"{scheme}://{netloc}{path_query}"
        headers = {"Host": host if port == default_port else f"{host}:{port}", "User-Agent": USER_AGENT,
                   "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9", "Accept-Encoding": "gzip"}
        try:
            with client.stream("GET", url, headers=headers, extensions={"sni_hostname": host}, follow_redirects=False) as resp:
                body = bytearray()
                truncated = False
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > limit:
                        del body[limit:]
                        truncated = True
                        break
                return resp, bytes(body), truncated
        except httpx.TimeoutException as e:
            raise FetchError("timeout", "the site did not answer in time", retryable=True) from e
        except httpx.HTTPError as e:
            raise FetchError("network_error", f"the request failed ({type(e).__name__})", retryable=True) from e

    def _robots_allows(self, client: httpx.Client, scheme: str, host: str, port: int, path_query: str) -> str:
        """``allowed`` / ``no_robots_file``, or ``FetchError``. Unreadable or erroring robots.txt forbids (fail closed)."""
        r_scheme, r_host, r_port, r_path = scheme, host, port, "/robots.txt"
        for _ in range(4):
            try:
                resp, body, _trunc = self._request(client, r_scheme, r_host, r_port, r_path, limit=ROBOTS_LIMIT)
            except FetchError as e:
                if e.code == "blocked_host":
                    raise
                raise FetchError("robots_unreadable", f"robots.txt for {host} could not be read ({e.code}); not fetching",
                                 retryable=e.retryable) from e
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    break
                target = urljoin(f"{r_scheme}://{r_host}{r_path}", loc)
                r_scheme, r_host, r_port = validate_url(target)
                sp = urlsplit(target)
                r_path = urlunsplit(("", "", sp.path or "/", sp.query, ""))
                continue
            if 400 <= resp.status_code < 500:
                return "no_robots_file"
            if resp.status_code >= 500:
                raise FetchError("robots_unreadable", f"robots.txt for {host} returned {resp.status_code}; not fetching", retryable=True)
            rp = robotparser.RobotFileParser()
            rp.parse(body.decode("utf-8", "replace").splitlines())
            if not rp.can_fetch(USER_AGENT, f"{scheme}://{host}{path_query}"):
                raise FetchError("robots_disallowed", f"{host} asks automated tools not to fetch this path (robots.txt)")
            return "allowed"
        raise FetchError("robots_unreadable", f"robots.txt for {host} redirected too many times; not fetching")

    # --- public ---------------------------------------------------------------------------------------------------
    def fetch(self, url: str) -> Fetched:
        scheme, host, port = validate_url(url)
        current = url
        with httpx.Client(transport=self._transport, timeout=self.timeout, verify=True, trust_env=False) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                parts = urlsplit(current)
                path_query = urlunsplit(("", "", parts.path or "/", parts.query, ""))
                robots = self._robots_allows(client, scheme, host, port, path_query)
                resp, body, truncated = self._request(client, scheme, host, port, path_query, limit=self.max_bytes)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        raise FetchError("bad_redirect", "the site redirected without saying where")
                    current = urljoin(current, loc)
                    scheme, host, port = validate_url(current)
                    continue
                break
            else:
                raise FetchError("too_many_redirects", f"more than {MAX_REDIRECTS} redirects")
        if resp.status_code >= 400:
            raise FetchError("http_error", f"the site answered {resp.status_code}", retryable=resp.status_code in (408, 429) or resp.status_code >= 500)
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in ALLOWED_TYPES:
            raise FetchError("unsupported_type", f"content type '{ctype or 'unknown'}' is not supported (HTML and plain text only)")
        charset = re.search(r"charset=([\w-]+)", resp.headers.get("content-type", ""), re.I)
        try:
            decoded = body.decode(charset.group(1) if charset else "utf-8", "replace")
        except LookupError:
            decoded = body.decode("utf-8", "replace")
        if ctype == "text/plain":
            title, text = "", decoded
        else:
            title, text = extract_text(decoded)
        return Fetched(url=url, final_url=current, status=resp.status_code, content_type=ctype, title=title, text=text,
                       sha256=hashlib.sha256(body).hexdigest(), raw_bytes=len(body), truncated=truncated, retrieved_at=utcnow(), robots=robots)
