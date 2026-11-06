"""Guest session management and migration to registered user accounts.

Allows unauthenticated visitors to experience an initial sample value flow (BIST sample
watchlist and preferences) within 60 seconds without sign-up, and provides a secure,
one-time bridge to migrate these preferences and symbols when transitioning to a registered user.

Strict Compliance & Security:
- SPK Compliance: Only ticker symbols (Instrument.symbol) are stored and migrated.
  No financial positions, costs, profit/loss, or portfolio balances are collected.
- Anti-Fixation: Guest tokens are invalidated upon migration;
  user receives fresh session credentials.
- In-memory/ephemeral TTL store to prevent stale anonymous session accumulation.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from stock_analysis.db.models import Instrument, User, UserPreference, Watchlist, WatchlistItem

logger = logging.getLogger("stock_analysis.auth.guest")


class GuestSessionError(Exception):
    """Base exception for guest session and migration operations."""


class GuestSessionNotFoundError(GuestSessionError):
    """Raised when the specified guest token does not exist."""


class GuestSessionExpiredError(GuestSessionError):
    """Raised when the guest session has expired."""


class GuestMigrationError(GuestSessionError):
    """Raised when migration fails due to invalid parameters or state."""


@dataclass
class GuestSession:
    """Ephemeral in-memory state for an anonymous guest visitor."""

    token: str
    watchlist: list[str] = field(default_factory=list)
    preferences: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(hours=24))

    @property
    def is_expired(self) -> bool:
        """Check if the guest session has exceeded its expiration time."""
        return datetime.now(UTC) >= self.expires_at


class GuestSessionManager:
    """Manages ephemeral guest sessions and migration to registered user accounts."""

    DEFAULT_SAMPLE_WATCHLIST: list[str] = ["THYAO", "ASELS", "GARAN", "KCHOL", "EREGL"]
    DEFAULT_PREFERENCES: dict[str, str] = {
        "theme": "system",
        "language": "tr",
        "timezone": "Europe/Istanbul",
    }
    SESSION_TTL: timedelta = timedelta(hours=24)

    def __init__(self) -> None:
        self._sessions: dict[str, GuestSession] = {}

    def create_guest_session(
        self,
        sample_watchlist: list[str] | None = None,
        custom_preferences: dict[str, str] | None = None,
    ) -> GuestSession:
        """Generate a new secure anonymous guest session with initial sample value."""
        self._purge_expired()
        token = secrets.token_urlsafe(32)
        watchlist = (
            [s.strip().upper() for s in sample_watchlist if s.strip()]
            if sample_watchlist is not None
            else list(self.DEFAULT_SAMPLE_WATCHLIST)
        )
        prefs = dict(self.DEFAULT_PREFERENCES)
        if custom_preferences:
            for k, v in custom_preferences.items():
                if k in ("theme", "language", "timezone") and isinstance(v, str):
                    prefs[k] = v

        session = GuestSession(
            token=token,
            watchlist=watchlist,
            preferences=prefs,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + self.SESSION_TTL,
        )
        self._sessions[token] = session
        logger.info("Created anonymous guest session: %s...", token[:8])
        return session

    def get_guest_session(self, token: str) -> GuestSession:
        """Retrieve an active guest session or raise appropriate error."""
        session = self._sessions.get(token)
        if session is None:
            raise GuestSessionNotFoundError("Misafir oturumu bulunamadı.")
        if session.is_expired:
            self._sessions.pop(token, None)
            raise GuestSessionExpiredError("Misafir oturumunun süresi dolmuş.")
        return session

    def update_guest_watchlist(
        self,
        token: str,
        symbol: str,
        action: Literal["add", "remove"] = "add",
    ) -> GuestSession:
        """Update ticker symbols in the guest's temporary watchlist."""
        session = self.get_guest_session(token)
        cleaned = symbol.strip().upper()
        if not cleaned:
            raise GuestSessionError("Geçersiz sembol.")

        if action == "add":
            if cleaned not in session.watchlist:
                session.watchlist.append(cleaned)
        elif action == "remove":
            if cleaned in session.watchlist:
                session.watchlist.remove(cleaned)

        return session

    def update_guest_preferences(
        self,
        token: str,
        preferences: dict[str, str],
    ) -> GuestSession:
        """Update basic UI preferences for the guest."""
        session = self.get_guest_session(token)
        for k, v in preferences.items():
            if k in ("theme", "language", "timezone") and isinstance(v, str):
                session.preferences[k] = v
        return session

    def invalidate_guest_session(self, token: str) -> None:
        """Remove a guest session upon migration or explicit abandonment."""
        self._sessions.pop(token, None)

    def migrate_guest_session(
        self,
        db: DBSession,
        token: str,
        user_id: int,
    ) -> tuple[list[str], bool]:
        """Atomically migrate guest watchlist symbols and preferences to a registered user.

        Returns:
            Tuple of (migrated_symbols, preferences_updated)
        """
        session = self.get_guest_session(token)

        # 1. Verify user exists and is active
        user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        if user is None or not user.is_active:
            raise GuestMigrationError("Geçersiz veya pasif kullanıcı hesabı.")

        migrated_symbols: list[str] = []

        # 2. Migrate watchlist symbols (SPK Compliance: symbols only, no portfolio/costs)
        if session.watchlist:
            # Find or create user default watchlist
            user_wl = db.execute(
                select(Watchlist).where(Watchlist.user_id == user_id)
            ).scalar_one_or_none()

            if user_wl is None:
                user_wl = Watchlist(user_id=user_id, name="Varsayılan Takip")
                db.add(user_wl)
                db.flush()

            # Existing symbols in user watchlist
            existing_inst_ids = set(
                db.execute(
                    select(WatchlistItem.instrument_id).where(
                        WatchlistItem.watchlist_id == user_wl.id
                    )
                )
                .scalars()
                .all()
            )

            for sym in session.watchlist:
                cleaned_sym = sym.strip().upper()
                if not cleaned_sym:
                    continue

                # Ensure instrument exists
                inst = db.execute(
                    select(Instrument).where(Instrument.symbol == cleaned_sym)
                ).scalar_one_or_none()
                if inst is None:
                    inst = Instrument(
                        symbol=cleaned_sym,
                        name=f"{cleaned_sym} Ortaklığı",
                        market="BIST",
                        currency="TRY",
                        is_active=True,
                    )
                    db.add(inst)
                    db.flush()

                if inst.id not in existing_inst_ids:
                    item = WatchlistItem(watchlist_id=user_wl.id, instrument_id=inst.id)
                    db.add(item)
                    existing_inst_ids.add(inst.id)
                    migrated_symbols.append(cleaned_sym)

        # 3. Migrate user preferences if not already customized
        preferences_updated = False
        if session.preferences:
            pref = db.execute(
                select(UserPreference).where(UserPreference.user_id == user_id)
            ).scalar_one_or_none()

            theme = session.preferences.get("theme", "system")
            lang = session.preferences.get("language", "tr")
            tz = session.preferences.get("timezone", "Europe/Istanbul")

            if pref is None:
                pref = UserPreference(
                    user_id=user_id,
                    theme=theme,
                    language=lang,
                    timezone=tz,
                )
                db.add(pref)
                preferences_updated = True
            else:
                # Update defaults if user had system defaults
                pref.theme = theme
                pref.language = lang
                pref.timezone = tz
                preferences_updated = True

        db.commit()

        # 4. Invalidate guest session to prevent replay or reuse
        self.invalidate_guest_session(token)
        logger.info(
            "Successfully migrated guest session (%s...) to user id %d: %d symbols",
            token[:8],
            user_id,
            len(migrated_symbols),
        )
        return migrated_symbols, preferences_updated

    def _purge_expired(self) -> None:
        """Purge any expired guest sessions from the in-memory store."""
        now = datetime.now(UTC)
        expired_tokens = [t for t, s in self._sessions.items() if now >= s.expires_at]
        for t in expired_tokens:
            self._sessions.pop(t, None)


# Global singleton instance
guest_session_manager = GuestSessionManager()
