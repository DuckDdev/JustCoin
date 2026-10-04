"""Репозиторий: все SQL-запросы к данным бота."""

from __future__ import annotations

import logging
from typing import Any

from bot.db.database import Database, Transaction
from bot.models import Package, User
from bot.utils.formatters import from_timestamp, to_epoch, utcnow

logger = logging.getLogger(__name__)

USER_COLUMNS = (
    "user_id, username, first_name, last_name, balance, jarvis_coins, "
    "jarvis_unlocked, multiplier, cooldown_reduction, instant_cooldown, "
    "theme, status, just_id, slowdown, slowdown_at, "
    "total_earned, claims_count, packages_count, last_claim_at, created_at"
)


def _row_to_user(row: Any) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        balance=row["balance"],
        jarvis_coins=row["jarvis_coins"],
        jarvis_unlocked=row["jarvis_unlocked"],
        multiplier=row["multiplier"],
        cooldown_reduction=row["cooldown_reduction"],
        instant_cooldown=row["instant_cooldown"],
        theme=row["theme"] or "default",
        status=row["status"],
        just_id=row["just_id"],
        slowdown=row["slowdown"],
        slowdown_at=from_timestamp(row["slowdown_at"]),
        total_earned=row["total_earned"],
        claims_count=row["claims_count"],
        packages_count=row["packages_count"],
        last_claim_at=from_timestamp(row["last_claim_at"]),
        created_at=from_timestamp(row["created_at"]),
    )


def _row_to_package(row: Any) -> Package:
    return Package(
        id=row["id"],
        user_id=row["user_id"],
        amount=row["amount"],
        deliver_at=from_timestamp(row["deliver_at"]),
        delivered=row["delivered"],
        created_at=from_timestamp(row["created_at"]),
        notified=row["notified"] if "notified" in row.keys() else 0,
    )


class UserRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    async def get(self, user_id: int) -> User | None:
        row = await self.db.fetch_one(
            f"SELECT {USER_COLUMNS} FROM users WHERE user_id = ?", (user_id,)
        )
        return _row_to_user(row) if row else None

    async def get_in_tx(self, tx: Transaction, user_id: int) -> User | None:
        row = await tx.fetch_one(
            f"SELECT {USER_COLUMNS} FROM users WHERE user_id = ?", (user_id,)
        )
        return _row_to_user(row) if row else None

    async def ensure(self, user_id: int, username: str | None = None,
                     first_name: str | None = None,
                     last_name: str | None = None) -> User:
        """Создаёт игрока при первом обращении и обновляет имя/username."""
        now = utcnow()
        await self.db.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name),
                last_name  = COALESCE(excluded.last_name, users.last_name)
            """,
            (user_id, username, first_name, last_name, int(now.timestamp())),
        )
        user = await self.get(user_id)
        if user is None:  # pragma: no cover - защита от логических сбоев
            raise RuntimeError(f"Не удалось создать или прочитать игрока {user_id}")
        return user

    async def ensure_in_tx(self, tx: Transaction, user_id: int,
                           username: str | None = None,
                           first_name: str | None = None,
                           last_name: str | None = None) -> User:
        now = utcnow()
        await tx.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name),
                last_name  = COALESCE(excluded.last_name, users.last_name)
            """,
            (user_id, username, first_name, last_name, int(now.timestamp())),
        )
        user = await self.get_in_tx(tx, user_id)
        if user is None:  # pragma: no cover
            raise RuntimeError(f"Не удалось создать или прочитать игрока {user_id}")
        return user

    async def count(self) -> int:
        row = await self.db.fetch_one("SELECT COUNT(*) AS c FROM users")
        return int(row["c"]) if row else 0

    async def top(self, limit: int, offset: int = 0) -> list[User]:
        """Страница топа: limit строк начиная с offset."""
        rows = await self.db.fetch_all(
            f"SELECT {USER_COLUMNS} FROM users "
            "ORDER BY balance DESC, created_at ASC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [_row_to_user(row) for row in rows]

    async def place(self, user: User) -> int:
        """Место игрока в глобальном топе (1-based).

        Выше тот, у кого больше баланс; при равенстве — кто раньше
        зарегистрировался; при полном равенстве — меньший user_id.
        """
        created_at = to_epoch(user.created_at)
        row = await self.db.fetch_one(
            """
            SELECT COUNT(*) AS c FROM users
            WHERE balance > ?
               OR (balance = ? AND created_at < ?)
               OR (balance = ? AND created_at = ? AND user_id < ?)
            """,
            (
                user.balance,
                user.balance,
                created_at,
                user.balance,
                created_at,
                user.user_id,
            ),
        )
        return int(row["c"]) + 1 if row else 1


    # --- мутации, работающие внутри уже открытой транзакции ----------------
    async def apply_claim_in_tx(self, tx: Transaction, user_id: int, amount: int,
                                now_ts: int, cooldown_seconds: int) -> User | None:
        """Начислить коины за /jcoin. Возвращает None, если кулдаун не прошёл.

        Кулдаун передаётся снаружи: он зависит от купленных сокращений
        и мгновенного получения, которые хранятся в самой строке игрока.
        """
        row = await tx.fetch_one(
            "SELECT last_claim_at FROM users WHERE user_id = ?", (user_id,)
        )
        if row is None:
            return None

        last_claim = row["last_claim_at"]
        if last_claim is not None and now_ts - int(last_claim) < cooldown_seconds:
            return None
        await tx.execute(
            """
            UPDATE users
            SET balance = balance + ?,
                total_earned = total_earned + ?,
                claims_count = claims_count + 1,
                last_claim_at = ?
            WHERE user_id = ?
            """,
            (amount, amount, now_ts, user_id),
        )
        return await self.get_in_tx(tx, user_id)

    async def grant_jarvis_in_tx(self, tx: Transaction, user_id: int,
                                 amount: int, unlock: bool) -> None:
        await tx.execute(
            "UPDATE users SET jarvis_coins = jarvis_coins + ?, "
            "jarvis_unlocked = MAX(jarvis_unlocked, ?) WHERE user_id = ?",
            (amount, 1 if unlock else 0, user_id),
        )

    async def spend_jarvis_in_tx(self, tx: Transaction, user_id: int,
                                 cost: int) -> bool:
        """Списать Jarvis-коины. False — не хватило средств."""
        cursor = await tx.execute(
            "UPDATE users SET jarvis_coins = jarvis_coins - ? "
            "WHERE user_id = ? AND jarvis_coins >= ?",
            (cost, user_id, cost),
        )
        return cursor.rowcount > 0

    @staticmethod
    async def set_multiplier_in_tx(tx: Transaction, user_id: int,
                                   multiplier: int) -> None:
        await tx.execute(
            "UPDATE users SET multiplier = ? WHERE user_id = ?", (multiplier, user_id)
        )

    @staticmethod
    async def set_cooldown_reduction_in_tx(tx: Transaction, user_id: int,
                                            reduction: int) -> None:
        await tx.execute(
            "UPDATE users SET cooldown_reduction = ? WHERE user_id = ?",
            (reduction, user_id),
        )

    @staticmethod
    async def set_instant_in_tx(tx: Transaction, user_id: int,
                                enabled: bool) -> None:
        await tx.execute(
            "UPDATE users SET instant_cooldown = ? WHERE user_id = ?",
            (1 if enabled else 0, user_id),
        )

    @staticmethod
    async def spend_balance_in_tx(tx: Transaction, user_id: int, cost: int) -> bool:
        """Списать обычных джаст коинов. False — не хватило средств."""
        cursor = await tx.execute(
            "UPDATE users SET balance = balance - ? "
            "WHERE user_id = ? AND balance >= ?",
            (cost, user_id, cost),
        )
        return cursor.rowcount > 0

    @staticmethod
    async def set_theme_in_tx(tx: Transaction, user_id: int, theme: str) -> None:
        await tx.execute(
            "UPDATE users SET theme = ? WHERE user_id = ?", (theme, user_id)
        )

    @staticmethod
    async def set_status_in_tx(tx: Transaction, user_id: int,
                               status: str | None) -> None:
        await tx.execute(
            "UPDATE users SET status = ? WHERE user_id = ?", (status, user_id)
        )

    @staticmethod
    async def set_slowdown_in_tx(tx: Transaction, user_id: int, slowdown: int,
                                 at_ts: int | None) -> None:
        await tx.execute(
            "UPDATE users SET slowdown = ?, slowdown_at = ? WHERE user_id = ?",
            (slowdown, at_ts, user_id),
        )

    @staticmethod
    async def set_just_id_in_tx(tx: Transaction, user_id: int,
                                just_id: str) -> None:
        await tx.execute(
            "UPDATE users SET just_id = ? WHERE user_id = ?", (just_id, user_id)
        )


class ThemeRepository:
    """Покупленные темы карточки."""

    def __init__(self, database: Database) -> None:
        self.db = database

    async def owned(self, user_id: int) -> set[str]:
        rows = await self.db.fetch_all(
            "SELECT theme FROM user_themes WHERE user_id = ?", (user_id,)
        )
        return {row["theme"] for row in rows}

    @staticmethod
    async def buy_in_tx(tx: Transaction, user_id: int, theme: str) -> bool:
        """Пометить тему купленной. False — уже куплена."""
        cursor = await tx.execute(
            "INSERT OR IGNORE INTO user_themes (user_id, theme, bought_at) "
            "VALUES (?, ?, ?)",
            (user_id, theme, int(utcnow().timestamp())),
        )
        return cursor.rowcount > 0

    async def count_owners(self, theme: str) -> int:
        row = await self.db.fetch_one(
            "SELECT COUNT(*) AS c FROM user_themes WHERE theme = ?", (theme,)
        )
        return int(row["c"]) if row else 0


class CustomShopRepository:
    """Товары, созданные админом: апгрейды и темы, которых нет в конфиге."""

    def __init__(self, database: Database) -> None:
        self.db = database

    # --- апгрейды ----------------------------------------------------------
    async def list_upgrades(self, only_active: bool = True) -> list[dict]:
        sql = ("SELECT id, code, title, description, kind, value, cost, active "
               "FROM custom_upgrades")
        if only_active:
            sql += " WHERE active = 1"
        sql += " ORDER BY cost ASC, id ASC"
        return [dict(row) for row in await self.db.fetch_all(sql)]

    async def create_upgrade(self, code: str, title: str, description: str | None,
                             kind: str, value: int, cost: int) -> dict | None:
        """Создаёт товар. None — такой код уже есть."""
        try:
            await self.db.execute(
                "INSERT INTO custom_upgrades "
                "(code, title, description, kind, value, cost, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (code, title, description, kind, value, cost,
                 int(utcnow().timestamp())),
            )
        except Exception as exc:  # noqa: BLE001 - нарушение уникальности
            logger.info("Товар %s не создан: %s", code, exc)
            return None
        return {
            "code": code, "title": title, "description": description,
            "kind": kind, "value": value, "cost": cost,
        }

    async def set_upgrade_active(self, code: str, active: bool) -> None:
        await self.db.execute(
            "UPDATE custom_upgrades SET active = ? WHERE code = ?",
            (1 if active else 0, code),
        )

    async def delete_upgrade(self, code: str) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM custom_upgrades WHERE code = ?", (code,)
        )
        return cursor.rowcount > 0

    # --- темы --------------------------------------------------------------
    async def list_themes(self, only_active: bool = True) -> list[dict]:
        sql = ("SELECT id, code, title, description, cost, base_color, active "
               "FROM custom_themes")
        if only_active:
            sql += " WHERE active = 1"
        sql += " ORDER BY cost ASC, id ASC"
        return [dict(row) for row in await self.db.fetch_all(sql)]

    async def create_theme(self, code: str, title: str, cost: int,
                           base_color: str,
                           description: str | None = None) -> dict | None:
        try:
            await self.db.execute(
                "INSERT INTO custom_themes "
                "(code, title, description, cost, base_color, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (code, title, description, cost, base_color,
                 int(utcnow().timestamp())),
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("Тема %s не создана: %s", code, exc)
            return None
        return {"code": code, "title": title, "cost": cost,
                "base_color": base_color, "description": description}

    async def set_theme_active(self, code: str, active: bool) -> None:
        await self.db.execute(
            "UPDATE custom_themes SET active = ? WHERE code = ?",
            (1 if active else 0, code),
        )

    async def delete_theme(self, code: str) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM custom_themes WHERE code = ?", (code,)
        )
        return cursor.rowcount > 0

    async def grant_product(self, user_id: int, code: str) -> bool:
        """Помечает товар выданным игроку: покупать он больше не должен."""
        await self.db.execute(
            "INSERT OR IGNORE INTO user_themes (user_id, theme, bought_at) "
            "VALUES (?, ?, ?)",
            (user_id, f"product:{code}", int(utcnow().timestamp())),
        )
        return True


