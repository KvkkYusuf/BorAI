# Sürüm 4 — Zaman Bazlı Split ve Sızıntı Kabul Raporu

> **İlgili Faz:** FAZ-17 Sürüm Planı (Sürüm 4 — PyTorch araştırma hattı)  
> **Değerlendirme Zamanı:** 2026-10-29 15:35:00 UTC  
> **Genel Kapı Durumu:** **✅ GEÇTİ (PASS)**  
> **Başarı Oranı:** 6 / 6 kontrol  

## 1. Kriter Değerlendirme Özeti

| No | Kontrol Kimliği | Kontrol Adı | Durum | Detaylar |
|---|---|---|---|---|
| 1 | `REL4-SPLIT-01-CHRONOLOGICAL-ORDER` | Kronolojik Sıralama ve Rastgele Karıştırma Engeli | ✅ GEÇTİ | Kronolojik zaman sıralılığı güvenceye alındı; rastgele satır karıştırma (shuffled split) DataLeakageError ile bloke edildi. |
| 2 | `REL4-SPLIT-02-PARTITION-PURITY` | Kesin Bölüm Sınırları ve Ayrıklık | ✅ GEÇTİ | Eğitim, doğrulama ve test bölümleri arasında kesin kronolojik sınır ayrıklığı (Train < Val < Test) sağlandı; sınır ihlalleri engellendi. |
| 3 | `REL4-SPLIT-03-EMBARGO-ISOLATION` | Embargo ve Arındırma Boşluğu (Purge Gap) | ✅ GEÇTİ | Etiket ufku ve otokorelasyon arındırması için embargo aralıkları (embargo_days) başarıyla uygulandı ve ihlaller engellendi. |
| 4 | `REL4-SPLIT-04-LOOKAHEAD-BIAS-SAFETY` | Gelecek Bilgisi Sızıntısı ve Pencere Güvenliği | ✅ GEÇTİ | Ortalanmış pencereler (center=True), ileriye dönük kaydırmalar (forward shift) ve t sonrasındaki özellik zaman damgaları kesin olarak engellendi. |
| 5 | `REL4-SPLIT-05-TARGET-LEAKAGE` | Hedef Değişken Sızıntısı (Target Leakage) | ✅ GEÇTİ | Hedef değişkenin doğrudan girdi sütunlarına sızması ve mükemmel korelasyonlu (|r| >= 0.999) etiket kopyalanması koruması doğrulandı. |
| 6 | `REL4-SPLIT-06-PREPROCESSING-AND-REGISTRY` | Ön İşleme İzolasyonu ve Bölünme Kayıt Defteri | ✅ GEÇTİ | Ön işleme parametrelerinin (scaler/normalizer) yalnızca Train üzerinde fit edilmesi güvenceye alındı; SplitRegistry ile SHA-256 sürümleme ve bölünme değişmezliği doğrulandı. |

## 2. Zaman Serisi Sızıntı Önleme Metodolojisi

- **Kronolojik Ayrım:** Rastgele satır karıştırma (shuffling) yasaklanmıştır.
- **Bölüm Ayrıklığı:** `Train < Validation < Test` kronolojik sınırları katıdır.
- **Embargo Penceresi:** Etiket ufku ve otokorelasyon arındırması için `embargo_days` uygulanır.
- **Gelecek Bilgisi Güvenliği:** `center=True` ve `shift(-k)` gibi geleceğe bakan pencereler yasaklanmıştır.
- **Hedef Değişken Denetimi:** Doğrudan girdi sütunu sızıntısı ve $|r| \ge 0.999$ aşırı korelasyon elenir.
- **Ön İşleme İzolasyonu:** Scaler/normalizer fit parametreleri sadece `Train` kümesinden elde edilir.
- **Kayıt Defteri & Değişmezlik:** `SplitRegistry` üzerinden SHA-256 ile sürümleme ve tekrarlanabilirlik denetlenir.

---
*Rapor, `stock_analysis.release.release_four_time_split_leakage` tarafından otomatik oluşturulmuştur.*
