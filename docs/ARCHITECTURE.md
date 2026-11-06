# Mimari Taslak

Bu belge, Borsa Analiz Sistemi'nin katmanlı mimarisini, veri akışını, bileşenler arası sınırları ve önbellek/toplu iş stratejilerini tanımlar. Uç nokta ve API sözleşme ayrıntıları için: [API_DRAFT.md](file:///c:/Users/msi/OneDrive/Desktop/BorA%C4%B1/BoraI/borsa_analiz_yol_haritasi_v2/docs/API_DRAFT.md).

---

## 1. Sistem Mimari Diyagramı

```mermaid
flowchart TB
    subgraph ClientLayer ["İstemci Katmanı"]
        WebClient["React / Next.js Web App\n(Responsive, TypeScript)"]
    end

    subgraph APILayer ["API Ağ Geçidi & Güvenlik"]
        APIGw["FastAPI Gateway\n(Rate Limiting, JWT Auth, Feature Flags)"]
        FF["Özellik Bayrakları\n(FORECAST_ENABLED, LLM_ENABLED)"]
        APIGw --- FF
    end

    subgraph CacheLayer ["Önbellek & Kuyruk (Redis)"]
        RedisCache[("Redis Cache\nsembol:aralik:surum")]
        JobQueue["İş Kuyruğu (Redis / RQ-Celery)\nEOD Batch Tasks"]
    end

    subgraph ServiceLayer ["Uygulama Servisleri"]
        AuthSvc["Auth & Profil Servisi\n(İzleme Listesi, Tercihler)"]
        AnalyticsSvc["Analitik Servisi\n(İndikatörler & Tarayıcı)"]
        InferenceSvc["PyTorch Çıkarım Servisi\n(Sürümlenmiş Model / ONNX)"]
        AlertSvc["Uyarı Servisi\n(E-posta Bildirimleri)"]
        DataPipeline["Veri Hattı & Doğrulayıcı\n(EOD Adaptörü & Kontrol)"]
    end

    subgraph StorageLayer ["Veri Katmanı (PostgreSQL - Mantıksal İzolasyon)"]
        DBUser[("Şema: user_data\n(Kullanıcı, Liste, Ayarlar)")]
        DBMarket[("Şema: market_data\n(Ham & Düzeltilmiş EOD)")]
        DBModels[("Şema: ml_models\n(Kayıtlar, Metrikler, Sürümler)")]
    end

    subgraph ExternalSources ["Dış Servisler & Sağlayıcılar"]
        MarketProvider["Lisanslı BIST Veri Sağlayıcı\n(EOD JSON/CSV, 18:40)"]
        SMTPProvider["İşlemsel E-posta Servisi\n(DKIM/SPF Onaylı)"]
    end

    %% Bağlantılar
    WebClient <-->|HTTPS / REST API| APIGw
    APIGw <-->|Önbellek Okuma / Yazma| RedisCache
    APIGw -->|Kullanıcı İşlemleri| AuthSvc
    APIGw -->|İndikatör & Tarama Talebi| AnalyticsSvc
    APIGw -->|Model Tahmin Talebi| InferenceSvc

    AuthSvc <--> DBUser
    AnalyticsSvc <--> DBMarket
    InferenceSvc <--> DBModels
    InferenceSvc <--> DBMarket

    MarketProvider -->|Günlük Seans Verisi| DataPipeline
    DataPipeline -->|Doğrulanmış Veri Yazımı| DBMarket
    DataPipeline -->|Batch Hesap Tetikleme| JobQueue

    JobQueue --> AnalyticsSvc
    JobQueue --> InferenceSvc
    JobQueue --> AlertSvc

    AlertSvc -->|İşlemsel Bildirim| SMTPProvider
    AlertSvc <--> DBUser
    AlertSvc <--> DBMarket
```

---

## 2. Uçtan Uca Veri Akışı (EOD Seans Sonu)

