import re
import threading
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    delete,
    func,
    inspect,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import IntegrityError
from pgvector.sqlalchemy import VECTOR as _PGVectorType  # registers reflection support

from .catalog import Product, ProductSearchHit, ProductVectorSearchHit
from .config import get_settings
from .contracts import (
    ConversationMemory,
    ConversationTurn,
    Evidence,
    UserPreferences,
    UserRecord,
)
from .memory import merge_conversation_memory
from .support import (
    OrderItem,
    OrderRecord,
    PolicyRecord,
    ReturnRequestRecord,
    ShipmentRecord,
    TrackingEvent,
    TrackingWebhookPayload,
)
from .security import hash_password


DEMO_PRODUCTS = (
    Product(
        product_id="CP-AUD-101",
        title="云雀 Lite 通勤蓝牙耳机",
        category="耳机",
        brand="云雀",
        description="轻量头戴设计，支持双设备连接，适合日常通勤和在线会议。",
        price_cents=32900,
        rating=4.6,
        review_count=184,
        stock=26,
        tags=["耳机", "蓝牙", "无线", "通勤", "头戴式", "会议"],
    ),
    Product(
        product_id="CP-AUD-102",
        title="潮汐 Buds 2 降噪耳机",
        category="耳机",
        brand="潮汐",
        description="入耳式主动降噪耳机，配备通透模式和充电盒。",
        price_cents=46900,
        rating=4.7,
        review_count=96,
        stock=18,
        tags=["耳机", "蓝牙", "无线", "降噪", "入耳式", "通勤"],
    ),
    Product(
        product_id="CP-AUD-103",
        title="原野 Air 开放式耳机",
        category="耳机",
        brand="原野",
        description="开放式佩戴，强调环境感知和长时间佩戴舒适度。",
        price_cents=59900,
        rating=4.4,
        review_count=61,
        stock=12,
        tags=["耳机", "蓝牙", "无线", "开放式", "运动", "舒适"],
    ),
    Product(
        product_id="CP-KEY-201",
        title="方糖 75 静音机械键盘",
        category="键盘",
        brand="方糖",
        description="75% 配列，热插拔静音轴，支持有线与蓝牙连接。",
        price_cents=39900,
        rating=4.5,
        review_count=73,
        stock=20,
        tags=["键盘", "机械键盘", "静音", "蓝牙", "办公", "热插拔"],
    ),
    Product(
        product_id="CP-MOU-301",
        title="跃点 M3 无线鼠标",
        category="鼠标",
        brand="跃点",
        description="轻量双模鼠标，可调节 DPI，适合移动办公。",
        price_cents=15900,
        rating=4.3,
        review_count=128,
        stock=41,
        tags=["鼠标", "无线", "蓝牙", "轻量", "办公"],
    ),
)

PRODUCT_EMBEDDING_DIMENSIONS = 1024


KNOWN_TERMS = (
    "机械键盘",
    "开放式",
    "入耳式",
    "头戴式",
    "蓝牙",
    "无线",
    "耳机",
    "键盘",
    "鼠标",
    "通勤",
    "降噪",
    "静音",
    "办公",
    "运动",
    "会议",
)


metadata = MetaData()

products_table = Table(
    "products",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("product_id", String(64), nullable=False, unique=True),
    Column("title", Text, nullable=False),
    Column("category", String(128), nullable=False),
    Column("brand", String(128), nullable=False),
    Column("description", Text, nullable=False),
    Column("price_cents", Integer, nullable=False),
    Column("rating", Float, nullable=False),
    Column("review_count", Integer, nullable=False, server_default="0"),
    Column("stock", Integer, nullable=False, server_default="0"),
    Column("tags", Text, nullable=False, server_default=""),
    Column("returnable", Boolean, nullable=False, server_default="true"),
    Column("return_window_days", Integer, nullable=False, server_default="7"),
    CheckConstraint("price_cents >= 0"),
    CheckConstraint("rating BETWEEN 0 AND 5"),
    CheckConstraint("review_count >= 0"),
    CheckConstraint("stock >= 0"),
    CheckConstraint("return_window_days > 0"),
)

orders_table = Table(
    "orders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("order_id", String(64), nullable=False, unique=True),
    Column("status", String(64), nullable=False),
    Column("placed_at", DateTime(timezone=True), nullable=False),
)

order_items_table = Table(
    "order_items",
    metadata,
    Column("order_id", ForeignKey("orders.id"), primary_key=True),
    Column("product_id", ForeignKey("products.id"), primary_key=True),
    Column("quantity", Integer, nullable=False),
    Column("unit_price_cents", Integer, nullable=False),
    CheckConstraint("quantity > 0"),
    CheckConstraint("unit_price_cents >= 0"),
)

shipments_table = Table(
    "shipments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("shipment_id", String(64), nullable=False, unique=True),
    Column("order_id", ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
    Column("tracking_number", String(128), nullable=False, unique=True),
    Column("carrier_code", String(64), nullable=False),
    Column("status", String(64), nullable=False),
    Column("estimated_delivery_at", DateTime(timezone=True)),
    Column("shipped_at", DateTime(timezone=True)),
    Column("delivered_at", DateTime(timezone=True)),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    ),
)
Index("shipments_by_order", shipments_table.c.order_id, shipments_table.c.id)