class DonationRepository:
    """Донаты в звёздах: журнал платежей.

    Платёж сначала pending, потом approved, потом paid или cancelled.
    Смена состояния проверяется по текущему, поэтому повторный
    successful_payment уже оплаченный заказ не зачислит дважды.
    """

    def __init__(self, database: Database) -> None:
        self.db = database

    async def register(self, payload: str, user_id: int, username: str | None,
                       stars: int) -> bool:
        try:
            await self.db.execute(
                "INSERT INTO donations "
                "(user_id, username, payload, stars, status, created_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (user_id, username, payload, stars, int(utcnow().timestamp())),
            )
            return True
        except Exception as exc:  # noqa: BLE001 - дубль payload
            logger.info("Платёж %s не заведён: %s", payload, exc)
            return False

    async def set_status(self, payload: str, status: str,
                         error: str | None = None) -> bool:
        cursor = await self.db.execute(
            "UPDATE donations SET status = ?, error = ? "
            "WHERE payload = ? AND status = 'pending'",
            (status, error, payload),
        )
        return cursor.rowcount > 0

    async def complete(self, payload: str, charge_id: str,
                       provider_charge_id: str | None) -> bool:
        cursor = await self.db.execute(
            "UPDATE donations SET status = 'paid', charge_id = ?, "
            "provider_charge_id = ?, paid_at = ? "
            "WHERE payload = ? AND status IN ('pending', 'approved')",
            (charge_id, provider_charge_id, int(utcnow().timestamp()), payload),
        )
        return cursor.rowcount > 0

    async def refund(self, charge_id: str) -> bool:
        cursor = await self.db.execute(
            "UPDATE donations SET status = 'refunded' WHERE charge_id = ?",
            (charge_id,),
        )
        return cursor.rowcount > 0

    async def by_payload(self, payload: str) -> dict | None:
        row = await self.db.fetch_one(
            "SELECT id, user_id, username, payload, stars, status, charge_id "
            "FROM donations WHERE payload = ?", (payload,),
        )
        return dict(row) if row else None

    async def total_stars(self) -> int:
        row = await self.db.fetch_one(
            "SELECT COALESCE(SUM(stars), 0) AS total FROM donations "
            "WHERE status = 'paid'"
        )
        return int(row["total"]) if row else 0


