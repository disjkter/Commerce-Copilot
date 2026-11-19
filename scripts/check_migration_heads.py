"""Require the Alembic migration graph to have exactly one active head."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory


def main() -> int:
    config = Config("alembic.ini")
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        print(f"Migration topology check failed: expected 1 head, found {heads}")
        return 1
    print(f"Migration topology check passed: {heads[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