shipment_events_table = Table(
    "shipment_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "shipment_id",
        ForeignKey("shipments.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("provider_event_id", String(128), nullable=False),
    Column("status", String(64), nullable=False),
    Column("location", String(256)),
    Column("description", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "shipment_id",
        "provider_event_id",
        name="shipment_events_provider_event_unique",
    ),
)
Index(
    "shipment_events_timeline",
    shipment_events_table.c.shipment_id,
    shipment_events_table.c.occurred_at,
    shipment_events_table.c.id,
)

logistics_webhook_events_table = Table(
    "logistics_webhook_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("provider", String(64), nullable=False),
    Column("provider_event_id", String(128), nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("processing_status", String(32), nullable=False, server_default="pending"),
    Column(
        "received_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    UniqueConstraint(
        "provider",
        "provider_event_id",
        name="logistics_webhook_provider_event_unique",
    ),
)

service_policies_table = Table(
    "service_policies",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("topic", String(64), nullable=False),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("effective_from", String(32), nullable=False),
)

conversation_turns_table = Table(
    "conversation_turns",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("conversation_id", String(64), nullable=False),
    Column("user_message", Text, nullable=False),
    Column("assistant_answer", Text, nullable=False),
    Column("created_at", String(32), nullable=False, server_default=func.current_timestamp()),
)
Index(
    "conversation_turns_by_conversation",
    conversation_turns_table.c.conversation_id,
    conversation_turns_table.c.id,
)

conversation_evidence_table = Table(
    "conversation_turn_evidence",
    metadata,
    Column(
        "turn_id",
        ForeignKey("conversation_turns.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column("source", String(128), nullable=False),
    Column("reference", String(256), nullable=False),
    Column("attributes_json", JSON, nullable=False),
)

users_table = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(64), nullable=False, unique=True),
    Column("username", String(32), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("created_at", String(32), nullable=False, server_default=func.current_timestamp()),
)
Index("users_username_lower_unique", func.lower(users_table.c.username), unique=True)

order_owners_table = Table(
    "order_owners",
    metadata,
    Column("order_id", ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)

return_requests_table = Table(
    "return_requests",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("request_id", String(64), nullable=False, unique=True),
    Column("order_id", ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("status", String(32), nullable=False, server_default="pending_review"),
    Column("reason", Text, nullable=False),
    Column("precheck_decision", String(64), nullable=False, server_default="manual_review"),
    Column("risk_score", Integer, nullable=False, server_default="0"),
    Column("risk_level", String(16), nullable=False, server_default="low"),
    Column("decision_reasons", JSON, nullable=False, server_default="[]"),
    Column("matched_rules", JSON, nullable=False, server_default="[]"),
    Column("missing_evidence", JSON, nullable=False, server_default="[]"),
    Column("rule_version", String(64), nullable=False, server_default="legacy"),
    Column("idempotency_key", String(256), nullable=False, unique=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    CheckConstraint(
        "status IN ('pending_review', 'approved', 'rejected', 'cancelled')",
        name="return_requests_status_valid",
    ),
)
Index(
    "return_requests_by_user",
    return_requests_table.c.user_id,
    return_requests_table.c.created_at,
)

return_request_events_table = Table(
    "return_request_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "return_request_id",
        ForeignKey("return_requests.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("from_status", String(32)),
    Column("to_status", String(32), nullable=False),
    Column("actor_type", String(32), nullable=False),
    Column("actor_id", String(64), nullable=False),
    Column("reason", Text, nullable=False),
    Column(
        "occurred_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    CheckConstraint(
        "actor_type IN ('user', 'system', 'reviewer')",
        name="return_request_events_actor_type_valid",
    ),
)
Index(
    "return_request_events_timeline",
    return_request_events_table.c.return_request_id,
    return_request_events_table.c.occurred_at,
    return_request_events_table.c.id,
)

conversation_owners_table = Table(
    "conversation_owners",
    metadata,
    Column("conversation_id", String(64), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
)

conversation_memories_table = Table(
    "conversation_memories",
    metadata,
    Column(
        "conversation_id",
        ForeignKey("conversation_owners.conversation_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("summary_text", Text, nullable=False),
    Column("entities_json", JSON, nullable=False),
    Column("constraints_json", JSON, nullable=False),
    Column("evidence_json", JSON, nullable=False),
    Column("compacted_through_turn_id", Integer, nullable=False),
    Column("source_turn_count", Integer, nullable=False),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    ),
)

user_preferences_table = Table(
    "user_preferences",
    metadata,
    Column(
        "user_id",
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("max_budget_cents", Integer),
    Column("preferred_categories", JSON, nullable=False),
    Column("preferred_brands", JSON, nullable=False),
    Column("scenarios", JSON, nullable=False),
    Column("priorities", JSON, nullable=False),
    Column("updated_at", String(32), nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("max_budget_cents IS NULL OR max_budget_cents >= 0"),
)


class CatalogRepository:
    def __init__(self, target: Path | str):
        self.database_url, self.path = _resolve_database_target(target)
        connect_args = {"check_same_thread": False} if self.database_url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            self.database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        self._ready = False
        self._ready_lock = threading.Lock()

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    @property
    def lexical_source(self) -> str:
        """Return the lexical backend name exposed in retrieval evidence."""
        return "fts5" if self.dialect == "sqlite" else "postgres_fts"

    @property
    def vector_source(self) -> str:
        return "vector" if self.dialect == "sqlite" else "pgvector"

    def initialize(self) -> None:
        if self._ready:
            return
        with self._ready_lock:
            if self._ready:
                return
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.dialect == "sqlite":
                metadata.create_all(self.engine)
                self._initialize_sqlite_fts()
            elif not inspect(self.engine).has_table("products"):
                raise RuntimeError(
                    "PostgreSQL schema is missing; run `alembic upgrade head` first"
                )
            self._ready = True

    def _initialize_sqlite_fts(self) -> None:
        statements = (
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS product_fts USING fts5(
                title, category, brand, description, tags,
                content='products', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
            """,
            """
            CREATE TRIGGER IF NOT EXISTS products_after_insert AFTER INSERT ON products BEGIN
                INSERT INTO product_fts(rowid, title, category, brand, description, tags)
                VALUES (new.id, new.title, new.category, new.brand, new.description, new.tags);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS products_after_delete AFTER DELETE ON products BEGIN
                INSERT INTO product_fts(product_fts, rowid, title, category, brand, description, tags)
                VALUES ('delete', old.id, old.title, old.category, old.brand, old.description, old.tags);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS products_after_update AFTER UPDATE ON products BEGIN
                INSERT INTO product_fts(product_fts, rowid, title, category, brand, description, tags)
                VALUES ('delete', old.id, old.title, old.category, old.brand, old.description, old.tags);
                INSERT INTO product_fts(rowid, title, category, brand, description, tags)
                VALUES (new.id, new.title, new.category, new.brand, new.description, new.tags);
            END
            """,
        )
        with self.engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)

    def seed_products(self, products: Iterable[Product] = DEMO_PRODUCTS) -> None:
        self.initialize()
        with self.engine.begin() as connection:
            for product in products:
                existing = connection.scalar(
                    select(products_table.c.id).where(
                        products_table.c.product_id == product.product_id
                    )
                )
                if existing is None:
                    connection.execute(
                        insert(products_table).values(
                            product_id=product.product_id,
                            title=product.title,
                            category=product.category,
                            brand=product.brand,
                            description=product.description,
                            price_cents=product.price_cents,
                            rating=product.rating,
                            review_count=product.review_count,
                            stock=product.stock,
                            tags=" ".join(product.tags),
                            returnable=product.returnable,
                            return_window_days=product.return_window_days,
                        )
                    )

    def seed_support_data(self) -> None:
        self.initialize()
        demo_now = datetime.now(timezone.utc).replace(microsecond=0)
        order_rows = (
            ("ORD-2026-1001", "shipped", _seed_datetime("2026-10-15 14:30:00")),
            ("ORD-2026-1002", "delivered", _seed_datetime("2026-10-10 09:20:00")),
            ("ORD-2026-1003", "delivered", demo_now - timedelta(days=5)),
        )
        item_rows = (
            ("ORD-2026-1001", "CP-AUD-101", 1, 32900),
            ("ORD-2026-1002", "CP-KEY-201", 1, 39900),
            ("ORD-2026-1003", "CP-AUD-102", 1, 46900),
        )
        shipment_rows = (
            (
                "SHP-2026-1001",
                "ORD-2026-1001",
                "SF202610150001",
                "SF",
                "in_transit",
                _seed_datetime("2026-10-18 18:00:00"),
                _seed_datetime("2026-10-16 09:10:00"),
                None,
            ),
            (
                "SHP-2026-1002",
                "ORD-2026-1002",
                "JD202610100002",
                "JD",
                "delivered",
                _seed_datetime("2026-10-12 18:00:00"),
                _seed_datetime("2026-10-10 16:40:00"),
                _seed_datetime("2026-10-12 11:25:00"),
            ),
            (
                "SHP-2026-1003",
                "ORD-2026-1003",
                "SF202611150003",
                "SF",
                "delivered",
                demo_now - timedelta(days=2),
                demo_now - timedelta(days=4),
                demo_now - timedelta(days=2),
            ),
        )
        tracking_event_rows = (
            (
                "SHP-2026-1001",
                "SF-EVT-1001-01",
                "picked_up",
                "杭州余杭集散中心",
                "快件已由承运商揽收",
                _seed_datetime("2026-10-16 09:10:00"),
            ),
            (
                "SHP-2026-1001",
                "SF-EVT-1001-02",
                "in_transit",
                "上海转运中心",
                "快件正在发往下一站",
                _seed_datetime("2026-10-17 03:25:00"),
            ),
            (
                "SHP-2026-1002",
                "JD-EVT-1002-01",
                "picked_up",
                "苏州分拨中心",
                "包裹已揽收",
                _seed_datetime("2026-10-10 16:40:00"),
            ),
            (
                "SHP-2026-1002",
                "JD-EVT-1002-02",
                "delivered",
                "上海市浦东新区",
                "包裹已签收",
                _seed_datetime("2026-10-12 11:25:00"),
            ),
            (
                "SHP-2026-1003",
                "SF-EVT-1003-01",
                "picked_up",
                "杭州余杭集散中心",
                "快件已由承运商揽收",
                demo_now - timedelta(days=4),
            ),
            (
                "SHP-2026-1003",
                "SF-EVT-1003-02",
                "delivered",
                "上海市浦东新区",
                "包裹已签收",
                demo_now - timedelta(days=2),
            ),
        )
        policies = (
            (
                "returns",
                "七日退货规则",
                "签收后7日内，商品保持完好且配件齐全时可以申请退货；定制和已激活商品除外。",
                "2026-01-01",
            ),
            (
                "shipping",
                "发货与物流规则",
                "现货订单通常在付款后48小时内发出，具体状态以订单查询结果为准。",
                "2026-01-01",
            ),
        )
        with self.engine.begin() as connection:
            for order_id, status, placed_at in order_rows:
                if connection.scalar(
                    select(orders_table.c.id).where(orders_table.c.order_id == order_id)
                ) is None:
                    connection.execute(
                        insert(orders_table).values(
                            order_id=order_id,
                            status=status,
                            placed_at=placed_at,
                        )
                    )
            for order_id, product_id, quantity, unit_price_cents in item_rows:
                internal_order_id = connection.scalar(
                    select(orders_table.c.id).where(orders_table.c.order_id == order_id)
                )
                internal_product_id = connection.scalar(
                    select(products_table.c.id).where(
                        products_table.c.product_id == product_id
                    )
                )
                if internal_order_id is None or internal_product_id is None:
                    continue
                exists = connection.scalar(
                    select(order_items_table.c.order_id).where(
                        and_(
                            order_items_table.c.order_id == internal_order_id,
                            order_items_table.c.product_id == internal_product_id,
                        )
                    )
                )
                if exists is None:
                    connection.execute(
                        insert(order_items_table).values(
                            order_id=internal_order_id,
                            product_id=internal_product_id,
                            quantity=quantity,
                            unit_price_cents=unit_price_cents,
                        )
                    )
            for topic, title, body, effective_from in policies:
                if connection.scalar(
                    select(service_policies_table.c.id).where(
                        service_policies_table.c.topic == topic
                    )
                ) is None:
                    connection.execute(
                        insert(service_policies_table).values(
                            topic=topic,
                            title=title,
                            body=body,
                            effective_from=effective_from,
                        )
                    )
            for (
                shipment_id,
                order_id,
                tracking_number,
                carrier_code,
                status,
                estimated_delivery_at,
                shipped_at,
                delivered_at,
            ) in shipment_rows:
                order_key = connection.scalar(
                    select(orders_table.c.id).where(orders_table.c.order_id == order_id)
                )
                if order_key is None:
                    continue
                existing = connection.scalar(
                    select(shipments_table.c.id).where(
                        shipments_table.c.shipment_id == shipment_id
                    )
                )
                if existing is None:
                    connection.execute(
                        insert(shipments_table).values(
                            shipment_id=shipment_id,
                            order_id=order_key,
                            tracking_number=tracking_number,
                            carrier_code=carrier_code,
                            status=status,
                            estimated_delivery_at=estimated_delivery_at,
                            shipped_at=shipped_at,
                            delivered_at=delivered_at,
                        )
                    )
            for (
                shipment_id,
                provider_event_id,
                status,
                location,
                description,
                occurred_at,
            ) in tracking_event_rows:
                shipment_key = connection.scalar(
                    select(shipments_table.c.id).where(
                        shipments_table.c.shipment_id == shipment_id
                    )
                )
                if shipment_key is None:
                    continue
                existing = connection.scalar(
                    select(shipment_events_table.c.id).where(
                        and_(
                            shipment_events_table.c.shipment_id == shipment_key,
                            shipment_events_table.c.provider_event_id
                            == provider_event_id,
                        )
                    )
                )
                if existing is None:
                    connection.execute(
                        insert(shipment_events_table).values(
                            shipment_id=shipment_key,
                            provider_event_id=provider_event_id,
                            status=status,
                            location=location,
                            description=description,
                            occurred_at=occurred_at,
                        )
                    )

    def seed_demo_identity(self) -> None:
        self.initialize()
        if self.get_user_by_username("demo") is None:
            self.create_user("demo", hash_password("demo12345"), user_id="user-demo")
        with self.engine.begin() as connection:
            user_key = connection.scalar(
                select(users_table.c.id).where(users_table.c.user_id == "user-demo")
            )
            if user_key is None:
                return
            order_keys = connection.scalars(select(orders_table.c.id)).all()
            for order_key in order_keys:
                exists = connection.scalar(
                    select(order_owners_table.c.order_id).where(
                        and_(
                            order_owners_table.c.order_id == order_key,
                            order_owners_table.c.user_id == user_key,
                        )
                    )
                )
                if exists is None:
                    connection.execute(
                        insert(order_owners_table).values(
                            order_id=order_key,
                            user_id=user_key,
                        )
                    )

    def search_products(
        self,
        query: str,
        *,
        max_price_cents: int | None = None,
        categories: list[str] | None = None,
        in_stock_only: bool = True,
        limit: int = 5,
    ) -> list[ProductSearchHit]:
        self.initialize()
        if self.dialect == "sqlite":
            return self._search_products_sqlite(
                query,
                max_price_cents=max_price_cents,
                categories=categories,
                in_stock_only=in_stock_only,
                limit=limit,
            )
        return self._search_products_postgres(
            query,
            max_price_cents=max_price_cents,
            categories=categories,
            in_stock_only=in_stock_only,
            limit=limit,
        )

    def _search_products_sqlite(
        self,
        query: str,
        *,
        max_price_cents: int | None,
        categories: list[str] | None,
        in_stock_only: bool,
        limit: int,
    ) -> list[ProductSearchHit]:
        fts_query = _build_fts_query(query)
        if not fts_query:
            return []
        conditions = ["product_fts MATCH :fts_query"]
        parameters: dict[str, object] = {"fts_query": fts_query, "limit": limit}
        if max_price_cents is not None:
            conditions.append("p.price_cents <= :max_price_cents")
            parameters["max_price_cents"] = max_price_cents
        if categories:
            placeholders = []
            for index, category in enumerate(categories):
                key = f"category_{index}"
                placeholders.append(f":{key}")
                parameters[key] = category
            conditions.append(f"p.category IN ({', '.join(placeholders)})")
        if in_stock_only:
            conditions.append("p.stock > 0")
        statement = text(
            f"""
            SELECT p.product_id, p.title, p.category, p.brand, p.description,
                   p.price_cents, p.rating, p.review_count, p.stock, p.tags,
                   p.returnable, p.return_window_days,
                   bm25(product_fts, 4.0, 2.0, 1.5, 1.0, 3.0) AS lexical_score
            FROM product_fts
            JOIN products AS p ON p.id = product_fts.rowid
            WHERE {' AND '.join(conditions)}
            ORDER BY lexical_score ASC, p.rating DESC, p.review_count DESC
            LIMIT :limit
            """
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement, parameters).mappings().all()
        return [_row_to_hit(row) for row in rows]

    def _search_products_postgres(
        self,
        query: str,
        *,
        max_price_cents: int | None,
        categories: list[str] | None,
        in_stock_only: bool,
        limit: int,
    ) -> list[ProductSearchHit]:
        search_query = " ".join(_search_terms(query))
        if not search_query:
            return []
        parameters: dict[str, object] = {
            "search_query": search_query,
            "limit": limit,
        }
        conditions = ["p.search_vector @@ search.query"]
        if max_price_cents is not None:
            conditions.append("p.price_cents <= :max_price_cents")
            parameters["max_price_cents"] = max_price_cents
        if categories:
            placeholders = []
            for index, category in enumerate(categories):
                key = f"category_{index}"
                placeholders.append(f":{key}")
                parameters[key] = category
            conditions.append(f"p.category IN ({', '.join(placeholders)})")
        if in_stock_only:
            conditions.append("p.stock > 0")
        statement = text(
            f"""
            WITH search AS (
                SELECT plainto_tsquery('simple', :search_query) AS query
            )
            SELECT p.product_id, p.title, p.category, p.brand, p.description,
                   p.price_cents, p.rating, p.review_count, p.stock, p.tags,
                   p.returnable, p.return_window_days,
                   ts_rank_cd(p.search_vector, search.query, 32)::float
                       AS lexical_score
            FROM products AS p
            CROSS JOIN search
            WHERE {' AND '.join(conditions)}
            ORDER BY lexical_score DESC, p.rating DESC, p.review_count DESC
            LIMIT :limit
            """
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement, parameters).mappings().all()
        return [_row_to_hit(row) for row in rows]

    def count_products(self) -> int:
        self.initialize()
        with self.engine.connect() as connection:
            return int(connection.scalar(select(func.count()).select_from(products_table)) or 0)

    def list_products(
        self,
        *,
        max_price_cents: int | None = None,
        categories: list[str] | None = None,
        in_stock_only: bool = True,
    ) -> list[Product]:
        self.initialize()
        statement = select(products_table)
        if max_price_cents is not None:
            statement = statement.where(products_table.c.price_cents <= max_price_cents)
        if categories:
            statement = statement.where(products_table.c.category.in_(categories))
        if in_stock_only:
            statement = statement.where(products_table.c.stock > 0)
        statement = statement.order_by(products_table.c.product_id)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_row_to_product(row) for row in rows]

    def save_product_embeddings(
        self,
        embeddings: dict[str, Sequence[float]],
        *,
        model: str,
        source_hashes: dict[str, str] | None = None,
    ) -> int:
        """Persist precomputed product vectors in PostgreSQL.

        SQLite deliberately keeps its in-process vector baseline so unit tests
        and the zero-setup demo do not require a PostgreSQL extension.
        """
        self.initialize()
        if self.dialect != "postgresql":
            return 0
        statement = text(
            """
            UPDATE products
            SET embedding = CAST(:embedding AS vector),
                embedding_model = :model,
                embedding_source_hash = :source_hash,
                embedding_updated_at = CURRENT_TIMESTAMP
            WHERE product_id = :product_id
            """
        )
        updated = 0
        with self.engine.begin() as connection:
            for product_id, embedding in embeddings.items():
                result = connection.execute(
                    statement,
                    {
                        "product_id": product_id,
                        "embedding": _vector_literal(embedding),
                        "model": model,
                        "source_hash": (source_hashes or {}).get(product_id),
                    },
                )
                updated += result.rowcount
        return updated

    def get_product_embedding_metadata(
        self,
    ) -> dict[str, tuple[str | None, str | None]]:
        self.initialize()
        if self.dialect != "postgresql":
            return {}
        statement = text(
            """
            SELECT product_id, embedding_model, embedding_source_hash
            FROM products
            """
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return {
            row["product_id"]: (
                row["embedding_model"],
                row["embedding_source_hash"],
            )
            for row in rows
        }

    def search_products_by_vector(
        self,
        query_vector: Sequence[float],
        *,
        max_price_cents: int | None = None,
        categories: list[str] | None = None,
        in_stock_only: bool = True,
        min_score: float = 0.08,
        limit: int = 20,
    ) -> list[ProductVectorSearchHit]:
        self.initialize()
        if self.dialect != "postgresql":
            raise RuntimeError("database vector search requires PostgreSQL")

        parameters: dict[str, object] = {
            "query_vector": _vector_literal(query_vector),
            "max_distance": 1.0 - min_score,
            "limit": limit,
        }
        conditions = [
            "p.embedding IS NOT NULL",
            "p.embedding <=> CAST(:query_vector AS vector) <= :max_distance",
        ]
        if max_price_cents is not None:
            conditions.append("p.price_cents <= :max_price_cents")
            parameters["max_price_cents"] = max_price_cents
        if categories:
            placeholders = []
            for index, category in enumerate(categories):
                key = f"vector_category_{index}"
                placeholders.append(f":{key}")
                parameters[key] = category
            conditions.append(f"p.category IN ({', '.join(placeholders)})")
        if in_stock_only:
            conditions.append("p.stock > 0")

        statement = text(
            f"""
            SELECT p.product_id, p.title, p.category, p.brand, p.description,
                   p.price_cents, p.rating, p.review_count, p.stock, p.tags,
                   p.returnable, p.return_window_days,
                   1 - (p.embedding <=> CAST(:query_vector AS vector))
                       AS vector_score
            FROM products AS p
            WHERE {' AND '.join(conditions)}
            ORDER BY p.embedding <=> CAST(:query_vector AS vector),
                     p.rating DESC,
                     p.review_count DESC
            LIMIT :limit
            """
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement, parameters).mappings().all()
        return [_row_to_vector_hit(row) for row in rows]

    def get_product(self, product_id: str) -> Product | None:
        self.initialize()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(products_table).where(products_table.c.product_id == product_id)
            ).mappings().first()
        return _row_to_product(row) if row else None

    def get_order(
        self,
        order_id: str,
        *,
        user_id: str | None = None,
    ) -> OrderRecord | None:
        self.initialize()
        statement = select(
            orders_table.c.id,
            orders_table.c.order_id,
            orders_table.c.status,
            orders_table.c.placed_at,
        ).where(orders_table.c.order_id == order_id)
        if user_id is not None:
            statement = (
                statement.join(
                    order_owners_table,
                    order_owners_table.c.order_id == orders_table.c.id,
                )
                .join(users_table, users_table.c.id == order_owners_table.c.user_id)
                .where(users_table.c.user_id == user_id)
            )
        with self.engine.connect() as connection:
            order = connection.execute(statement).mappings().first()
            if order is None:
                return None
            item_rows = connection.execute(
                select(
                    products_table.c.product_id,
                    products_table.c.title,
                    order_items_table.c.quantity,
                    order_items_table.c.unit_price_cents,
                    products_table.c.returnable,
                    products_table.c.return_window_days,
                )
                .join(products_table, products_table.c.id == order_items_table.c.product_id)
                .where(order_items_table.c.order_id == order["id"])
                .order_by(products_table.c.product_id)
            ).mappings().all()
        return OrderRecord(
            order_id=order["order_id"],
            status=order["status"],
            placed_at=_database_datetime(order["placed_at"]),
            items=[
                OrderItem(
                    product_id=row["product_id"],
                    title=row["title"],
                    quantity=row["quantity"],
                    unit_price_cents=row["unit_price_cents"],
                    returnable=row["returnable"],
                    return_window_days=row["return_window_days"],
                )
                for row in item_rows
            ],
        )

    def list_user_orders(
        self,
        user_id: str,
        *,
        limit: int = 10,
    ) -> list[OrderRecord]:
        """List only orders owned by the authenticated application user."""
        self.initialize()
        safe_limit = max(1, min(limit, 50))
        with self.engine.connect() as connection:
            order_ids = connection.scalars(
                select(orders_table.c.order_id)
                .select_from(
                    orders_table.join(
                        order_owners_table,
                        order_owners_table.c.order_id == orders_table.c.id,
                    ).join(users_table, users_table.c.id == order_owners_table.c.user_id)
                )
                .where(users_table.c.user_id == user_id)
                .order_by(orders_table.c.placed_at.desc(), orders_table.c.id.desc())
                .limit(safe_limit)
            ).all()
        orders = [
            self.get_order(order_id, user_id=user_id)
            for order_id in order_ids
        ]
        return [order for order in orders if order is not None]

    def create_return_request(
        self,
        order_id: str,
        user_id: str,
        *,
        reason: str,
        precheck_decision: str = "manual_review",
        risk_score: int = 0,
        risk_level: str = "low",
        decision_reasons: list[str] | None = None,
        matched_rules: list[str] | None = None,
        missing_evidence: list[str] | None = None,
        rule_version: str = "legacy",
    ) -> tuple[ReturnRequestRecord, bool] | None:
        """Create one return work order per user/order, safely retryable."""
        self.initialize()
        idempotency_key = f"return:{user_id}:{order_id}"
        try:
            with self.engine.begin() as connection:
                owned = connection.execute(
                    select(
                        orders_table.c.id.label("order_key"),
                        users_table.c.id.label("user_key"),
                    )
                    .select_from(
                        orders_table.join(
                            order_owners_table,
                            order_owners_table.c.order_id == orders_table.c.id,
                        ).join(
                            users_table,
                            users_table.c.id == order_owners_table.c.user_id,
                        )
                    )
                    .where(
                        and_(
                            orders_table.c.order_id == order_id,
                            users_table.c.user_id == user_id,
                        )
                    )
                ).mappings().first()
                if owned is None:
                    return None
                existing = connection.execute(
                    _return_request_statement().where(
                        return_requests_table.c.idempotency_key == idempotency_key
                    )
                ).mappings().first()
                if existing is not None:
                    return _row_to_return_request(existing), False
                request_id = f"RET-{uuid4().hex[:12].upper()}"
                connection.execute(
                    insert(return_requests_table).values(
                        request_id=request_id,
                        order_id=owned["order_key"],
                        user_id=owned["user_key"],
                        status="pending_review",
                        reason=reason,
                        precheck_decision=precheck_decision,
                        risk_score=risk_score,
                        risk_level=risk_level,
                        decision_reasons=decision_reasons or [],
                        matched_rules=matched_rules or [],
                        missing_evidence=missing_evidence or [],
                        rule_version=rule_version,
                        idempotency_key=idempotency_key,
                    )
                )
                return_request_key = connection.scalar(
                    select(return_requests_table.c.id).where(
                        return_requests_table.c.request_id == request_id
                    )
                )
                connection.execute(
                    insert(return_request_events_table).values(
                        return_request_id=return_request_key,
                        from_status=None,
                        to_status="pending_review",
                        actor_type="user",
                        actor_id=user_id,
                        reason="用户确认提交退货申请",
                    )
                )
                created = connection.execute(
                    _return_request_statement().where(
                        return_requests_table.c.request_id == request_id
                    )
                ).mappings().one()
                return _row_to_return_request(created), True
        except IntegrityError:
            # A concurrent retry may win the unique idempotency key race.
            existing = self.get_return_request(order_id, user_id=user_id)
            if existing is None:
                raise
            return existing, False

    def get_return_request(
        self,
        order_id: str,
        *,
        user_id: str,
    ) -> ReturnRequestRecord | None:
        self.initialize()
        with self.engine.connect() as connection:
            row = connection.execute(
                _return_request_statement().where(
                    and_(
                        orders_table.c.order_id == order_id,
                        users_table.c.user_id == user_id,
                    )
                )
            ).mappings().first()
        return _row_to_return_request(row) if row is not None else None

    def get_return_request_by_id(
        self,
        request_id: str,
        *,
        user_id: str,
    ) -> ReturnRequestRecord | None:
        self.initialize()
        with self.engine.connect() as connection:
            row = connection.execute(
                _return_request_statement().where(
                    and_(
                        return_requests_table.c.request_id == request_id,
                        users_table.c.user_id == user_id,
                    )
                )
            ).mappings().first()
        return _row_to_return_request(row) if row is not None else None

    def cancel_return_request(
        self,
        request_id: str,
        *,
        user_id: str,
    ) -> tuple[ReturnRequestRecord, bool] | None:
        """Apply the only user-controlled transition: pending_review -> cancelled."""
        self.initialize()
        with self.engine.begin() as connection:
            row = connection.execute(
                _return_request_statement()
                .where(
                    and_(
                        return_requests_table.c.request_id == request_id,
                        users_table.c.user_id == user_id,
                    )
                )
                .with_for_update()
            ).mappings().first()
            if row is None:
                return None
            current = _row_to_return_request(row)
            if current.status != "pending_review":
                return current, False
            connection.execute(
                update(return_requests_table)
                .where(return_requests_table.c.id == row["id"])
                .values(status="cancelled", updated_at=func.current_timestamp())
            )
            connection.execute(
                insert(return_request_events_table).values(
                    return_request_id=row["id"],
                    from_status="pending_review",
                    to_status="cancelled",
                    actor_type="user",
                    actor_id=user_id,
                    reason="用户确认取消退货申请",
                )
            )
            updated = connection.execute(
                _return_request_statement().where(
                    return_requests_table.c.id == row["id"]
                )
            ).mappings().one()
            return _row_to_return_request(updated), True

    def count_return_request_events(self, request_id: str) -> int:
        """Expose an audit count for diagnostics and automated verification."""
        self.initialize()
        with self.engine.connect() as connection:
            return int(
                connection.scalar(
                    select(func.count())
                    .select_from(
                        return_request_events_table.join(
                            return_requests_table,
                            return_requests_table.c.id
                            == return_request_events_table.c.return_request_id,
                        )
                    )
                    .where(return_requests_table.c.request_id == request_id)
                )
                or 0
            )

    def get_order_shipments(
        self,
        order_id: str,
        *,
        user_id: str | None = None,
    ) -> list[ShipmentRecord]:
        """Return an order's shipment timeline after optional ownership filtering."""
        self.initialize()
        statement = (
            select(
                shipments_table,
                orders_table.c.order_id.label("external_order_id"),
            )
            .join(orders_table, orders_table.c.id == shipments_table.c.order_id)
            .where(orders_table.c.order_id == order_id)
            .order_by(shipments_table.c.id)
        )
        if user_id is not None:
            statement = (
                statement.join(
                    order_owners_table,
                    order_owners_table.c.order_id == orders_table.c.id,
                )
                .join(users_table, users_table.c.id == order_owners_table.c.user_id)
                .where(users_table.c.user_id == user_id)
            )
        with self.engine.connect() as connection:
            shipment_rows = connection.execute(statement).mappings().all()
            return [
                _row_to_shipment(
                    row,
                    _load_tracking_events(connection, row["id"]),
                )
                for row in shipment_rows
            ]

    def list_user_shipments(
        self,
        user_id: str,
        *,
        limit: int = 20,
    ) -> list[ShipmentRecord]:
        """List recent shipments through the owning order relationship."""
        self.initialize()
        safe_limit = max(1, min(limit, 50))
        statement = (
            select(
                shipments_table,
                orders_table.c.order_id.label("external_order_id"),
            )
            .join(orders_table, orders_table.c.id == shipments_table.c.order_id)
            .join(
                order_owners_table,
                order_owners_table.c.order_id == orders_table.c.id,
            )
            .join(users_table, users_table.c.id == order_owners_table.c.user_id)
            .where(users_table.c.user_id == user_id)
            .order_by(orders_table.c.placed_at.desc(), shipments_table.c.id.desc())
            .limit(safe_limit)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
            return [
                _row_to_shipment(
                    row,
                    _load_tracking_events(connection, row["id"]),
                )
                for row in rows
            ]

    def get_shipment(
        self,
        tracking_number: str,
        *,
        user_id: str | None = None,
    ) -> ShipmentRecord | None:
        """Look up a shipment without bypassing the owning order's user boundary."""
        self.initialize()
        statement = (
            select(
                shipments_table,
                orders_table.c.order_id.label("external_order_id"),
            )
            .join(orders_table, orders_table.c.id == shipments_table.c.order_id)
            .where(shipments_table.c.tracking_number == tracking_number)
        )
        if user_id is not None:
            statement = (
                statement.join(
                    order_owners_table,
                    order_owners_table.c.order_id == orders_table.c.id,
                )
                .join(users_table, users_table.c.id == order_owners_table.c.user_id)
                .where(users_table.c.user_id == user_id)
            )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            events = _load_tracking_events(connection, row["id"])
        return _row_to_shipment(row, events)

    def ingest_tracking_event(
        self,
        provider: str,
        event: TrackingWebhookPayload,
    ) -> str:
        """Persist one carrier event exactly once and prevent status regression."""
        self.initialize()
        normalized_provider = provider.strip().upper()
        occurred_at = _utc_timestamp(event.occurred_at)
        estimated_delivery_at = (
            _utc_timestamp(event.estimated_delivery_at)
            if event.estimated_delivery_at is not None
            else None
        )
        payload = event.model_dump(mode="json")
        try:
            with self.engine.begin() as connection:
                duplicate = connection.scalar(
                    select(logistics_webhook_events_table.c.id).where(
                        and_(
                            logistics_webhook_events_table.c.provider
                            == normalized_provider,
                            logistics_webhook_events_table.c.provider_event_id
                            == event.provider_event_id,
                        )
                    )
                )
                if duplicate is not None:
                    return "duplicate"
                inbox_result = connection.execute(
                    insert(logistics_webhook_events_table).values(
                        provider=normalized_provider,
                        provider_event_id=event.provider_event_id,
                        payload_json=payload,
                        processing_status="pending",
                    )
                )
                inbox_key = int(inbox_result.inserted_primary_key[0])
                shipment = connection.execute(
                    select(shipments_table.c.id)
                    .where(
                        and_(
                            shipments_table.c.tracking_number
                            == event.tracking_number.upper(),
                            func.upper(shipments_table.c.carrier_code)
                            == normalized_provider,
                        )
                    )
                ).mappings().first()
                if shipment is None:
                    connection.execute(
                        update(logistics_webhook_events_table)
                        .where(logistics_webhook_events_table.c.id == inbox_key)
                        .values(processing_status="shipment_not_found")
                    )
                    return "shipment_not_found"

                shipment_key = int(shipment["id"])
                connection.execute(
                    insert(shipment_events_table).values(
                        shipment_id=shipment_key,
                        provider_event_id=event.provider_event_id,
                        status=event.status,
                        location=event.location,
                        description=event.description,
                        occurred_at=occurred_at,
                    )
                )
                latest_occurred_at = connection.scalar(
                    select(shipment_events_table.c.occurred_at)
                    .where(shipment_events_table.c.shipment_id == shipment_key)
                    .order_by(shipment_events_table.c.occurred_at.desc())
                    .limit(1)
                )
                is_latest = (
                    _database_datetime(latest_occurred_at) == occurred_at
                )
                result = "processed" if is_latest else "processed_out_of_order"
                if is_latest:
                    values: dict[str, object] = {
                        "status": event.status,
                        "updated_at": func.current_timestamp(),
                    }
                    if estimated_delivery_at is not None:
                        values["estimated_delivery_at"] = estimated_delivery_at
                    if event.status == "delivered":
                        values["delivered_at"] = occurred_at
                    connection.execute(
                        update(shipments_table)
                        .where(shipments_table.c.id == shipment_key)
                        .values(**values)
                    )
                connection.execute(
                    update(logistics_webhook_events_table)
                    .where(logistics_webhook_events_table.c.id == inbox_key)
                    .values(processing_status=result)
                )
                return result
        except IntegrityError:
            # Database uniqueness is the final guard when duplicate webhooks race.
            return "duplicate"

    def get_policy(self, topic: str) -> PolicyRecord | None:
        self.initialize()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(service_policies_table)
                .where(service_policies_table.c.topic == topic)
                .order_by(service_policies_table.c.effective_from.desc())
                .limit(1)
            ).mappings().first()
        if row is None:
            return None
        return PolicyRecord(
            topic=row["topic"],
            title=row["title"],
            body=row["body"],
            effective_from=row["effective_from"],
        )

    def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        user_id: str | None = None,
    ) -> UserRecord:
        self.initialize()
        active_user_id = user_id or f"user-{uuid4().hex}"
        normalized_username = username.strip()
        with self.engine.begin() as connection:
            connection.execute(
                insert(users_table).values(
                    user_id=active_user_id,
                    username=normalized_username,
                    password_hash=password_hash,
                )
            )
        return UserRecord(
            user_id=active_user_id,
            username=normalized_username,
            password_hash=password_hash,
        )

    def get_user_by_username(self, username: str) -> UserRecord | None:
        self.initialize()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(users_table).where(
                    func.lower(users_table.c.username) == username.strip().casefold()
                )
            ).mappings().first()
        return _row_to_user(row) if row else None

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        self.initialize()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(users_table).where(users_table.c.user_id == user_id)
            ).mappings().first()
        return _row_to_user(row) if row else None

    def get_user_preferences(self, user_id: str) -> UserPreferences:
        self.initialize()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(user_preferences_table)
                .select_from(
                    user_preferences_table.join(
                        users_table,
                        users_table.c.id == user_preferences_table.c.user_id,
                    )
                )
                .where(users_table.c.user_id == user_id)
            ).mappings().first()
        if row is None:
            return UserPreferences()
        return UserPreferences(
            max_budget_cents=row["max_budget_cents"],
            preferred_categories=row["preferred_categories"],
            preferred_brands=row["preferred_brands"],
            scenarios=row["scenarios"],
            priorities=row["priorities"],
        )

    def save_user_preferences(
        self,
        user_id: str,
        preferences: UserPreferences,
    ) -> UserPreferences:
        self.initialize()
        values = preferences.model_dump()
        values["updated_at"] = func.current_timestamp()
        with self.engine.begin() as connection:
            user_key = connection.scalar(
                select(users_table.c.id).where(users_table.c.user_id == user_id)
            )
            if user_key is None:
                raise ValueError("user does not exist")
            existing = connection.scalar(
                select(user_preferences_table.c.user_id).where(
                    user_preferences_table.c.user_id == user_key
                )
            )
            if existing is None:
                connection.execute(
                    insert(user_preferences_table).values(
                        user_id=user_key,
                        **values,
                    )
                )
            else:
                connection.execute(
                    update(user_preferences_table)
                    .where(user_preferences_table.c.user_id == user_key)
                    .values(**values)
                )
        return preferences

    def assign_order_to_user(self, order_id: str, user_id: str) -> bool:
        self.initialize()
        with self.engine.begin() as connection:
            order_key = connection.scalar(
                select(orders_table.c.id).where(orders_table.c.order_id == order_id)
            )
            user_key = connection.scalar(
                select(users_table.c.id).where(users_table.c.user_id == user_id)
            )
            if order_key is None or user_key is None:
                return False
            existing = connection.scalar(
                select(order_owners_table.c.order_id).where(
                    and_(
                        order_owners_table.c.order_id == order_key,
                        order_owners_table.c.user_id == user_key,
                    )
                )
            )
            if existing is not None:
                return False
            connection.execute(
                insert(order_owners_table).values(order_id=order_key, user_id=user_key)
            )
            return True

    def ensure_conversation_owner(self, conversation_id: str, user_id: str) -> bool:
        self.initialize()
        with self.engine.begin() as connection:
            owner = connection.execute(
                select(users_table.c.user_id)
                .select_from(
                    conversation_owners_table.join(
                        users_table,
                        users_table.c.id == conversation_owners_table.c.user_id,
                    )
                )
                .where(conversation_owners_table.c.conversation_id == conversation_id)
            ).scalar_one_or_none()
            if owner is not None:
                return owner == user_id
            user_key = connection.scalar(
                select(users_table.c.id).where(users_table.c.user_id == user_id)
            )
            if user_key is None:
                return False
            connection.execute(
                insert(conversation_owners_table).values(
                    conversation_id=conversation_id,
                    user_id=user_key,
                )
            )
            return True

    def add_conversation_turn(
        self,
        conversation_id: str,
        user_message: str,
        assistant_answer: str,
        evidence: Iterable[Evidence] = (),
    ) -> None:
        self.initialize()
        with self.engine.begin() as connection:
            result = connection.execute(
                insert(conversation_turns_table).values(
                    conversation_id=conversation_id,
                    user_message=user_message,
                    assistant_answer=assistant_answer,
                )
            )
            turn_id = int(result.inserted_primary_key[0])
            evidence_rows = [
                {
                    "turn_id": turn_id,
                    "position": position,
                    "source": item.source,
                    "reference": item.reference,
                    "attributes_json": item.model_dump(mode="json")["attributes"],
                }
                for position, item in enumerate(evidence)
            ]
            if evidence_rows:
                connection.execute(insert(conversation_evidence_table), evidence_rows)

    def delete_conversation(self, conversation_id: str, *, user_id: str) -> bool:
        """Delete one owned conversation and its turns, used by benchmark cleanup."""
        self.initialize()
        with self.engine.begin() as connection:
            owner = connection.execute(
                select(users_table.c.user_id)
                .select_from(
                    conversation_owners_table.join(
                        users_table,
                        users_table.c.id == conversation_owners_table.c.user_id,
                    )
                )
                .where(conversation_owners_table.c.conversation_id == conversation_id)
            ).scalar_one_or_none()
            if owner != user_id:
                return False
            turn_ids = select(conversation_turns_table.c.id).where(
                conversation_turns_table.c.conversation_id == conversation_id
            )
            connection.execute(
                delete(conversation_evidence_table).where(
                    conversation_evidence_table.c.turn_id.in_(turn_ids)
                )
            )
            connection.execute(
                delete(conversation_turns_table).where(
                    conversation_turns_table.c.conversation_id == conversation_id
                )
            )
            connection.execute(
                delete(conversation_memories_table).where(
                    conversation_memories_table.c.conversation_id == conversation_id
                )
            )
            connection.execute(
                delete(conversation_owners_table).where(
                    conversation_owners_table.c.conversation_id == conversation_id
                )
            )
            return True

    def get_recent_conversation_turns(
        self,
        conversation_id: str,
        *,
        limit: int = 6,
    ) -> list[ConversationTurn]:
        self.initialize()
        safe_limit = max(1, min(limit, 20))
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(conversation_turns_table)
                .where(conversation_turns_table.c.conversation_id == conversation_id)
                .order_by(conversation_turns_table.c.id.desc())
                .limit(safe_limit)
            ).mappings().all()
            turns: list[ConversationTurn] = []
            for row in reversed(rows):
                evidence_rows = connection.execute(
                    select(conversation_evidence_table)
                    .where(conversation_evidence_table.c.turn_id == row["id"])
                    .order_by(conversation_evidence_table.c.position)
                ).mappings().all()
                turns.append(
                    ConversationTurn(
                        user_message=row["user_message"],
                        assistant_answer=row["assistant_answer"],
                        evidence=[
                            Evidence(
                                source=item["source"],
                                reference=item["reference"],
                                attributes=item["attributes_json"],
                            )
                            for item in evidence_rows
                        ],
                    )
                )
        return turns

    def get_conversation_memory(
        self,
        conversation_id: str,
    ) -> ConversationMemory | None:
        self.initialize()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(conversation_memories_table).where(
                    conversation_memories_table.c.conversation_id == conversation_id
                )
            ).mappings().one_or_none()
        return _conversation_memory_from_row(row) if row is not None else None

    def get_conversation_context(
        self,
        conversation_id: str,
        *,
        recent_limit: int = 4,
    ) -> tuple[list[ConversationTurn], ConversationMemory | None, int]:
        recent = self.get_recent_conversation_turns(
            conversation_id,
            limit=recent_limit,
        )
        memory = self.get_conversation_memory(conversation_id)
        history = [memory.as_context_turn(), *recent] if memory is not None else recent
        return history, memory, len(recent)

    def compact_conversation_memory(
        self,
        conversation_id: str,
        *,
        recent_limit: int = 4,
    ) -> ConversationMemory | None:
        """Compact turns older than the raw recent window into structured memory."""

        self.initialize()
        safe_limit = max(1, min(recent_limit, 20))
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(conversation_turns_table)
                .where(conversation_turns_table.c.conversation_id == conversation_id)
                .order_by(conversation_turns_table.c.id)
            ).mappings().all()
            memory_row = connection.execute(
                select(conversation_memories_table).where(
                    conversation_memories_table.c.conversation_id == conversation_id
                )
            ).mappings().one_or_none()
            previous = (
                _conversation_memory_from_row(memory_row)
                if memory_row is not None
                else None
            )
            if len(rows) <= safe_limit:
                return previous
            compactable = rows[:-safe_limit]
            compacted_through = (
                previous.compacted_through_turn_id if previous is not None else 0
            )
            new_rows = [row for row in compactable if row["id"] > compacted_through]
            if not new_rows:
                return previous

            turns: list[tuple[int, ConversationTurn]] = []
            for row in new_rows:
                evidence_rows = connection.execute(
                    select(conversation_evidence_table)
                    .where(conversation_evidence_table.c.turn_id == row["id"])
                    .order_by(conversation_evidence_table.c.position)
                ).mappings().all()
                turns.append(
                    (
                        int(row["id"]),
                        ConversationTurn(
                            user_message=row["user_message"],
                            assistant_answer=row["assistant_answer"],
                            evidence=[
                                Evidence(
                                    source=item["source"],
                                    reference=item["reference"],
                                    attributes=item["attributes_json"],
                                )
                                for item in evidence_rows
                            ],
                        ),
                    )
                )
            memory = merge_conversation_memory(previous, turns)
            values = {
                "summary_text": memory.summary,
                "entities_json": memory.entities,
                "constraints_json": memory.constraints,
                "evidence_json": [
                    item.model_dump(mode="json") for item in memory.evidence
                ],
                "compacted_through_turn_id": memory.compacted_through_turn_id,
                "source_turn_count": memory.source_turn_count,
                "updated_at": datetime.now(timezone.utc),
            }
            result = connection.execute(
                update(conversation_memories_table)
                .where(conversation_memories_table.c.conversation_id == conversation_id)
                .values(**values)
            )
            if result.rowcount == 0:
                connection.execute(
                    insert(conversation_memories_table).values(
                        conversation_id=conversation_id,
                        **values,
                    )
                )
            return memory


def _conversation_memory_from_row(row: RowMapping) -> ConversationMemory:
    return ConversationMemory(
        summary=row["summary_text"],
        entities=row["entities_json"] or {},
        constraints=row["constraints_json"] or {},
        evidence=[Evidence.model_validate(item) for item in row["evidence_json"] or []],
        compacted_through_turn_id=row["compacted_through_turn_id"],
        source_turn_count=row["source_turn_count"],
    )


def _build_fts_query(query: str) -> str:
    lowered = query.casefold()
    terms = list(dict.fromkeys(term for term in KNOWN_TERMS if term in lowered))
    terms.extend(re.findall(r"[a-z][a-z0-9_-]{1,}", lowered))
    return " AND ".join(f'"{term}"' for term in dict.fromkeys(terms))


def _search_terms(query: str) -> list[str]:
    lowered = query.casefold()
    terms = [term for term in KNOWN_TERMS if term in lowered]
    terms.extend(re.findall(r"[a-z][a-z0-9_-]{1,}", lowered))
    return list(dict.fromkeys(terms))


def _resolve_database_target(target: Path | str) -> tuple[str, Path | None]:
    if isinstance(target, Path) or "://" not in str(target):
        path = Path(target).resolve()
        return f"sqlite:///{path.as_posix()}", path
    database_url = str(target)
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url, None


def _row_to_hit(row: RowMapping) -> ProductSearchHit:
    return ProductSearchHit(
        product=_row_to_product(row),
        lexical_score=row["lexical_score"],
    )


def _row_to_vector_hit(row: RowMapping) -> ProductVectorSearchHit:
    return ProductVectorSearchHit(
        product=_row_to_product(row),
        vector_score=row["vector_score"],
    )


def _row_to_product(row: RowMapping) -> Product:
    return Product(
        product_id=row["product_id"],
        title=row["title"],
        category=row["category"],
        brand=row["brand"],
        description=row["description"],
        price_cents=row["price_cents"],
        rating=row["rating"],
        review_count=row["review_count"],
        stock=row["stock"],
        tags=row["tags"].split(),
        returnable=row["returnable"],
        return_window_days=row["return_window_days"],
    )


def _load_tracking_events(
    connection: Connection,
    shipment_key: int,
) -> list[TrackingEvent]:
    rows = connection.execute(
        select(shipment_events_table)
        .where(shipment_events_table.c.shipment_id == shipment_key)
        .order_by(
            shipment_events_table.c.occurred_at,
            shipment_events_table.c.id,
        )
    ).mappings().all()
    return [
        TrackingEvent(
            provider_event_id=row["provider_event_id"],
            status=row["status"],
            location=row["location"],
            description=row["description"],
            occurred_at=_database_datetime(row["occurred_at"]),
        )
        for row in rows
    ]


def _row_to_shipment(
    row: RowMapping,
    events: list[TrackingEvent],
) -> ShipmentRecord:
    return ShipmentRecord(
        shipment_id=row["shipment_id"],
        order_id=row["external_order_id"],
        tracking_number=row["tracking_number"],
        carrier_code=row["carrier_code"],
        status=row["status"],
        estimated_delivery_at=_database_datetime(row["estimated_delivery_at"]),
        shipped_at=_database_datetime(row["shipped_at"]),
        delivered_at=_database_datetime(row["delivered_at"]),
        events=events,
    )


def _return_request_statement():
    return (
        select(
            return_requests_table,
            orders_table.c.order_id.label("external_order_id"),
            users_table.c.user_id.label("external_user_id"),
        )
        .join(orders_table, orders_table.c.id == return_requests_table.c.order_id)
        .join(users_table, users_table.c.id == return_requests_table.c.user_id)
    )


def _row_to_return_request(row: RowMapping) -> ReturnRequestRecord:
    return ReturnRequestRecord(
        request_id=row["request_id"],
        order_id=row["external_order_id"],
        user_id=row["external_user_id"],
        status=row["status"],
        reason=row["reason"],
        precheck_decision=row["precheck_decision"],
        risk_score=row["risk_score"],
        risk_level=row["risk_level"],
        decision_reasons=row["decision_reasons"] or [],
        matched_rules=row["matched_rules"] or [],
        missing_evidence=row["missing_evidence"] or [],
        rule_version=row["rule_version"],
        created_at=_database_datetime(row["created_at"]),
        updated_at=_database_datetime(row["updated_at"]),
    )


def _row_to_user(row: RowMapping) -> UserRecord:
    return UserRecord(
        user_id=row["user_id"],
        username=row["username"],
        password_hash=row["password_hash"],
    )


def _vector_literal(values: Sequence[float]) -> str:
    if len(values) != PRODUCT_EMBEDDING_DIMENSIONS:
        raise ValueError(
            "product embedding must contain "
            f"{PRODUCT_EMBEDDING_DIMENSIONS} dimensions"
        )
    return "[" + ",".join(format(float(value), ".12g") for value in values) + "]"


def _utc_timestamp(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _seed_datetime(value: str) -> datetime:
    local_value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone(timedelta(hours=8))
    )
    return local_value.astimezone(timezone.utc)


def _database_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@lru_cache
def get_catalog() -> CatalogRepository:
    settings = get_settings()
    repository = CatalogRepository(settings.database_url or settings.database_path)
    repository.seed_products()
    repository.seed_support_data()
    repository.seed_demo_identity()
    return repository
