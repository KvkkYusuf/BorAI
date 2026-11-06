# Borsa Analiz Sistemi — Veri Sözlüğü ve Erişim Sınırları

Bu belge; sistemin veri modellerini, PostgreSQL mantıksal şema sınırlarını, servis erişim yetkilerini ve veri standartlarını (ham/düzeltilmiş fiyat, UTC, para birimi) kesin çizgilerle tanımlar.

---

## 1. Veri ve Servis Erişim Sınırları (Schema Isolation)

PostgreSQL üzerinde 3 ayrı mantıksal şema tanımlanır. Her servis yalnızca kendi görev alanındaki şemaya okuma/yazma yetkisine sahiptir.

| Şema Adı | Sorumlu Servis | Okuma Yetkisi Olan Servisler | Yazma Yetkisi Olan Servisler | İçerik ve Güvenlik Sınırı |
|---|---|---|---|---|
| `market_data` | `market_data_pipeline` | `api`, `analytics`, `inference`, `alerts` | `market_data_pipeline` (yalnızca EOD batch) | BIST 30/100 EOD mumları, endeksler, düzeltme katsayıları. Kesinlikle kullanıcı veya oturum verisi barındırmaz. |
| `user_data` | `auth_user_service` | `auth_user_service`, `alerts` (salt okunur e-posta/uyarı) | `auth_user_service`, `alerts` (durum güncelleme) | E-posta, hash'lenmiş oturumlar, izleme listesi (yalnızca semboller), uyarı kuralları. **Kesinlikle maliyet/lot/kâr-zarar tutulmaz**. |
| `ml_models` | `inference_service` | `inference_service`, `api` (salt okunur sürüm sorgusu) | `inference_service` (çevrimdışı model kayıt hattı) | Model ağırlık metadata'sı, doğruluk metrikleri, tahmin geçmişi, uncertainty (belirsizlik) aralıkları. |

---

## 2. Temel Standartlar

1. **Zaman Damgası (Timestamp):**
   - Veritabanında saklama: `UTC (ISO 8601)` (Örn: `2026-10-18T15:00:00Z`).
   - Veri tipi: `TIMESTAMPTZ`.
   - İstemci gösterimi: `Europe/Istanbul (UTC+3)`.
2. **Para Birimi:**
   - Standart: `TRY` (Türk Lirası - ISO 4217).
   - Finansal fiyat tipleri: `NUMERIC(12, 4)` (Kuruş ve küsurat hassasiyeti için kesin kayan noktasız aritmetik).
   - Hacim/Lot tipleri: `BIGINT`.
3. **Sembol Formatı:**
   - Borsa İstanbul resmi işlem kodu: Büyük harf, alfanümerik, azami 10 karakter (Örn: `THYAO`, `ASELS`, `GARAN`).
4. **Veri Kaynağı ve İzlilik:**
   - Her piyasa kaydı hangi lisanslı sağlayıcıdan, hangi parti/dosya ile ve hangi UTC anında alındığını `source_id` ve `ingested_at` alanlarında saklar.

---

## 3. Piyasa Verisi Modeli (`market_data` Şeması)

### 3.1. Ham Fiyat vs. Düzeltilmiş Fiyat Ayrımı
- **Ham Fiyat (`raw_ohlcv`):** Seans günü borsada gerçekleşen gerçek işlem fiyatlarıdır. Temettü veya bedelli/bedelsiz sermaye artırımlarından arındırılmamıştır.
- **Düzeltilmiş Fiyat (`adjusted_ohlcv`):** Geçmişe dönük temettü dağıtımları ve sermaye artırımları katsayılarıyla düzeltilmiş seridir. **Teknik indikatör hesaplamaları ve ML modelleri İSTİSNASIZ düzeltilmiş fiyatları kullanır.**

