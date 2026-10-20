from dataclasses import dataclass

from .catalog import Product
from .contracts import Claim, Evidence


@dataclass(frozen=True)
class ComparisonBundle:
    message: str
    evidence: Evidence
    claims: list[Claim]


def compare_products(products: list[Product]) -> ComparisonBundle:
    if len(products) < 2:
        raise ValueError("at least two products are required for comparison")

    cheapest = min(products, key=lambda product: product.price_cents)
    highest_rated = max(products, key=lambda product: product.rating)
    price_gap_cents = (
        max(product.price_cents for product in products)
        - min(product.price_cents for product in products)
    )
    reference = "comparison:" + ":".join(
        product.product_id for product in products
    )
    attributes = {
        "cheapest_reference": cheapest.product_id,
        "cheapest_title": cheapest.title,
        "cheapest_price_cents": cheapest.price_cents,
        "highest_rated_reference": highest_rated.product_id,
        "highest_rated_title": highest_rated.title,
        "highest_rating": highest_rated.rating,
        "price_gap_cents": price_gap_cents,
    }

    if cheapest.product_id == highest_rated.product_id:
        conclusion = (
            f"{cheapest.title} 同时价格最低（{cheapest.price_cents / 100:.2f} 元）"
            f"且评分最高（{cheapest.rating:.1f} 分）"
        )
    else:
        conclusion = (
            f"{cheapest.title} 价格最低，为 {cheapest.price_cents / 100:.2f} 元；"
            f"{highest_rated.title} 评分最高，为 {highest_rated.rating:.1f} 分"
        )
    message = (
        f"对比结论：{conclusion}；最高价与最低价相差 "
        f"{price_gap_cents / 100:.2f} 元。"
        f"若优先控制预算，可先看 {cheapest.title}；"
        f"若优先考虑评分，可先看 {highest_rated.title}。"
    )
    evidence = Evidence(
        source="derived_comparison",
        reference=reference,
        attributes=attributes,
    )
    claims = [
        Claim(
            statement=f"商品对比的 {attribute} 为 {value}",
            evidence_reference=reference,
            attribute=attribute,
            expected_value=value,
        )
        for attribute, value in attributes.items()
    ]
    return ComparisonBundle(message=message, evidence=evidence, claims=claims)
