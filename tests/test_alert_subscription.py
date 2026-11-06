"""FAZ-12: Kullanıcı bildirim izni (opt-in) ve RFC 8058 tek tıkla abonelikten çıkma testleri."""

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.alerts.email_notifier import (
    AlertEmailComposer,
    AlertNotificationPayload,
)
from stock_analysis.alerts.subscription import (
    InvalidUnsubscribeTokenError,
    NotificationConsentManager,
    UnsubscribeTokenPayload,
    build_rfc8058_headers,
    build_unsubscribe_url,
    default_consent_manager,
    generate_unsubscribe_token,
    verify_unsubscribe_token,
)
from stock_analysis.api.app import create_app
from stock_analysis.api.deps import AuthenticatedUser, get_current_user
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.db.base import Base
from stock_analysis.db.models import AlertRule, AuditEvent, Instrument, User, UserPreference
from stock_analysis.db.session import get_db

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="module")
def setup_db() -> Generator[None, None, None]:
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session(setup_db: None) -> Generator[DBSession, None, None]:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def clean_records(db_session: DBSession) -> None:
    reset_all_rate_limiters()
    db_session.query(AuditEvent).delete()
    db_session.query(AlertRule).delete()
    db_session.query(UserPreference).delete()
    db_session.query(User).delete()
    db_session.commit()
    # Reset in-memory consents
    default_consent_manager._in_memory_consents.clear()


# =====================================================================
# 1. Token Üretimi ve Doğrulama Birim Testleri
# =====================================================================


def test_generate_and_verify_valid_token() -> None:
    """Geçerli token üretimi ve başarıyla doğrulanması."""
    token = generate_unsubscribe_token(
        user_id=42,
        email="yatirimci@example.com",
        alert_id="alt-rule-999",
        scope="alert_rule",
        expires_in_days=7,
    )

    assert isinstance(token, str)
    assert "." in token

    payload = verify_unsubscribe_token(token)
    assert isinstance(payload, UnsubscribeTokenPayload)
    assert payload.user_id == 42
    assert payload.scope == "alert_rule"
    assert payload.alert_id == "alt-rule-999"
    assert payload.email_masked == "y***@example.com"
    assert payload.expires_at_utc > datetime.now(UTC)


def test_token_rejects_invalid_inputs() -> None:
    """Geçersiz kullanıcı kimliği veya e-posta için ValueError fırlatılmalıdır."""
    with pytest.raises(ValueError, match="Geçersiz kullanıcı kimliği"):
        generate_unsubscribe_token(user_id=0, email="test@example.com")

    with pytest.raises(ValueError, match="Geçersiz e-posta"):
        generate_unsubscribe_token(user_id=1, email="invalid-email")


def test_verify_rejects_tampered_payload_or_signature() -> None:
    """Kurcalanmış veya değiştirilmiş token'lar reddedilmelidir."""
    token = generate_unsubscribe_token(user_id=10, email="user@example.com")
    payload_part, sig_part = token.split(".")

    # Kurcalanmış veri
    tampered_payload = payload_part[:-2] + "AA"
    with pytest.raises(InvalidUnsubscribeTokenError, match="doğrulanamadı veya kurcalandı"):
        verify_unsubscribe_token(f"{tampered_payload}.{sig_part}")

    # Kurcalanmış imza
    tampered_sig = sig_part[:-2] + "ZZ"
    with pytest.raises(InvalidUnsubscribeTokenError, match="doğrulanamadı veya kurcalandı"):
        verify_unsubscribe_token(f"{payload_part}.{tampered_sig}")


def test_verify_rejects_malformed_tokens() -> None:
    """Biçimsel olarak bozuk token'lar yakalanmalıdır."""
    with pytest.raises(InvalidUnsubscribeTokenError, match="Geçersiz token biçimi"):
        verify_unsubscribe_token("")

    with pytest.raises(InvalidUnsubscribeTokenError, match="Geçersiz token biçimi"):
        verify_unsubscribe_token("no-dot-token")


def test_verify_rejects_expired_token() -> None:
    """Süresi dolmuş token'lar reddedilmelidir."""
    token = generate_unsubscribe_token(
        user_id=10,
        email="user@example.com",
        expires_in_days=-1,  # Geçmiş tarih
    )
    with pytest.raises(InvalidUnsubscribeTokenError, match="süresi dolmuş"):
        verify_unsubscribe_token(token)


# =====================================================================
# 2. RFC 8058 Standart Başlıkları ve URL Üretimi
# =====================================================================


