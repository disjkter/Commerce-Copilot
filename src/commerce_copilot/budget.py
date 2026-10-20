import re


_AMOUNT = r"(\d+(?:\.\d+)?)"
_CURRENCY = r"(?:元|块钱?|人民币)?"
_BUDGET_PATTERNS = (
    rf"预算\s*(?:为|是|[:：])?\s*[¥￥]?\s*{_AMOUNT}",
    rf"[¥￥]?\s*{_AMOUNT}\s*{_CURRENCY}\s*(?:以内|以下|之内)",
    rf"(?:不超过|不高于|低于|最多)\s*[¥￥]?\s*{_AMOUNT}\s*{_CURRENCY}",
)


def extract_budget_cents(text: str) -> int | None:
    """Extract a maximum RMB budget from common Chinese shopping expressions."""
    for pattern in _BUDGET_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return round(float(match.group(1)) * 100)
    return None
