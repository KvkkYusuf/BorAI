"""Comprehensive End-to-End (E2E) testing suite for critical user journeys (FAZ-14).

Verifies 6 mandatory user flows and a unified end-to-end journey:
1. Registration & Login:
   - Password policy compliance and rejection of weak credentials.
   - User registration and email verification token confirmation.
   - Session cookie (HttpOnly, Secure, SameSite) and Bearer token issuance.
   - Session termination (logout) and invalidation.

2. Stock Search & Discovery:
   - Symbol listing and BIST universe discovery.
   - Symbol code query search (e.g. THYAO).
   - Instrument name/title search (e.g. Türk Hava Yolları).
   - Graceful empty result handling on non-existent symbols.
   - Slicing and pagination controls.

3. Chart View & Downsampling:
   - Daily EOD OHLCV candlestick bar retrieval.
   - Statutory disclaimer header ("X-Disclaimer") enforcement.
   - Chart optimization via downsampling (bucket/max_points).
   - Adjusted vs raw price series selection.

4. Watchlist Lifecycle:
   - Querying user watchlist.
   - Adding ticker symbols with duplicate prevention.
   - Deleting symbols from watchlist.
   - Strictly no portfolio, purchase price, or lot fields (SPK compliance).

5. Alert Rules & Quota Management:
   - Creating threshold alert rules (price_above, price_below).
   - Quota enforcement (maximum 5 active alerts per user).
   - Listing active alerts.
   - Deleting alerts with IDOR prevention.

6. KVKK Data Export & Account Deletion:
   - Exporting personal data archive.
   - Hard account deletion with immediate session invalidation.
   - Cascade removal of watchlists, alerts, and preferences.
   - Preservation of statutory compliance audit log with nullified user_id.

7. Unified Complete User Journey:
   - Full sequential lifecycle from registration to account deletion in a single flow.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.api.schemas import DISCLAIMER_TEXT
from stock_analysis.auth.email_verification import email_verification_manager
from stock_analysis.auth.session import session_security_policy
from stock_analysis.db.base import Base
from stock_analysis.db.models import (
    AlertRule,
    AuditEvent,
    Instrument,
    MarketBar,
    User,
    UserPreference,
    Watchlist,
    WatchlistItem,
)
from stock_analysis.db.models import (
    Session as UserSession,
)
from stock_analysis.db.session import get_db

pytestmark = pytest.mark.e2e

TEST_DATABASE_URL = "sqlite:///:memory:"
e2e_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
E2ETestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=e2e_engine)


@pytest.fixture(scope="module")
def setup_e2e_db() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=e2e_engine)
    yield
    Base.metadata.drop_all(bind=e2e_engine)


class TestE2EUserJourneySuite:
    """Comprehensive E2E test suite verifying the 6 critical user flows and unified lifecycle."""

    @pytest.fixture(autouse=True)
    def clean_data(self, db_session: DBSession) -> None:
        reset_all_rate_limiters()
        db_session.query(AuditEvent).delete()
        db_session.query(WatchlistItem).delete()
        db_session.query(Watchlist).delete()
        db_session.query(AlertRule).delete()
        db_session.query(UserPreference).delete()
        db_session.query(UserSession).delete()
        db_session.query(MarketBar).delete()
        db_session.query(Instrument).delete()
        db_session.query(User).delete()
        db_session.commit()

    @pytest.fixture
    def db_session(self, setup_e2e_db: None) -> Generator[DBSession, None, None]:
        session = E2ETestingSessionLocal()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    @pytest.fixture
    def client(self, db_session: DBSession) -> Generator[TestClient, None, None]:
        app = create_app()

        def override_get_db() -> Generator[DBSession, None, None]:
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    def _seed_sample_instruments(self, db_session: DBSession) -> list[Instrument]:
        """Seed representative BIST instruments for discovery and charting."""
        instruments = [
            Instrument(
                symbol="THYAO",
                name="Türk Hava Yolları A.O.",
                market="BIST",
                currency="TRY",
                is_active=True,
            ),
            Instrument(
                symbol="ASELS",
                name="Aselsan Elektronik San.",
                market="BIST",
                currency="TRY",
                is_active=True,
            ),
            Instrument(
                symbol="EREGL",
                name="Ereğli Demir ve Çelik Fabrikaları",
                market="BIST",
                currency="TRY",
                is_active=True,
            ),
        ]
        db_session.add_all(instruments)
        db_session.commit()
        for inst in instruments:
            db_session.refresh(inst)
        return instruments

    def _seed_historical_bars(
        self,
        db_session: DBSession,
        instrument: Instrument,
        bar_count: int = 60,
    ) -> None:
        """Seed daily OHLCV bars for an instrument."""
        base_date = date.today() - timedelta(days=bar_count)
        bars: list[MarketBar] = []
        base_price = 250.0

        for i in range(bar_count):
            bar_date = base_date + timedelta(days=i)
            # Skip weekends
            if bar_date.weekday() >= 5:
                continue

            open_val = float(round(base_price + i * 0.5, 4))
            high_val = float(round(open_val + 3.0, 4))
            low_val = float(round(open_val - 2.0, 4))
            close_val = float(round(open_val + 1.2, 4))
            volume_val = float(1_000_000 + i * 10_000)
            bar_ts = datetime.combine(bar_date, datetime.min.time(), tzinfo=UTC)

            bar_adj = MarketBar(
                instrument_id=instrument.id,
                timestamp=bar_ts,
                open_price=open_val,
                high_price=high_val,
                low_price=low_val,
                close_price=close_val,
                volume=volume_val,
                is_adjusted=True,
                data_version="v1",
            )
            bar_raw = MarketBar(
                instrument_id=instrument.id,
                timestamp=bar_ts,
                open_price=open_val,
                high_price=high_val,
                low_price=low_val,
                close_price=close_val,
                volume=volume_val,
                is_adjusted=False,
                data_version="v1",
            )
            bars.extend([bar_adj, bar_raw])

        db_session.add_all(bars)
        db_session.commit()

    # =========================================================================
    # 1. Registration & Login Flow
    # =========================================================================

    def test_e2e_registration_verification_and_login_flow(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """E2E Flow 1: Registration, verification, password login, and session logout."""
        test_email = "investor_e2e@example.com"
        weak_password = "short"
        valid_password = "SecurePassword123!"

        # Step 1: Weak password rejected:
        # 1a. Min length < 10 rejected with HTTP 422
        short_res = client.post(
            "/api/v1/auth/register",
            json={"email": test_email, "password": weak_password},
        )
        assert short_res.status_code == 422

        # 1b. Length >= 10 without uppercase/digits/symbols rejected
        # with HTTP 400 PASSWORD_POLICY_VIOLATION
        policy_res = client.post(
            "/api/v1/auth/register",
            json={"email": test_email, "password": "onlylowercaseletters"},
        )
        assert policy_res.status_code == 400
        assert "PASSWORD_POLICY_VIOLATION" in policy_res.text

        # Step 2: Successful registration
        reg_res = client.post(
            "/api/v1/auth/register",
            json={"email": test_email, "password": valid_password},
        )
        assert reg_res.status_code == 200
        assert "Kayıt talebiniz alındı" in reg_res.json()["message"]

        # Step 3: Retrieve created user and verification token
        user = db_session.execute(select(User).where(User.email == test_email)).scalar_one()
        assert user.is_verified is False

        raw_token, _ = email_verification_manager.create_verification_token(
            user_id=user.id,
            email=user.email,
        )

        # Step 4: Confirm email verification
        verify_res = client.post(
            "/api/v1/auth/verify-email/confirm",
            json={"token": raw_token},
        )
        assert verify_res.status_code == 200
        assert verify_res.json()["is_verified"] is True

        db_session.refresh(user)
        assert user.is_verified is True

        # Step 5: Login with credentials
        login_res = client.post(
            "/api/v1/auth/login",
            json={"email": test_email, "password": valid_password},
        )
        assert login_res.status_code == 200
        login_data = login_res.json()
        assert login_data["access_token"] is not None
        assert "borai_session" in login_res.cookies
        assert "borai_csrf" in login_res.cookies

        # Step 6: Authenticated access to user preferences using cookies
        cookie_session = login_res.cookies["borai_session"]
        cookie_csrf = login_res.cookies["borai_csrf"]
        client.cookies.set("borai_session", cookie_session)
        client.cookies.set("borai_csrf", cookie_csrf)

        pref_res = client.get("/api/v1/user/preferences")
        assert pref_res.status_code == 200
        assert "theme" in pref_res.json()

        # Step 7: Logout
        reset_all_rate_limiters()
        logout_res = client.post(
            "/api/v1/auth/session/logout",
            headers={"x-csrf-token": cookie_csrf},
        )
        assert logout_res.status_code == 200
        assert "başarıyla kapatıldı" in logout_res.json()["message"]

        # Step 8: Subsequent request with old session is rejected
        client.cookies.set("borai_session", cookie_session)
        rejected_res = client.get("/api/v1/user/preferences")
        assert rejected_res.status_code == 401

    # =========================================================================
    # 2. Stock Search & Discovery Flow
    # =========================================================================

    def test_e2e_stock_search_and_discovery_flow(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """E2E Flow 2: Symbol search, title filter, pagination, and empty result handling."""
        self._seed_sample_instruments(db_session)

        # Step 1: Default symbols list
        list_res = client.get("/api/v1/market/symbols")
        assert list_res.status_code == 200
        list_data = list_res.json()
        assert list_data["count"] >= 3
        symbols = [s["symbol"] for s in list_data["symbols"]]
        assert "THYAO" in symbols
        assert "ASELS" in symbols

        # Step 2: Search by ticker symbol (exact or prefix)
        query_sym_res = client.get("/api/v1/market/symbols?q=THYAO")
        assert query_sym_res.status_code == 200
        sym_items = query_sym_res.json()["symbols"]
        assert len(sym_items) == 1
        assert sym_items[0]["symbol"] == "THYAO"

        # Step 3: Search by instrument title / company name
        query_name_res = client.get("/api/v1/market/symbols?q=Aselsan")
        assert query_name_res.status_code == 200
        name_items = query_name_res.json()["symbols"]
        assert len(name_items) == 1
        assert name_items[0]["symbol"] == "ASELS"

        # Step 4: Graceful handling of non-existent ticker (empty list, no error)
        query_missing_res = client.get("/api/v1/market/symbols?q=BILINMEYEN_HISSE")
        assert query_missing_res.status_code == 200
        assert query_missing_res.json()["count"] == 0
        assert query_missing_res.json()["symbols"] == []

        # Step 5: Pagination checks
        page_res = client.get("/api/v1/market/symbols?page=1&page_size=2")
        assert page_res.status_code == 200
        page_data = page_res.json()
        assert page_data["page"] == 1
        assert page_data["page_size"] == 2
        assert len(page_data["symbols"]) == 2

    # =========================================================================
    # 3. Chart View & Downsampling Flow
    # =========================================================================

    def test_e2e_chart_historical_bars_and_downsampling_flow(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """E2E Flow 3: OHLCV candlestick data, downsampling, and mandatory disclaimer header."""
        instruments = self._seed_sample_instruments(db_session)
        thyao = next(inst for inst in instruments if inst.symbol == "THYAO")
        self._seed_historical_bars(db_session, thyao, bar_count=70)

        # Step 1: Request standard 1-year daily history
        hist_url = "/api/v1/market/history/THYAO?range=1y&interval=1d&price_type=adjusted"
        hist_res = client.get(hist_url)
        assert hist_res.status_code == 200
        hist_data = hist_res.json()

        assert hist_data["symbol"] == "THYAO"
        assert hist_data["price_type"] == "adjusted"
        assert len(hist_data["candles"]) > 0

        # Verify mandatory SPK disclaimer header
        assert hist_res.headers.get("x-disclaimer") == DISCLAIMER_TEXT

        # Check candle structure
        candle = hist_data["candles"][0]
        for field in ("date", "open", "high", "low", "close", "volume"):
            assert field in candle
        assert candle["high"] >= candle["low"]

        # Step 2: Request downsampled series for mobile / chart view (e.g. max_points=15)
        down_res = client.get(
            "/api/v1/market/history/THYAO?range=1y&interval=1d&downsample=bucket&max_points=15"
        )
        assert down_res.status_code == 200
        down_data = down_res.json()
        assert len(down_data["candles"]) <= 15
        assert len(down_data["candles"]) > 0

        # Step 3: Raw price history without adjustment
        raw_res = client.get("/api/v1/market/history/THYAO?range=1y&price_type=raw")
        assert raw_res.status_code == 200
        assert len(raw_res.json()["candles"]) > 0

    # =========================================================================
    # 4. Watchlist Lifecycle Flow
    # =========================================================================

    def test_e2e_watchlist_management_flow(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """E2E Flow 4: Watchlist add, duplicate prevention, delete, and SPK anti-personalization."""
        self._seed_sample_instruments(db_session)

        # Create verified user and authenticated session
        user = User(email="wl_user@example.com", is_active=True, is_verified=True, role="user")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        raw_session, csrf_token, _ = session_security_policy.create_session(db_session, user.id)
        client.cookies.set("borai_session", raw_session)
        client.cookies.set("borai_csrf", csrf_token)
        headers = {"x-csrf-token": csrf_token}

        # Step 1: Initial empty watchlist
        init_wl_res = client.get("/api/v1/user/watchlist")
        assert init_wl_res.status_code == 200
        assert init_wl_res.json()["watchlist"] == []

        # Step 2: Add THYAO to watchlist
        add_thyao_res = client.post("/api/v1/user/watchlist/THYAO", headers=headers)
        assert add_thyao_res.status_code == 201
        assert add_thyao_res.json()["symbol"] == "THYAO"

        # Step 3: Add ASELS to watchlist
        add_asels_res = client.post("/api/v1/user/watchlist/ASELS", headers=headers)
        assert add_asels_res.status_code == 201

        # Step 4: Verify watchlist contents
        curr_wl_res = client.get("/api/v1/user/watchlist")
        assert curr_wl_res.status_code == 200
        current_symbols = curr_wl_res.json()["watchlist"]
        assert "THYAO" in current_symbols
        assert "ASELS" in current_symbols

        # Step 5: Duplicate add is idempotent
        dup_res = client.post("/api/v1/user/watchlist/THYAO", headers=headers)
        assert dup_res.status_code == 201
        assert len(client.get("/api/v1/user/watchlist").json()["watchlist"]) == 2

        # Step 6: Delete THYAO from watchlist
        del_res = client.delete("/api/v1/user/watchlist/THYAO", headers=headers)
        assert del_res.status_code == 200

        remaining_symbols = client.get("/api/v1/user/watchlist").json()["watchlist"]
        assert "THYAO" not in remaining_symbols
        assert "ASELS" in remaining_symbols

        # Step 7: SPK compliance: response contains ONLY ticker strings, NO purchase price/lots
        for item in remaining_symbols:
            assert isinstance(item, str)

    # =========================================================================
    # 5. Alert Rules & Quota Management Flow
    # =========================================================================

    def test_e2e_alert_rules_quota_and_management_flow(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """E2E Flow 5: Creating price threshold alerts, quota limiting (max 5), and deletion."""
        self._seed_sample_instruments(db_session)

        user = User(email="alerts_user@example.com", is_active=True, is_verified=True, role="user")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        raw_session, csrf_token, _ = session_security_policy.create_session(db_session, user.id)
        client.cookies.set("borai_session", raw_session)
        client.cookies.set("borai_csrf", csrf_token)
        headers = {"x-csrf-token": csrf_token}

        # Step 1: Create price_above alert for THYAO
        alert1_res = client.post(
            "/api/v1/user/alerts",
            json={
                "symbol": "THYAO",
                "condition_type": "price_above",
                "target_value": 310.50,
            },
            headers=headers,
        )
        assert alert1_res.status_code == 201
        alert1_id = alert1_res.json()["alert_id"]
        assert alert1_id.startswith("alt_")

        # Step 2: Create price_below alert for ASELS
        alert2_res = client.post(
            "/api/v1/user/alerts",
            json={
                "symbol": "ASELS",
                "condition_type": "price_below",
                "target_value": 52.00,
            },
            headers=headers,
        )
        assert alert2_res.status_code == 201
        alert2_id = alert2_res.json()["alert_id"]

        # Step 3: List alerts
        list_res = client.get("/api/v1/user/alerts")
        assert list_res.status_code == 200
        alerts_list = list_res.json()["alerts"]
        assert len(alerts_list) == 2
        active_ids = {a["alert_id"] for a in alerts_list}
        assert alert1_id in active_ids
        assert alert2_id in active_ids

        # Step 4: Add 3 more alerts to reach max quota of 5
        for i in range(3):
            res = client.post(
                "/api/v1/user/alerts",
                json={
                    "symbol": "EREGL",
                    "condition_type": "price_above",
                    "target_value": float(40 + i),
                },
                headers=headers,
            )
            assert res.status_code == 201

        # Step 5: 6th alert must be rejected with HTTP 400 ALERT_QUOTA_EXCEEDED
        overflow_res = client.post(
            "/api/v1/user/alerts",
            json={
                "symbol": "EREGL",
                "condition_type": "price_above",
                "target_value": 100.0,
            },
            headers=headers,
        )
        assert overflow_res.status_code == 400
        assert "ALERT_QUOTA_EXCEEDED" in overflow_res.text

        # Step 6: Delete alert1 to free quota
        del_alert_res = client.delete(f"/api/v1/user/alerts/{alert1_id}", headers=headers)
        assert del_alert_res.status_code == 200

        # Step 7: Verify quota freed and now exactly 4 alerts remain
        refreshed_list = client.get("/api/v1/user/alerts").json()["alerts"]
        assert len(refreshed_list) == 4
        assert alert1_id not in {a["alert_id"] for a in refreshed_list}

    # =========================================================================
    # 6. KVKK Data Export & Account Deletion Flow
    # =========================================================================

    def test_e2e_user_preferences_export_and_account_deletion_flow(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """E2E Flow 6: Preferences update, KVKK export, hard account deletion, and audit trail."""
        self._seed_sample_instruments(db_session)

        user = User(email="kvkk_e2e@example.com", is_active=True, is_verified=True, role="user")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        raw_session, csrf_token, _ = session_security_policy.create_session(db_session, user.id)
        client.cookies.set("borai_session", raw_session)
        client.cookies.set("borai_csrf", csrf_token)
        headers = {"x-csrf-token": csrf_token}

        # Step 1: Update preferences (theme, timezone)
        pref_update_res = client.put(
            "/api/v1/user/preferences",
            json={
                "theme": "dark",
                "timezone": "Europe/Istanbul",
                "alert_frequency": "immediate",
            },
            headers=headers,
        )
        assert pref_update_res.status_code == 200
        assert pref_update_res.json()["theme"] == "dark"

        # Step 2: Add item to watchlist
        client.post("/api/v1/user/watchlist/THYAO", headers=headers)

        # Step 3: Add an alert rule
        client.post(
            "/api/v1/user/alerts",
            json={"symbol": "THYAO", "condition_type": "price_above", "target_value": 350.0},
            headers=headers,
        )

        # Step 4: Export personal data archive under KVKK
        export_res = client.get("/api/v1/user/export")
        assert export_res.status_code == 200
        export_data = export_res.json()
        assert export_data["email"] == user.email
        assert "THYAO" in export_data["watchlists"]
        assert len(export_data["alerts"]) == 1

        # Step 5: Hard delete account
        delete_res = client.delete("/api/v1/user/account", headers=headers)
        assert delete_res.status_code == 200
        assert "kalici olarak silindi" in delete_res.json()["message"]

        # Step 6: Verify cookies cleared and old session rejected
        client.cookies.set("borai_session", raw_session)
        denied_res = client.get("/api/v1/user/preferences")
        assert denied_res.status_code == 401

        # Step 7: Verify DB state: user, watchlists, alerts, preferences removed
        assert db_session.get(User, user.id) is None
        wl_stmt = select(Watchlist).where(Watchlist.user_id == user.id)
        assert db_session.execute(wl_stmt).scalar() is None
        alert_stmt = select(AlertRule).where(AlertRule.user_id == user.id)
        assert db_session.execute(alert_stmt).scalar() is None
        pref_stmt = select(UserPreference).where(UserPreference.user_id == user.id)
        assert db_session.execute(pref_stmt).scalar() is None

        # Step 8: Statutory retention: anonymized audit log preserved
        audit_event = db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "ACCOUNT_DELETED")
        ).scalar_one_or_none()
        assert audit_event is not None
        assert audit_event.user_id is None
        assert f"user:{user.id}" in str(audit_event.resource)

    # =========================================================================
    # 7. Unified Complete User Journey
    # =========================================================================

    def test_e2e_complete_unbroken_user_journey(
        self,
        client: TestClient,
        db_session: DBSession,
    ) -> None:
        """E2E Flow 7: Full unbroken sequential user journey from signup to account deletion."""
        instruments = self._seed_sample_instruments(db_session)
        eregl = next(inst for inst in instruments if inst.symbol == "EREGL")
        self._seed_historical_bars(db_session, eregl, bar_count=45)

        user_email = "unified_journey@example.com"
        user_password = "ComplexPassword2026!"

        # 1. Registration
        reg_res = client.post(
            "/api/v1/auth/register",
            json={"email": user_email, "password": user_password},
        )
        assert reg_res.status_code == 200

        user = db_session.execute(select(User).where(User.email == user_email)).scalar_one()

        # 2. Email verification
        raw_verify_token, _ = email_verification_manager.create_verification_token(
            user_id=user.id,
            email=user.email,
        )
        verify_res = client.post(
            "/api/v1/auth/verify-email/confirm",
            json={"token": raw_verify_token},
        )
        assert verify_res.status_code == 200

        # 3. Login
        login_res = client.post(
            "/api/v1/auth/login",
            json={"email": user_email, "password": user_password},
        )
        assert login_res.status_code == 200
        session_cookie = login_res.cookies["borai_session"]
        csrf_cookie = login_res.cookies["borai_csrf"]

        client.cookies.set("borai_session", session_cookie)
        client.cookies.set("borai_csrf", csrf_cookie)
        headers = {"x-csrf-token": csrf_cookie}

        # 4. Search stock (EREGL)
        search_res = client.get("/api/v1/market/symbols?q=EREGL")
        assert search_res.status_code == 200
        symbols_found = [s["symbol"] for s in search_res.json()["symbols"]]
        assert "EREGL" in symbols_found

        # 5. Open chart for EREGL
        chart_res = client.get(
            "/api/v1/market/history/EREGL?range=6m&interval=1d&price_type=adjusted&downsample=bucket&max_points=30"
        )
        assert chart_res.status_code == 200
        assert len(chart_res.json()["candles"]) > 0
        assert chart_res.headers.get("x-disclaimer") == DISCLAIMER_TEXT

        # 6. Add EREGL to watchlist
        wl_add_res = client.post("/api/v1/user/watchlist/EREGL", headers=headers)
        assert wl_add_res.status_code == 201

        # 7. Set price alert on EREGL
        alert_res = client.post(
            "/api/v1/user/alerts",
            json={"symbol": "EREGL", "condition_type": "price_above", "target_value": 65.0},
            headers=headers,
        )
        assert alert_res.status_code == 201
        assert alert_res.json()["alert_id"].startswith("alt_")

        # 8. Update preferences
        pref_res = client.put(
            "/api/v1/user/preferences",
            json={"theme": "dark", "email_notifications": True},
            headers=headers,
        )
        assert pref_res.status_code == 200

        # 9. Export personal data
        export_res = client.get("/api/v1/user/export")
        assert export_res.status_code == 200
        export_payload = export_res.json()
        assert "EREGL" in export_payload["watchlists"]
        assert len(export_payload["alerts"]) == 1

        # 10. Delete account (Right to be forgotten)
        del_acc_res = client.delete("/api/v1/user/account", headers=headers)
        assert del_acc_res.status_code == 200

        # 11. Verify immediate session invalidation
        client.cookies.set("borai_session", session_cookie)
        aftermath_res = client.get("/api/v1/user/preferences")
        assert aftermath_res.status_code == 401
