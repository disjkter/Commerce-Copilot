import json
import re
from typing import Any


_FENCED_JSON = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)


def parse_json_object(content: str) -> dict[str, Any]:
    """Decode a JSON object even when a compatible model wraps it in Markdown."""
    if not isinstance(content, str):
        raise TypeError("model content must be a string")
    normalized = content.strip().lstrip("\ufeff")
    fenced = _FENCED_JSON.match(normalized)
    if fenced:
        normalized = fenced.group(1).strip()

    try:
        value = json.loads(normalized)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        for index, character in enumerate(normalized):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(normalized[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise original_error

    if not isinstance(value, dict):
        raise TypeError("model output must be a JSON object")
    return value