class LinkRepository:
    """Ссылки: общие для бота и привязанные к товарам.

    scope различает, к чему относится ссылка: 'bot' — общая,
    'upgrade' или 'theme' — к конкретному товару с кодом code.
    """

    def __init__(self, database: Database) -> None:
        self.db = database

    # --- общие ссылки ------------------------------------------------------
    async def bot_links(self, section: str = "help",
                        only_active: bool = True) -> list[dict]:
        sql = ("SELECT id, title, url, section, position, active "
               "FROM bot_links WHERE section = ?")
        if only_active:
            sql += " AND active = 1"
        sql += " ORDER BY position ASC, id ASC"
        return [dict(row) for row in await self.db.fetch_all(sql, (section,))]

    async def all_bot_links(self) -> list[dict]:
        sql = ("SELECT id, title, url, section, position, active FROM bot_links "
               "ORDER BY section ASC, position ASC, id ASC")
        return [dict(row) for row in await self.db.fetch_all(sql)]

    async def add_bot_link(self, title: str, url: str, section: str = "help") -> int:
        next_position = await self.db.fetch_one(
            "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM bot_links "
            "WHERE section = ?", (section,),
        )
        cursor = await self.db.execute(
            "INSERT INTO bot_links (title, url, section, position, active, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (title, url, section, int(next_position["pos"] if next_position else 1),
             int(utcnow().timestamp())),
        )
        return int(cursor.lastrowid or 0)

    async def set_bot_link_active(self, link_id: int, active: bool) -> None:
        await self.db.execute(
            "UPDATE bot_links SET active = ? WHERE id = ?",
            (1 if active else 0, link_id),
        )

    async def delete_bot_link(self, link_id: int) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM bot_links WHERE id = ?", (link_id,)
        )
        return cursor.rowcount > 0

    # --- ссылки товаров ----------------------------------------------------
    async def product_links(self, scope: str, code: str) -> list[dict]:
        sql = ("SELECT id, title, url FROM product_links "
               "WHERE scope = ? AND code = ? ORDER BY position ASC, id ASC")
        return [dict(row) for row in await self.db.fetch_all(sql, (scope, code))]

    async def product_links_bulk(self, scope: str,
                                 codes: list[str]) -> dict[str, list[dict]]:
        """Ссылки сразу по нескольким товарам — магазины зовут это на каждый
        показ, поэтому одним запросом вместо запроса на товар."""
        if not codes:
            return {}
        placeholders = ", ".join("?" * len(codes))
        sql = (f"SELECT code, title, url FROM product_links "
               f"WHERE scope = ? AND code IN ({placeholders}) "
               f"ORDER BY position ASC, id ASC")
        rows = await self.db.fetch_all(sql, (scope, *codes))
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["code"], []).append(
                {"title": row["title"], "url": row["url"]}
            )
        return grouped

    async def add_product_link(self, scope: str, code: str, title: str,
                               url: str) -> int:
        next_position = await self.db.fetch_one(
            "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM product_links "
            "WHERE scope = ? AND code = ?", (scope, code),
        )
        cursor = await self.db.execute(
            "INSERT INTO product_links (scope, code, title, url, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scope, code, title, url,
             int(next_position["pos"] if next_position else 1),
             int(utcnow().timestamp())),
        )
        return int(cursor.lastrowid or 0)

    async def delete_product_link(self, link_id: int) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM product_links WHERE id = ?", (link_id,)
        )
        return cursor.rowcount > 0