```mermaid
sequenceDiagram
    autonumber
    actor Cron as Zamanlayıcı (18:40 UTC+3)
    participant Pipe as Veri Hattı (Data Pipeline)
    participant Prov as Lisanslı Sağlayıcı
    participant DB as PostgreSQL (market_data)
    participant Queue as Redis Kuyruğu
    participant Worker as Analitik & ML Worker
    participant Cache as Redis Önbellek
    participant User as Web İstemcisi

    Cron->>Pipe: Günlük EOD alımını tetikle
    Pipe->>Prov: BIST 30/100 gün sonu verisini çek
    Prov-->>Pipe: Ham OHLCV verisi
    Pipe->>Pipe: Veri bütünlüğü, null & aykırı değer doğrulaması
    Pipe->>DB: Ham ve düzeltilmiş fiyatları kaydet (UTC damgasıyla)
    Pipe->>Queue: Toplu hesaplama işi kuyruğa ekle (symbol_list)
    Queue->>Worker: İşi dağıt
    Worker->>DB: Geçmiş veriyi oku
    Worker->>Worker: Teknik indikatörleri ve sinyalleri hesapla
    alt FORECAST_ENABLED == true
        Worker->>Worker: PyTorch tahmin modelini çalıştır
    end
    Worker->>DB: Hesaplanan analiz çıktılarını yaz
    Worker->>Cache: Sıcak indikatör & özet önbelleğini güncelle (TTL: 24h)
    User->>Cache: Sembol analizi görüntüle
    alt Önbellek Mevcut (Hit)
        Cache-->>User: Anında veri sunumu (<100ms)
    else Önbellek Boş (Miss)
        Worker-->>User: DB'den oku, önbelleğe yaz ve dön
    end
```

---

## 3. Servis Sorumlulukları ve İzolasyon Sınırları

- **`market_data`**: Sağlayıcı adaptörleri, şema doğrulama, aykırı değer filtreleme, normalizasyon (ham vs düzeltilmiş fiyat). Kullanıcı kimlik verilerine erişemez.
- **`analytics`**: Saf matematiksel ve finansal formüllerle teknik göstergeler üretir; deterministiktir.
- **`inference`**: Yalnızca sürüm onayı almış ve test edilmiş PyTorch modellerini barındırır. `FORECAST_ENABLED=false` iken arayüze devre dışı bilgisi iletir.
- **`alerts`**: EOD seans sonu tetiklenen kuralları işler, mükerrer bildirim engellemesi (rate limit) uygular.
- **`api` (FastAPI)**: JWT doğrulama, oran sınırlama (rate limit), önbellek koordinasyonu ve OpenAPI sözleşmelerini yönetir. Sağlayıcı API anahtarları asla istemciye sızdırılmaz.
- **`web`**: Erişilebilir, salt-okunur ve bilgilendirme odaklı arayüz.

---

## 4. Önbellek ve Toplu Hesap Stratejisi (v2)

- **Toplu (Batch) Üretim:** Kullanıcı istek anında ağır indikatör veya ML hesaplaması yapılmaz. Tüm hesaplamalar seans kapanışını (18:40) takiben worker'lar tarafından topluca yapılır.
- **Önbellek Anahtar Formatı:** `sembol:aralik:veri_surumu` (Örn: `THYAO:1D:20261018`).
- **Önbellek Yaşam Döngüsü (TTL):** EOD verisi için 24 saat veya yeni EOD paketi yüklenene kadar.
- **Özellik Bayrakları (Feature Flags):**
  - `FORECAST_ENABLED`: Model tahminlerini açar/kapatır. Kapalıyken UI "Tahmin modeli bakımda" uyarısı verir.
  - `LLM_ENABLED`: İsteğe bağlı metin özetini açar/kapatır.

---

## 5. Zaman ve Para Birimi Standartları

- **Zaman Saklama:** Veritabanındaki tüm zaman damgaları istisnasız `UTC (ISO 8601)` olarak saklanır.
- **Zaman Gösterimi:** Arayüzde `Europe/Istanbul (UTC+3)` yerel saat dilimine dönüştürülerek gösterilir.
- **Para Birimi:** BIST için `TRY` olarak saklanır ve formatlanır.
