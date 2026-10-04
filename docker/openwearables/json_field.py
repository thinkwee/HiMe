#!/usr/bin/env python3
"""Print a top-level JSON field from stdin, or an empty string if absent/invalid.

Used by `./hime.sh wearables bootstrap|seed` to pull `access_token` out of the
OW login response and `id` (the `sk-...` value) out of the API-key response,
without relying on fragile shell/grep JSON parsing.

Usage: json_field.py <field-name>
Exit status is always 0 (missing/invalid input just prints "") so callers can
do their own `[ -n "$value" ]` check with a clear error message.
"""

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("", end="")
        return 0
    field = sys.argv[1]
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("", end="")
        return 0
    value = data.get(field) if isinstance(data, dict) else None
    print(value if value is not None else "", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
