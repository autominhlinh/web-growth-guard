"""Single-origin HTTPS GET collector; no cookies, proxies, credentials or browser."""
import http.client
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urljoin
from . import __version__
from .core import checked_url, require, MAX_FILE, MAX_TOTAL

UA = 'WebGrowthGuard/' + __version__
MAX_REQUESTS = 32
MAX_REDIRECTS = 3


class FetchError(ValueError):
    pass


def public_url(url, origin=None):
    checked_url(url, origin)
    u = urlsplit(url)
    host = u.hostname
    require(host.isascii() and len(host) <= 253 and '.' in host and
            all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in host.split('.')) and
            re.search(r'[a-z]', host.split('.')[-1]) is not None and
            not host.endswith(('.local', '.localhost', '.internal', '.home.arpa')), 'Public DNS hostname required')
    # No query strings, credentials, literal IPs, control characters or ambiguous encoded paths.
    require(url.isascii() and not re.search(r'[\x7f-\xff]', url) and
            not re.search(r'%(?![0-9a-fA-F]{2})|%(?:0[0-9a-f]|1[0-9a-f]|7f|2f|5c|2e)', u.path, re.I) and
            not any(x in ('.', '..') for x in u.path.split('/')), 'Unsupported URL path')
    return url


def resolve_public(host, resolver=socket.getaddrinfo):
    # IPv4 only: avoids mapped, scoped and translation IPv6 ambiguity. Fail closed.
    answers = resolver(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    ips = sorted({row[4][0] for row in answers})
    if not ips:
        raise FetchError('DNS_NO_PUBLIC_IPV4')
    for ip in ips:
        address = ipaddress.IPv4Address(ip)
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise FetchError('DNS_NONPUBLIC_ADDRESS')
    return ips[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Numeric IP dial, no second hostname lookup; hostname still used for TLS SNI/certificate.
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            raw.settimeout(self.timeout)
            raw.connect((self.address, self.port))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    headers: tuple
    body: bytes
    redirects: tuple = ()

    def values(self, name):
        return [v for k, v in self.headers if k.lower() == name.lower()]


class Collector:
    def __init__(self, origin, resolver=socket.getaddrinfo, connection=PinnedHTTPS,
                 pause=time.sleep, clock=time.monotonic):
        self.origin = public_url(origin)
        require(origin == 'https://' + urlsplit(origin).hostname, 'Invalid origin')
        self.resolver, self.connection, self.pause, self.clock = resolver, connection, pause, clock
        self.deadline = clock() + 90
        self.requests = self.total = 0
        self.last = None

    def remaining(self):
        value = self.deadline - self.clock()
        if value <= 0:
            raise FetchError('HTTP_TIME_BUDGET')
        return min(8, value)

    def once(self, url, limit):
        public_url(url, self.origin)
        if self.requests >= MAX_REQUESTS:
            raise FetchError('HTTP_REQUEST_BUDGET')
        if self.last is not None:
            self.pause(max(0, 0.5 - (self.clock() - self.last)))
        self.remaining()
        self.requests += 1
        self.last = self.clock()
        u = urlsplit(url)
        conn = None
        try:
            address = resolve_public(u.hostname, self.resolver)
            conn = self.connection(u.hostname, address, self.remaining())
            conn.request('GET', u.path or '/', headers={'User-Agent': UA, 'Accept': '*/*',
                         'Accept-Encoding': 'identity', 'Connection': 'close'})
            res = conn.getresponse()
            headers = tuple(res.getheaders())
            if res.status in (301, 302, 303, 307, 308) or res.status != 200:
                return Response(url, res.status, headers, b'')
            enc = res.getheader('Content-Encoding', 'identity').strip().lower()
            if enc not in ('', 'identity'):
                raise FetchError('HTTP_ENCODING_UNSUPPORTED')
            lengths = [v for k, v in headers if k.lower() == 'content-length']
            if lengths and (len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > limit):
                raise FetchError('HTTP_BODY_LIMIT_OR_INVALID_LENGTH')
            data = bytearray()
            while True:
                self.remaining()
                # read1 avoids an unbounded accumulation inside one read on slow responses.
                chunk = res.read1(min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                self.total += len(chunk)
                if len(data) > limit or self.total > MAX_TOTAL:
                    raise FetchError('HTTP_BODY_BUDGET')
            if lengths and len(data) != int(lengths[0]):
                raise FetchError('HTTP_INCOMPLETE_BODY')
            return Response(url, res.status, headers, bytes(data))
        except FetchError:
            raise
        except (OSError, ValueError, http.client.HTTPException, UnicodeError):
            raise FetchError('HTTP_UNAVAILABLE_OR_INVALID') from None
        finally:
            if conn is not None:
                conn.close()

    def get(self, url, allowed=None, limit=MAX_FILE):
        redirects, visited = [], set()
        for _ in range(MAX_REDIRECTS + 1):
            public_url(url, self.origin)
            if url in visited:
                raise FetchError('HTTP_REDIRECT_LOOP')
            visited.add(url)
            if allowed is not None and not allowed(urlsplit(url).path or '/'):
                raise FetchError('ROBOTS_TOOL_DISALLOWED')
            result = self.once(url, limit)
            if result.status not in (301, 302, 303, 307, 308):
                return Response(result.url, result.status, result.headers, result.body, tuple(redirects))
            locations = result.values('Location')
            if len(locations) != 1 or not locations[0] or not locations[0].isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in locations[0]):
                raise FetchError('HTTP_REDIRECT_INVALID')
            try:
                target = public_url(urljoin(url, locations[0]), self.origin)
            except ValueError:
                raise FetchError('HTTP_REDIRECT_OUT_OF_SCOPE') from None
            redirects.append({'status': result.status, 'to': target})
            url = target
        raise FetchError('HTTP_REDIRECT_LIMIT')
