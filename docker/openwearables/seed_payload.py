#!/usr/bin/env python3
"""Build a POST /api/v1/settings/seed request body for a given OW seed preset.

open-wearables' seed endpoint takes a full `profile` object, not just a preset
id — the intended flow is: fetch GET /api/v1/settings/seed/presets, pick one,
and echo its `profile` field back in the POST body. This script does that
match/extraction so `./hime.sh wearables seed` stays in the same JSON-safe
style as json_field.py.

Usage: seed_payload.py <preset-id> [num-users]
Reads the JSON array returned by GET /api/v1/settings/seed/presets on stdin.
Prints the request JSON on success; on failure, prints nothing and writes an
error (listing the available preset ids, when known) to stderr, exit 1.
"""

import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: seed_payload.py <preset-id> [num-users]", file=sys.stderr)
        return 1
    preset_id = sys.argv[1]
    num_users = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    try:
        presets = json.load(sys.stdin)
    except Exception as exc:
        print(f"could not parse presets response: {exc}", file=sys.stderr)
        return 1

    if not isinstance(presets, list):
        print("presets response was not a JSON array", file=sys.stderr)
        return 1

    match = next((p for p in presets if isinstance(p, dict) and p.get("id") == preset_id), None)
    if match is None:
        ids = ", ".join(p.get("id", "?") for p in presets if isinstance(p, dict)) or "(none returned)"
        print(f"preset '{preset_id}' not found. Available: {ids}", file=sys.stderr)
        return 1

    print(json.dumps({"num_users": num_users, "profile": match["profile"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
