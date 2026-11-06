"""Security incident response plan, contact directory, and recovery workflows.

Compliant with NIST SP 800-61 Rev. 2, ISO/IEC 27035, KVKK Law No. 6698 Art. 12
(72-hour notification rule), and Capital Markets Board (SPK) Information Systems
Regulations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class IncidentSeverity(StrEnum):
    """Incident severity classification based on impact and urgency."""

    SEV_1_CRITICAL = (
        "SEV_1_CRITICAL"  # Active data breach, wiper/ransomware, full outage, compromised admin
    )
    SEV_2_HIGH = (
        "SEV_2_HIGH"  # Partial outage, credential compromise, volumetric attack, auth bypass
    )
    SEV_3_MEDIUM = "SEV_3_MEDIUM"  # Suspicious bot flood, single user brute-force, isolated anomaly
    SEV_4_LOW = "SEV_4_LOW"  # False positive triage, minor policy alert, non-impacting scan


class IncidentCategory(StrEnum):
    """Categorization of security incidents."""

    DATA_BREACH = "DATA_BREACH"
    ACCOUNT_TAKEOVER = "ACCOUNT_TAKEOVER"
    PRIVILEGE_ESCALATION = "PRIVILEGE_ESCALATION"
    SUPPLY_CHAIN = "SUPPLY_CHAIN"
    DENIAL_OF_SERVICE = "DENIAL_OF_SERVICE"
    INTEGRITY_COMPROMISE = "INTEGRITY_COMPROMISE"
    SUSPICIOUS_ACTIVITY = "SUSPICIOUS_ACTIVITY"


class IncidentStage(StrEnum):
    """Six-phase PICERL incident response lifecycle."""

    PREPARATION = "PREPARATION"
    IDENTIFICATION = "IDENTIFICATION"
    CONTAINMENT = "CONTAINMENT"
    ERADICATION = "ERADICATION"
    RECOVERY = "RECOVERY"
    LESSONS_LEARNED = "LESSONS_LEARNED"


class ContactRole(StrEnum):
    """Defined roles within the Security Incident Response Team (SIRT)."""

    INCIDENT_COMMANDER = "INCIDENT_COMMANDER"
    SECURITY_LEAD = "SECURITY_LEAD"
    LEGAL_COMPLIANCE = "LEGAL_COMPLIANCE"
    COMMUNICATIONS_LEAD = "COMMUNICATIONS_LEAD"
    INFRASTRUCTURE_LEAD = "INFRASTRUCTURE_LEAD"


class ContainmentAction(StrEnum):
    """Automated and manual containment containment measures."""

    ACTIVATE_KILL_SWITCH = "ACTIVATE_KILL_SWITCH"
    REVOKE_ALL_SESSIONS = "REVOKE_ALL_SESSIONS"
    REVOKE_SECRETS = "REVOKE_SECRETS"
    QUARANTINE_IP = "QUARANTINE_IP"
    READ_ONLY_LOCKDOWN = "READ_ONLY_LOCKDOWN"
    ISOLATE_SERVICE = "ISOLATE_SERVICE"


class IncidentWorkflowError(Exception):
    """Raised when an invalid state transition or operation occurs in incident response."""


@dataclass(frozen=True)
class IncidentContact:
    """Contact entry for an incident team member."""

    name: str
    role: ContactRole
    email: str
    phone: str
    is_primary: bool = True
    backup_name: str | None = None
    backup_email: str | None = None


@dataclass
class IncidentContactDirectory:
    """Directory of SIRT personnel, escalation paths, and external authorities."""

    contacts: dict[ContactRole, list[IncidentContact]] = field(default_factory=dict)
    external_authorities: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def get_default_directory(cls) -> IncidentContactDirectory:
        """Instantiates default canonical contact directory."""
        directory = cls()
        directory.contacts = {
            ContactRole.INCIDENT_COMMANDER: [
                IncidentContact(
                    name="CISO / Baş Güvenlik Sorumlusu",
                    role=ContactRole.INCIDENT_COMMANDER,
                    email="ciso@bora-analiz.local",
                    phone="+90-212-555-0101",
                    is_primary=True,
                    backup_name="Teknoloji Direktörü (CTO)",
                    backup_email="cto@bora-analiz.local",
                )
            ],
            ContactRole.SECURITY_LEAD: [
                IncidentContact(
                    name="Kıdemli Güvenlik Mühendisi (SecOps)",
                    role=ContactRole.SECURITY_LEAD,
                    email="secops@bora-analiz.local",
                    phone="+90-212-555-0102",
                    is_primary=True,
                    backup_name="Güvenlik Analisti",
                    backup_email="sec-analyst@bora-analiz.local",
                )
            ],
            ContactRole.LEGAL_COMPLIANCE: [
                IncidentContact(
                    name="Uyum ve Hukuk Müşaviri (KVKK/SPK)",
                    role=ContactRole.LEGAL_COMPLIANCE,
                    email="compliance@bora-analiz.local",
                    phone="+90-212-555-0103",
                    is_primary=True,
                    backup_name="Dış Hukuk Danışmanı",
                    backup_email="legal-ext@bora-analiz.local",
                )
            ],
            ContactRole.COMMUNICATIONS_LEAD: [
                IncidentContact(
                    name="Kurumsal İletişim Yöneticisi",
                    role=ContactRole.COMMUNICATIONS_LEAD,
                    email="comms@bora-analiz.local",
                    phone="+90-212-555-0104",
                    is_primary=True,
                    backup_name="Destek ve Operasyon Lideri",
                    backup_email="support-lead@bora-analiz.local",
                )
            ],
            ContactRole.INFRASTRUCTURE_LEAD: [
                IncidentContact(
                    name="Site Reliability Engineer (DevOps Lideri)",
                    role=ContactRole.INFRASTRUCTURE_LEAD,
                    email="sre-lead@bora-analiz.local",
                    phone="+90-212-555-0105",
                    is_primary=True,
                    backup_name="Veritabanı Yöneticisi (DBA)",
                    backup_email="dba@bora-analiz.local",
                )
            ],
        }
        directory.external_authorities = {
            "KVKK": {
                "name": "Kişisel Verileri Koruma Kurumu",
                "portal": "https://ihlal.kvkk.gov.tr",
                "phone": "+90-312-216-5000",
                "sla_hours": "72",
                "basis": "KVKK Kanunu m. 12/5",
            },
            "SPK": {
                "name": "Sermaye Piyasası Kurulu Bilgi Sistemleri",
                "email": "bilgisistemleri@spk.gov.tr",
                "phone": "+90-212-334-5500",
                "sla_hours": "24",
                "basis": "VII-128.9 Bilgi Sistemleri Yönetimi Tebliği",
            },
            "USOM": {
                "name": "Ulusal Siber Olaylara Müdahale Merkezi",
                "portal": "https://www.usom.gov.tr/bildir",
                "email": "ihbar@usom.gov.tr",
                "phone": "+90-312-582-8282",
                "sla_hours": "24",
                "basis": "Siber Güvenlik Kurulu Mevzuatı",
            },
        }
        return directory

    def get_primary(self, role: ContactRole) -> IncidentContact:
        """Returns the primary on-call contact for a specific SIRT role."""
        if role not in self.contacts or not self.contacts[role]:
            raise IncidentWorkflowError(f"Tanımlı {role.value} sorumlusu bulunamadı.")
        for contact in self.contacts[role]:
            if contact.is_primary:
                return contact
        return self.contacts[role][0]

    def get_escalation_chain(self, role: ContactRole) -> list[IncidentContact]:
        """Returns full escalation chain including primary and secondary contacts."""
        return self.contacts.get(role, [])


@dataclass
class ContainmentExecutionResult:
    """Result of an executed containment measure."""

    action: ContainmentAction
    executed_at: datetime
    success: bool
    operator: str
    details: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LegalComplianceDeadline:
    """Legal notification requirements with countdown timestamps."""

    regulatory_body: str
    deadline_hours: int
    due_at: datetime
    status: str = "PENDING"  # PENDING, SUBMITTED, EXEMPT, OVERDUE
    submitted_at: datetime | None = None
    submission_reference: str | None = None
    notes: str | None = None

    def is_overdue(self, as_of: datetime | None = None) -> bool:
        """Checks if deadline has elapsed without submission."""
        now = as_of or datetime.now(UTC)
        return self.status == "PENDING" and now > self.due_at


@dataclass
class IncidentTimelineEntry:
    """Historical timeline event in incident lifecycle."""

    timestamp: datetime
    stage: IncidentStage
    actor: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IncidentRecord:
    """Core tracking document for a security incident."""

    incident_id: str
    title: str
    category: IncidentCategory
    severity: IncidentSeverity
    current_stage: IncidentStage
    lead_commander: str
    detected_at: datetime
    created_at: datetime
    affected_systems: list[str]
    is_pii_affected: bool
    timeline: list[IncidentTimelineEntry] = field(default_factory=list)
    containment_actions: list[ContainmentExecutionResult] = field(default_factory=list)
    legal_deadlines: list[LegalComplianceDeadline] = field(default_factory=list)
    closed_at: datetime | None = None
    root_cause: str | None = None
    remediation_notes: str | None = None
    affected_user_count: int = 0


# Allowed stage transitions ensuring PICERL discipline
VALID_STAGE_TRANSITIONS: dict[IncidentStage, set[IncidentStage]] = {
    IncidentStage.PREPARATION: {IncidentStage.IDENTIFICATION},
    IncidentStage.IDENTIFICATION: {
        IncidentStage.CONTAINMENT,
        IncidentStage.LESSONS_LEARNED,
    },  # can close if false positive
    IncidentStage.CONTAINMENT: {IncidentStage.ERADICATION},
    IncidentStage.ERADICATION: {IncidentStage.RECOVERY},
    IncidentStage.RECOVERY: {IncidentStage.LESSONS_LEARNED},
    IncidentStage.LESSONS_LEARNED: set(),  # Terminal state
}

# SLA targets (response time and status update frequency)
SEVERITY_SLAS: dict[IncidentSeverity, dict[str, int]] = {
    IncidentSeverity.SEV_1_CRITICAL: {"response_minutes": 15, "update_interval_minutes": 30},
    IncidentSeverity.SEV_2_HIGH: {"response_minutes": 30, "update_interval_minutes": 60},
    IncidentSeverity.SEV_3_MEDIUM: {"response_minutes": 120, "update_interval_minutes": 240},
    IncidentSeverity.SEV_4_LOW: {"response_minutes": 1440, "update_interval_minutes": 1440},
}


class IncidentResponseCoordinator:
    """Coordinates detection, containment, eradication, legal disclosure, and post-mortems."""

    def __init__(self, directory: IncidentContactDirectory | None = None) -> None:
        self.directory = directory or IncidentContactDirectory.get_default_directory()
        self._incidents: dict[str, IncidentRecord] = {}

    def declare_incident(
        self,
        title: str,
        category: IncidentCategory,
        severity: IncidentSeverity,
        lead_commander: str,
        affected_systems: list[str],
        is_pii_affected: bool = False,
        detected_at: datetime | None = None,
        affected_user_count: int = 0,
    ) -> IncidentRecord:
        """Declares and registers a new security incident."""
        now = datetime.now(UTC)
        det_time = detected_at or now

        # Generate unique incident tracking identifier
        incident_id = f"INC-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        record = IncidentRecord(
            incident_id=incident_id,
            title=title,
            category=category,
            severity=severity,
            current_stage=IncidentStage.IDENTIFICATION,
            lead_commander=lead_commander,
            detected_at=det_time,
            created_at=now,
            affected_systems=affected_systems,
            is_pii_affected=is_pii_affected,
            affected_user_count=affected_user_count,
        )

        # Initial timeline entry
        record.timeline.append(
            IncidentTimelineEntry(
                timestamp=now,
                stage=IncidentStage.IDENTIFICATION,
                actor=lead_commander,
                description=(
                    f"Olay başlatıldı. Şiddet: {severity.value}, Kategori: {category.value}"
                ),
                metadata={"affected_systems": affected_systems, "is_pii_affected": is_pii_affected},
            )
        )

        # Configure statutory legal deadlines if PII or market integrity is compromised
        if is_pii_affected or category == IncidentCategory.DATA_BREACH:
            # KVKK 72-hour mandatory deadline from detection
            from datetime import timedelta

            kvkk_due = det_time + timedelta(hours=72)
            record.legal_deadlines.append(
                LegalComplianceDeadline(
                    regulatory_body="KVKK",
                    deadline_hours=72,
                    due_at=kvkk_due,
                    status="PENDING",
                    notes="KVKK m. 12/5 uyarınca en geç 72 saat içinde Kuruma bildirim zorunludur.",
                )
            )

        if severity in (IncidentSeverity.SEV_1_CRITICAL, IncidentSeverity.SEV_2_HIGH):
            # SPK and USOM 24-hour notification
            from datetime import timedelta

            record.legal_deadlines.append(
                LegalComplianceDeadline(
                    regulatory_body="SPK",
                    deadline_hours=24,
                    due_at=det_time + timedelta(hours=24),
                    status="PENDING",
                    notes="SPK Bilgi Sistemleri Tebliği uyarınca ivedilikle bilgilendirme.",
                )
            )
            record.legal_deadlines.append(
                LegalComplianceDeadline(
                    regulatory_body="USOM",
                    deadline_hours=24,
                    due_at=det_time + timedelta(hours=24),
                    status="PENDING",
                    notes="USOM siber vaka ihbarı.",
                )
            )

        self._incidents[incident_id] = record
        return record

    def get_incident(self, incident_id: str) -> IncidentRecord:
        """Retrieves an incident record by ID."""
        if incident_id not in self._incidents:
            raise IncidentWorkflowError(f"Olay kaydı bulunamadı: {incident_id}")
        return self._incidents[incident_id]

    def transition_stage(
        self,
        incident_id: str,
        target_stage: IncidentStage,
        actor: str,
        notes: str,
    ) -> IncidentRecord:
        """Transitions an incident to a new PICERL lifecycle stage."""
        incident = self.get_incident(incident_id)

        valid_next = VALID_STAGE_TRANSITIONS.get(incident.current_stage, set())
        if target_stage not in valid_next:
            next_stages = [s.value for s in valid_next]
            raise IncidentWorkflowError(
                f"Geçersiz aşama geçişi: {incident.current_stage.value} -> {target_stage.value}. "
                f"İzin verilen sonraki aşamalar: {next_stages}"
            )

        now = datetime.now(UTC)
        incident.current_stage = target_stage

        if target_stage == IncidentStage.LESSONS_LEARNED:
            incident.closed_at = now

        incident.timeline.append(
            IncidentTimelineEntry(
                timestamp=now,
                stage=target_stage,
                actor=actor,
                description=f"Aşama güncellendi: {target_stage.value}. Not: {notes}",
            )
        )
        return incident

    def apply_containment_action(
        self,
        incident_id: str,
        action: ContainmentAction,
        operator: str,
        details: str,
        success: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> ContainmentExecutionResult:
        """Executes and records a containment action against an active incident."""
        incident = self.get_incident(incident_id)

        now = datetime.now(UTC)
        result = ContainmentExecutionResult(
            action=action,
            executed_at=now,
            success=success,
            operator=operator,
            details=details,
            metadata=metadata or {},
        )
        incident.containment_actions.append(result)

        status_str = "Başarılı" if success else "Başarısız"
        incident.timeline.append(
            IncidentTimelineEntry(
                timestamp=now,
                stage=incident.current_stage,
                actor=operator,
                description=(
                    f"Sınırlama tedbiri: {action.value}. Sonuç: {status_str}. Detay: {details}"
                ),
            )
        )
        return result

    def record_legal_submission(
        self,
        incident_id: str,
        regulatory_body: str,
        submission_reference: str,
        submitted_by: str,
        submitted_at: datetime | None = None,
        notes: str | None = None,
    ) -> LegalComplianceDeadline:
        """Marks a regulatory notification as officially submitted."""
        incident = self.get_incident(incident_id)
        now = submitted_at or datetime.now(UTC)

        target_deadline: LegalComplianceDeadline | None = None
        for deadline in incident.legal_deadlines:
            if deadline.regulatory_body.upper() == regulatory_body.upper():
                target_deadline = deadline
                break

        if not target_deadline:
            raise IncidentWorkflowError(
                f"{regulatory_body} için bekleyen bir yasal bildirim gereksinimi bulunamadı."
            )

        target_deadline.status = "SUBMITTED"
        target_deadline.submitted_at = now
        target_deadline.submission_reference = submission_reference
        if notes:
            target_deadline.notes = f"{target_deadline.notes or ''} | {notes}".strip(" |")

        incident.timeline.append(
            IncidentTimelineEntry(
                timestamp=now,
                stage=incident.current_stage,
                actor=submitted_by,
                description=f"{regulatory_body} bildirimi yapıldı. Ref: {submission_reference}",
            )
        )
        return target_deadline

    def generate_kvkk_breach_report(self, incident_id: str) -> dict[str, Any]:
        """Generates structured KVKK Article 12 data breach notification form payload."""
        incident = self.get_incident(incident_id)
        legal_lead = self.directory.get_primary(ContactRole.LEGAL_COMPLIANCE)

        containment_summary = [
            f"{action.action.value} ({action.executed_at.isoformat()}): {action.details}"
            for action in incident.containment_actions
            if action.success
        ]

        return {
            "form_standard": "KVKK-VERI-IHLAL-BILDIRIM-FORMU-V1",
            "veri_sorumlusu": "Borsa Analiz Sistemleri A.Ş.",
            "irtibat_kisisu": {
                "ad_soyad": legal_lead.name,
                "eposta": legal_lead.email,
                "telefon": legal_lead.phone,
            },
            "olay_kimligi": incident.incident_id,
            "ihlal_tespit_tarihi": incident.detected_at.isoformat(),
            "ihlal_kategorisi": incident.category.value,
            "etkilenen_veri_turleri": [
                "E-posta adresleri (maskeli/kriptolu)",
                "Oturum ve erişim kayıtları",
                "Kullanıcı izleme listesi tercihleri",
            ],
            "tahmini_etkilenen_kisi_sayisi": incident.affected_user_count,
            "alinan_teknik_ve_idari_tedbirler": containment_summary
            or ["İnceleme ve tecrit süreci devam ediyor."],
            "ilgili_kisilere_bildirim_durumu": (
                "Etkilenen kullanıcılara doğrudan güvenli e-posta duyurusu iletilecektir."
                if incident.affected_user_count > 0
                else "Değerlendirme aşamasında."
            ),
        }

    def generate_user_advisory_notice(self, incident_id: str) -> str:
        """Generates transparent, compliant public/user advisory notice without speculation."""
        incident = self.get_incident(incident_id)

        notice_lines = [
            "================================================================================",
            "                   GÜVENLİK BİLGİLENDİRMESİ VE KULLANICI UYARISI               ",
            "================================================================================",
            f"Olay Referansı: {incident.incident_id}",
            f"Yayınlanma Tarihi: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "Sayın Kullanıcımız,",
            "",
            "Borsa Analiz Sistemi altyapımızda gerçekleştirilen rutin güvenlik denetimleri ve",
            f"izleme faaliyetleri kapsamında, {incident.title.lower()} ile ilişkili şüpheli bir",
            "aktivite tespit edilmiştir.",
            "",
            "GÜVENLİK TEDBİRLERİ VE GÜNCEL DURUM:",
            "- Sistem savunma kalkanlarımız ve sınırlama protokollerimiz devreye alınmıştır.",
            "- Hassas kimlik doğrulama anahtarları yenilenmiş ve şüpheli oturumlar kapatılmıştır.",
            "- SPK mevzuatı gereği veri bütünlüğü ve analiz motoru güvenliği teyit edilmiştir.",
            "- Parolalarınız açık olarak değil, endüstri standardı Argon2id özetleri olarak",
            "  saklandığından açık parola sızıntısı söz konusu değildir.",
            "",
            "KULLANICILARIMIZIN ALMASI TAVSİYE EDİLEN ADIMLAR:",
            "1. Sistemimize giriş yaparak parolanızı güçlü yeni bir parola ile güncelleyiniz.",
            "2. İki Aşamalı Doğrulama (TOTP / MFA) seçeneğini devreye alınız.",
            "3. Diğer platformlarda aynı parolayı kullanıyorsanız ilgili serviste yenileyiniz.",
            "",
            "KVKK ve yasal mevzuat kapsamındaki resmi bilgilendirmeler ilgili mercilere iletildi.",
            "Destek ve sorularınız için: guvenlik@bora-analiz.local",
            "================================================================================",
        ]
        return "\n".join(notice_lines)

    def generate_post_mortem_report(
        self,
        incident_id: str,
        root_cause: str,
        remediation_actions: list[str],
    ) -> str:
        """Generates comprehensive post-mortem root-cause analysis document."""
        incident = self.get_incident(incident_id)
        incident.root_cause = root_cause
        incident.remediation_notes = "\n".join(remediation_actions)

        commander = incident.lead_commander
        sec_lead = self.directory.get_primary(ContactRole.SECURITY_LEAD).name

        duration_hours = (
            (incident.closed_at - incident.detected_at).total_seconds() / 3600.0
            if incident.closed_at
            else (datetime.now(UTC) - incident.detected_at).total_seconds() / 3600.0
        )

        timeline_rows = [
            (
                f"- {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')} "
                f"[{entry.stage.value}] ({entry.actor}): {entry.description}"
            )
            for entry in incident.timeline
        ]

        action_rows = [f"{idx + 1}. [ ] {action}" for idx, action in enumerate(remediation_actions)]

        closed_str = (
            incident.closed_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            if incident.closed_at
            else "Henüz açık"
        )
        sign_date = datetime.now(UTC).strftime("%Y-%m-%d")

        doc = [
            f"# Olay Sonrası Değerlendirme Raporu (Post-Mortem): {incident.incident_id}",
            "",
            "## 1. Olay Özeti",
            f"- **Başlık:** {incident.title}",
            f"- **Şiddet Seviyesi:** {incident.severity.value}",
            f"- **Kategori:** {incident.category.value}",
            f"- **Olay Yöneticisi:** {commander}",
            f"- **Güvenlik İnceleme Lideri:** {sec_lead}",
            f"- **Tespit Zamanı:** {incident.detected_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"- **Kapanış Zamanı:** {closed_str}",
            f"- **Toplam Etki Süresi:** {duration_hours:.2f} saat",
            f"- **Etkilenen Kullanıcı Sayısı:** {incident.affected_user_count}",
            f"- **Etkilenen Sistemler:** {', '.join(incident.affected_systems)}",
            "",
            "## 2. Kök Neden Analizi (Root Cause Analysis)",
            f"{root_cause}",
            "",
            "## 3. Olay Kronolojisi (Timeline)",
            "\n".join(timeline_rows),
            "",
            "## 4. Uygulanan Sınırlama ve Kurtarma Tedbirleri",
        ]

        for act in incident.containment_actions:
            act_time = act.executed_at.strftime("%H:%M:%S UTC")
            act_status = "Başarılı" if act.success else "Hata"
            doc.append(
                f"- **{act.action.value}** ({act_time}): {act.details} "
                f"[Operatör: {act.operator}, Durum: {act_status}]"
            )

        doc.extend(
            [
                "",
                "## 5. İyileştirme ve Önleyici Aksiyonlar (Action Items)",
                "\n".join(action_rows),
                "",
                "## 6. Onaylar",
                f"- **Olay Komutanı:** {commander} (İmza / Tarih: {sign_date})",
                f"- **Güvenlik Lideri:** {sec_lead}",
                "- **Durum:** ONAYLANDI VE ARŞİVLENDİ",
            ]
        )

        return "\n".join(doc)
