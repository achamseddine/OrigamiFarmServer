"""The farm app for the browser, served at /app/ beside the API
(app/web_client.py, OrigamiFarmOS ADR-004)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def web_build(tmp_path, monkeypatch):
    """A minimal web build: what `flutter build web --base-href /` leaves
    behind, as far as serving it is concerned."""
    (tmp_path / "index.html").write_text(
        '<!DOCTYPE html><html><head><base href="/"><title>Origami</title></head><body></body></html>',
        encoding="utf-8",
    )
    (tmp_path / "main.dart.js").write_text("console.log('farm');", encoding="utf-8")
    (tmp_path / "sqlite3.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n")
    monkeypatch.setenv("FARMOS_WEB_DIR", str(tmp_path))
    return tmp_path


def test_without_a_build_the_app_says_how_to_install_it(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FARMOS_WEB_DIR", str(tmp_path / "nowhere"))
    r = client.get("/app/")
    assert r.status_code == 404
    assert "not installed" in r.text and "origami-farmos-web.zip" in r.text
    # The API is unaffected either way.
    assert client.get("/health").status_code == 200


def test_the_build_is_served_under_app_with_its_base_rewritten(client, web_build):
    # /app → /app/ so the app's relative paths resolve against a folder.
    r = client.get("/app", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"].endswith("/app/")

    index = client.get("/app/")
    assert index.status_code == 200, index.text
    assert '<base href="/app/">' in index.text and '<base href="/">' not in index.text
    assert index.headers["cache-control"] == "no-cache"
    assert client.get("/app/index.html").text == index.text

    # WebAssembly must be served as such or the browser refuses to compile it.
    wasm = client.get("/app/sqlite3.wasm")
    assert wasm.status_code == 200 and wasm.headers["content-type"].startswith("application/wasm")
    assert wasm.headers["cache-control"] == "public, max-age=3600"

    # Fixed-name files are revalidated on every load; hashed assets are cached.
    js = client.get("/app/main.dart.js")
    assert js.status_code == 200 and js.headers["cache-control"] == "no-cache"
    assert "javascript" in js.headers["content-type"]
    logo = client.get("/app/assets/logo.png")
    assert logo.status_code == 200 and logo.headers["cache-control"] == "public, max-age=3600"

    assert client.get("/app/assets/missing.png").status_code == 404
