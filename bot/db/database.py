"""Подключение к SQLite, миграции схемы и работа с транзакциями."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from bot.config import DB_PATH, MAINTENANCE_FEATURES, PROMO_CODES
from bot.utils.formatters import utcnow

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Храним время в UNIX-секундах (UTC) — это упрощает сравнения и миграции.
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id        INTEGER PRIMARY KEY,
        username       TEXT,
        first_name     TEXT,
        last_name      TEXT,
        balance        INTEGER NOT NULL DEFAULT 0,
        jarvis_coins   INTEGER NOT NULL DEFAULT 0,
        jarvis_unlocked INTEGER NOT NULL DEFAULT 0,
        multiplier     INTEGER NOT NULL DEFAULT 1,
        cooldown_reduction INTEGER NOT NULL DEFAULT 0,
        instant_cooldown INTEGER NOT NULL DEFAULT 0,
        theme TEXT NOT NULL DEFAULT 'default',
        status TEXT,
        just_id TEXT,
        slowdown INTEGER NOT NULL DEFAULT 0,
        slowdown_at INTEGER,
        total_earned   INTEGER NOT NULL DEFAULT 0,
        claims_count   INTEGER NOT NULL DEFAULT 0,
        packages_count INTEGER NOT NULL DEFAULT 0,
        last_claim_at  INTEGER,
        created_at     INTEGER NOT NULL
    )
    """,
    # Индекс для быстрого глобального топа: баланс по убыванию,
    # при равенстве раньше созданный игрок выше.
    "CREATE INDEX IF NOT EXISTS idx_users_top ON users (balance DESC, created_at ASC)",
    """
    CREATE TABLE IF NOT EXISTS packages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        amount      INTEGER NOT NULL,
        deliver_at  INTEGER NOT NULL,
        delivered   INTEGER NOT NULL DEFAULT 0,
        created_at  INTEGER NOT NULL,
        notified    INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    )
    """,
    # Индекс по notified создаётся после миграций: см. MIGRATION_INDEXES.
    "CREATE INDEX IF NOT EXISTS idx_packages_user ON packages (user_id, delivered)",
    """
    CREATE TABLE IF NOT EXISTS features (
        name    TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        reason  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS promo_redemptions (
        user_id     INTEGER NOT NULL,
        code        TEXT NOT NULL,
        redeemed_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, code)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_promo_user ON promo_redemptions (user_id)",
    # Каталог промокодов: админ создаёт коды прямо в боте, а не в коде.
    # max_uses = 0 означает «без ограничений по числу активаций».
    """
    CREATE TABLE IF NOT EXISTS promo_codes (
        code           TEXT PRIMARY KEY,
        jarvis_coins   INTEGER NOT NULL DEFAULT 0,
        unlock_secret  INTEGER NOT NULL DEFAULT 0,
        max_uses       INTEGER NOT NULL DEFAULT 0,
        uses           INTEGER NOT NULL DEFAULT 0,
        active         INTEGER NOT NULL DEFAULT 1,
        created_at     INTEGER NOT NULL
    )
    """,
    # Покупленные темы карточки. Активна одна (users.theme), остальные
    # остаются купленными и переключаются бесплатно.
    """
    CREATE TABLE IF NOT EXISTS user_themes (
        user_id     INTEGER NOT NULL,
        theme       TEXT NOT NULL,
        bought_at   INTEGER NOT NULL,
        PRIMARY KEY (user_id, theme)
    )
    """,
    # Чаты, где бот встречался: нужны для /announce.
    """
    CREATE TABLE IF NOT EXISTS chats (
        chat_id       INTEGER PRIMARY KEY,
        title         TEXT,
        chat_type     TEXT,
        username      TEXT,
        first_seen    INTEGER NOT NULL,
        last_seen     INTEGER NOT NULL,
        last_announce INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chats_announce ON chats (last_announce)",
    # Баны игроков. История не удаляется — снятые баны помечаются
    # lifted_at, истёкшие считаются неактивными по полю until.
    """
    CREATE TABLE IF NOT EXISTS bans (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        reason      TEXT,
        banned_by   INTEGER,
        banned_at   INTEGER NOT NULL,
        until       INTEGER,
        lifted_at   INTEGER
    )
    """,
    # Частичный индекс: быстрый поиск действующего бана игрока.
    "CREATE INDEX IF NOT EXISTS idx_bans_active ON bans (user_id, lifted_at, until)",
    # Товары, созданные админом прямо в боте.
    # kind: multiplier | cooldown — тип апгрейда в Jarvis Shop.
    """
    CREATE TABLE IF NOT EXISTS custom_upgrades (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code        TEXT NOT NULL UNIQUE,
        title       TEXT NOT NULL,
        description TEXT,
        kind        TEXT NOT NULL,
        value       INTEGER NOT NULL,
        cost        INTEGER NOT NULL,
        active      INTEGER NOT NULL DEFAULT 1,
        created_at  INTEGER NOT NULL
    )
    """,
    # Темы карточки: палитра строится из base_color по правилам градиента.
    """
    CREATE TABLE IF NOT EXISTS custom_themes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code        TEXT NOT NULL UNIQUE,
        title       TEXT NOT NULL,
        cost        INTEGER NOT NULL,
        base_color  TEXT NOT NULL,
        active      INTEGER NOT NULL DEFAULT 1,
        created_at  INTEGER NOT NULL
    )
    """,
    # Общие ссылки бота: сайт, каналы, проекты. Настраиваются в панели,
    # доступны всем игрокам.
    """
    CREATE TABLE IF NOT EXISTS bot_links (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT NOT NULL,
        url        TEXT NOT NULL,
        section    TEXT NOT NULL DEFAULT 'help',
        position   INTEGER NOT NULL DEFAULT 0,
        active     INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL
    )
    """,
    # Ссылки конкретного товара. scope — 'upgrade' или 'theme', code —
    # id товара: встроенного (x2, gold) или созданного админом.
    """
    CREATE TABLE IF NOT EXISTS product_links (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        scope      TEXT NOT NULL,
        code       TEXT NOT NULL,
        title      TEXT NOT NULL,
        url        TEXT NOT NULL,
        position   INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )
    """,
    # Платежи звёздами. charge_id обязателен: без него возврат средств
    # невозможен, а Telegram не даёт его восстановить.
    """
    CREATE TABLE IF NOT EXISTS donations (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        username     TEXT,
        payload      TEXT NOT NULL UNIQUE,
        stars        INTEGER NOT NULL,
        charge_id    TEXT,
        provider_charge_id TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        error        TEXT,
        created_at   INTEGER NOT NULL,
        paid_at      INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
]

# Индексы по колонкам, которых в базе может ещё не быть: на старой базе
# без just_id и notified их создание падало бы с «no such column» ДО того,
# как миграции успеют добавить сами колонки. Поэтому такие индексы
# создаются отдельным шагом ПОСЛЕ _migrate_missing_columns().
MIGRATION_INDEXES: list[str] = [
    # JustID уникален глобально: $John принадлежит ровно одному игроку.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_just_id ON users (just_id)",
    # Выборка посылок, которые пора доставить курьеру.
    "CREATE INDEX IF NOT EXISTS idx_packages_deliver ON packages (delivered, notified, deliver_at)",
    # Ссылки одного товара читаются пачкой по его id.
    "CREATE INDEX IF NOT EXISTS idx_product_links ON product_links (scope, code, position)",
    "CREATE INDEX IF NOT EXISTS idx_bot_links ON bot_links (section, position)",
    "CREATE INDEX IF NOT EXISTS idx_donations_user ON donations (user_id, created_at)",
]

# Колонки, которые могли появиться в более новой версии схемы.
MIGRATIONS: dict[str, dict[str, str]] = {
    "users": {
        "first_name": "TEXT",
        "last_name": "TEXT",
        "jarvis_coins": "INTEGER NOT NULL DEFAULT 0",
        "jarvis_unlocked": "INTEGER NOT NULL DEFAULT 0",
        "multiplier": "INTEGER NOT NULL DEFAULT 1",
        "cooldown_reduction": "INTEGER NOT NULL DEFAULT 0",
        "instant_cooldown": "INTEGER NOT NULL DEFAULT 0",
        "theme": "TEXT NOT NULL DEFAULT 'default'",
        "status": "TEXT",
        "just_id": "TEXT",
        "slowdown": "INTEGER NOT NULL DEFAULT 0",
        "slowdown_at": "INTEGER",
        "total_earned": "INTEGER NOT NULL DEFAULT 0",
        "packages_count": "INTEGER NOT NULL DEFAULT 0",
    },
    "packages": {
        "notified": "INTEGER NOT NULL DEFAULT 0",
    },
    # Описание есть и у апгрейдов с самого начала, у тем появилось
    # в обновлении 1.1.2 вместе с ссылками.
    "custom_themes": {
        "description": "TEXT",
    },
}


class Database:
    """Асинхронная обёртка над SQLite с сериализованным доступом."""

    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # --- lifecycle ----------------------------------------------------------
    async def connect(self) -> None:
        if self._conn is not None:
            return
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, timeout=30)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=30000")
        await self._conn.commit()
        logger.info("Подключение к БД: %s", self.path)

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None
        logger.info("Соединение с БД закрыто")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("БД не инициализирована, сначала вызовите connect()")
        return self._conn

    # --- миграции -----------------------------------------------------------
    async def migrate(self) -> None:
        async with self.transaction():
            for statement in SCHEMA:
                await self.conn.execute(statement)
            # Порядок важен: колонки — раньше индексов по ним.
            await self._migrate_missing_columns()
            for statement in MIGRATION_INDEXES:
                await self.conn.execute(statement)
            await self._seed_features()
            await self._seed_promo_codes()
            await self.conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        logger.info("Схема БД актуальна (версия %s)", SCHEMA_VERSION)

    async def _migrate_missing_columns(self) -> None:
        for table, columns in MIGRATIONS.items():
            cursor = await self.conn.execute(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in await cursor.fetchall()}
            await cursor.close()
            for column, ddl in columns.items():
                if column not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                    )
                    logger.info("Миграция: %s.%s добавлен", table, column)

    async def _seed_features(self) -> None:
        for feature in MAINTENANCE_FEATURES:
            await self.conn.execute(
                "INSERT OR IGNORE INTO features (name, enabled, reason) VALUES (?, 1, NULL)",
                (feature,),
            )

    async def _seed_promo_codes(self) -> None:
        """Засеивает промокоды из конфига, не трогая созданные админом."""
        now = int(utcnow().timestamp())
        for code, settings in PROMO_CODES.items():
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO promo_codes
                    (code, jarvis_coins, unlock_secret, max_uses, uses,
                     active, created_at)
                VALUES (?, ?, ?, ?, 0, 1, ?)
                """,
                (
                    code.upper(),
                    int(settings.get("jarvis_coins", 0)),
                    1 if settings.get("unlock_secret") else 0,
                    int(settings.get("max_uses", 0)),
                    now,
                ),
            )

    # --- запросы ------------------------------------------------------------
    async def execute(self, sql: str, params: tuple = ()):
        """Выполняет запрос и коммитит. Возвращает курсор — нужен для rowcount."""
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            await self.conn.commit()
            return cursor

    async def fetch_one(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            row = await cursor.fetchone()
            await cursor.close()
            return row

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()
            return list(rows)

    # --- транзакции ---------------------------------------------------------
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["Transaction"]:
        """Сериализованная транзакция: BEGIN IMMEDIATE ... COMMIT/ROLLBACK.

        Гарантирует отсутствие гонок при двойном нажатии кнопки
        и повторной активации промокода.
        """
        async with self._lock:
            tx = Transaction(self.conn)
            await tx.__aenter__()
            try:
                yield tx
            except Exception:
                await tx.__aexit__(None, None, None)
                raise
            else:
                await tx.__aexit__(None, None, None)


class Transaction:
    """Контекст транзакции с уже захваченным локом."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._finished = False

    async def __aenter__(self) -> "Transaction":
        # IMMEDIATE — сразу берём write-лок, чтобы два конкурентных
        # обработчика не читали одно и то же состояние.
        await self._conn.execute("BEGIN IMMEDIATE")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._finished:
            return
        try:
            if exc_type is None:
                await self._conn.commit()
            else:
                await self._conn.rollback()
        finally:
            self._finished = True

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        return await self._conn.execute(sql, params)

    async def fetch_one(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)


db = Database()
