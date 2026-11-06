# Sürüm 4 — Kaggle Veri Kaynağı ve Lisansı Kabul Raporu

> **İlgili Faz:** FAZ-17 Sürüm Planı (Sürüm 4 — PyTorch araştırma hattı)  
> **Değerlendirme Zamanı:** 2026-10-29 15:22:22 UTC  
> **Genel Kapı Durumu:** **✅ GEÇTİ (PASS)**  
> **Başarı Oranı:** 6 / 6 kontrol  

## 1. Kriter Değerlendirme Özeti

| No | Kontrol Kimliği | Kontrol Adı | Durum | Detaylar |
|---|---|---|---|---|
| 1 | `REL4-KAGGLE-01-DOC-AND-CARD` | Kaggle Veri Kartı ve Dokümantasyon Varlığı | ✅ GEÇTİ | DATASET_CARD_KAGGLE.md mevcut ve onaylı (5631 karakter). Tüm zorunlu metadata başlıkları belgelendi. |
| 2 | `REL4-KAGGLE-02-LICENSE-COMPLIANCE` | Lisans Uygunluğu ve Üretim Politikası | ✅ GEÇTİ | Referans lisans: CC-BY-4.0 -> PRODUCTION_ALLOWED; v2 kuralı doğrulaması: Lisansı belirsiz veri setleri otomatik olarak LOCAL_EXPERIMENT_ONLY olarak izole edildi. |
| 3 | `REL4-KAGGLE-03-PROVENANCE-TRAIL` | Kaynak Zinciri (Provenance Trail) Denetimi | ✅ GEÇTİ | Kaynak zinciri 4 katmanlı olarak doğrulandı: Kamuyu Aydınlatma Platformu (KAP) Bültenleri -> Borsa İstanbul Günlük Resmi Kapanış Verileri -> Açık Finans Normalizasyon Katmanı -> Kaggle Arşiv Deposu |
| 4 | `REL4-KAGGLE-04-CURRENCY-TIMEZONE` | Para Birimi ve Zaman Dilimi Standardizasyonu | ✅ GEÇTİ | Pazar: Borsa Istanbul (BIST); Para Birimi: TRY; Saat Dilimi: Europe/Istanbul (Standardize UTC dönüşümü destekli). |
| 5 | `REL4-KAGGLE-05-CORPORATE-ACTION-AND-BIAS` | Kurumsal İşlemler ve Yanlılık (Bias) Şeffaflığı | ✅ GEÇTİ | Yöntem: dual_stream; Delist sembol sayısı: 64; Hayatta kalma yanlılığı risk notu ve azaltım stratejisi belgelendi. |
| 6 | `REL4-KAGGLE-06-RAW-DATA-GIT-EXCLUSION` | Ham Veri Depolama ve Git İzolasyonu | ✅ GEÇTİ | .gitignore içinde büyük veri ve ham dosya filtreleri (*.csv, data/, vb.) doğrulandı. |

## 2. Kanonik Veri Seti Kartı Değerlendirmesi

- **Veri Seti ID:** `bist-daily-stock-historical-data`
- **Lisans Türü:** `CC-BY-4.0` (Creative Commons Attribution 4.0 International)
- **Onaylanan Kullanım Kapsamı:** `PRODUCTION_ALLOWED`
- **v2 Uyumluluk İlkesi:** Lisansı belirsiz (`AMBIGUOUS_UNKNOWN`) veri setleri `LOCAL_EXPERIMENT_ONLY` ile sınırlandırılır.
- **Kaynak Zinciri:** Kamuyu Aydınlatma Platformu (KAP) -> Borsa İstanbul Kapanış Bültenleri -> Dondurulmuş Depo
- **Kurumsal İşlemler:** `DUAL_STREAM` (Ham fiyatlar + Bölünme düzeltmeli seriler)
- **Hayatta Kalma Yanlılığı (Survivorship Bias):** 64 adet delist hisse dahil edilerek asgariye indirilmiştir.
- **Ham Veri Güvenliği:** Git izolasyonu (.gitignore) aktiftir.

---
*Rapor, `stock_analysis.release.release_four_kaggle_provenance` tarafından otomatik oluşturulmuştur.*