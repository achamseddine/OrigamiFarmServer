"""The CORS allowlist, and the trailing slash that silently breaks it.

A browser sends `Origin: https://example.com` — scheme, host, optional
port, and nothing else. An allowlist entry of `https://example.com/`
therefore matches nothing, the browser blocks the request before it is
sent, and the server never logs a thing. Someone filling this in by
pasting from the address bar gets that slash for free, so the parsing
takes it off rather than leaving them to find out the hard way.
"""

from __future__ import annotations

from app.config.settings import Settings


def test_a_pasted_url_keeps_working_despite_its_trailing_slash():
    settings = Settings(cors_allowed_origins="https://example.azurewebsites.net/")
    assert settings.cors_origins_list == ["https://example.azurewebsites.net"]


def test_several_origins_are_split_trimmed_and_normalised():
    settings = Settings(
        cors_allowed_origins=" https://a.example/ , http://localhost:3000 ,, https://b.example "
    )
    assert settings.cors_origins_list == [
        "https://a.example",
        "http://localhost:3000",
        "https://b.example",
    ]


def test_the_wildcard_is_left_alone():
    """ "*" is a wildcard, not a URL — rstrip("/") would be wrong on it and
    harmless here, but the intent is worth pinning."""
    assert Settings(cors_allowed_origins="*").cors_origins_list == ["*"]


def test_the_default_allows_the_deployed_host_and_the_dev_server():
    origins = Settings().cors_origins_list
    assert "https://leb-container-test-staging.azurewebsites.net" in origins
    assert "http://localhost:3000" in origins
    assert all(not origin.endswith("/") for origin in origins)
