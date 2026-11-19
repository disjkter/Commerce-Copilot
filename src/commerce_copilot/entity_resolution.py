import re
from collections import Counter
from collections.abc import Sequence

from .catalog import Product


_NORMALIZE_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def resolve_product_mentions(text: str, products: Sequence[Product]) -> list[Product]:
    """Resolve explicit catalog product mentions in their textual order.

    Matching is deliberately limited to identifiers derived from catalog data:
    product id, full title, a unique brand, and the model portion of a title.
    Generic category/tag words are removed from model aliases so a query that
    merely says "耳机" does not become bound to an arbitrary product.
    """

    normalized_text = _normalize(text)
    if not normalized_text:
        return []

    brand_counts = Counter(
        _normalize(product.brand) for product in products if _normalize(product.brand)
    )
    matches: list[tuple[int, int, Product]] = []
    for product in products:
        aliases = _product_aliases(product, brand_counts)
        matched_aliases = [alias for alias in aliases if alias in normalized_text]
        if not matched_aliases:
            continue
        first_position = min(normalized_text.index(alias) for alias in matched_aliases)
        strongest_alias = max(len(alias) for alias in matched_aliases)
        matches.append((first_position, -strongest_alias, product))

    matches.sort(key=lambda item: (item[0], item[1], item[2].product_id))
    return [product for _, _, product in matches]


def _product_aliases(product: Product, brand_counts: Counter[str]) -> set[str]:
    product_id = _normalize(product.product_id)
    title = _normalize(product.title)
    brand = _normalize(product.brand)
    removable_terms = {
        _normalize(product.category),
        *(_normalize(tag) for tag in product.tags),
    }
    model = title
    if brand:
        model = model.replace(brand, "")
    for term in sorted(filter(None, removable_terms), key=len, reverse=True):
        model = model.replace(term, "")

    aliases = {alias for alias in (product_id, title) if alias}
    if brand and brand_counts[brand] == 1:
        aliases.add(brand)
    if len(model) >= 2:
        aliases.add(model)
        if brand:
            aliases.add(f"{brand}{model}")
    return aliases


def _normalize(text: str) -> str:
    return _NORMALIZE_PATTERN.sub("", text.casefold())
