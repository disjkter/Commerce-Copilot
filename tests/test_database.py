from commerce_copilot.database import CatalogRepository
from commerce_copilot.contracts import Evidence, UserPreferences
from commerce_copilot.security import hash_password


def test_seed_is_idempotent(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")

    repository.seed_products()
    repository.seed_products()

    assert repository.count_products() == 5


def test_fts_search_returns_relevant_products(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()

    hits = repository.search_products("想买通勤用的无线耳机")

    assert hits
    assert all(hit.product.category == "耳机" for hit in hits)
    assert hits[0].product.product_id in {"CP-AUD-101", "CP-AUD-102"}


def test_budget_is_a_hard_filter(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()

    hits = repository.search_products("蓝牙耳机", max_price_cents=40000)

    assert [hit.product.product_id for hit in hits] == ["CP-AUD-101"]


def test_category_is_a_hard_filter(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()

    hits = repository.search_products(
        "办公",
        max_price_cents=30000,
        categories=["耳机"],
    )

    assert hits == []


def test_unknown_term_returns_no_result(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()

    assert repository.search_products("咖啡机") == []


def test_conversation_turns_are_returned_in_chronological_order(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.add_conversation_turn("session-a", "第一问", "第一答")
    repository.add_conversation_turn("session-a", "第二问", "第二答")
    repository.add_conversation_turn("session-b", "其他问题", "其他回答")

    turns = repository.get_recent_conversation_turns("session-a")

    assert [turn.user_message for turn in turns] == ["第一问", "第二问"]
    assert [turn.assistant_answer for turn in turns] == ["第一答", "第二答"]


def test_conversation_turn_preserves_ordered_evidence(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    evidence = [
        Evidence(
            source="local_catalog",
            reference="CP-AUD-102",
            attributes={"title": "潮汐 Buds 2 降噪耳机"},
        ),
        Evidence(
            source="local_catalog",
            reference="CP-AUD-101",
            attributes={"title": "云雀 Lite 通勤蓝牙耳机"},
        ),
    ]

    repository.add_conversation_turn("session-a", "推荐耳机", "两款商品", evidence)
    turn = repository.get_recent_conversation_turns("session-a")[0]

    assert [item.reference for item in turn.evidence] == ["CP-AUD-102", "CP-AUD-101"]


def test_order_lookup_enforces_user_ownership(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()
    repository.seed_support_data()
    owner = repository.create_user("owner", hash_password("owner-password"))
    other = repository.create_user("other", hash_password("other-password"))
    repository.assign_order_to_user("ORD-2026-1001", owner.user_id)

    assert repository.get_order("ORD-2026-1001", user_id=owner.user_id) is not None
    assert repository.get_order("ORD-2026-1001", user_id=other.user_id) is None


def test_delete_conversation_requires_owner_and_removes_history(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()
    owner = repository.create_user("cleanup-owner", hash_password("owner-password"))
    other = repository.create_user("cleanup-other", hash_password("other-password"))
    conversation_id = "benchmark-cleanup"
    assert repository.ensure_conversation_owner(conversation_id, owner.user_id)
    repository.add_conversation_turn(conversation_id, "hello", "world")

    assert repository.delete_conversation(conversation_id, user_id=other.user_id) is False
    assert repository.get_recent_conversation_turns(conversation_id)
    assert repository.delete_conversation(conversation_id, user_id=owner.user_id) is True
    assert repository.get_recent_conversation_turns(conversation_id) == []


def test_user_preferences_are_persisted_and_replaced(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    user = repository.create_user("preference-owner", hash_password("password-123"))

    assert repository.get_user_preferences(user.user_id) == UserPreferences()

    first = UserPreferences(
        max_budget_cents=50000,
        preferred_categories=["耳机"],
        scenarios=["通勤"],
    )
    repository.save_user_preferences(user.user_id, first)
    assert repository.get_user_preferences(user.user_id) == first

    replacement = UserPreferences(
        max_budget_cents=40000,
        priorities=["轻量"],
    )
    repository.save_user_preferences(user.user_id, replacement)
    assert repository.get_user_preferences(user.user_id) == replacement
