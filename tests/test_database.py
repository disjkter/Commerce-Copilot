import pytest

from commerce_copilot.database import (
    PRODUCT_EMBEDDING_DIMENSIONS,
    CatalogRepository,
    _vector_literal,
)
from commerce_copilot.contracts import Evidence, UserPreferences
from commerce_copilot.memory import extract_memory_candidates
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


def test_sqlite_reports_its_lexical_backend(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")

    assert repository.lexical_source == "fts5"


def test_postgres_reports_its_lexical_backend():
    repository = CatalogRepository(
        "postgresql+psycopg://user:password@127.0.0.1:5432/database"
    )

    assert repository.lexical_source == "postgres_fts"
    repository.engine.dispose()


def test_vector_literal_requires_the_catalog_dimension():
    values = [0.0] * PRODUCT_EMBEDDING_DIMENSIONS
    values[3] = 0.25

    literal = _vector_literal(values)

    assert literal.startswith("[0,0,0,0.25,")
    assert literal.endswith("]")
    with pytest.raises(ValueError, match="1024 dimensions"):
        _vector_literal([0.0, 1.0])


def test_sqlite_keeps_vector_storage_in_process(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")

    assert repository.vector_source == "vector"
    assert repository.get_product_embedding_metadata() == {}
    assert (
        repository.save_product_embeddings(
            {"product": [0.0] * PRODUCT_EMBEDDING_DIMENSIONS},
            model="test-model",
        )
        == 0
    )


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


def test_conversation_memory_compacts_old_turns_and_keeps_recent_window(tmp_path):
    repository = CatalogRepository(tmp_path / "memory.db")
    owner = repository.create_user("memory-owner", hash_password("password-123"))
    conversation_id = "memory-session"
    assert repository.ensure_conversation_owner(conversation_id, owner.user_id)
    product = Evidence(
        source="local_catalog",
        reference="CP-AUD-101",
        attributes={"title": "云雀 Lite 通勤蓝牙耳机"},
    )
    repository.add_conversation_turn(
        conversation_id,
        "预算500元，推荐耳机",
        "找到商品",
        [product],
    )
    repository.add_conversation_turn(
        conversation_id,
        "查询订单 ORD-2026-1001",
        "订单已发货",
    )
    for index in range(4):
        repository.add_conversation_turn(
            conversation_id,
            f"普通问题 {index}",
            f"普通回答 {index}",
        )

    memory = repository.compact_conversation_memory(conversation_id, recent_limit=2)
    history, loaded_memory, recent_count = repository.get_conversation_context(
        conversation_id,
        recent_limit=2,
    )

    assert memory is not None
    assert memory.source_turn_count == 4
    assert memory.entities["order_ids"] == ["ORD-2026-1001"]
    assert memory.constraints["max_price_cents"] == 50000
    assert [item.reference for item in memory.evidence] == ["CP-AUD-101"]
    assert loaded_memory == memory
    assert recent_count == 2
    assert len(history) == 3
    assert history[0].user_message.startswith("历史会话摘要：")

    unchanged = repository.compact_conversation_memory(
        conversation_id,
        recent_limit=2,
    )
    assert unchanged is not None
    assert unchanged.source_turn_count == 4


def test_atomic_memory_versions_conflicts_without_losing_provenance(tmp_path):
    repository = CatalogRepository(tmp_path / "atomic-memory.db")
    owner = repository.create_user("atomic-owner", hash_password("password-123"))
    conversation_id = "atomic-session"
    assert repository.ensure_conversation_owner(conversation_id, owner.user_id)

    first_turn = repository.add_conversation_turn(
        conversation_id,
        "我喜欢入耳式耳机",
        "已记录",
    )
    first_candidate = extract_memory_candidates("我喜欢入耳式耳机")
    first = repository.record_memory_candidates(
        user_id=owner.user_id,
        conversation_id=conversation_id,
        source_turn_id=first_turn,
        candidates=first_candidate,
    )[0]

    second_turn = repository.add_conversation_turn(
        conversation_id,
        "我不喜欢入耳式耳机",
        "已更新",
    )
    second_candidate = extract_memory_candidates("我不喜欢入耳式耳机")
    second = repository.record_memory_candidates(
        user_id=owner.user_id,
        conversation_id=conversation_id,
        source_turn_id=second_turn,
        candidates=second_candidate,
    )[0]

    active = repository.list_memory_items(user_id=owner.user_id)
    audit = repository.list_memory_items(
        user_id=owner.user_id,
        include_invalidated=True,
    )

    assert len(active) == 1
    assert active[0].value == {"term": "入耳式", "accepted": False}
    assert second.supersedes_memory_id == first.memory_id
    assert len(audit) == 2
    invalidated = next(item for item in audit if item.memory_id == first.memory_id)
    assert invalidated.status.value == "invalidated"
    assert invalidated.valid_to is not None
    assert second.source_turn_id == second_turn
    assert repository.invalidate_memory_item(
        second.memory_id,
        user_id=owner.user_id,
    ) is True
    assert repository.list_memory_items(user_id=owner.user_id) == []


def test_conversation_memory_does_not_leak_to_another_user(tmp_path):
    repository = CatalogRepository(tmp_path / "memory-isolation.db")
    owner = repository.create_user("memory-owner-a", hash_password("password-123"))
    other = repository.create_user("memory-owner-b", hash_password("password-123"))
    conversation_id = "isolated-memory-session"
    assert repository.ensure_conversation_owner(conversation_id, owner.user_id)
    turn_id = repository.add_conversation_turn(conversation_id, "预算500元", "收到")
    repository.record_memory_candidates(
        user_id=owner.user_id,
        conversation_id=conversation_id,
        source_turn_id=turn_id,
        candidates=extract_memory_candidates("预算500元"),
    )

    assert repository.list_memory_items(user_id=other.user_id) == []


def test_order_lookup_enforces_user_ownership(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()
    repository.seed_support_data()
    owner = repository.create_user("owner", hash_password("owner-password"))
    other = repository.create_user("other", hash_password("other-password"))
    repository.assign_order_to_user("ORD-2026-1001", owner.user_id)

    assert repository.get_order("ORD-2026-1001", user_id=owner.user_id) is not None
    assert repository.get_order("ORD-2026-1001", user_id=other.user_id) is None

    assert [
        order.order_id for order in repository.list_user_orders(owner.user_id)
    ] == ["ORD-2026-1001"]
    assert repository.list_user_orders(other.user_id) == []


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
