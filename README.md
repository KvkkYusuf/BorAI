# BorAI — Borsa Analiz ve Tahmin Altyapısı 📈🤖

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.5+-EE4C2C.svg?logo=pytorch&logoColor=white)](https://pytorch.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

BorAI, modern yazılım mimarisi, yüksek performanslı asenkron veri hatları ve yapay zeka tabanlı zaman serisi modellerini harmanlayarak borsa ve finansal varlıklar için teknik analiz, kural tabanlı stratejiler ve olasılıksal piyasa öngörüleri sunan yeni nesil bir analiz platformudur.

> [!IMPORTANT]
> **Yasal Uyarı (SPK & Mevzuat Bilgilendirmesi):**  
> Bu projede sunulan analizler, grafikler, teknik göstergeler ve yapay zeka modelleri tarafından üretilen tahminler yalnızca **eğitim, araştırma ve bilgi amaçlıdır**. 6362 sayılı Sermaye Piyasası Kanunu kapsamında **kesinlikle kişiye özel yatırım danışmanlığı veya yatırım tavsiyesi niteliğinde değildir**.

---

## 🚀 Öne Çıkan Özellikler

- **Modern & Asenkron Mimari:** FastAPI, `asyncpg`, SQLAlchemy 2.0 ve Redis tabanlı yüksek performanslı arka uç.
- **Yapay Zeka Destekli Modeller:** PyTorch ve CUDA 12.4 altyapısıyla eğitilen derin öğrenme (LSTM, Transformer) modelleri.
- **Klasik & Modern Teknik Analiz:** RSI, MACD, Bollinger Bantları, Hareketli Ortalamalar ve özel momentum algoritmaları.
- **Güvenli Kimlik Doğrulama & Yetkilendirme:** JWT, argon2 şifreleme, RBAC (Rol Tabanlı Erişim Kontrolü) ve hız sınırlama (Rate Limiting).
- **Hukuk & KVKK Uyumluluğu:** Açık rıza yönetimi, anonimleştirme ve yasal sorumluluk reddi mekanizmaları.
- **Gelişmiş Gözlemlenebilirlik & CI/CD:** Prometheus metrikleri, yapılandırılmış JSON logları, GitHub Actions entegrasyonu, Ruff ve Mypy ile sıkı tip/kod denetimleri.

---

## 📁 Proje Dizin Yapısı

```bash
├── .agent/                  # Ajan ve kural tanımları
├── .github/workflows/       # GitHub Actions CI/CD hatları
├── docs/                    # Mimari, hukuk, güvenlik ve faz dokümantasyonları
│   ├── phases/              # Adım adım faz planları (FAZ-00'dan FAZ-18'e)
│   ├── ARCHITECTURE.md      # Sistem mimarisi ve bileşen tasarımı
│   ├── COMPLIANCE_TR.md     # Türkiye mevzuatı ve SPK uyumluluk kılavuzu
│   └── SECURITY.md          # Güvenlik ve veri koruma protokolleri
├── migrations/              # Alembic veritabanı migrasyonları
├── src/                     # Uygulama kaynak kodları
│   └── stock_analysis/
│       ├── api/             # FastAPI yönlendiricileri ve uç noktalar
│       ├── core/            # Yapılandırma, güvenlik ve yardımcı araçlar
│       ├── db/              # Veritabanı modelleri ve oturum yönetimi
│       ├── ml/              # PyTorch model tanımları ve çıkarım boru hatları
│       └── services/        # İş mantığı ve veri işleme servisleri
├── tests/                   # Kapsamlı birim, entegrasyon ve güvenlik testleri
├── pyproject.toml           # Proje bağımlılıkları ve araç ayarları
├── Dockerfile               # Konteynerizasyon yapılandırması
└── uv.lock                  # Kilitli bağımlılık ağacı
```

---

## 🛠️ Kurulum ve Başlangıç

Bu proje bağımlılık ve sanal ortam yönetimi için son derece hızlı olan [`uv`](https://github.com/astral-sh/uv) paket yöneticisini kullanmaktadır.

### 1. Gereksinimler
- Python 3.12+
- `uv` paket yöneticisi
- PostgreSQL 16+ & Redis 7+ (veya Docker)

### 2. Kurulum Adımları

1. **Depoyu klonlayın:**
   ```bash
   git clone https://github.com/KvkkYusuf/BorAI.git
   cd BorAI

   ```

2. **Bağımlılıkları yükleyin (GPU / PyTorch desteği dahil):**
   ```bash
   uv sync --all-groups
   ```

3. **Çevresel değişkenleri yapılandırın:**
   ```bash
   cp .env.example .env
   # .env dosyasındaki DB_URL, SECRET_KEY vb. değişkenleri düzenleyin
   ```

4. **Veritabanı migrasyonlarını çalıştırın:**
   ```bash
   uv run alembic upgrade head
   ```

5. **Geliştirme sunucusunu başlatın:**
   ```bash
   uv run uvicorn src.stock_analysis.main:app --reload --port 8000
   ```

Sunucu çalıştıktan sonra API belgelerine tarayıcınızdan erişebilirsiniz:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## 🧪 Test ve Kod Kalitesi

Tüm kalite kontrolleri ve testler `uv` üzerinden çalıştırılır:

```bash
# Kod biçimlendirme kontrolü
uv run ruff format --check .

# Lint kontrolü ve otomatik düzeltme
uv run ruff check --fix .

# Statik tip denetimi (Strict mypy)
uv run mypy

# Testleri çalıştırma
uv run pytest -v
```

---

## 🐳 Docker ile Çalıştırma

Projeyi Docker Compose veya tekil konteyner olarak ayağa kaldırabilirsiniz:

```bash
# Docker imajını derleyin
docker build -t borai:latest .

# Konteyneri başlatın
docker run -d -p 8000:8000 --env-file .env borai:latest
```

---

## 📜 Lisans

Bu proje [MIT Lisansı](LICENSE) kapsamında lisanslanmıştır. Detaylar için `LICENSE` dosyasına bakabilirsiniz.
