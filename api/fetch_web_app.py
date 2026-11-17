"""Download the farm app's web build into a folder — the image build's
`webapp` stage (api/Dockerfile), kept as a file rather than a Dockerfile
heredoc so the classic builder can run it too.

Usage: fetch_web_app.py <owner/repo> <release tag or empty> <destination>

An empty tag or a failed download leaves the destination empty and exits
0: the image is then built without the web app, and /app/ says so
(app/web_client.py). The API is unaffected either way.
"""

from __future__ import annotations

import io
import os
import sys
import urllib.request
import zipfile


def main() -> int:
    repo, tag, dest = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(dest, exist_ok=True)
    if not tag:
        print("no web release tag given: image built without the web app", file=sys.stderr)
        return 0
    url = f"https://github.com/{repo}/releases/download/{tag}/origami-farmos-web.zip"
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
