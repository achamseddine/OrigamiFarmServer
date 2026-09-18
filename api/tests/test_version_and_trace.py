"""The build stamp, and the debug trace that makes an invisible refusal visible.

Both exist for the same reason: a deployment that silently serves the
previous image, and a browser client silently refused by CORS, are the two
failures that leave nothing behind to look at. One is answered by asking
the server which build it is; the other by logging the refusal the
middleware otherwise swallows.
"""

from __future__ import annotations

from app.common.logging import RequestTraceMiddleware
from app.config.settings import Settings


def test_health_reports_which_build_is_answering(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    # "dev" is what a checkout reports; an image carries its commit.
    assert body["version"]
    assert "built_at" in body


def test_healthz_reports_the_same_build(client):
    assert client.get("/healthz").json() == client.get("/health").json()


def test_the_version_comes_from_the_environment():
    """The image sets APP_VERSION at build time, so this is the whole
    mechanism by which a deployed container knows what it is."""
    assert Settings(app_version="sha-deadbee").app_version == "sha-deadbee"
    assert Settings().app_version == "dev"


def test_a_refused_origin_is_logged_by_name(client, capsys):
    """Without this line the failure is perfectly silent: the browser
    blocks the request and the server records nothing, so a working
    password looks like a wrong one.

    capsys rather than caplog: structlog is configured with a
    PrintLoggerFactory, so these lines go straight to stdout and never pass
    through the stdlib logging handlers caplog attaches to.
    """
    resp = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://not-allowed.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 400

    logged = capsys.readouterr().out
    assert "cors_origin_refused" in logged
    assert "https://not-allowed.example" in logged
    # The allowlist it was measured against, so the fix is in the log too.
    assert "cors_allowed" in logged


def test_the_trace_never_logs_credentials(client, capsys):
    """A debug switch that leaks an Authorization header into a log stream
    would be a worse problem than the one it was turned on to solve."""
    client.get(
        "/health",
        headers={
            "Authorization": "Bearer super-secret-token-value",
            "Origin": "https://not-allowed.example",
        },
    )
    assert "super-secret-token-value" not in capsys.readouterr().out


def test_the_middleware_treats_a_wildcard_as_allowing_everything():
    trace = RequestTraceMiddleware(app=None, allowed_origins=["*"])
    assert trace._allowed == ["*"]
