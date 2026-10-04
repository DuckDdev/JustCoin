"""Админ-команды: режим технических работ."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import messages
from bot.config import DEFAULT_BAN_REASON, DEFAULT_MAINTENANCE_REASON, MAINTENANCE_FEATURES, is_admin
from bot.models import User as Player
from bot.services.ban_service import BanResult, ban_text, parse_duration
from bot.services.maintenance_service import MaintenanceService
from bot.services.promo_service import PromoService
from bot.services.status_service import StatusResult, StatusService
from bot.services.upgrade_service import UpgradeService
from bot.services.user_service import UserService
from bot.utils.formatters import format_date

logger = logging.getLogger(__name__)

FEATURES_HINT = ", ".join(MAINTENANCE_FEATURES)


async def _deny_if_not_admin(message: Message) -> bool:
    """True — пользователь не админ и ответ уже отправлен."""
    if is_admin(message.from_user.id):
        return False
    await message.answer(messages.NO_RIGHTS)
    return True


def make_router() -> Router:
    """Создаёт новый экземпляр роутера (aiogram не даёт делить Router)."""
    router = Router(name="admin")
    _register(router)
    _register_upgrades(router)
    _register_moderation(router)
    return router


def _resolve_target(message: Message) -> Player | None:
    """Кому выдаём: reply в личке или сам пользователь из аргумента.

    В группах у сообщения может не быть отправителя (анонимный админ),
    поэтому там нужен явный @username, ID или JustID.
    """
    if message.reply_to_message and message.reply_to_message.from_user:
        return Player(
            user_id=message.reply_to_message.from_user.id,
            username=message.reply_to_message.from_user.username,
            first_name=message.reply_to_message.from_user.first_name,
        )
    if message.from_user and message.chat.type == ChatType.PRIVATE:
        return Player(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
    return None


async def _resolve_by_argument(argument: str, data: dict[str, Any]) -> Player | None:
    """Ищет игрока по JustID ($John), числовому ID или @username."""
    argument = (argument or "").strip()
    if not argument:
        return None
    users: UserService = data["users"]

    if argument.startswith("$"):
        return await data["justids"].by_id(argument)

    if argument.lstrip("-").isdigit():
        return await users.get_or_none(int(argument))

    row = await data["db"].fetch_one(
        "SELECT user_id FROM users WHERE LOWER(username) = ? LIMIT 1",
        (argument.lstrip("@").lower(),),
    )
    if row is None:
        return None
    return await users.get_or_none(int(row["user_id"]))


async def _target_from_args(
    message: Message, command: CommandObject, data: dict[str, Any]
) -> tuple[Player | None, list[str]]:
    """Разбирает аргументы вида `/cmd <игрок> [срок] [причина]`.

    Если игрок не указан, берётся тот, на чьё сообщение ответили,
    либо сам отправитель в личке.

    Явную ссылку на игрока ($John, @name, ID) не подменяем: если она
    не нашлась — цели нет. Иначе /ban $Опечатка молча забанил бы
    самого админа, а /setstatus $Неизвестно — выдал бы статус ему.
    """
    args = (command.args or "").split()
    if not args:
        return _resolve_target(message), []

    first = args[0]
    looks_like_target = (
        first.startswith("$") or first.startswith("@") or first.lstrip("-").isdigit()
    )
    target = await _resolve_by_argument(first, data)
    if target is None:
        if looks_like_target:
            return None, args
        # Это срок или причина, а игрок — тот, на кого ответили.
        return _resolve_target(message), args
    return target, args[1:]


def _register_moderation(router: Router) -> None:
    @router.message(Command("ban"))
    async def cmd_ban(message: Message, command: CommandObject,
                      **data: Any) -> None:
        """/ban <игрок> [срок] [причина]"""
        if await _deny_if_not_admin(message):
            return
        target, rest = await _target_from_args(message, command, data)
        if target is None:
            await message.answer(messages.BAN_NO_TARGET)
            return

        # Срок стоит первым, если похож на срок: 30м, 12h, 7д, 4w.
        duration = parse_duration(rest[0]) if rest else None
        reason = " ".join(rest[1:]) if duration is not None else " ".join(rest)

        outcome = await data["bans"].ban(
            target.user_id, message.from_user.id, reason or None, duration
        )
        if outcome.result is BanResult.SUCCESS:
            if outcome.until:
                text = messages.BAN_DONE.format(
                    name=target.name, reason=reason or DEFAULT_BAN_REASON,
                    until=f" до {format_date(outcome.until)}",
                )
            else:
                text = messages.BAN_DONE_FOREVER.format(
                    name=target.name, reason=reason or DEFAULT_BAN_REASON
                )
            await message.answer(text)
            # Предупреждаем игрока сразу, а не блокируем молча.
            try:
                await message.bot.send_message(
                    target.user_id,
                    ban_text(reason, outcome.until, outcome.until is not None),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Не удалось уведомить о бане: %s", exc)
        elif outcome.result is BanResult.SELF:
            await message.answer(messages.BAN_SELF)
        elif outcome.result is BanResult.ADMIN_TARGET:
            await message.answer(messages.BAN_ADMIN)
        elif outcome.result is BanResult.ALREADY_BANNED:
            await message.answer(messages.BAN_ALREADY.format(name=target.name))
        else:
            await message.answer(messages.BOT_ERROR)

    @router.message(Command("unban"))
    async def cmd_unban(message: Message, command: CommandObject,
                        **data: Any) -> None:
        """/unban <игрок>"""
        if await _deny_if_not_admin(message):
            return
        target, _rest = await _target_from_args(message, command, data)
        if target is None:
            await message.answer(messages.BAN_NO_TARGET)
            return
        outcome = await data["bans"].unban(target.user_id)
        if outcome.result is BanResult.SUCCESS:
            await message.answer(messages.BAN_UNDONE.format(name=target.name))
            try:
                await message.bot.send_message(target.user_id, messages.BAN_LIFTED)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Не удалось уведомить о снятии бана: %s", exc)
        else:
            await message.answer(messages.BAN_NOT_BANNED.format(name=target.name))

    @router.message(Command("bans"))
    async def cmd_bans(message: Message, **data: Any) -> None:
        """/bans — список забаненных"""
        if await _deny_if_not_admin(message):
            return
        rows = await data["bans"].list_active()
        if not rows:
            await message.answer(messages.BAN_LIST_EMPTY)
            return
        lines = [messages.BAN_LIST_HEADER.format(count=len(rows))]
        for row in rows:
            name = f"@{row['username']}" if row["username"] else str(row["user_id"])
            until = (
                messages.BAN_LIST_UNTIL.format(date=format_date(row["until"]))
                if row["until"] else messages.BAN_LIST_FOREVER
            )
            lines.append(
                messages.BAN_LIST_ROW.format(
                    name=name, reason=row["reason"] or "—", until=until
                )
            )
        await message.answer("\n".join(lines))

    @router.message(Command("announce"))
    async def cmd_announce(message: Message, bot: Bot, command: CommandObject,
                           **data: Any) -> None:
        """/announce <текст> — рассылка по всем чатам"""
        if await _deny_if_not_admin(message):
            return
        text = (command.args or "").strip()
        if not text:
            await message.answer(messages.ANNOUNCE_USAGE)
            return

        result = await data["announce"].broadcast(bot, text)
        if result.total == 0:
            await message.answer(messages.ANNOUNCE_EMPTY)
            return
        await message.answer(messages.ANNOUNCE_SENDING.format(total=result.total))
        await message.answer(
            messages.ANNOUNCE_DONE.format(sent=result.sent, failed=result.failed)
        )

    @router.message(Command("setstatus"))
    async def cmd_setstatus(message: Message, command: CommandObject,
                            **data: Any) -> None:
        """/setstatus <статус>|off — ответьте игроку"""
        if await _deny_if_not_admin(message):
            return
        statuses: StatusService = data["statuses"]
        target, rest = await _target_from_args(message, command, data)
        if target is None or not rest:
            await message.answer(messages.ADMIN_SETSTATUS_USAGE)
            return

        player = await data["users"].get_or_none(target.user_id) or target
        value = rest[0].lower()
        if value in ("off", "none", "-"):
            await statuses.clear(player)
            await message.answer(
                messages.ADMIN_SETSTATUS_CLEARED.format(name=player.name)
            )
            return

        if await statuses.grant(player, value) is StatusResult.UNKNOWN:
            await message.answer(
                messages.ADMIN_SETSTATUS_UNKNOWN.format(
                    status=value,
                    statuses=", ".join(
                        s.title for s in StatusService.catalog().values()
                    ),
                )
            )
            return
        await message.answer(
            messages.ADMIN_SETSTATUS_OK.format(
                name=player.name, label=StatusService.catalog()[value].label
            )
        )


def _register_upgrades(router: Router) -> None:
    @router.message(Command("jinstant"))
    async def cmd_jinstant(message: Message, command: CommandObject,
                           **data: Any) -> None:
        """/jinstant on|off — мгновенное получение игроку."""
        if await _deny_if_not_admin(message):
            return
        upgrades: UpgradeService = data["upgrade"]
        users: UserService = data["users"]
        arg = (command.args or "").strip().lower()
        if arg not in ("on", "off"):
            await message.answer(messages.ADMIN_UPGRADE_USAGE)
            return

        target = _resolve_target(message)
        if target is None:
            await message.answer(messages.ADMIN_NO_TARGET)
            return

        player = await users.get_or_none(target.user_id) or target
        await upgrades.set_instant(player, arg == "on")
        template = (
            messages.ADMIN_INSTANT_ON if arg == "on" else messages.ADMIN_INSTANT_OFF
        )
        await message.answer(template.format(name=player.name))

    @router.message(Command("jgrant"))
    async def cmd_jgrant(message: Message, command: CommandObject,
                         **data: Any) -> None:
        """/jgrant <апгрейд> — выдать апгрейд игроку в ответе."""
        if await _deny_if_not_admin(message):
            return
        upgrades: UpgradeService = data["upgrade"]
        users: UserService = data["users"]
        arg = (command.args or "").strip()
        if not arg:
            await message.answer(messages.ADMIN_UPGRADE_USAGE)
            return

        upgrade_id = arg.split()[0].lower()
        if not UpgradeService.exists(upgrade_id):
            await message.answer(
                messages.ADMIN_UPGRADE_UNKNOWN.format(
                    upgrade=upgrade_id,
                    upgrades=", ".join(UpgradeService.catalog()),
                )
            )
            return

        target = _resolve_target(message)
        if target is None:
            await message.answer(messages.ADMIN_NO_TARGET)
            return

        player = await users.get_or_none(target.user_id) or target
        if not await upgrades.grant(player, upgrade_id):
            await message.answer(messages.BOT_ERROR)
            return
        title = UpgradeService.catalog()[upgrade_id]["title"]
        await message.answer(messages.ADMIN_GRANT_OK.format(name=player.name, title=title))

    @router.message(Command("jpromo"))
    async def cmd_jpromo(message: Message, command: CommandObject,
                         **data: Any) -> None:
        """/jpromo <код> <монеты> [лимит] | off <код> | list"""
        if await _deny_if_not_admin(message):
            return
        promo: PromoService = data["promo"]
        args = (command.args or "").split()

        if not args:
            await message.answer(messages.ADMIN_PROMO_USAGE)
            return

        if args[0].lower() == "list":
            codes = await promo.codes.list_all()
            if not codes:
                await message.answer(messages.ADMIN_PROMO_EMPTY)
                return
            rows = [
                messages.ADMIN_PROMO_ROW.format(
                    code=item["code"],
                    coins=f"{item['jarvis_coins']:,}".replace(",", " "),
                    uses=(
                        f" — {item['uses']}/{item['max_uses']}"
                        if item["max_uses"] else f" — {item['uses']} активаций"
                    ),
                    state="" if item["active"] else " (выключен)",
                )
                for item in codes
            ]
            await message.answer(messages.ADMIN_PROMO_LIST + "\n".join(rows))
            return

        if args[0].lower() in ("off", "on") and len(args) >= 2:
            code = args[1]
            if not await promo.codes.exists(code):
                await message.answer(messages.PROMO_INVALID)
                return
            enable = args[0].lower() == "on"
            await promo.codes.set_active(code, enable)
            await message.answer(
                (messages.ADMIN_PROMO_ENABLED if enable else messages.ADMIN_PROMO_DISABLED)
                .format(code=code.upper())
            )
            return

        code = args[0]
        if len(args) < 2 or not args[1].lstrip("-").isdigit():
            await message.answer(messages.ADMIN_PROMO_USAGE)
            return
        coins = int(args[1])
        if coins <= 0:
            await message.answer(messages.ADMIN_PROMO_USAGE)
            return
        max_uses = int(args[2]) if len(args) >= 3 and args[2].isdigit() else 0

        created = await promo.codes.create(code, coins, unlock_secret=False,
                                           max_uses=max_uses)
        if not created:
            await message.answer(messages.ADMIN_PROMO_EXISTS)
            return
        await message.answer(
            messages.ADMIN_PROMO_CREATED.format(
                code=code.upper(), coins=f"{coins:,}".replace(",", " ")
            )
        )


def _register(router: Router) -> None:
    @router.message(Command("maintenance_on", "maintenance_off", "maintenance_status"))
    async def maintenance_dispatch(message: Message, command: CommandObject,
                                   **data: Any) -> None:
        """Диспетчер техработ: проверяет права и маршрутизирует команду."""
        if await _deny_if_not_admin(message):
            return

        maintenance: MaintenanceService = data["maintenance"]
        args = (command.args or "").strip()
        # CommandObject.command хранит имя БЕЗ префикса: "maintenance_on".
        name = (command.command or "").lower()

        if name == "maintenance_status":
            await _status(message, maintenance)
            return

        if not args:
            usage = (
                messages.MAINTENANCE_OFF_USAGE
                if name == "maintenance_off"
                else messages.MAINTENANCE_USAGE
            )
            await message.answer(usage)
            return

        feature = args.split()[0].lower()
        if not maintenance.is_valid_feature(feature):
            await message.answer(
                messages.MAINTENANCE_UNKNOWN_FEATURE.format(
                    feature=feature, features=FEATURES_HINT
                )
            )
            return

        if name == "maintenance_off":
            await maintenance.turn_off(feature)
            await message.answer(
                messages.MAINTENANCE_OFF_CONFIRM.format(feature=feature)
            )
            return

        parts = args.split(maxsplit=1)
        reason = parts[1].strip() if len(parts) > 1 else DEFAULT_MAINTENANCE_REASON
        await maintenance.turn_on(feature, reason)
        await message.answer(
            messages.MAINTENANCE_ON_CONFIRM.format(
                feature=feature, reason=reason or DEFAULT_MAINTENANCE_REASON
            )
        )


async def _status(message: Message, maintenance: MaintenanceService) -> None:
    states = await maintenance.status()
    disabled = [
        messages.MAINTENANCE_STATUS_ON.format(
            feature=name, reason=reason or DEFAULT_MAINTENANCE_REASON
        )
        for name, enabled, reason in states
        if not enabled
    ]
    if not disabled:
        await message.answer(
            messages.MAINTENANCE_STATUS_HEADER + "\n" + messages.MAINTENANCE_STATUS_NONE
        )
        return
    await message.answer(messages.MAINTENANCE_STATUS_HEADER + "\n" + "\n".join(disabled))
