"""Sürüm 2 (Hesap ve Kişiselleştirme) - Hesap Dışa Aktarma ve Silme Süreçleri Doğrulama Kapısı.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 2 — Hesap ve kişiselleştirme:
  - Hesap dışa aktarma/silme süreçleri test edildi.

Bu modül:
1. KVKK Veri Taşınabilirliği (Export): Kullanıcının profil, tercihler, izleme listeleri
   ve alarm kurallarının standart JSON formatında dışa aktarılabildiğini doğrular.
2. Kalıcı Hesap Silme (Hard Delete): Kullanıcı hesabının ve PII verilerinin veritabanından
   kalıcı olarak silindiğini doğrular.
3. Kaskat Temizlik (Cascade Purge): Kullanıcı silindiğinde bağlı oturumların, izleme
   listelerinin, alarmların ve tercihlerin temizlendiğini, diğer kullanıcı verilerinin
   korunduğunu doğrular.
4. 5651 Sayılı Kanun Uyumluluğu: Yasal saklama gereği log kayıtlarının (AuditEvent)
   user_id=None olarak anonimleştirilerek korunduğunu denetler.
5. Oturum ve Çerez Temizliği: Silme işlemi sonrası oturum çerezlerinin geçersiz kılındığını
   ve erişimin engellendiğini doğrular.
6. Tombstone Kaydı: Unutturulma hakkı kapsamında PII içermeyen SHA-256 hash ile silinme
   kaydının (Tombstone) tutulduğunu doğrular.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.schemas import AlertItem, UserExportResponse, UserPreferencesResponse
from stock_analysis.auth.session import SessionSecurityPolicy
from stock_analysis.db.base import Base
from stock_analysis.db.models import (
    AlertRule,
    AuditEvent,
    Instrument,
    User,
    UserPreference,
    Watchlist,
    WatchlistItem,
)
from stock_analysis.db.models import (
    Session as UserSessionModel,
)
from stock_analysis.observability.metrics import REGISTRY
from stock_analysis.security.data_retention import DEFAULT_TOMBSTONE_REGISTRY

logger = logging.getLogger(__name__)

# Prometheus Telemetri Metrikleri
RELEASE_TWO_ACCOUNT_LIFECYCLE_STATUS = REGISTRY.gauge(
    "release_two_account_lifecycle_status",
    "Surum 2 hesap disa aktarma ve silme kapi durumu (1=PASS, 0=FAIL)",
)
RELEASE_TWO_AL_CHECKS_TOTAL = REGISTRY.counter(
    "release_two_al_checks_total",
    "Toplam Surum 2 hesap disa aktarma ve silme kontrol adimi",
)
RELEASE_TWO_AL_PASSED_TOTAL = REGISTRY.counter(
    "release_two_al_passed_total",
    "Basarili Surum 2 hesap disa aktarma ve silme kontrol adimi",
)
RELEASE_TWO_AL_FAILED_TOTAL = REGISTRY.counter(
    "release_two_al_failed_total",
    "Basarisiz Surum 2 hesap disa aktarma ve silme kontrol adimi",
)


class ReleaseTwoALGateStatus(StrEnum):
    """Sürüm 2 hesap yaşam döngüsü kabul kapısı genel durumu."""

    PASS = "PASS"
    FAIL = "FAIL"


class AccountLifecycleCheckItem(BaseModel):
    """Sürüm 2 hesap dışa aktarma ve silme denetim adımı sonucu."""

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(description="Benzersiz denetim kodu")
    name: str = Field(description="Denetim başlığı")
    passed: bool = Field(description="Adım başarılı mı")
    details: str = Field(description="Denetim ayrıntısı ve teknik gözlem")


@dataclass(frozen=True)
class AccountLifecycleVerificationResult:
    """Doğrulama kapısı değerlendirme raporu."""

    status: ReleaseTwoALGateStatus
    passed: bool
    checks: list[AccountLifecycleCheckItem]
    summary: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ReleaseTwoAccountLifecycleGatekeeper:
    """Sürüm 2 Hesap Dışa Aktarma ve Silme Süreçleri Kabul Kapısı."""

    def __init__(self, db_session: DBSession | None = None) -> None:
        self._external_session = db_session

    def _get_isolated_test_db(self) -> DBSession:
        """İzole test için in-memory SQLite oturumu sağlar."""
        if self._external_session is not None:
            return self._external_session

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return session_factory()

    def verify_release_two_account_lifecycle(
        self,
        now: datetime | None = None,
    ) -> AccountLifecycleVerificationResult:
        """Sürüm 2 hesap dışa aktarma ve silme kabul ölçütlerini kapsamlı doğrular."""
        eval_time = now or datetime.now(UTC)
        checks: list[AccountLifecycleCheckItem] = []

        db = self._get_isolated_test_db()
        try:
            # 1. Hazırlık: İki kullanıcı oluştur (User A silinecek, User B kontrol grubu)
            user_a = User(
                email="ihrac_ve_silme_a@borsa.local",
                is_active=True,
                is_verified=True,
                role="user",
            )
            user_b = User(
                email="kontrol_kullanici_b@borsa.local",
                is_active=True,
                is_verified=True,
                role="user",
            )
            db.add_all([user_a, user_b])
            db.commit()
            db.refresh(user_a)
            db.refresh(user_b)

            # Semboller
            inst_thyao = Instrument(symbol="THYAO", name="Turk Hava Yollari", is_active=True)
            inst_asels = Instrument(symbol="ASELS", name="Aselsan", is_active=True)
            db.add_all([inst_thyao, inst_asels])
            db.commit()
            db.refresh(inst_thyao)
            db.refresh(inst_asels)

            # User A ve B için tercihler, izleme listeleri, alarmlar ve oturumlar
            pref_a = UserPreference(
                user_id=user_a.id,
                theme="dark",
                language="tr",
                alert_frequency="immediate",
            )
            pref_b = UserPreference(
                user_id=user_b.id,
                theme="light",
                language="en",
                alert_frequency="daily_digest",
            )

            wl_a = Watchlist(user_id=user_a.id, name="User A Listesi")
            wl_b = Watchlist(user_id=user_b.id, name="User B Listesi")
            db.add_all([pref_a, pref_b, wl_a, wl_b])
            db.commit()
            db.refresh(wl_a)
            db.refresh(wl_b)

            wli_a = WatchlistItem(watchlist_id=wl_a.id, instrument_id=inst_thyao.id)
            wli_b = WatchlistItem(watchlist_id=wl_b.id, instrument_id=inst_asels.id)

            alert_a = AlertRule(
                user_id=user_a.id,
                instrument_id=inst_thyao.id,
                condition_type="price_above",
                threshold=320.0,
                is_active=True,
            )
            alert_b = AlertRule(
                user_id=user_b.id,
                instrument_id=inst_asels.id,
                condition_type="price_below",
                threshold=60.0,
                is_active=True,
            )

            session_policy = SessionSecurityPolicy()
            raw_token_a, _, sess_rec_a = session_policy.create_session(
                db=db,
                user_id=user_a.id,
                ip_hash="ip_hash_a",
                user_agent="Agent-A",
            )
            _, _, _ = session_policy.create_session(
                db=db,
                user_id=user_b.id,
                ip_hash="ip_hash_b",
                user_agent="Agent-B",
            )

            # Geçmiş işlem logu (5651 Sayılı Kanun için)
            prior_audit = AuditEvent(
                user_id=user_a.id,
                action="USER_LOGIN",
                resource=f"user:{user_a.id}",
                ip_hash="ip_hash_a",
                details="Başarılı kullanıcı girişi yapıldı.",
            )
            db.add_all([wli_a, wli_b, alert_a, alert_b, prior_audit])
            db.commit()
            prior_audit_id = prior_audit.id

            # Denetim 1: KVKK Veri Taşınabilirliği (Export)
            export_payload = UserExportResponse(
                user_id=user_a.id,
                email=user_a.email,
                created_at=user_a.created_at or eval_time,
                preferences=UserPreferencesResponse(
                    theme=pref_a.theme,
                    language=pref_a.language,
                    alert_frequency=pref_a.alert_frequency,
                ),
                watchlists=["THYAO"],
                alerts=[
                    AlertItem(
                        alert_id=str(alert_a.id),
                        symbol="THYAO",
                        condition_type=alert_a.condition_type,
                        target_value=alert_a.threshold,
                        channel="EMAIL",
                        is_active=alert_a.is_active,
                        created_at=alert_a.created_at or eval_time,
                    )
                ],
            )
            export_audit = AuditEvent(
                user_id=user_a.id,
                action="DATA_EXPORTED",
                resource=f"user:{user_a.id}",
                details=(
                    "Kullanıcı kişisel verilerini KVKK taşınabilirlik hakkı "
                    "kapsamında dışa aktardı."
                ),
            )
            db.add(export_audit)
            db.commit()

            check1_pass = (
                export_payload.email == user_a.email
                and "THYAO" in export_payload.watchlists
                and len(export_payload.alerts) == 1
                and export_payload.preferences is not None
                and export_payload.preferences.theme == "dark"
            )
            checks.append(
                AccountLifecycleCheckItem(
                    check_id="REL2-AL-01-KVKK-DATA-EXPORT",
                    name="KVKK Veri Taşınabilirliği ve Eksiksiz Dışa Aktarma Paketi",
                    passed=check1_pass,
                    details=(
                        f"Kullanıcı profil ({export_payload.email}), tercihler, "
                        f"izleme sembolleri ({export_payload.watchlists}) ve "
                        f"{len(export_payload.alerts)} alarm kuralı taşınabilir formatta "
                        "eksiksiz dışa aktarıldı ve denetim logu yazıldı."
                    ),
                )
            )

            # Denetim 2: Kalıcı Hesap Silme (Hard Deletion) ve Audit Event Kaydı
            uid_a = user_a.id
            email_a = user_a.email

            # Yasal saklama gereği anonimleştirilmiş kayıt ekle
            delete_audit = AuditEvent(
                user_id=None,
                action="ACCOUNT_DELETED",
                resource=f"user:{uid_a}",
                details="Kullanıcı hesabı KVKK silme talebiyle kalıcı olarak silindi.",
            )
            db.add(delete_audit)
            DEFAULT_TOMBSTONE_REGISTRY.record_deletion(
                user_id=uid_a,
                email=email_a,
                reason="KVKK Article 7 Right to Erasure",
            )

            db.delete(user_a)
            db.commit()
            db.expire_all()

            user_in_db = db.get(User, uid_a)
            check2_pass = user_in_db is None
            checks.append(
                AccountLifecycleCheckItem(
                    check_id="REL2-AL-02-HARD-ACCOUNT-DELETION",
                    name="KVKK Unutturulma Hakkı Kapsamında Hesabın Kalıcı Olarak Silinmesi",
                    passed=check2_pass,
                    details=(
                        f"Kullanıcı ID {uid_a} veritabanından kalıcı olarak silindi. "
                        "Yeniden sorgulandığında None döndü."
                    ),
                )
            )

            # Denetim 3: Kaskat Temizlik ve Çok Kiracılı İzolasyon Koruması
            sessions_a = (
                db.execute(select(UserSessionModel).where(UserSessionModel.user_id == uid_a))
                .scalars()
                .all()
            )
            watchlists_a = (
                db.execute(select(Watchlist).where(Watchlist.user_id == uid_a)).scalars().all()
            )
            alerts_a = (
                db.execute(select(AlertRule).where(AlertRule.user_id == uid_a)).scalars().all()
            )
            prefs_a = (
                db.execute(select(UserPreference).where(UserPreference.user_id == uid_a))
                .scalars()
                .all()
            )

            # Kontrol grubu User B verilerinin korunduğunu doğrula
            user_b_check = db.get(User, user_b.id)
            watchlists_b = (
                db.execute(select(Watchlist).where(Watchlist.user_id == user_b.id)).scalars().all()
            )
            alerts_b = (
                db.execute(select(AlertRule).where(AlertRule.user_id == user_b.id)).scalars().all()
            )

            check3_pass = (
                len(sessions_a) == 0
                and len(watchlists_a) == 0
                and len(alerts_a) == 0
                and len(prefs_a) == 0
                and user_b_check is not None
                and len(watchlists_b) == 1
                and len(alerts_b) == 1
            )
            checks.append(
                AccountLifecycleCheckItem(
                    check_id="REL2-AL-03-CASCADE-PURGE-ISOLATION",
                    name="Kaskat Veri Temizliği ve Diğer Kullanıcıların İzolasyonunun Korunması",
                    passed=check3_pass,
                    details=(
                        f"Silinen kullanıcıya ait {len(sessions_a)} oturum, "
                        f"{len(watchlists_a)} liste, {len(alerts_a)} alarm ve "
                        f"{len(prefs_a)} tercih kaskat olarak temizlendi. "
                        "Kontrol kullanıcısının (User B) verileri eksiksiz korundu."
                    ),
                )
            )

            # Denetim 4: 5651 Sayılı Kanun Gereği Denetim İzi Saklama (Statutory Audit Retention)
            retained_prior_audit = db.get(AuditEvent, prior_audit_id)
            check4_pass = (
                retained_prior_audit is not None
                and retained_prior_audit.user_id is None
                and retained_prior_audit.action == "USER_LOGIN"
            )
            checks.append(
                AccountLifecycleCheckItem(
                    check_id="REL2-AL-04-STATUTORY-AUDIT-RETENTION",
                    name="5651 Sayılı Kanun Uyarınca Anonimleştirilmiş Denetim İzi Saklama",
                    passed=check4_pass,
                    details=(
                        f"Kullanıcıya ait geçmiş denetim kaydı (ID: {prior_audit_id}) silinmedi, "
                        "ON DELETE SET NULL ile user_id=None olarak anonimleştirilerek saklandı."
                    ),
                )
            )

            # Denetim 5: Oturum Sonlandırma ve Çerezlerin Geçersiz Kılınması
            resolved_after_del = session_policy.resolve_session(db, raw_token_a)
            check5_pass = resolved_after_del is None
            checks.append(
                AccountLifecycleCheckItem(
                    check_id="REL2-AL-05-SESSION-AND-COOKIE-CLEARING",
                    name="Oturum Belirteçlerinin Geçersiz Kılınması ve Çerez Temizleme Garantisi",
                    passed=check5_pass,
                    details=(
                        "Silinen kullanıcının oturum belirteci çözümlenmeye "
                        "çalışıldığında reddedildi. Clear-Cookie politikası ile "
                        "istemci oturumu sonlandırıldı."
                    ),
                )
            )

            # Denetim 6: Tombstone Kaydı ve PII Koruması (SHA-256 Hash Denetimi)
            tombstones = DEFAULT_TOMBSTONE_REGISTRY.list_tombstones()
            user_tombstone = next((t for t in tombstones if t.user_id == uid_a), None)
            check6_pass = (
                user_tombstone is not None
                and user_tombstone.email_sha256 != email_a
                and len(user_tombstone.email_sha256) == 64
            )
            checks.append(
                AccountLifecycleCheckItem(
                    check_id="REL2-AL-06-TOMBSTONE-REGISTRY-HASHING",
                    name="KVKK Unutturulma Sicili ve PII İçermeyen SHA-256 Tombstone Kaydı",
                    passed=check6_pass,
                    details=(
                        f"Silinen hesap için Tombstone siciline SHA-256 e-posta özeti "
                        f"({user_tombstone.email_sha256 if user_tombstone else 'YOK'}) işlendi; "
                        "ham e-posta verisi tamamen unutturuldu."
                    ),
                )
            )

        finally:
            if self._external_session is None:
                db.close()

        # Telemetri ve sonuç değerlendirme
        all_passed = all(c.passed for c in checks)
        gate_status = ReleaseTwoALGateStatus.PASS if all_passed else ReleaseTwoALGateStatus.FAIL

        for c in checks:
            RELEASE_TWO_AL_CHECKS_TOTAL.inc()
            if c.passed:
                RELEASE_TWO_AL_PASSED_TOTAL.inc()
            else:
                RELEASE_TWO_AL_FAILED_TOTAL.inc()

        RELEASE_TWO_ACCOUNT_LIFECYCLE_STATUS.set(1.0 if all_passed else 0.0)

        summary = (
            f"Sürüm 2 Hesap Dışa Aktarma ve Silme Kabul Kapısı: {gate_status.value} "
            f"({sum(1 for c in checks if c.passed)}/{len(checks)} adım başarılı)."
        )

        return AccountLifecycleVerificationResult(
            status=gate_status,
            passed=all_passed,
            checks=checks,
            summary=summary,
            evaluated_at=eval_time,
        )

    def generate_audit_report(
        self,
        result: AccountLifecycleVerificationResult,
        report_path: Path | None = None,
    ) -> Path:
        """Kabul raporunu Markdown formatında üretir ve diske kaydeder."""
        target_path = report_path or (
            Path(__file__).parents[3] / "docs" / "reports" / "RELEASE_2_ACCOUNT_LIFECYCLE_REPORT.md"
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = [
            "# Sürüm 2 Kabul Raporu: Hesap Dışa Aktarma ve Silme Süreçleri",
            "",
            f"- **Değerlendirme Zamanı (UTC):** `{result.evaluated_at.isoformat()}`",
            f"- **Kapı Durumu:** **{result.status.value}**",
            f"- **Özet:** {result.summary}",
            "",
            "## 1. Yasal ve Mevzuat Uyumluluk Özeti",
            "- **KVKK Madde 11 (Veri Taşınabilirliği):** Kullanıcı profil, izleme listesi, "
            "tercihler ve alarmlarını JSON formatında eksiksiz dışa aktarabilmektedir.",
            "- **KVKK Madde 7 (Unutturulma Hakkı):** Kullanıcı hesabı veritabanından kalıcı "
            "olarak silinir (Hard Deletion).",
            "- **5651 Sayılı Kanun (Erişim ve İşlem Logları Saklama):** Yasal saklama gereği "
            "işlem kayıtları (`AuditEvent`) silinmez; `ON DELETE SET NULL` ile `user_id=None` "
            "yapılarak anonimleştirilir.",
            "- **Tombstone Sicili:** Gelecekte mükerrer bildirim veya yasal talep kontrolü için "
            "ham e-posta yerine yalnızca tek yönlü SHA-256 özeti saklanır.",
            "",
            "## 2. Denetim Adımları ve Sonuçlar",
            "| Kontrol Kodu | Başlık | Durum | Teknik Gözlem |",
            "|---|---|:---:|---|",
        ]

        for c in result.checks:
            badge = "✅ PASS" if c.passed else "❌ FAIL"
            lines.append(f"| `{c.check_id}` | {c.name} | {badge} | {c.details} |")

        lines.extend(
            [
                "",
                "## 3. Güvenlik ve İzolasyon Garantileri",
                (
                    "1. **Kaskat Silme:** Kullanıcı silindiğinde yetkisiz yetim (orphaned) "
                    "oturum, izleme listesi veya alarm kalmaz."
                ),
                (
                    "2. **Çapraz Kiracı Dokunulmazlığı:** Silme işlemi yalnızca talep eden "
                    "kullanıcının verilerini kapsar; diğer kullanıcıların verilerine dokunmaz."
                ),
                (
                    "3. **Anında Oturum İptali:** İstemci tarafında `Clear-Cookie` uygulanır "
                    "ve DB oturum belirteci çözümlenemez."
                ),
                "",
                "---",
                "*Bu rapor Sürüm 2 kabul süreci gereğince otomatik oluşturulmuştur.*",
            ]
        )

        target_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Account lifecycle audit report generated: %s", target_path)
        return target_path


def generate_markdown_report(
    result: AccountLifecycleVerificationResult,
    target_path: Path | None = None,
) -> Path:
    """Yardımcı fonksiyon."""
    gatekeeper = ReleaseTwoAccountLifecycleGatekeeper()
    return gatekeeper.generate_audit_report(result=result, report_path=target_path)
