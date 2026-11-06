"""Tests for Alert History, Provenance Tracking, and SPK Compliance (FAZ-12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stock_analysis.alerts.history import (
    LEGAL_DISCLAIMER_HISTORY,
    AlertHistoryRecord,
    AlertHistoryRepository,
    AlertRecordValidationError,
    TriggeredConditionDetail,
)
from stock_analysis.db.base import Base
from stock_analysis.db.models import AlertEvent, AlertRule, Instrument, User


def _create_sample_condition(
    condition_type: str = "RSI_THRESHOLD",
    operator: str = "LESS_THAN",
    threshold_value: float = 30.0,
    observed_value: float = 27.5,
    description: str = "RSI 30 eşik değerinin altına inmiştir (27.5)",
) -> TriggeredConditionDetail:
    """Geçerli örnek bir tetiklenen koşul detayı üretir."""
    return TriggeredConditionDetail(
        condition_type=condition_type,
        operator=operator,
        threshold_value=threshold_value,
        observed_value=observed_value,
        description=description,
    )


def test_triggered_condition_detail_valid() -> None:
    """TriggeredConditionDetail başarıyla oluşturulmalı ve sözlüğe dönüştürülebilmelidir."""
    cond = _create_sample_condition()
    assert cond.condition_type == "RSI_THRESHOLD"
    assert cond.operator == "LESS_THAN"
    assert cond.threshold_value == 30.0
    assert cond.observed_value == 27.5
    assert "RSI 30" in cond.description

    data = cond.to_dict()
    assert data["threshold_value"] == 30.0

    restored = TriggeredConditionDetail.from_dict(data)
    assert restored == cond


def test_triggered_condition_detail_empty_validation() -> None:
    """Boş alan verildiğinde AlertRecordValidationError fırlatılmalıdır."""
    with pytest.raises(AlertRecordValidationError, match="condition_type"):
        TriggeredConditionDetail(
            condition_type="",
            operator="GREATER_THAN",
            threshold_value=10.0,
            observed_value=12.0,
            description="Açıklama",
        )

    with pytest.raises(AlertRecordValidationError, match="operator"):
        TriggeredConditionDetail(
            condition_type="PRICE_ABOVE",
            operator="   ",
            threshold_value=10.0,
            observed_value=12.0,
            description="Açıklama",
        )

    with pytest.raises(AlertRecordValidationError, match="description"):
        TriggeredConditionDetail(
            condition_type="PRICE_ABOVE",
            operator="GREATER_THAN",
            threshold_value=10.0,
            observed_value=12.0,
            description="",
        )


def test_triggered_condition_spk_compliance_rejection() -> None:
    """Koşul açıklamasında yatırım tavsiyesi veya al/sat olduğunda ValueError fırlatılmalıdır."""
    with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
        _create_sample_condition(description="Bu hisseyi kesin al sinyali")

    with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
        _create_sample_condition(description="Hedef fiyat 300 TL")


def test_alert_history_record_creation_valid() -> None:
    """Zorunlu alanlarla AlertHistoryRecord başarıyla üretilmeli ve sembol normalize edilmelidir."""
    now_utc = datetime.now(UTC)
    cond = _create_sample_condition()

    record = AlertHistoryRecord(
        symbol=" thyao ",
        data_timestamp_utc=now_utc - timedelta(minutes=15),
        data_source="BIST_EOD_OFFICIAL",
        triggered_conditions=(cond,),
        provider_delay_seconds=900,
        is_delayed=True,
    )

    assert record.symbol == "THYAO"
    assert record.data_source == "BIST_EOD_OFFICIAL"
    assert len(record.triggered_conditions) == 1
    assert record.is_delayed is True
    assert record.provider_delay_seconds == 900
    assert record.disclaimer == LEGAL_DISCLAIMER_HISTORY
    assert record.alert_id is not None


def test_alert_history_record_validation_errors() -> None:
    """Geçersiz veya eksik alanlarda AlertRecordValidationError fırlatılmalıdır."""
    now_utc = datetime.now(UTC)
    cond = _create_sample_condition()

    # Boş sembol
    with pytest.raises(AlertRecordValidationError, match="symbol"):
        AlertHistoryRecord(
            symbol="  ",
            data_timestamp_utc=now_utc,
            data_source="BIST",
            triggered_conditions=(cond,),
        )

    # Boş veri kaynağı
    with pytest.raises(AlertRecordValidationError, match="data_source"):
        AlertHistoryRecord(
            symbol="GARAN",
            data_timestamp_utc=now_utc,
            data_source="  ",
            triggered_conditions=(cond,),
        )

    # Timezone bilgisi olmayan naive datetime
    naive_dt = datetime(2026, 10, 24, 12, 0, 0)
    with pytest.raises(AlertRecordValidationError, match="saat dilimi"):
        AlertHistoryRecord(
            symbol="GARAN",
            data_timestamp_utc=naive_dt,
            data_source="BIST",
            triggered_conditions=(cond,),
        )

    # Boş koşul listesi
    with pytest.raises(AlertRecordValidationError, match="triggered_conditions"):
        AlertHistoryRecord(
            symbol="GARAN",
            data_timestamp_utc=now_utc,
            data_source="BIST",
            triggered_conditions=(),
        )

    # Negatif gecikme süresi
    with pytest.raises(AlertRecordValidationError, match="provider_delay_seconds"):
        AlertHistoryRecord(
            symbol="GARAN",
            data_timestamp_utc=now_utc,
            data_source="BIST",
            triggered_conditions=(cond,),
            provider_delay_seconds=-5,
        )


def test_alert_history_record_spk_compliance() -> None:
    """Başlık veya kural adında yasaklı terim geçtiğinde ValueError fırlatılmalıdır."""
    now_utc = datetime.now(UTC)
    cond = _create_sample_condition()

    with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
        AlertHistoryRecord(
            symbol="KCHOL",
            data_timestamp_utc=now_utc,
            data_source="BIST",
            triggered_conditions=(cond,),
            headline="Güçlü Al Sinyali Oluştu",
        )

    with pytest.raises(ValueError, match="Yasaklı finansal tavsiye"):
        AlertHistoryRecord(
            symbol="KCHOL",
            data_timestamp_utc=now_utc,
            data_source="BIST",
            triggered_conditions=(cond,),
            rule_name="Garantili Kazanç Kuralı",
        )


def test_alert_history_record_serialization_roundtrip() -> None:
    """AlertHistoryRecord nesnesi JSON ve sözlüğe dönüştürülüp kayıpsız geri yüklenebilmelidir."""
    now_utc = datetime.now(UTC)
    cond1 = _create_sample_condition(
        condition_type="PRICE_ABOVE",
        threshold_value=150.0,
        observed_value=152.0,
        description="Fiyat 150 TL eşiğini aştı",
    )
    cond2 = _create_sample_condition(
        condition_type="VOLUME_SPIKE",
        threshold_value=2.0,
        observed_value=2.5,
        description="Hacim 20 günlük ortalamanın 2.5 katına çıktı",
    )

    record = AlertHistoryRecord(
        symbol="ASELS",
        data_timestamp_utc=now_utc - timedelta(hours=1),
        triggered_at_utc=now_utc,
        data_source="MATRIKS_DIRECT_FEED",
        triggered_conditions=(cond1, cond2),
        rule_id="rule-999",
        rule_name="Teknik Eşik Takibi",
        user_id=42,
        headline="Teknik Gösterge ve Hacim Eşleşmesi",
        explanation="ASELS için 2 teknik gösterge koşulu karşılandı.",
        provider_delay_seconds=0,
        is_delayed=False,
        metadata={"scan_job_id": "job-abc-123", "market_state": "OPEN"},
    )

    raw_json = record.to_json()
    assert "ASELS" in raw_json
    assert "MATRIKS_DIRECT_FEED" in raw_json

    data_dict = record.to_dict()
    restored = AlertHistoryRecord.from_dict(data_dict)

    assert restored.alert_id == record.alert_id
    assert restored.symbol == "ASELS"
    assert restored.data_source == "MATRIKS_DIRECT_FEED"
    assert len(restored.triggered_conditions) == 2
    assert restored.triggered_conditions[0].condition_type == "PRICE_ABOVE"
    assert restored.triggered_conditions[1].condition_type == "VOLUME_SPIKE"
    assert restored.user_id == 42
    assert restored.metadata["scan_job_id"] == "job-abc-123"


def test_alert_history_repository_operations() -> None:
    """AlertHistoryRepository filtreleme, sorgulama ve temizleme işlemlerini doğru yürütmelidir."""
    repo = AlertHistoryRepository()
    now_utc = datetime.now(UTC)

    cond = _create_sample_condition()

    rec1 = AlertHistoryRecord(
        symbol="THYAO",
        data_timestamp_utc=now_utc - timedelta(hours=2),
        triggered_at_utc=now_utc - timedelta(hours=2),
        data_source="BIST_EOD",
        triggered_conditions=(cond,),
        user_id=1,
        rule_id="rule-1",
    )

    rec2 = AlertHistoryRecord(
        symbol="BIMAS",
        data_timestamp_utc=now_utc - timedelta(hours=1),
        triggered_at_utc=now_utc - timedelta(hours=1),
        data_source="KAGGLE_TEST",
        triggered_conditions=(cond,),
        user_id=2,
        rule_id="rule-2",
    )

    rec3 = AlertHistoryRecord(
        symbol="THYAO",
        data_timestamp_utc=now_utc,
        triggered_at_utc=now_utc,
        data_source="BIST_EOD",
        triggered_conditions=(cond,),
        user_id=1,
        rule_id="rule-3",
    )

    repo.record_many([rec1, rec2, rec3])
    assert repo.count() == 3

    # get_by_id
    found = repo.get_by_id(rec1.alert_id)
    assert found is not None
    assert found.symbol == "THYAO"

    assert repo.get_by_id("non-existent-id") is None

    # list_all (sıralama: son tetiklenen ilk sırada)
    all_recs = repo.list_all()
    assert len(all_recs) == 3
    assert all_recs[0].symbol == "THYAO"
    assert all_recs[0].triggered_at_utc == rec3.triggered_at_utc

    # list_by_symbol
    thyao_recs = repo.list_by_symbol("thyao")
    assert len(thyao_recs) == 2
    assert all(r.symbol == "THYAO" for r in thyao_recs)

    # list_by_user
    user1_recs = repo.list_by_user(1)
    assert len(user1_recs) == 2

    # list_by_rule
    rule2_recs = repo.list_by_rule("rule-2")
    assert len(rule2_recs) == 1
    assert rule2_recs[0].symbol == "BIMAS"

    # list_by_source
    kaggle_recs = repo.list_by_source("kaggle_test")
    assert len(kaggle_recs) == 1
    assert kaggle_recs[0].symbol == "BIMAS"

    # list_between
    between_recs = repo.list_between(
        now_utc - timedelta(minutes=90), now_utc + timedelta(minutes=10)
    )
    assert len(between_recs) == 2  # rec2 and rec3

    # clear
    repo.clear()
    assert repo.count() == 0


def test_alert_history_db_synchronization() -> None:
    """Veritabanı oturumu verildiğinde AlertEvent tablosuna senkronizasyon çalışmalıdır."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    try:
        # Test kullanıcısı, enstrüman ve kuralı oluştur
        user = User(email="test_alert_user@example.com", hashed_password="pw", is_active=True)
        session.add(user)
        session.flush()

        inst = Instrument(symbol="EREGL", name="Ereğli Demir Çelik")
        session.add(inst)
        session.flush()

        rule = AlertRule(
            user_id=user.id,
            instrument_id=inst.id,
            condition_type="PRICE_ABOVE",
            threshold=50.0,
            is_active=True,
        )
        session.add(rule)
        session.flush()

        # Uyarı geçmiş kaydı üret ve DB'ye senkronize et
        repo = AlertHistoryRepository()
        now_utc = datetime.now(UTC)
        cond = _create_sample_condition(
            condition_type="PRICE_ABOVE",
            threshold_value=50.0,
            observed_value=51.5,
            description="EREGL fiyatı 50.0 TL eşiğini aştı",
        )

        record = AlertHistoryRecord(
            symbol="EREGL",
            data_timestamp_utc=now_utc - timedelta(minutes=5),
            triggered_at_utc=now_utc,
            data_source="BIST_OFFICIAL",
            triggered_conditions=(cond,),
            rule_id=str(rule.id),
            user_id=user.id,
            headline="Fiyat Eşik Koşulu Gerçekleşti",
            explanation="EREGL 50.0 TL üzerinde fiyatlandı.",
        )

        repo.record(record)
        repo.sync_to_db(session, record)

        # DB'den AlertEvent çekip doğrula
        event = session.query(AlertEvent).filter_by(alert_rule_id=rule.id).first()
        assert event is not None
        assert event.alert_rule_id == rule.id
        assert "Fiyat Eşik Koşulu Gerçekleşti" in event.message
        assert event.payload is not None
        assert "BIST_OFFICIAL" in event.payload
        assert "51.5" in event.payload
    finally:
        session.close()
