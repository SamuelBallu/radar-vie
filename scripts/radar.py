#!/usr/bin/env python3
"""Command line client for the Radar VIE API, used by the scheduled agents.

Standard library only, so it runs with any Python 3.11+ without a virtualenv.
Reads RADAR_URL and RADAR_API_TOKEN from the environment, or from an env file
(default: ~/.config/radar-vie/agent.env, KEY=value lines).

    radar.py list                       all offers, as JSON
    radar.py get ID
    radar.py put FILE.json              create or merge offers (one object with "id", or a list)
    radar.py patch ID JSON|FILE.json    merge fields; null removes a field
    radar.py delete ID
    radar.py posting ID                 full text of an offer, as read by the morning run
    radar.py meta-get KEY
    radar.py meta-put KEY JSON|FILE.json
    radar.py upload FILE.pdf [NAME]     store a PDF, prints {"id", "url", "size"}
    radar.py delete-file ID
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_ENV = Path.home() / ".config" / "radar-vie" / "agent.env"


def load_env() -> tuple[str, str]:
    env_file = Path(os.environ.get("RADAR_ENV_FILE", DEFAULT_ENV))
    values = {}
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"')
    url = os.environ.get("RADAR_URL") or values.get("RADAR_URL", "")
    token = os.environ.get("RADAR_API_TOKEN") or values.get("RADAR_API_TOKEN", "")
    if not url or not token:
        sys.exit(f"RADAR_URL and RADAR_API_TOKEN are needed (environment or {env_file}).")
    return url.rstrip("/"), token


def call(method: str, path: str, body: object = None, raw: bytes | None = None) -> object:
    base, token = load_env()
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "radar-vie-cli"}
    data = None
    if raw is not None:
        data, headers["Content-Type"] = raw, "application/pdf"
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")
        sys.exit(f"{method} {path} failed: HTTP {err.code} {detail}")


def json_arg(value: str) -> object:
    """A JSON string, or the path of a JSON file."""
    path = Path(value)
    return json.loads(path.read_text() if path.is_file() else value)


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    command, args = argv[0], argv[1:]
    quote = lambda s: urllib.parse.quote(s, safe="")  # noqa: E731
    if command == "list":
        result = call("GET", "/api/offers")
    elif command == "get":
        result = call("GET", f"/api/offers/{quote(args[0])}")
    elif command == "put":
        offers = json_arg(args[0])
        result = [call("PUT", f"/api/offers/{quote(str(o['id']))}", o) for o in (offers if isinstance(offers, list) else [offers])]
    elif command == "patch":
        result = call("PATCH", f"/api/offers/{quote(args[0])}", json_arg(args[1]))
    elif command == "delete":
        result = call("DELETE", f"/api/offers/{quote(args[0])}")
    elif command == "posting":
        result = call("GET", f"/api/postings/{quote(args[0])}")
    elif command == "meta-get":
        result = call("GET", f"/api/meta/{quote(args[0])}")
    elif command == "meta-put":
        result = call("PUT", f"/api/meta/{quote(args[0])}", json_arg(args[1]))
    elif command == "upload":
        pdf = Path(args[0])
        name = args[1] if len(args) > 1 else pdf.name
        result = call("POST", "/api/files?name=" + urllib.parse.quote(name), raw=pdf.read_bytes())
    elif command == "delete-file":
        result = call("DELETE", f"/api/files/{quote(args[0])}")
    else:
        sys.exit(f"Unknown command: {command}. Run with --help.")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