def test_build_unsubscribe_url_and_rfc8058_headers() -> None:
    """RFC 8058 One-Click başlıkları ve URL'si standartlara uygun oluşturulmalıdır."""
    token = "sample.token.payload.signature"
    url = build_unsubscribe_url("https://app.boraai.com/", token)

    assert (
        url
        == "https://app.boraai.com/api/v1/alerts/unsubscribe?token=sample.token.payload.signature"
    )

    headers = build_rfc8058_headers(url)
    assert headers["List-Unsubscribe"] == f"<{url}>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


# =====================================================================
# 3. NotificationConsentManager İş Mantığı ve DB Senkronizasyonu
# =====================================================================


def test_consent_manager_grant_and_revoke(db_session: DBSession) -> None:
    """Rıza verme, rıza iptal etme ve veritabanı senkronizasyonu doğrulanmalıdır."""
    manager = NotificationConsentManager()

    # Kullanıcı oluştur
    user = User(email="test_consent@example.com", hashed_password="dummy_hash")
    db_session.add(user)
    db_session.commit()

    # Başlangıçta varsayılan izin durumu
    assert manager.is_notification_permitted(user.id, channel="email", db=db_session) is True

    # Rıza iptali (revoke)
    revoke_res = manager.revoke_consent(
        user_id=user.id,
        channel="email",
        scope="all",
        reason="user_opt_out",
        source_ip="192.168.1.100",
        db=db_session,
    )
    assert revoke_res.success is True
    assert manager.is_notification_permitted(user.id, channel="email", db=db_session) is False

    # Veritabanı UserPreference tablosu kontrolü
    pref = db_session.execute(
        select(UserPreference).where(UserPreference.user_id == user.id)
    ).scalar_one()
    assert pref.email_notifications is False

    # AuditEvent kaydı kontrolü
    audit = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.user_id == user.id,
            AuditEvent.action == "NOTIFICATION_CONSENT_REVOKED",
        )
    ).scalar_one()
    import hashlib

    expected_hash = hashlib.sha256(b"192.168.1.100").hexdigest()[:16]
    assert audit.ip_hash == expected_hash

    # Tekrar rıza verme (grant)
    grant_record = manager.grant_consent(
        user_id=user.id,
        channel="email",
        scope="all",
        source_ip="192.168.1.100",
        db=db_session,
    )
    assert grant_record.is_granted is True
    assert manager.is_notification_permitted(user.id, channel="email", db=db_session) is True

    pref_after = db_session.execute(
        select(UserPreference).where(UserPreference.user_id == user.id)
    ).scalar_one()
    assert pref_after.email_notifications is True


def test_process_one_click_unsubscribe_with_token(db_session: DBSession) -> None:
    """Token ile tek tıkla abonelikten çıkma süreci."""
    manager = NotificationConsentManager()

    user = User(email="oneclick@example.com", hashed_password="dummy_hash")
    db_session.add(user)
    db_session.commit()

    token = generate_unsubscribe_token(
        user_id=user.id,
        email="oneclick@example.com",
        scope="all",
    )

    result = manager.process_one_click_unsubscribe(token, source_ip="10.0.0.1", db=db_session)
    assert result.success is True
    assert result.user_id == user.id
    assert result.masked_email == "o***@example.com"
    assert manager.is_notification_permitted(user.id, channel="email", db=db_session) is False


def test_scope_specific_rule_unsubscribe(db_session: DBSession) -> None:
    """Belirli bir alarm kuralına özel abonelikten çıkma."""
    manager = NotificationConsentManager()

    user = User(email="rule_unsub@example.com", hashed_password="dummy_hash")
    db_session.add(user)
    db_session.commit()

    inst = Instrument(symbol="THYAO", name="Türk Hava Yolları", market="BIST")
    db_session.add(inst)
    db_session.commit()

    rule = AlertRule(
        user_id=user.id,
        instrument_id=inst.id,
        condition_type="RSI_ABOVE",
        threshold=70.0,
        is_active=True,
    )
    db_session.add(rule)
    db_session.commit()

    token = generate_unsubscribe_token(
        user_id=user.id,
        email="rule_unsub@example.com",
        alert_id=str(rule.id),
        scope="alert_rule",
    )

    result = manager.process_one_click_unsubscribe(token, db=db_session)
    assert result.success is True
    assert result.scope == "alert_rule"

    # İlgili kural pasife çekilmeli
    refreshed_rule = db_session.execute(
        select(AlertRule).where(AlertRule.id == rule.id)
    ).scalar_one()
    assert refreshed_rule.is_active is False


# =====================================================================
# 4. E-posta Şablonu Entegrasyonu Testleri
# =====================================================================


