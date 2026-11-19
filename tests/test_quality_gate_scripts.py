from pathlib import Path

from scripts.check_repository_secrets import find_secret_matches


def test_secret_scanner_detects_a_private_key(tmp_path: Path):
    candidate = tmp_path / "leaked-key.txt"
    candidate.write_text(
        "-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key\n",
        encoding="utf-8",
    )

    violations = find_secret_matches([candidate])

    assert len(violations) == 1
    assert "private key" in violations[0]


def test_secret_scanner_allows_example_environment_values(tmp_path: Path):
    candidate = tmp_path / ".env.example"
    candidate.write_text(
        "LLM_API_KEY=\nAUTH_SECRET=replace-with-a-random-secret\n",
        encoding="utf-8",
    )

    assert find_secret_matches([candidate]) == []