class BanRepository:
    """Блокировки игроков.

    Нужна история (в том числе для повторного бана), поэтому записи не
    удаляются, а помечаются снятыми. Истекающие баны считаются снятыми
    по полю until.
    """

    def __init__(self, database: Database) -> None:
        self.db = database

    async def active(self, user_id: int) -> dict | None:
        """Действующий бан игрока либо None."""
        now = int(utcnow().timestamp())
        row = await self.db.fetch_one(
            """
            SELECT user_id, reason, banned_by, banned_at, until
            FROM bans
            WHERE user_id = ? AND lifted_at IS NULL
              AND (until IS NULL OR until > ?)
            ORDER BY banned_at DESC LIMIT 1
            """,
            (user_id, now),
        )
        if row is None:
            return None
        return {
            "user_id": row["user_id"],
            "reason": row["reason"],
            "banned_by": row["banned_by"],
            "banned_at": from_timestamp(row["banned_at"]),
            "until": from_timestamp(row["until"]),
        }

    async def is_banned(self, user_id: int) -> bool:
        return await self.active(user_id) is not None

    async def ban(self, user_id: int, reason: str | None, banned_by: int,
                  until_ts: int | None) -> None:
        await self.db.execute(
            "INSERT INTO bans (user_id, reason, banned_by, banned_at, until) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, reason, banned_by, int(utcnow().timestamp()), until_ts),
        )

    async def lift(self, user_id: int) -> bool:
        """Снимает бан. False — бана не было."""
        now = int(utcnow().timestamp())
        cursor = await self.db.execute(
            "UPDATE bans SET lifted_at = ? WHERE user_id = ? AND lifted_at IS NULL",
            (now, user_id),
        )
        return cursor.rowcount > 0

    async def list_active(self, limit: int = 100) -> list[dict]:
        now = int(utcnow().timestamp())
        rows = await self.db.fetch_all(
            """
            SELECT b.user_id, b.reason, b.banned_by, b.banned_at, b.until,
                   u.username, u.first_name
            FROM bans b LEFT JOIN users u ON u.user_id = b.user_id
            WHERE b.lifted_at IS NULL AND (b.until IS NULL OR b.until > ?)
            ORDER BY b.banned_at DESC LIMIT ?
            """,
            (now, limit),
        )
        return [
            {
                "user_id": row["user_id"],
                "reason": row["reason"],
                "banned_by": row["banned_by"],
                "banned_at": from_timestamp(row["banned_at"]),
                "until": from_timestamp(row["until"]),
                "username": row["username"],
                "first_name": row["first_name"],
            }
            for row in rows
        ]

    async def count_active(self) -> int:
        now = int(utcnow().timestamp())
        row = await self.db.fetch_one(
            "SELECT COUNT(*) AS c FROM bans "
            "WHERE lifted_at IS NULL AND (until IS NULL OR until > ?)",
            (now,),
        )
        return int(row["c"]) if row else 0

    async def history(self, user_id: int, limit: int = 10) -> list[dict]:
        rows = await self.db.fetch_all(
            "SELECT reason, banned_by, banned_at, until, lifted_at "
            "FROM bans WHERE user_id = ? ORDER BY banned_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [
            {
                "reason": row["reason"],
                "banned_by": row["banned_by"],
                "banned_at": from_timestamp(row["banned_at"]),
                "until": from_timestamp(row["until"]),
                "lifted_at": from_timestamp(row["lifted_at"]),
            }
            for row in rows
        ]