def test_email_composer_renders_unsubscribe_link_and_headers() -> None:
    """E-posta oluşturucu unsubscribe_url sağlandığında RFC 8058 başlıkları ve link eklemelidir."""
    unsub_url = "https://app.boraai.com/api/v1/alerts/unsubscribe?token=test_tok_123"

    payload = AlertNotificationPayload(
        alert_id="alt-abc-123",
        symbol="ASELS",
        rule_name="MACD Kesişimi",
        condition_summary="MACD çizgisi sinyal çizgisini yukarı kesti",
        data_timestamp_utc=datetime(2026, 10, 24, 16, 0, 0, tzinfo=UTC),
        data_source="BIST Gün Sonu (EOD)",
        recipient_email="yatirimci@example.com",
        unsubscribe_url=unsub_url,
    )

    composer = AlertEmailComposer()
    notification = composer.compose(payload)

    # Başlıklar
    assert notification.unsubscribe_url == unsub_url
    assert notification.headers["List-Unsubscribe"] == f"<{unsub_url}>"
    assert notification.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    # Düz Metin
    assert "ABONELİKTEN ÇIKMA:" in notification.plaintext_body
    assert unsub_url in notification.plaintext_body

    # HTML
    assert "tek tıkla abonelikten çıkabilirsiniz" in notification.html_body
    assert unsub_url in notification.html_body


# =====================================================================
# 5. FastAPI HTTP Uç Noktaları Testleri
# =====================================================================


@pytest.fixture
def test_app(db_session: DBSession) -> FastAPI:
    app = create_app()

    def override_get_db() -> Generator[DBSession, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture
def client(test_app: FastAPI) -> Generator[TestClient, None, None]:
    test_client = TestClient(test_app)
    yield test_client
    test_app.dependency_overrides.clear()


def test_api_one_click_unsubscribe_post_success(client: TestClient, db_session: DBSession) -> None:
    """POST /api/v1/alerts/unsubscribe tek tıkla başarılı yanıt dönmelidir."""
    user = User(email="api_unsub@example.com", hashed_password="dummy_hash")
    db_session.add(user)
    db_session.commit()

    token = generate_unsubscribe_token(user_id=user.id, email="api_unsub@example.com")

    resp = client.post(f"/api/v1/alerts/unsubscribe?token={token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["masked_email"] == "a***@example.com"
    assert "başarıyla iptal edildi" in data["message"]


def test_api_one_click_unsubscribe_post_invalid_token(client: TestClient) -> None:
    """POST /api/v1/alerts/unsubscribe geçersiz token durumunda 400 dönmelidir."""
    resp = client.post("/api/v1/alerts/unsubscribe?token=invalid.token")
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"]["code"] == "INVALID_TOKEN"


def test_api_one_click_unsubscribe_get_json_and_html(
    client: TestClient, db_session: DBSession
) -> None:
    """GET /api/v1/alerts/unsubscribe hem JSON hem HTML formatlarında çalışmalıdır."""
    user = User(email="browser_unsub@example.com", hashed_password="dummy_hash")
    db_session.add(user)
    db_session.commit()

    token = generate_unsubscribe_token(user_id=user.id, email="browser_unsub@example.com")

    # JSON formatı
    resp_json = client.get(f"/api/v1/alerts/unsubscribe?token={token}&format=json")
    assert resp_json.status_code == 200
    assert resp_json.json()["success"] is True

    # HTML formatı
    token2 = generate_unsubscribe_token(user_id=user.id, email="browser_unsub@example.com")
    resp_html = client.get(f"/api/v1/alerts/unsubscribe?token={token2}&format=html")
    assert resp_html.status_code == 200
    assert "text/html" in resp_html.headers["content-type"]
    assert "Aboneliğiniz İptal Edildi" in resp_html.text


def test_api_user_consent_endpoints(
    client: TestClient, test_app: FastAPI, db_session: DBSession
) -> None:
    """Giriş yapmış kullanıcının bildirim izni verme ve sorgulama uç noktaları."""
    user = User(email="consent_api@example.com", hashed_password="dummy_hash")
    db_session.add(user)
    db_session.commit()

    # Kullanıcı oturumu taklit et
    auth_user = AuthenticatedUser(user_id=user.id, email=user.email, role="USER")
    test_app.dependency_overrides[get_current_user] = lambda: auth_user

    # İzin kapatma (opt-out)
    resp = client.post(
        "/api/v1/alerts/consent",
        json={"channel": "email", "is_granted": False, "scope": "all", "reason": "user_settings"},
    )
    assert resp.status_code == 200
    assert resp.json()["is_granted"] is False

    # İzin durumu sorgulama
    status_resp = client.get("/api/v1/alerts/consent/status?channel=email")
    assert status_resp.status_code == 200
    assert status_resp.json()["is_granted"] is False

    # İzin tekrar açma (opt-in)
    resp_opt_in = client.post(
        "/api/v1/alerts/consent",
        json={"channel": "email", "is_granted": True, "scope": "all"},
    )
    assert resp_opt_in.status_code == 200
    assert resp_opt_in.json()["is_granted"] is True
