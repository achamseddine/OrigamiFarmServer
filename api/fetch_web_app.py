"""Download the farm app's web build into a folder — the image build's
`webapp` stage (api/Dockerfile), kept as a file rather than a Dockerfile
heredoc so the classic builder can run it too.

Usage: fetch_web_app.py <owner/repo> <release tag, empty, or "none"> <destination>

An empty tag means the newest `web-*` release that carries the zip, looked
up on GitHub — so a plain `docker build`, or `az acr build` from source,
ships the web app too, not only the image publish workflow that passes the
tag explicitly. "none" skips the download on purpose. A failed lookup or
download leaves the destination empty and exits 0: the image is then built
without the web app, and /app/ says so (app/web_client.py). The API is
unaffected either way.
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
import zipfile

ASSET = "origami-farmos-web.zip"


def newest_web_release(repo: str) -> str:
    """The tag of the newest web release carrying the zip, or "" when
    there is none (or GitHub cannot be asked)."""
    url = f"https://api.github.com/repos/{repo}/releases?per_page=30"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https host
        releases = json.load(response)
    candidates = [
        r
        for r in releases
        if r.get("tag_name", "").startswith("web-")
        and any(a.get("name") == ASSET for a in r.get("assets", []))
    ]
    candidates.sort(key=lambda r: r.get("published_at", ""), reverse=True)
    return candidates[0]["tag_name"] if candidates else ""


def main() -> int:
    repo, tag, dest = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(dest, exist_ok=True)
    if tag == "none":
        print("web app skipped on purpose: image built without it", file=sys.stderr)
        return 0
    if not tag:
        try:
            tag = newest_web_release(repo)
        except Exception as exc:  # noqa: BLE001 - GitHub unreachable must not fail the build
            print(
                f"could not look up the web releases ({exc}): image built without the web app",
                file=sys.stderr,
            )
            return 0
        if not tag:
            print("no web release found: image built without the web app", file=sys.stderr)
            return 0
        print(f"newest web release: {tag}")
    url = f"https://github.com/{repo}/releases/download/{tag}/{ASSET}"
    try:
        with urllib.request.urlopen(url, timeout=180) as response:  # noqa: S310 - fixed https host
            data = response.read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(dest)
    except Exception as exc:  # noqa: BLE001 - a missing release must not fail the build
        print(f"web app {tag} could not be downloaded ({exc}): image built without it", file=sys.stderr)
        return 0
    print(f"web app {tag}: {len(data)} bytes, {len(os.listdir(dest))} entries in {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