### 3.2. Tablo: `daily_bars` (Günlük EOD Mumları)
```sql
CREATE TABLE market_data.daily_bars (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    -- Ham Fiyatlar (TRY)
    raw_open NUMERIC(12, 4) NOT NULL,
    raw_high NUMERIC(12, 4) NOT NULL,
    raw_low NUMERIC(12, 4) NOT NULL,
    raw_close NUMERIC(12, 4) NOT NULL,
    -- Düzeltilmiş Fiyatlar (TRY)
    adj_open NUMERIC(12, 4) NOT NULL,
    adj_high NUMERIC(12, 4) NOT NULL,
    adj_low NUMERIC(12, 4) NOT NULL,
    adj_close NUMERIC(12, 4) NOT NULL,
    -- Hacim ve İşlem
    volume BIGINT NOT NULL,              -- Lot adedi
    turnover_try NUMERIC(16, 2) NOT NULL, -- İşlem hacmi tutarı
    split_factor NUMERIC(8, 6) DEFAULT 1.0, -- Sermaye artırım katsayısı
    dividend_try NUMERIC(10, 4) DEFAULT 0.0, -- Hisse başına brüt temettü
    -- Meta Veri & Doğrulama
    source_name VARCHAR(50) NOT NULL,
    is_verified BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_symbol_date UNIQUE (symbol, trade_date)
);

CREATE INDEX idx_daily_bars_symbol_date ON market_data.daily_bars(symbol, trade_date DESC);
```

---

## 4. Kullanıcı Verisi Modeli (`user_data` Şeması)

### 4.1. Tablo: `users`
```sql
CREATE TABLE user_data.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    email_verified BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 4.2. Tablo: `watchlist_items` (İzleme Listesi)
> **Kırmızı Çizgi:** Yalnızca takip edilen sembol tutulur. Maliyet, adet, alış tarihi veya kâr/zarar alanı EKLEMEK KESİNLİKLE YASAKTIR.
```sql
CREATE TABLE user_data.watchlist_items (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_data.users(id) ON DELETE CASCADE,
    symbol VARCHAR(10) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_user_symbol UNIQUE (user_id, symbol)
);
```

### 4.3. Tablo: `alerts` (Kullanıcı Bildirim Kuralları)
```sql
CREATE TABLE user_data.alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_data.users(id) ON DELETE CASCADE,
    symbol VARCHAR(10) NOT NULL,
    condition_type VARCHAR(20) NOT NULL, -- 'PRICE_ABOVE', 'PRICE_BELOW', 'RSI_BELOW'
    target_value NUMERIC(12, 4) NOT NULL,
    channel VARCHAR(20) DEFAULT 'EMAIL',
    is_triggered BOOLEAN DEFAULT FALSE,
    triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 5. Model & Analitik Çıktı Modeli (`ml_models` Şeması)

### 5.1. Tablo: `model_registry`
```sql
CREATE TABLE ml_models.model_registry (
    model_version VARCHAR(50) PRIMARY KEY,
    model_type VARCHAR(50) NOT NULL,     -- 'PyTorch-LSTM', 'PyTorch-Transformer'
    training_dataset_id VARCHAR(100) NOT NULL,
    test_accuracy NUMERIC(5, 4) NOT NULL,
    baseline_accuracy NUMERIC(5, 4) NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,     -- Yalnızca insan onayı almış model aktifleşebilir
    approved_by VARCHAR(100),
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 5.2. Tablo: `daily_forecasts`
```sql
CREATE TABLE ml_models.daily_forecasts (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    model_version VARCHAR(50) NOT NULL REFERENCES ml_models.model_registry(model_version),
    forecast_date DATE NOT NULL,
    horizon_days INT NOT NULL DEFAULT 5,
    predicted_direction VARCHAR(10) NOT NULL, -- 'UP', 'DOWN', 'NEUTRAL'
    confidence_score NUMERIC(5, 4) NOT NULL,
    lower_bound_price NUMERIC(12, 4),
    upper_bound_price NUMERIC(12, 4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_symbol_forecast_date UNIQUE (symbol, forecast_date, model_version)
);
```