class ChatRepository:
    """Чаты, где бот встречался — база для рассылки /announce."""

    def __init__(self, database: Database) -> None:
        self.db = database

    async def touch(self, chat_id: int, title: str | None = None,
                    chat_type: str | None = None,
                    username: str | None = None) -> None:
        now = int(utcnow().timestamp())
        await self.db.execute(
            """
            INSERT INTO chats (chat_id, title, chat_type, username, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title     = COALESCE(excluded.title, chats.title),
                chat_type = COALESCE(excluded.chat_type, chats.chat_type),
                username  = COALESCE(excluded.username, chats.username),
                last_seen = excluded.last_seen
            """,
            (chat_id, title, chat_type, username, now, now),
        )

    async def all(self, limit: int = 5000) -> list[dict]:
        rows = await self.db.fetch_all(
            "SELECT chat_id, title, chat_type, last_announce FROM chats "
            "ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    async def count(self) -> int:
        row = await self.db.fetch_one("SELECT COUNT(*) AS c FROM chats")
        return int(row["c"]) if row else 0

    async def mark_announced(self, chat_id: int, at_ts: int) -> None:
        await self.db.execute(
            "UPDATE chats SET last_announce = ? WHERE chat_id = ?", (at_ts, chat_id)
        )

    async def recent_announce(self) -> int | None:
        """Время последней рассылки по любому чату."""
        row = await self.db.fetch_one(
            "SELECT MAX(last_announce) AS ts FROM chats"
        )
        return int(row["ts"]) if row and row["ts"] is not None else None


class PackageRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    async def create(self, user_id: int, amount: int, deliver_at_ts: int) -> int:
        async with self.db.transaction() as tx:
            return await self.create_in_tx(tx, user_id, amount, deliver_at_ts)

    @staticmethod
    async def create_in_tx(tx: Transaction, user_id: int, amount: int,
                           deliver_at_ts: int) -> int:
        cursor = await tx.execute(
            """
            INSERT INTO packages (user_id, amount, deliver_at, created_at, notified)
            VALUES (?, ?, ?, ?, 0)
            """,
            (user_id, amount, deliver_at_ts, int(utcnow().timestamp())),
        )
        return int(cursor.lastrowid)

    async def get(self, package_id: int) -> Package | None:
        row = await self.db.fetch_one(
            "SELECT id, user_id, amount, deliver_at, delivered, created_at, notified "
            "FROM packages WHERE id = ?",
            (package_id,),
        )
        return _row_to_package(row) if row else None

    async def get_in_tx(self, tx: Transaction, package_id: int) -> Package | None:
        row = await tx.fetch_one(
            "SELECT id, user_id, amount, deliver_at, delivered, created_at, notified "
            "FROM packages WHERE id = ?",
            (package_id,),
        )
        return _row_to_package(row) if row else None

    async def due(self, limit: int = 100) -> list[Package]:
        """Посылки, время доставки которых наступило и ещё не отправлено."""
        now = int(utcnow().timestamp())
        rows = await self.db.fetch_all(
            "SELECT id, user_id, amount, deliver_at, delivered, created_at, notified "
            "FROM packages WHERE delivered = 0 AND notified = 0 AND deliver_at <= ? "
            "ORDER BY deliver_at ASC LIMIT ?",
            (now, limit),
        )
        return [_row_to_package(row) for row in rows]

    async def pending_for_user(self, user_id: int) -> list[Package]:
        rows = await self.db.fetch_all(
            "SELECT id, user_id, amount, deliver_at, delivered, created_at, notified "
            "FROM packages WHERE user_id = ? AND delivered = 0 "
            "ORDER BY deliver_at ASC",
            (user_id,),
        )
        return [_row_to_package(row) for row in rows]

    async def mark_notified(self, package_id: int) -> None:
        await self.db.execute(
            "UPDATE packages SET notified = 1 WHERE id = ? AND notified = 0",
            (package_id,),
        )

    async def deliver(self, package_id: int, user_id: int) -> Package | None:
        """Атомарно помечает посылку доставленной и начисляет баланс.

        Возвращает обновлённую посылку или None, если посылка уже была
        забрана (защита от двойного нажатия).
        """
        async with self.db.transaction() as tx:
            package = await self.get_in_tx(tx, package_id)
            if package is None or package.is_delivered or package.user_id != user_id:
                return None
            await tx.execute(
                "UPDATE packages SET delivered = 1 WHERE id = ? AND delivered = 0",
                (package_id,),
            )
            await tx.execute(
                """
                UPDATE users
                SET balance = balance + ?,
                    total_earned = total_earned + ?,
                    packages_count = packages_count + 1
                WHERE user_id = ?
                """,
                (package.amount, package.amount, user_id),
            )
            package.delivered = 1
            return package


class FeatureRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    async def all(self) -> dict[str, dict]:
        rows = await self.db.fetch_all("SELECT name, enabled, reason FROM features")
        return {
            row["name"]: {"enabled": bool(row["enabled"]), "reason": row["reason"]}
            for row in rows
        }

    async def get(self, name: str) -> dict:
        row = await self.db.fetch_one(
            "SELECT name, enabled, reason FROM features WHERE name = ?", (name,)
        )
        if row is None:
            return {"enabled": True, "reason": None}
        return {"enabled": bool(row["enabled"]), "reason": row["reason"]}

    async def set(self, name: str, enabled: bool, reason: str | None = None) -> None:
        await self.db.execute(
            "INSERT INTO features (name, enabled, reason) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled, "
            "reason = excluded.reason",
            (name, 1 if enabled else 0, reason),
        )


class PromoRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    @staticmethod
    async def redeem_in_tx(tx: Transaction, user_id: int, code: str) -> bool:
        """Одноразовая активация. True — код только что активирован этим игроком.

        Работает внутри транзакции сервиса, поэтому запись о погашении
        и начисление награды неразделимы: двойная активация невозможна.
        """
        cursor = await tx.execute(
            "INSERT OR IGNORE INTO promo_redemptions (user_id, code, redeemed_at) "
            "VALUES (?, ?, ?)",
            (user_id, code, int(utcnow().timestamp())),
        )
        return cursor.rowcount > 0

    async def is_redeemed(self, user_id: int, code: str) -> bool:
        row = await self.db.fetch_one(
            "SELECT 1 FROM promo_redemptions WHERE user_id = ? AND code = ?",
            (user_id, PromoCodeRepository.normalize(code)),
        )
        return row is not None


class PromoCodeRepository:
    """Каталог промокодов в БД: админ создаёт коды, не трогая конфиг."""

    def __init__(self, database: Database) -> None:
        self.db = database

    @staticmethod
    def normalize(code: str) -> str:
        return code.strip().upper()

    async def get(self, code: str) -> dict | None:
        row = await self.db.fetch_one(
            "SELECT code, jarvis_coins, unlock_secret, max_uses, uses, active "
            "FROM promo_codes WHERE code = ?",
            (self.normalize(code),),
        )
        if row is None:
            return None
        return {
            "code": row["code"],
            "jarvis_coins": row["jarvis_coins"],
            "unlock_secret": bool(row["unlock_secret"]),
            "max_uses": row["max_uses"],
            "uses": row["uses"],
            "active": bool(row["active"]),
        }

    async def exists(self, code: str) -> bool:
        row = await self.db.fetch_one(
            "SELECT 1 FROM promo_codes WHERE code = ?", (self.normalize(code),)
        )
        return row is not None

    async def list_all(self) -> list[dict]:
        rows = await self.db.fetch_all(
            "SELECT code, jarvis_coins, unlock_secret, max_uses, uses, active "
            "FROM promo_codes ORDER BY created_at DESC"
        )
        return [
            {
                "code": row["code"],
                "jarvis_coins": row["jarvis_coins"],
                "unlock_secret": bool(row["unlock_secret"]),
                "max_uses": row["max_uses"],
                "uses": row["uses"],
                "active": bool(row["active"]),
            }
            for row in rows
        ]

    async def create(self, code: str, jarvis_coins: int, unlock_secret: bool,
                     max_uses: int = 0) -> bool:
        """Создаёт код. False — код уже существует."""
        try:
            await self.db.execute(
                """
                INSERT INTO promo_codes
                    (code, jarvis_coins, unlock_secret, max_uses, uses,
                     active, created_at)
                VALUES (?, ?, ?, ?, 0, 1, ?)
                """,
                (
                    self.normalize(code), jarvis_coins,
                    1 if unlock_secret else 0, max_uses,
                    int(utcnow().timestamp()),
                ),
            )
            return True
        except Exception as exc:  # noqa: BLE001 - нарушение уникальности
            logger.info("Промокод %s не создан: %s", code, exc)
            return False

    async def set_active(self, code: str, active: bool) -> None:
        await self.db.execute(
            "UPDATE promo_codes SET active = ? WHERE code = ?",
            (1 if active else 0, self.normalize(code)),
        )

    @staticmethod
    async def find_usable_in_tx(tx: Transaction, code: str) -> dict | None:
        """Ищет активный код, исчерпавший лимит активаций, — внутри транзакции."""
        row = await tx.fetch_one(
            "SELECT code, jarvis_coins, unlock_secret, max_uses, uses, active "
            "FROM promo_codes WHERE code = ?",
            (PromoCodeRepository.normalize(code),),
        )
        if row is None or not row["active"]:
            return None
        if row["max_uses"] and row["uses"] >= row["max_uses"]:
            return None
        return {
            "code": row["code"],
            "jarvis_coins": row["jarvis_coins"],
            "unlock_secret": bool(row["unlock_secret"]),
            "max_uses": row["max_uses"],
            "uses": row["uses"],
        }

    @staticmethod
    async def consume_use_in_tx(tx: Transaction, code: str) -> None:
        await tx.execute(
            "UPDATE promo_codes SET uses = uses + 1 WHERE code = ?",
            (PromoCodeRepository.normalize(code),),
        )
