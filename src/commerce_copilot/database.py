import re
import threading
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
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
from sqlalchemy.engine import Engine, RowMapping

from .catalog import Product, ProductSearchHit
from .config import get_settings
from .contracts import ConversationTurn, Evidence, UserPreferences, UserRecord
from .support import OrderItem, OrderRecord, PolicyRecord
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
    CheckConstraint("price_cents >= 0"),
    CheckConstraint("rating BETWEEN 0 AND 5"),
    CheckConstraint("review_count >= 0"),
    CheckConstraint("stock >= 0"),
)

orders_table = Table(
    "orders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("order_id", String(64), nullable=False, unique=True),
    Column("status", String(64), nullable=False),
    Column("placed_at", String(32), nullable=False),
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

conversation_owners_table = Table(
    "conversation_owners",
    metadata,
    Column("conversation_id", String(64), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
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
                        )
                    )

    def seed_support_data(self) -> None:
        self.initialize()
        order_rows = (
            ("ORD-2026-1001", "shipped", "2026-10-15 14:30:00"),
            ("ORD-2026-1002", "delivered", "2026-10-10 09:20:00"),
        )
        item_rows = (
            ("ORD-2026-1001", "CP-AUD-101", 1, 32900),
            ("ORD-2026-1002", "CP-KEY-201", 1, 39900),
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
                )
                .join(products_table, products_table.c.id == order_items_table.c.product_id)
                .where(order_items_table.c.order_id == order["id"])
                .order_by(products_table.c.product_id)
            ).mappings().all()
        return OrderRecord(
            order_id=order["order_id"],
            status=order["status"],
            placed_at=order["placed_at"],
            items=[
                OrderItem(
                    product_id=row["product_id"],
                    title=row["title"],
                    quantity=row["quantity"],
                    unit_price_cents=row["unit_price_cents"],
                )
                for row in item_rows
            ],
        )

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
                    "attributes_json": item.attributes,
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
    )


def _row_to_user(row: RowMapping) -> UserRecord:
    return UserRecord(
        user_id=row["user_id"],
        username=row["username"],
        password_hash=row["password_hash"],
    )


@lru_cache
def get_catalog() -> CatalogRepository:
    settings = get_settings()
    repository = CatalogRepository(settings.database_url or settings.database_path)
    repository.seed_products()
    repository.seed_support_data()
    repository.seed_demo_identity()
    return repository
