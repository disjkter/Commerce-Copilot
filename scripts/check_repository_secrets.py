"""Fail CI when repository source files contain common credential formats."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BINARY_SUFFIXES = {
    ".7z",
    ".db",
    ".dll",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pyc",
    ".sqlite",
    ".zip",
}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OpenAI-style API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub personal access token": re.compile(
        r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,})\b"
    ),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        PROJECT_ROOT / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]


def find_secret_matches(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        if path.suffix.casefold() in BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            relative = path.relative_to(PROJECT_ROOT)
        except ValueError:
            relative = path
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    violations.append(f"{relative}:{line_number}: possible {label}")
    return violations


def main() -> int:
    violations = find_secret_matches(tracked_files())
    if violations:
        print("Credential scan failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Credential scan passed: no known secret formats in repository files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
