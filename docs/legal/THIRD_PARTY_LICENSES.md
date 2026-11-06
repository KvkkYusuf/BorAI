# Üçüncü Taraf Lisans Envanteri ve Açık Kaynak Uyumluluk Raporu

**Denetim Tarihi:** 28 Ekim 2026  
**Durum:** ONAYLANDI (Approved) — Virütik Copyleft (GPL / AGPL) Bağımlılık Bulunmamaktadır.

## 1. Yönetici Özeti
Borsa Analiz Sistemi'nde kullanılan tüm üretim ve geliştirme açık kaynak bağımlılıkları, veri setleri ve kütüphaneler lisans uyumluluğu açısından denetlenmiştir. Sistemin tamamında serbest dağıtıma, kapalı ticari entegrasyona ve özel mülk barındırmaya izin veren esnek (permissive) lisanslar (MIT, Apache 2.0, BSD-3-Clause) tercih edilmiştir. Kaynak kodun tamamını açma zorunluluğu getiren (Copyleft / AGPL / GPL) hiçbir paket depoya kabul edilmemiştir.

## 2. Üretim Bağımlılıkları Lisans Matrisi

| Paket Adı | Sürüm Şartı | Lisans Türü | Lisans Sınıfı | Ticari Dağıtım İzni | Kaynak Kod Açma Zorunluluğu |
|---|---|---|---|---|---|
| `fastapi` | >=0.115.0 | MIT | Permissive | Evet | Yok |
| `uvicorn` | >=0.32.0 | BSD-3-Clause | Permissive | Evet | Yok |
| `pydantic` | >=2.9.0 | MIT | Permissive | Evet | Yok |
| `pydantic-settings` | >=2.5.0 | MIT | Permissive | Evet | Yok |
| `sqlalchemy` | >=2.0.35 | MIT | Permissive | Evet | Yok |
| `alembic` | >=1.13.0 | MIT | Permissive | Evet | Yok |
| `asyncpg` | >=0.30.0 | Apache-2.0 | Permissive | Evet | Yok |
| `redis` | >=5.1.0 | MIT | Permissive | Evet | Yok |
| `pandas` | >=2.2.0 | BSD-3-Clause | Permissive | Evet | Yok |
| `numpy` | >=2.0.0 | BSD-3-Clause | Permissive | Evet | Yok |
| `torch` | >=2.5.0 | Modified BSD | Permissive | Evet | Yok |

## 3. Geliştirme ve Test Bağımlılıkları Lisans Matrisi

| Paket Adı | Sürüm Şartı | Lisans Türü | Lisans Sınıfı | Not |
|---|---|---|---|---|
| `pytest` | >=8.3.0 | MIT | Permissive | Yalnızca test ortamında çalışır |
| `pytest-asyncio` | >=0.24.0 | Apache-2.0 | Permissive | Yalnızca test ortamında çalışır |
| `httpx` | >=0.27.0 | BSD-3-Clause | Permissive | Test ve HTTP istemcisi |
| `ruff` | >=0.7.0 | MIT / Apache-2.0 | Permissive | Yalnızca CI/CD linter aracı |
| `mypy` | >=1.12.0 | MIT | Permissive | Yalnızca statik tip denetleyicisi |
| `pandas-stubs` | >=2.2.0 | MIT | Permissive | Yalnızca tip açıklamaları |

## 4. Veri Seti ve Model Lisans Denetimi

| Veri Kaynağı | Lisans | Belge / Kart | Uyumluluk Notu |
|---|---|---|---|
| Kaggle Borsa İstanbul Tarihsel Veri Seti | CC BY-SA 4.0 | `docs/DATASET_CARD_KAGGLE.md` | Model eğitiminde türetilmiş veri olarak kullanıldı, ticari olmayan kısıtlama (NC) içermez. Atıf kurallarına tam uyulmuştur. |
| Sentetik Veri Üreteci | Dahili (MIT) | `src/stock_analysis/synthetic.py` | Telif ve lisans riski içermeyen tamamen deterministik matematiksel sentetik veri. |

## 5. Lisans Politikası ve Yasaklı Lisanslar (Blacklist)
Aşağıdaki lisanslara sahip kütüphaneler depoya, CI pipeline'ına veya çalışma ortamına kesinlikle eklenemez:
- **GPL v2 / GPL v3:** Virütik copyleft lisanslarıdır.
- **AGPL v3:** Ağ üzerinden sunulan SaaS sistemlerinde kaynak kod açma şartı getirir.
- **SSPL / Commons Clause:** Kısıtlayıcı tescil veya patent çekinceleri barındırır.
- **CC-BY-NC (Non-Commercial):** Ticari ölçeklenmeyi engeller.
