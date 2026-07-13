"""The image proxy — mostly a test that it is not an SSRF hole.

An endpoint that fetches a URL on request is one bad check away from being a
machine that fetches *any* URL an attacker names — cloud metadata, localhost,
anything our server can reach and the internet cannot. The gates are: an HMAC
over the URL (so only URLs we ourselves emitted are fetchable), a host allowlist,
no redirect following, and a validated, size-capped response. Each gets a test,
because each one silently failing open is a real vulnerability rather than a bug.

Network is faked throughout — a test that reaches pbs.twimg.com is a test that
fails on a plane.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from megatron.config import settings
from megatron.web import media_proxy

GOOD = "https://pbs.twimg.com/media/ABC123.jpg"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


class _Resp:
    def __init__(self, status=200, ctype="image/jpeg", chunks=(PNG,)):
        self.status_code = status
        self.headers = {"content-type": ctype}
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _FakeClient:
    """Records how it was constructed — `follow_redirects` is load-bearing."""

    def __init__(self, resp, calls, **kwargs):
        self._resp, self._calls, self._kwargs = resp, calls, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url):
        self._calls.append({"method": method, "url": url, **self._kwargs})
        return self._resp


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_cache_dir", str(tmp_path))
    monkeypatch.setattr(media_proxy, "_last_sweep", 0.0)
    return tmp_path


@pytest.fixture
def client():
    from megatron.web.app import app

    return TestClient(app)


@pytest.fixture
def net(monkeypatch):
    """Fake httpx. Returns the call log so a test can assert we did NOT fetch."""
    calls: list[dict] = []
    box = {"resp": _Resp()}

    def factory(**kwargs):
        return _FakeClient(box["resp"], calls, **kwargs)

    monkeypatch.setattr(media_proxy.httpx, "AsyncClient", factory)
    return {"calls": calls, "box": box}


# --- gate 0: what we are willing to hand out at all -------------------------


def test_only_allowlisted_hosts_get_a_proxy_url():
    assert media_proxy.proxied(GOOD).startswith("/img/")
    # An item whose media we will not vouch for renders with no picture at all.
    assert media_proxy.proxied("https://evil.example.com/x.jpg") == ""
    assert media_proxy.proxied("http://pbs.twimg.com/x.jpg") == ""  # not https
    assert media_proxy.proxied("") == ""


# --- gate 1: the signature is what makes this not an open proxy -------------


def test_a_url_we_never_signed_is_refused(client, cache, net):
    import base64

    target = "http://169.254.169.254/latest/meta-data/"  # the classic SSRF target
    packed = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")

    r = client.get(f"/img/0000000000000000?u={packed}")
    assert r.status_code == 403
    assert net["calls"] == [], "must not fetch a URL it refused"


def test_a_tampered_signature_is_refused(client, cache, net):
    url = media_proxy.proxied(GOOD)
    sig, _, query = url[len("/img/") :].partition("?")
    bad = ("f" if sig[0] != "f" else "0") + sig[1:]

    r = client.get(f"/img/{bad}?{query}")
    assert r.status_code == 403
    assert net["calls"] == []


def test_a_valid_signature_for_a_blocked_host_is_still_refused(client, cache, net, monkeypatch):
    """Belt and braces: even a correctly-signed URL must clear the allowlist."""
    import base64

    target = "https://evil.example.com/x.jpg"
    packed = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    sig = media_proxy._sign(target)  # as if the secret had leaked

    r = client.get(f"/img/{sig}?u={packed}")
    assert r.status_code == 403
    assert net["calls"] == []


# --- gates 2-4: what we do once we have decided to fetch --------------------


def test_the_happy_path_fetches_caches_and_serves(client, cache, net):
    r = client.get(media_proxy.proxied(GOOD))
    assert r.status_code == 200
    assert r.content == PNG
    assert "immutable" in r.headers["cache-control"]
    assert len(net["calls"]) == 1
    assert net["calls"][0]["url"] == GOOD
    assert list(cache.rglob("*.jpg")), "the image should be on disk"


def test_it_never_follows_redirects(client, cache, net):
    """A 302 from upstream would otherwise name its own target — and walk straight
    past the host allowlist into our network."""
    client.get(media_proxy.proxied(GOOD))
    assert net["calls"][0]["follow_redirects"] is False


def test_a_second_request_is_served_from_disk_without_refetching(client, cache, net):
    url = media_proxy.proxied(GOOD)
    assert client.get(url).status_code == 200
    assert len(net["calls"]) == 1

    assert client.get(url).status_code == 200
    assert len(net["calls"]) == 1, "cache hit must not touch the network"


def test_a_non_image_response_is_refused_and_not_cached(client, cache, net):
    net["box"]["resp"] = _Resp(ctype="text/html", chunks=(b"<html>gotcha</html>",))
    r = client.get(media_proxy.proxied(GOOD))
    assert r.status_code == 404
    assert not list(cache.rglob("*.*")), "nothing hostile should reach the disk"


def test_an_oversized_response_is_abandoned_mid_stream(client, cache, net, monkeypatch):
    monkeypatch.setattr(media_proxy, "MAX_BYTES", 1024)
    net["box"]["resp"] = _Resp(chunks=(b"x" * 600, b"x" * 600, b"x" * 600))

    r = client.get(media_proxy.proxied(GOOD))
    assert r.status_code == 404
    assert not list(cache.rglob("*.*"))


def test_an_upstream_error_degrades_to_404(client, cache, net):
    """The template renders a card with no picture — the page must not break."""
    net["box"]["resp"] = _Resp(status=404)
    assert client.get(media_proxy.proxied(GOOD)).status_code == 404
