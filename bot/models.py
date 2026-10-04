"""Модель игрока и посылки."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.config import effective_cooldown
from bot.utils.formatters import display_name, from_timestamp


@dataclass(slots=True)
class User:
    user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    balance: int = 0
    jarvis_coins: int = 0
    jarvis_unlocked: int = 0
    multiplier: int = 1
    cooldown_reduction: int = 0
    instant_cooldown: int = 0
    theme: str = "default"
    status: str | None = None
    just_id: str | None = None
    slowdown: int = 0
    slowdown_at: datetime | None = None
    total_earned: int = 0
    claims_count: int = 0
    packages_count: int = 0
    last_claim_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def has_upgrade(self) -> bool:
        return self.multiplier > 1

    @property
    def has_instant(self) -> bool:
        return bool(self.instant_cooldown)

    @property
    def has_cooldown_reduction(self) -> bool:
        return self.cooldown_reduction > 0

    @property
    def effective_cooldown(self) -> int:
        """Кулдаун с учётом сокращений и мгновенного получения."""
        return effective_cooldown(self.cooldown_reduction, self.has_instant)

    @property
    def secret_unlocked(self) -> bool:
        return bool(self.jarvis_unlocked)

    @property
    def name(self) -> str:
        return display_name(self.username, self.first_name, self.last_name)

    def cooldown_left(self, now: datetime) -> int:
        if self.instant_cooldown or self.last_claim_at is None:
            return 0
        elapsed = (now - self.last_claim_at).total_seconds()
        return max(0, int(self.effective_cooldown - elapsed))

    def is_on_cooldown(self, now: datetime) -> bool:
        return self.cooldown_left(now) > 0


@dataclass(slots=True)
class Package:
    id: int
    user_id: int
    amount: int
    deliver_at: datetime
    delivered: int = 0
    created_at: datetime | None = None
    notified: int = 0

    @property
    def is_delivered(self) -> bool:
        return bool(self.delivered)

    @property
    def is_notified(self) -> bool:
        return bool(self.notified)

    def time_left(self, now: datetime) -> int:
        if self.deliver_at is None:
            return 0
        return max(0, int((self.deliver_at - now).total_seconds()))

    def is_ready(self, now: datetime) -> bool:
        return not self.is_delivered and self.time_left(now) <= 0

    @property
    def deliver_at_dt(self) -> datetime:
        return self.deliver_at or from_timestamp(0)
