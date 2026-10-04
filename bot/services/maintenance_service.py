"""Режим технических работ: глобальный переключатель функций."""

from __future__ import annotations

import logging

from bot.config import DEFAULT_MAINTENANCE_REASON, MAINTENANCE_FEATURES
from bot.db.repository import FeatureRepository

logger = logging.getLogger(__name__)


class MaintenanceService:
    def __init__(self, features: FeatureRepository) -> None:
        self.features = features

    @staticmethod
    def is_valid_feature(name: str) -> bool:
        return name.lower() in MAINTENANCE_FEATURES

    @staticmethod
    def normalize(name: str) -> str:
        return name.lower().strip()

    async def is_disabled(self, feature: str) -> bool:
        """True — функция отключена техработами."""
        if not self.is_valid_feature(feature):
            return False
        if await self._is_off("all"):
            return True
        return await self._is_off(self.normalize(feature))

    async def _is_off(self, name: str) -> bool:
        state = await self.features.get(name)
        return not state["enabled"]

    async def reason_for(self, feature: str) -> str:
        """Причина отключения функции (с учётом режима all)."""
        for name in (self.normalize(feature), "all"):
            if not self.is_valid_feature(name):
                continue
            state = await self.features.get(name)
            if not state["enabled"]:
                return state["reason"] or DEFAULT_MAINTENANCE_REASON
        return DEFAULT_MAINTENANCE_REASON

    async def turn_on(self, feature: str, reason: str) -> None:
        name = self.normalize(feature)
        await self.features.set(name, False, reason or DEFAULT_MAINTENANCE_REASON)
        logger.info("Функция %s отключена: %s", name, reason)

    async def turn_off(self, feature: str) -> None:
        """Включает функцию. Для "all" снимает и точечные флаги.

        Иначе /maintenance_off all оставлял бы ранее отключённые функции
        выключенными, и админ не смог бы вернуть всё в работу одной
        командой — приходилось бы выключать каждую по отдельности.
        """
        name = self.normalize(feature)
        if name == "all":
            for feature_name in MAINTENANCE_FEATURES:
                if feature_name == "all":
                    continue
                await self.features.set(feature_name, True, None)
        await self.features.set(name, True, None)
        logger.info("Функция %s включена", name)

    async def status(self) -> list[tuple[str, bool, str | None]]:
        """Состояние всех функций.

        Эффективная доступность учитывает глобальный флаг "all": если он
        выключен, остальные функции тоже недоступны, даже когда их
        собственный флаг включён. Возвращаем именно её, иначе
        /maintenance_status вводил бы в заблуждение.
        """
        states = await self.features.all()
        all_off = not states.get("all", {"enabled": True}).get("enabled", True)
        result: list[tuple[str, bool, str | None]] = []
        for name in MAINTENANCE_FEATURES:
            state = states.get(name, {"enabled": True, "reason": None})
            enabled = bool(state["enabled"]) and not (all_off and name != "all")
            result.append((name, enabled, state["reason"]))
        return result
