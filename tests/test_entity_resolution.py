from commerce_copilot.catalog import Product
from commerce_copilot.database import DEMO_PRODUCTS
from commerce_copilot.entity_resolution import resolve_product_mentions


def test_resolves_catalog_title_brand_model_and_product_id():
    products = list(DEMO_PRODUCTS)

    assert [
        item.product_id for item in resolve_product_mentions("潮汐 Buds 2 评分", products)
    ] == ["CP-AUD-102"]
    assert [
        item.product_id for item in resolve_product_mentions("原野的耳机怎么样", products)
    ] == ["CP-AUD-103"]
    assert [
        item.product_id for item in resolve_product_mentions("查 CP-KEY-201", products)
    ] == ["CP-KEY-201"]


def test_generic_category_does_not_bind_an_arbitrary_product():
    assert resolve_product_mentions("推荐一款无线耳机", list(DEMO_PRODUCTS)) == []


def test_shared_brand_is_not_used_as_an_ambiguous_alias():
    products = [
        Product(
            product_id=f"P-{index}",
            title=f"同牌 Model {index} 耳机",
            category="耳机",
            brand="同牌",
            description="test",
            price_cents=100,
            rating=4,
            review_count=1,
            stock=1,
            tags=["耳机"],
        )
        for index in (1, 2)
    ]

    assert resolve_product_mentions("同牌的产品怎么样", products) == []
