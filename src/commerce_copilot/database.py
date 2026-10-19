import re
import sqlite3
import threading
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from .catalog import Product, ProductSearchHit
from .config import get_settings
from .support import OrderItem, OrderRecord, PolicyRecord


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    product_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    brand TEXT NOT NULL,
    description TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    rating REAL NOT NULL CHECK (rating BETWEEN 0 AND 5),
    review_count INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
    stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    tags TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS product_fts USING fts5(
    title,
    category,
    brand,
    description,
    tags,
    content='products',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS products_after_insert AFTER INSERT ON products BEGIN
    INSERT INTO product_fts(rowid, title, category, brand, description, tags)
    VALUES (new.id, new.title, new.category, new.brand, new.description, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS products_after_delete AFTER DELETE ON products BEGIN
    INSERT INTO product_fts(product_fts, rowid, title, category, brand, description, tags)
    VALUES ('delete', old.id, old.title, old.category, old.brand, old.description, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS products_after_update AFTER UPDATE ON products BEGIN
    INSERT INTO product_fts(product_fts, rowid, title, category, brand, description, tags)
    VALUES ('delete', old.id, old.title, old.category, old.brand, old.description, old.tags);
    INSERT INTO product_fts(rowid, title, category, brand, description, tags)
    VALUES (new.id, new.title, new.category, new.brand, new.description, new.tags);
END;

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    placed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    PRIMARY KEY (order_id, product_id)
);

CREATE TABLE IF NOT EXISTS service_policies (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    effective_from TEXT NOT NULL
);
"""


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


class CatalogRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._ready = False
        self._ready_lock = threading.Lock()

    def initialize(self) -> None:
        if self._ready:
            return
        with self._ready_lock:
            if self._ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(SCHEMA_SQL)
            self._ready = True

    def seed_products(self, products: Iterable[Product] = DEMO_PRODUCTS) -> None:
        self.initialize()
        rows = [
            (
                product.product_id,
                product.title,
                product.category,
                product.brand,
                product.description,
                product.price_cents,
                product.rating,
                product.review_count,
                product.stock,
                " ".join(product.tags),
            )
            for product in products
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO products (
                    product_id, title, category, brand, description,
                    price_cents, rating, review_count, stock, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO NOTHING
                """,
                rows,
            )

    def seed_support_data(self) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO orders (order_id, status, placed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(order_id) DO NOTHING
                """,
                (
                    ("ORD-2026-1001", "shipped", "2026-10-15 14:30:00"),
                    ("ORD-2026-1002", "delivered", "2026-10-10 09:20:00"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents)
                SELECT o.id, p.id, ?, ?
                FROM orders AS o, products AS p
                WHERE o.order_id = ? AND p.product_id = ?
                ON CONFLICT(order_id, product_id) DO NOTHING
                """,
                (
                    (1, 32900, "ORD-2026-1001", "CP-AUD-101"),
                    (1, 39900, "ORD-2026-1002", "CP-KEY-201"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO service_policies (topic, title, body, effective_from)
                SELECT ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM service_policies WHERE topic = ?
                )
                """,
                (
                    (
                        "returns",
                        "七日退货规则",
                        "签收后7日内，商品保持完好且配件齐全时可以申请退货；定制和已激活商品除外。",
                        "2026-01-01",
                        "returns",
                    ),
                    (
                        "shipping",
                        "发货与物流规则",
                        "现货订单通常在付款后48小时内发出，具体状态以订单查询结果为准。",
                        "2026-01-01",
                        "shipping",
                    ),
                ),
            )

    def search_products(
        self,
        query: str,
        *,
        max_price_cents: int | None = None,
        in_stock_only: bool = True,
        limit: int = 5,
    ) -> list[ProductSearchHit]:
        self.initialize()
        fts_query = _build_fts_query(query)
        if not fts_query:
            return []

        conditions = ["product_fts MATCH ?"]
        parameters: list[object] = [fts_query]
        if max_price_cents is not None:
            conditions.append("p.price_cents <= ?")
            parameters.append(max_price_cents)
        if in_stock_only:
            conditions.append("p.stock > 0")
        parameters.append(limit)

        statement = f"""
            SELECT
                p.product_id, p.title, p.category, p.brand, p.description,
                p.price_cents, p.rating, p.review_count, p.stock, p.tags,
                bm25(product_fts, 4.0, 2.0, 1.5, 1.0, 3.0) AS lexical_score
            FROM product_fts
            JOIN products AS p ON p.id = product_fts.rowid
            WHERE {' AND '.join(conditions)}
            ORDER BY lexical_score ASC, p.rating DESC, p.review_count DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return [_row_to_hit(row) for row in rows]

    def count_products(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM products").fetchone()
        return int(row["total"])

    def get_order(self, order_id: str) -> OrderRecord | None:
        self.initialize()
        with self._connect() as connection:
            order = connection.execute(
                "SELECT id, order_id, status, placed_at FROM orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if order is None:
                return None
            item_rows = connection.execute(
                """
                SELECT p.product_id, p.title, oi.quantity, oi.unit_price_cents
                FROM order_items AS oi
                JOIN products AS p ON p.id = oi.product_id
                WHERE oi.order_id = ?
                ORDER BY p.product_id
                """,
                (order["id"],),
            ).fetchall()
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
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT topic, title, body, effective_from
                FROM service_policies
                WHERE topic = ?
                ORDER BY effective_from DESC
                LIMIT 1
                """,
                (topic,),
            ).fetchone()
        if row is None:
            return None
        return PolicyRecord(
            topic=row["topic"],
            title=row["title"],
            body=row["body"],
            effective_from=row["effective_from"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _build_fts_query(query: str) -> str:
    lowered = query.casefold()
    terms = list(dict.fromkeys(term for term in KNOWN_TERMS if term in lowered))
    terms.extend(re.findall(r"[a-z][a-z0-9_-]{1,}", lowered))
    return " AND ".join(f'"{term}"' for term in dict.fromkeys(terms))


def _row_to_hit(row: sqlite3.Row) -> ProductSearchHit:
    return ProductSearchHit(
        product=Product(
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
        ),
        lexical_score=row["lexical_score"],
    )


@lru_cache
def get_catalog() -> CatalogRepository:
    repository = CatalogRepository(get_settings().database_path)
    repository.seed_products()
    repository.seed_support_data()
    return repository
