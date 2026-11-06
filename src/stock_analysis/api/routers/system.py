"""System health and operational inspection endpoints."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse

from stock_analysis.api.schemas import (
    AcceptanceCriteriaGateResponse,
    AccountLifecycleGateResponse,
    BackupRestoreGateResponse,
    BetaRolloutGateResponse,
    HealthResponse,
    ModelBaselineGateResponse,
    OperationsReadinessGateResponse,
    PreDeploymentVerificationResponse,
    UiTransparencyGateResponse,
    WebVitalIngestRequest,
    WebVitalIngestResponse,
)
from stock_analysis.api.versioning import ApiVersionInfo, get_current_api_version
from stock_analysis.config import Settings, get_settings
from stock_analysis.models.baseline_gatekeeper import MODEL_BASELINE_GATEKEEPER
from stock_analysis.observability.cost_tracker import COST_TRACKER
from stock_analysis.observability.metrics import REGISTRY
from stock_analysis.observability.model_performance import MODEL_PERFORMANCE_TRACKER
from stock_analysis.observability.operational import OPERATIONAL_MONITOR
from stock_analysis.observability.operations_gatekeeper import (
    OPERATIONS_READINESS_GATEKEEPER,
)
from stock_analysis.observability.provider_alerts import PROVIDER_ALERT_MANAGER
from stock_analysis.observability.status_page import STATUS_PAGE_COORDINATOR
from stock_analysis.observability.web_vitals_regression import PERFORMANCE_DETECTOR
from stock_analysis.security.acceptance_criteria_gatekeeper import (
    ACCEPTANCE_CRITERIA_GATEKEEPER,
)
from stock_analysis.security.account_lifecycle_gatekeeper import (
    ACCOUNT_LIFECYCLE_GATEKEEPER,
)
from stock_analysis.security.backup_restore_gatekeeper import (
    BACKUP_RESTORE_GATEKEEPER,
)
from stock_analysis.security.closed_beta_gatekeeper import (
    CLOSED_BETA_GATEKEEPER,
)
from stock_analysis.security.critical_verification_gatekeeper import (
    CRITICAL_VERIFICATION_GATEKEEPER,
)
from stock_analysis.security.ui_transparency_gatekeeper import (
    UI_TRANSPARENCY_GATEKEEPER,
)

router = APIRouter(tags=["System"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Sistem ve servis saglik durumu",
    description=(
        "Servis surumu, calisma zamani ve ozellik bayraklarinin (feature flags) durumunu dondurur."
    ),
)
def get_health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Return health status, current timestamp, and active feature toggles."""
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        timestamp=datetime.now(UTC),
        flags={
            "forecast_enabled": settings.forecast_enabled,
            "llm_enabled": settings.llm_enabled,
        },
    )


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus uyumlu operasyonel ve sistem metrikleri",
    description=(
        "HTTP istek sayaçları, p95/p99 gecikme histogramları ve sistem göstergelerini sunar."
    ),
)
def get_metrics() -> PlainTextResponse:
    """Expose Prometheus formatted application metrics."""
    return PlainTextResponse(
        content=REGISTRY.generate_prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get(
    "/version",
    response_model=ApiVersionInfo,
    summary="API sürümleme ve yaşam döngüsü bilgisi",
    description=(
        "Semantik sürüm (SemVer), desteklenen ana sürümler, asgari istemci sürümü "
        "ve kullanım ömrü / deprecation bilgilerini döndürür."
    ),
)
def get_version() -> ApiVersionInfo:
    """Return API version, lifecycle status, and backward compatibility metadata."""
    return get_current_api_version()


@router.get(
    "/operational-health",
    summary="Operasyonel sağlık, veri güncelliği ve SLA metrikleri",
    description=(
        "Veri güncelliği, API hata oranı, p95/p99 gecikme SLA hedefleri, "
        "kuyruk gecikmesi ve e-posta teslimat istatistiklerinin özetini döndürür."
    ),
)
def get_operational_health() -> dict[str, Any]:
    """Expose composite operational telemetry report."""
    return OPERATIONAL_MONITOR.get_operational_report()


@router.get(
    "/provider-alerts",
    summary="Piyasa verisi sağlayıcısı kesinti ve kota alarm paneli",
    description=(
        "Piyasa verisi sağlayıcılarının kesinti durumu, ardışık hata sayaçları, "
        "günlük kota kullanım oranları ve tetiklenen son alarmları listeler."
    ),
)
def get_provider_alerts(
    provider: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return current provider health, active outages, quota ratios, and alert logs."""
    all_statuses = PROVIDER_ALERT_MANAGER.get_all_statuses()
    active_outages = PROVIDER_ALERT_MANAGER.get_active_outages()
    recent_alerts = PROVIDER_ALERT_MANAGER.get_recent_alerts(provider=provider, limit=limit)
    return {
        "active_outages": active_outages,
        "is_healthy": len(active_outages) == 0,
        "providers": {k: v.to_dict() for k, v in all_statuses.items()},
        "recent_alerts": recent_alerts,
    }


@router.get(
    "/model-performance",
    summary="Model çevrimdışı doğrulama ve canlı gerçekleşen performans paneli",
    description=(
        "Yapay zeka modellerinin çevrimdışı doğrulama ölçütleri ile canlı gerçekleşen "
        "tahmin sonuçlarını ayrı takip eder; genelleme sapmasını ve sağlık durumunu listeler."
    ),
)
def get_model_performance(
    model_id: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Return offline vs live performance comparison and tracking reports."""
    if model_id is not None:
        comparison = MODEL_PERFORMANCE_TRACKER.evaluate_model(model_id, version)
        return comparison.model_dump(mode="json")
    return MODEL_PERFORMANCE_TRACKER.get_summary_report()


@router.get(
    "/status",
    summary="Platform genel durum sayfası özeti",
    description=(
        "Sistem bileşenlerinin (API, Veri Sağlayıcı, DB, ML Tahmin, Kuyruk, Bildirim) "
        "operasyonel durumunu, aktif kesinti/bakım olaylarını ve sistem sağlığını döndürür."
    ),
)
def get_status() -> dict[str, Any]:
    """Expose public system status page JSON payload."""
    return STATUS_PAGE_COORDINATOR.get_public_status()


@router.get(
    "/status/banner",
    summary="İstemci ve UI kesinti/bakım bilgilendirme bildirim başlığı (banner)",
    description=(
        "Aktif hizmet kesintisi veya planlı bakım durumunda web/mobil istemcilere "
        "gösterilecek bildirim bandı (outage banner) metnini, önem düzeyini ve "
        "aksiyon bilgisini sunar."
    ),
)
def get_status_banner() -> dict[str, Any]:
    """Expose active outage/maintenance notice banner for client apps."""
    return STATUS_PAGE_COORDINATOR.get_active_banner().to_dict()


@router.get(
    "/status/incidents",
    summary="Operasyonel olay ve hizmet kesintisi geçmişi",
    description="Aktif ve geçmiş operasyonel olayları ve yaşam döngüsü güncellemelerini listeler.",
)
def get_status_incidents(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent incident history with lifecycle stage updates."""
    return [inc.to_dict() for inc in STATUS_PAGE_COORDINATOR.get_incident_history(limit=limit)]


@router.get(
    "/status/html",
    response_class=HTMLResponse,
    summary="Bağımsız, görsel sistem durum paneli (HTML)",
    description="Tarayıcıdan doğrudan görüntülenebilen modern, duyarlı sistem durum paneli.",
)
def get_status_html() -> HTMLResponse:
    """Render standalone responsive HTML status page."""
    html_content = STATUS_PAGE_COORDINATOR.generate_html_status_page()
    return HTMLResponse(content=html_content, status_code=200)


@router.get(
    "/cost",
    summary="LLM token tüketimi, önbellek verimliliği ve maliyet özeti",
    description=(
        "Özellik başına token kullanımı, çağrı sayıları, önbellek (cache) isabet oranı, "
        "günlük/aylık bütçe harcaması ve bütçe durumu özet raporunu döndürür."
    ),
)
def get_cost_summary() -> dict[str, Any]:
    """Return consolidated LLM token, cache efficiency, and budget telemetry."""
    return COST_TRACKER.get_cost_summary()


@router.get(
    "/cost/features",
    summary="Özellik (feature) bazlı ayrıntılı token ve harcama dökümü",
    description=(
        "Sistemdeki analitik/LLM özelliklerinin çağrı sayısı, "
        "önbellek isabeti ve maliyet dökümünü sunar."
    ),
)
def get_cost_features() -> dict[str, dict[str, Any]]:
    """Return feature-by-feature cost, token, and cache telemetry."""
    metrics = COST_TRACKER.get_all_feature_metrics()
    return {k: v.to_dict() for k, v in metrics.items()}


@router.get(
    "/cost/alerts",
    summary="Bütçe aşım ve eşik alarmları geçmişi",
    description=(
        "Tetiklenen uyarı (soft limit) ve limit aşımı (hard limit) olay kayıtlarını listeler."
    ),
)
def get_cost_alerts(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent budget warning and exhaustion alerts."""
    return [a.to_dict() for a in COST_TRACKER.get_recent_alerts(limit=limit)]


@router.get(
    "/cost/html",
    response_class=HTMLResponse,
    summary="Görsel LLM maliyet ve bütçe paneli (HTML)",
    description="Tarayıcıdan doğrudan izlenebilen duyarlı, modern token ve bütçe paneli.",
)
def get_cost_html() -> HTMLResponse:
    """Render standalone responsive HTML cost dashboard."""
    html_content = COST_TRACKER.generate_html_dashboard()
    return HTMLResponse(content=html_content, status_code=200)


# --- Web Vitals and API p95 Performance Telemetry Endpoints ---


@router.post(
    "/telemetry/web-vitals",
    response_model=WebVitalIngestResponse,
    summary="Ön yüz Web Vitals telemetri bildirimi",
    description=(
        "Tarayıcı istemcisinden gelen Core Web Vitals ölçümlerini "
        "(LCP, INP, CLS, FCP, TTFB) kaydeder."
    ),
)
def ingest_web_vitals(payload: WebVitalIngestRequest) -> WebVitalIngestResponse:
    """Record incoming client Web Vitals measurements."""
    count = 0
    for item in payload.metrics:
        PERFORMANCE_DETECTOR.record_web_vital(
            metric_name=item.name,
            value=item.value,
            route=item.route,
            navigation_type=item.navigation_type,
            rating=item.rating,
        )
        count += 1
    return WebVitalIngestResponse(
        status="recorded",
        ingested_count=count,
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get(
    "/telemetry/web-vitals/summary",
    summary="Web Vitals istatistik ve sağlık özeti",
    description="Kayan penceredeki Web Vitals p75, p90 ve zayıf/başarılı dağılımını döndürür.",
)
def get_web_vitals_summary() -> list[dict[str, Any]]:
    """Return aggregated Web Vitals metrics per route."""
    return [v.to_dict() for v in PERFORMANCE_DETECTOR.get_web_vitals_summary()]


@router.get(
    "/telemetry/performance/p95",
    summary="API uç nokta p95 gecikme ve regresyon durumu",
    description=(
        "Uç nokta gruplarının p50, p95, p99 gecikmelerini, baseline ve SLA karşılaştırmasını sunar."
    ),
)
def get_performance_latencies() -> list[dict[str, Any]]:
    """Return rolling API latencies, baselines, and regression status."""
    return [lat.to_dict() for lat in PERFORMANCE_DETECTOR.get_endpoint_latencies()]


@router.get(
    "/telemetry/performance/alerts",
    summary="Performans regresyon ve SLA aşım alarmları",
    description="Tetiklenen p95 gecikme ve Web Vitals regresyon olaylarını listeler.",
)
def get_performance_alerts(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent performance regression alert events."""
    return [a.to_dict() for a in PERFORMANCE_DETECTOR.get_recent_alerts(limit=limit)]


@router.get(
    "/telemetry/performance/html",
    response_class=HTMLResponse,
    summary="Web Vitals & API p95 performans regresyon paneli (HTML)",
    description="Doğrudan izlenebilen duyarlı performans regresyon ve Web Vitals paneli.",
)
def get_performance_html() -> HTMLResponse:
    """Render standalone responsive HTML performance regression dashboard."""
    html_content = PERFORMANCE_DETECTOR.generate_html_dashboard()
    return HTMLResponse(content=html_content, status_code=200)


@router.get(
    "/pre-deployment/verify",
    response_model=PreDeploymentVerificationResponse,
    summary="Dağıtım öncesi kritik test ve güvenlik doğrulama kapısı",
    description=(
        "Tüm zorunlu kritik test paketlerinin (unit, security, e2e, integration, contracts, "
        "timeseries, model) ve çok vektörlü güvenlik taramalarının (SAST, bağımlılık/SBOM, "
        "CIS konteyner, secret, hukuk/lisans) sonuçlarını değerlendirir."
    ),
)
def get_pre_deployment_verification() -> dict[str, Any]:
    """Execute and return the pre-deployment test and security gate verification report."""
    report = CRITICAL_VERIFICATION_GATEKEEPER.evaluate_gate()
    return report.to_dict()


@router.get(
    "/pre-deployment/report",
    response_class=PlainTextResponse,
    summary="Dağıtım öncesi denetim raporu (Markdown formatında)",
    description=(
        "CI/CD kayıtları ve yayımlama onayı için hazırlanmış resmi Markdown formatındaki "
        "denetim ve kapı geçiş raporunu döndürür."
    ),
)
def get_pre_deployment_markdown_report() -> PlainTextResponse:
    """Return markdown formatted audit report for release sign-off."""
    report = CRITICAL_VERIFICATION_GATEKEEPER.evaluate_gate()
    return PlainTextResponse(
        content=report.generate_markdown_audit_report(),
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/pre-deployment/account-lifecycle/verify",
    response_model=AccountLifecycleGateResponse,
    summary="Dağıtım öncesi e-posta doğrulama, parola sıfırlama ve hesap silme güvenlik denetimi",
    description=(
        "E-posta doğrulama, parola sıfırlama, oturum iptali, KVKK hesap silme ve "
        "5651 denetim izi mekanizmalarının uçtan uca güvenlik durumunu değerlendirir."
    ),
)
def get_pre_deployment_account_lifecycle_verification() -> dict[str, Any]:
    """Execute and return the pre-deployment account lifecycle verification report."""
    report = ACCOUNT_LIFECYCLE_GATEKEEPER.evaluate_gate()
    return report.to_dict()


@router.get(
    "/pre-deployment/account-lifecycle/report",
    response_class=PlainTextResponse,
    summary="Dağıtım öncesi hesap yaşam döngüsü denetim raporu (Markdown formatında)",
    description=(
        "CI/CD kayıtları ve yayımlama onayı için hazırlanmış resmi Markdown formatındaki "
        "hesap yaşam döngüsü denetim raporunu döndürür."
    ),
)
def get_pre_deployment_account_lifecycle_markdown_report() -> PlainTextResponse:
    """Return markdown formatted account lifecycle audit report for release sign-off."""
    report = ACCOUNT_LIFECYCLE_GATEKEEPER.evaluate_gate()
    return PlainTextResponse(
        content=report.generate_markdown_audit_report(),
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/pre-deployment/model-baseline/verify",
    response_model=ModelBaselineGateResponse,
    summary="Dağıtım öncesi model baseline ve tahmin özelliği kontrol kapısı",
    description=(
        "Modelin naif baseline'ları ve friksiyonu geçme durumunu doğrular. "
        "Baseline geçilemezse FORECAST_ENABLED=False yapılmasını ve güvenli "
        "fallback davranışını denetler."
    ),
)
def get_pre_deployment_model_baseline_verification(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Execute and return the pre-deployment model baseline verification report."""
    report = MODEL_BASELINE_GATEKEEPER.evaluate_gate(
        target_forecast_enabled=settings.forecast_enabled,
    )
    return report.to_dict()


@router.get(
    "/pre-deployment/model-baseline/report",
    response_class=PlainTextResponse,
    summary="Dağıtım öncesi model baseline denetim raporu (Markdown formatında)",
    description=(
        "CI/CD kayıtları ve yayımlama onayı için hazırlanmış resmi Markdown formatındaki "
        "model baseline denetim raporunu döndürür."
    ),
)
def get_pre_deployment_model_baseline_markdown_report(
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    """Return markdown formatted model baseline audit report for release sign-off."""
    report = MODEL_BASELINE_GATEKEEPER.evaluate_gate(
        target_forecast_enabled=settings.forecast_enabled,
    )
    return PlainTextResponse(
        content=report.generate_markdown_audit_report(),
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/pre-deployment/ui-transparency/verify",
    response_model=UiTransparencyGateResponse,
    summary="Dağıtım öncesi arayüz veri gecikmesi ve model belirsizliği şeffaflık denetimi",
    description=(
        "Kullanıcı arayüzünde BIST 15 dk veri gecikmesi, anlık işlem yapılamaz uyarısı, "
        "model güven bandı (%90), belirsizlik skoru (±%), baseline kıyası ve güvenli "
        "fallback durumlarının eksiksiz olduğunu doğrular."
    ),
)
def get_pre_deployment_ui_transparency_verification() -> dict[str, Any]:
    """Execute and return the pre-deployment UI transparency verification report."""
    report = UI_TRANSPARENCY_GATEKEEPER.evaluate_gate()
    return report.to_dict()


@router.get(
    "/pre-deployment/ui-transparency/report",
    response_class=PlainTextResponse,
    summary="Dağıtım öncesi arayüz şeffaflık denetim raporu (Markdown formatında)",
    description=(
        "CI/CD kayıtları ve yayımlama onayı için hazırlanmış resmi Markdown formatındaki "
        "arayüz şeffaflık (veri gecikmesi ve model belirsizliği) denetim raporunu döndürür."
    ),
)
def get_pre_deployment_ui_transparency_markdown_report() -> PlainTextResponse:
    """Return markdown formatted UI transparency audit report for release sign-off."""
    report = UI_TRANSPARENCY_GATEKEEPER.evaluate_gate()
    return PlainTextResponse(
        content=report.generate_markdown_audit_report(),
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/pre-deployment/backup-restore/verify",
    response_model=BackupRestoreGateResponse,
    summary="Dağıtım öncesi yedekten geri yükleme tatbikatı ve RTO/RPO denetimi",
    description=(
        "Şifreli SQLite veritabanı yedeğinin oluşturulması, kurcalama tespiti, "
        "yetkisiz anahtar engelleme, izole sandbox geri yükleme ve yasal RTO/RPO "
        "kurtarma hedeflerinin eksiksiz olduğunu doğrular."
    ),
)
def get_pre_deployment_backup_restore_verification() -> dict[str, Any]:
    """Execute and return the pre-deployment database backup and restoration audit report."""
    report = BACKUP_RESTORE_GATEKEEPER.evaluate_gate()
    return report.to_dict()


@router.get(
    "/pre-deployment/backup-restore/report",
    response_class=PlainTextResponse,
    summary="Dağıtım öncesi yedekten geri yükleme denetim raporu (Markdown formatında)",
    description=(
        "CI/CD kayıtları ve yayımlama onayı için hazırlanmış resmi Markdown formatındaki "
        "veritabanı yedekleme ve kurtarma tatbikatı denetim raporunu döndürür."
    ),
)
def get_pre_deployment_backup_restore_markdown_report() -> PlainTextResponse:
    """Return markdown formatted backup and restoration audit report for release sign-off."""
    report = BACKUP_RESTORE_GATEKEEPER.evaluate_gate()
    return PlainTextResponse(
        content=report.generate_markdown_audit_report(),
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/pre-deployment/operations-readiness/verify",
    response_model=OperationsReadinessGateResponse,
    summary="Dağıtım öncesi izleme, alarmlar, olay müdahale ve rollback hazırbulunuşluk denetimi",
    description=(
        "Prometheus telemetrisi, alarm kuralları, PICERL olay müdahale rehberi "
        "ve otomatik rollback tatbikatının (RTO < 300s) hazır olduğunu doğrular."
    ),
)
def get_pre_deployment_operations_readiness_verification() -> dict[str, Any]:
    """Execute and return the pre-deployment operations readiness audit report."""
    report = OPERATIONS_READINESS_GATEKEEPER.evaluate_gate()
    return report.to_dict()


@router.get(
    "/pre-deployment/operations-readiness/report",
    response_class=PlainTextResponse,
    summary="Dağıtım öncesi operasyonel hazırbulunuşluk denetim raporu (Markdown formatında)",
    description=(
        "CI/CD kayıtları ve sürüm dağıtım onayı için hazırlanmış resmi Markdown formatındaki "
        "izleme, alarmlar, olay müdahale ve rollback denetim raporunu döndürür."
    ),
)
def get_pre_deployment_operations_readiness_markdown_report() -> PlainTextResponse:
    """Return markdown formatted operations readiness audit report for release sign-off."""
    report = OPERATIONS_READINESS_GATEKEEPER.evaluate_gate()
    return PlainTextResponse(
        content=report.to_markdown(),
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/pre-deployment/beta-rollout/verify",
    response_model=BetaRolloutGateResponse,
    summary="Dağıtım öncesi kapalı beta ve kademeli dağıtım hazırbulunuşluk denetimi",
    description=(
        "Kapalı beta erişim kontrolü, davetiye mekanizması, kademeli dağıtım aşamaları, "
        "deterministik kohortlama ve otomatik devre kesici (circuit breaker) tatbikatını doğrular."
    ),
)
def get_pre_deployment_beta_rollout_verification() -> dict[str, Any]:
    """Execute and return the pre-deployment closed beta rollout verification report."""
    report = CLOSED_BETA_GATEKEEPER.evaluate_gate()
    return report.to_dict()


@router.get(
    "/pre-deployment/beta-rollout/report",
    response_class=PlainTextResponse,
    summary="Dağıtım öncesi kapalı beta denetim raporu (Markdown formatında)",
    description=(
        "CI/CD kayıtları ve sürüm dağıtım onayı için hazırlanmış resmi Markdown formatındaki "
        "kapalı beta ve kademeli dağıtım denetim raporunu döndürür."
    ),
)
def get_pre_deployment_beta_rollout_markdown_report() -> PlainTextResponse:
    """Return markdown formatted closed beta audit report for release certification."""
    report = CLOSED_BETA_GATEKEEPER.evaluate_gate()
    return PlainTextResponse(
        content=report.generate_markdown_audit_report(),
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/pre-deployment/acceptance-criteria/verify",
    response_model=AcceptanceCriteriaGateResponse,
    summary="Dağıtım öncesi kabul ölçütleri (ACCEPTANCE_CRITERIA) 13/13 doğrulama kapısı",
    description=(
        "docs/ACCEPTANCE_CRITERIA.md tablosundaki tüm 13 ölçütün (veri güncelliği, veri kalitesi, "
        "p95 süreleri, web metrikleri, uyarı gecikmesi, erişilebilirlik, güvenlik ve UX testleri) "
        "gerçek operasyonel testler ve kanıtlarla 1:1 karşılandığını doğrular."
    ),
)
def get_pre_deployment_acceptance_criteria_verification() -> dict[str, Any]:
    """Execute and return the pre-deployment acceptance criteria verification report."""
    report = ACCEPTANCE_CRITERIA_GATEKEEPER.evaluate_gate()
    return report.to_dict()


@router.get(
    "/pre-deployment/acceptance-criteria/report",
    response_class=PlainTextResponse,
    summary="Dağıtım öncesi kabul ölçütleri denetim ve kanıt raporu (Markdown formatında)",
    description=(
        "CI/CD kayıtları ve sürüm dağıtım onayı için hazırlanmış resmi Markdown formatındaki "
        "kabul ölçütleri kanıt ve denetim raporunu döndürür."
    ),
)
def get_pre_deployment_acceptance_criteria_markdown_report() -> PlainTextResponse:
    """Return markdown formatted acceptance criteria audit report for release certification."""
    report = ACCEPTANCE_CRITERIA_GATEKEEPER.evaluate_gate()
    return PlainTextResponse(
        content=report.generate_markdown_audit_report(),
        media_type="text/markdown; charset=utf-8",
    )

