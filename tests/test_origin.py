"""Baby refuses writes and sockets that come from another site (v6.0.2).

Binding 127.0.0.1 sounds like it settles this and does not. Browsers do not apply
the same-origin policy to WebSockets and send no preflight for one, so any page on
the open web could open `ws://127.0.0.1:8765/ws/chat` and hold a conversation with
Baby -- reading the replies, driving the tools. The POSTs that take no JSON body
(`/kill` above all) were reachable by a plain cross-origin form submission for the
same reason: no JSON content type, no preflight to refuse.

None of that is new in 6.0.2. What "start Baby with Windows" changes is how long it
is true for: the listener is now up from logon rather than only while Baby is open,
which is what turned a latent gap into one worth closing before the build is signed.

The rule is deliberately narrow. A MISSING Origin passes, because only browsers send
one and Baby's own tray speaks raw tungstenite; a PRESENT Origin must be loopback,
and the port is not checked because the shell is served from :8765 and the dev SPA
from Vite's :5173.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.agent import AgentCore
from core.bus import EventBus
from core.safety import SafetyConfig, SafetyGate
from db.database import Database
from tests.conftest import FakeProvider
from ui.server import UIContext, _origin_is_local, create_app

_CONFIG = {"models": {"daily": {"provider": "ollama", "model": "m"}}}

EVIL = "https://evil.example"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BABY_HOME", str(tmp_path))
    db = Database(tmp_path / "s.db")

    async def _boot():
        await db.connect()
        return await db.create_conversation("ui")

    conv = asyncio.run(_boot())
    bus = EventBus()
    gate = SafetyGate(SafetyConfig(mode="dry_run"), bus)
    agent = AgentCore(FakeProvider([]), db, conv, channel="ui", bus=bus, gate=gate)
    ctx = UIContext(db=db, bus=bus, gate=gate, agent=agent, config=_CONFIG)
    yield TestClient(create_app(ctx))
    asyncio.run(db.close())


# --- the rule itself ----------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        None,  # a native client: the Rust tray sends no Origin at all
        "",
        "http://127.0.0.1:8765",  # the shell's own window
        "http://localhost:5173",  # the Vite dev server, whose proxy forwards this
        "http://LOCALHOST:5173",  # header values are not case-normalised for us
        "http://[::1]:8765",
    ],
)
def test_local_origins_are_allowed(origin):
    assert _origin_is_local(origin) is True


@pytest.mark.parametrize(
    "origin",
    [
        EVIL,
        "http://evil.example",
        "null",  # a file:// page or a sandboxed frame: no host at all
        "http://127.0.0.1.evil.example",  # the prefix trick, if this were a substring test
        "http://evil.example/#127.0.0.1",  # the fragment trick, likewise
        "http://localhost.evil.example",
    ],
)
def test_remote_origins_are_refused(origin):
    assert _origin_is_local(origin) is False


def test_a_missing_origin_is_allowed_on_purpose():
    """The Rust tray connects to /ws/activity with raw tungstenite, which sends no
    Origin header. Refusing that would take the tray's colour away to close a hole
    the tray cannot be on the other side of -- and anything that can send arbitrary
    headers to this port is not a web page, so nothing is gained by refusing it."""
    assert _origin_is_local(None) is True


# --- writes -------------------------------------------------------------------


def test_a_cross_origin_post_is_refused(client):
    r = client.post("/conversation/new", headers={"origin": EVIL})
    assert r.status_code == 403


def test_the_kill_endpoint_is_not_a_drive_by(client):
    """/kill takes no body, so before this it was one <form> on any web page away
    from shutting Baby down."""
    r = client.post("/kill", headers={"origin": EVIL})
    assert r.status_code == 403


def test_provisioning_cannot_be_started_by_another_site(client):
    """It downloads gigabytes. Nobody else gets to start that."""
    r = client.post("/api/setup/provision", headers={"origin": EVIL})
    assert r.status_code == 403


def test_the_apps_own_posts_still_work(client):
    """The window is served from :8765, so this is the origin it actually sends."""
    r = client.post("/conversation/new", headers={"origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200


def test_posts_without_an_origin_still_work(client):
    """Every test in this repo, ui/tray.py, and anything else that is not a browser."""
    assert client.post("/conversation/new").status_code == 200


def test_reads_are_not_blocked(client):
    """A cross-origin GET cannot read its own response, and over-blocking here would
    break the pages themselves for no gain."""
    assert client.get("/stats", headers={"origin": EVIL}).status_code == 200


# --- sockets ------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/ws/chat", "/ws/activity", "/ws/state"])
def test_a_cross_origin_socket_never_opens(client, route):
    """Closed with 1008 BEFORE accept, which is what matters: under uvicorn an
    app that closes before accepting makes the server answer the handshake with an
    HTTP 403, so the page never gets a socket at all. The TestClient surfaces the
    same refusal as a disconnect. An HTTP middleware cannot do this job -- a
    WebSocket handshake never goes through one."""
    with pytest.raises(WebSocketDisconnect) as err:
        with client.websocket_connect(route, headers={"origin": EVIL}):
            pass
    assert err.value.code == 1008, "closed, but not as a policy refusal"


def test_the_tray_socket_still_connects(client):
    """No Origin, because tungstenite sends none. This is the tray's colour."""
    with client.websocket_connect("/ws/activity") as ws:
        assert ws is not None


def test_the_windows_own_socket_still_connects(client):
    with client.websocket_connect(
        "/ws/chat", headers={"origin": "http://127.0.0.1:8765"}
    ) as ws:
        assert ws is not None
