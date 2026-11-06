## 3. Mimari ve teknoloji kararı

### Önerilen başlangıç yığını
- **Backend API:** Python + FastAPI
- **Modelleme:** PyTorch
- **Veri işleme:** pandas, NumPy; teknik göstergeler için test edilmiş bir kütüphane veya açıkça belgelenmiş hesaplamalar
- **Veritabanı:** PostgreSQL (kullanıcı, izleme listesi, uyarı, model kayıtları); zaman serisi hacmi büyürse TimescaleDB değerlendirmesi
- **Arka plan işleri:** Celery/RQ + Redis veya yönetilen kuyruk; veri sağlayıcı sözleşmesine uygun oran sınırlama
- **Frontend:** TypeScript + React/Next.js veya ekip yetkinliğine göre eşdeğer
- **Grafikler:** erişilebilir, finansal zaman serisine uygun bir grafik kütüphanesi
- **Kimlik doğrulama:** olgun bir kimlik sağlayıcısı veya güvenli e-posta doğrulamalı uygulama akışı
- **Dağıtım:** Docker, CI/CD, yönetilen veritabanı, TLS, merkezi log/izleme

### Mantıksal bileşenler
1. Veri sağlayıcı adaptörü
2. Veri doğrulama ve normalizasyon hattı
3. Tarihsel piyasa verisi deposu
4. Analitik/indikatör servisi
5. Trend tarama ve uyarı servisi
6. PyTorch eğitim ve değerlendirme hattı
7. Model kayıt/sürümleme ve tahmin servisi
8. Kimlik, kullanıcı ayarları ve izleme listesi servisi
9. Web arayüzü
10. Denetim kayıtları, gözlemlenebilirlik ve yönetim araçları

### Başlangıç klasör yapısı
```text
stock-analysis/
├── apps/
│   ├── web/                    # React/Next.js arayüzü
│   └── api/                    # FastAPI
├── services/
│   ├── market_data/            # Sağlayıcı adaptörleri ve veri doğrulama
│   ├── analytics/              # Göstergeler ve tarayıcı
│   ├── alerts/                 # Uyarı kuralları ve teslimat
│   └── inference/              # Model tahmini
├── ml/
│   ├── datasets/               # Veri sözleşmeleri; büyük ham veriler Git dışında
│   ├── features/
│   ├── training/
│   ├── evaluation/
│   └── model_registry/
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── backtest/
├── docs/
├── infra/
├── scripts/
├── .env.example
├── AGENTS.md
├── .agent/                     # rules/ ve workflows/
├── PROGRESS.md
├── DECISIONS.md
├── pyproject.toml
├── README.md
└── TODO.md
```

- [x] Mimari diyagramı ve veri akışını çiz.
- [x] API sözleşmelerini (OpenAPI) belirle.
- [x] Kullanıcı verisi, piyasa verisi ve model çıktıları için ayrı erişim sınırları kur.
- [x] Servisler arasında zaman damgası, saat dilimi, para birimi, sembol ve veri kaynağı standardını belirle.
- [x] “Düzeltilmiş fiyat” ve “ham fiyat” ayrımını veri modelinde açıkça tanımla.

**Çıktı:** `docs/ARCHITECTURE.md`, API taslağı (`docs/API_DRAFT.md`) ve veri sözlüğü (`docs/DATA_DICTIONARY.md`).

### v2 eklemeleri
- [x] Önbellek ve toplu hesap katmanını mimari diyagrama ekle (`docs/ARCHITECTURE.md` v2 bölümü).
- [x] Özellik bayrakları (`FORECAST_ENABLED`, `LLM_ENABLED`) ve kapalı durum davranışını tanımla.
- [x] Performans bütçelerini (`docs/PERFORMANCE.md`) tasarım kısıtı olarak kabul et.
