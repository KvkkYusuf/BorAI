# Sürüm 4 — Baseline Karşılaştırması ve Walk-Forward Kabul Raporu

> **İlgili Faz:** FAZ-17 Sürüm Planı (Sürüm 4 — PyTorch araştırma hattı)  
> **Değerlendirme Zamanı:** 2026-10-29 15:56:54 UTC  
> **Genel Kapı Durumu:** **✅ GEÇTİ (PASS)**  
> **Başarı Oranı:** 6 / 6 kontrol  

## 1. Yasal Uyarı (SPK Mevzuatı Madde Uyarınca)

> Bu model baseline değerlendirme ve kapı kontrol raporu, geriye dönük naif kıyaslama (son değer, tarihsel ortalama, basit momentum) ve işlem maliyetli strateji simülasyonu sonuçlarına dayanır. Geçmiş verideki üstünlük veya kapı onayı gelecekteki sonuçların aynen gerçekleşeceğini ifade etmez veya bir getiri vaadi oluşturmaz. SPK mevzuatı uyarınca yatırım danışmanlığı kapsamında olmayıp yalnızca model yayınlama kalite güvencesi ve dağıtım öncesi güvenlik amacıyla sunulmuştur.

## 2. Kriter Değerlendirme Özeti

| No | Kontrol Kimliği | Kontrol Adı | Durum | Detaylar |
|---|---|---|---|---|
| 1 | `REL4-BWF-01-NAIVE-BASELINES` | Naif Kıyas Modelleri Paketi | ✅ GEÇTİ | Naif kıyas modelleri (LastValue, HistoricalMean, SimpleMomentum) look-ahead bias olmadan t anı geçmiş verisiyle deterministik çalışıyor. |
| 2 | `REL4-BWF-02-WALK-FORWARD-ENGINE` | Walk-Forward & Expanding-Window Doğrulama Motoru | ✅ GEÇTİ | Walk-forward doğrulama motoru 3 katlamada temporal isolation ve embargo (3 adım) güvencesiyle doğrulandı. |
| 3 | `REL4-BWF-03-DIRECTIONAL-METRICS` | Finansal Yön ve Kalibrasyon Metrikleri | ✅ GEÇTİ | Finansal yön doğruluğu, MAE/RMSE iyileştirmesi ve naif kıyası geçemeyen modelleri onaylamama kuralı başarıyla doğrulandı. |
| 4 | `REL4-BWF-04-EXECUTION-FRICTIONS` | Gerçekçi Sürtünme Maliyetleri ve Simülasyon | ✅ GEÇTİ | İşlem maliyeti (komisyon: %0.10, kayma: %0.05, BSMV) simülasyonu ve risk metrikleri (Sharpe: -1.40, MaxDD: 3.47%) doğrulandı. |
| 5 | `REL4-BWF-05-OVERFITTING-ALERT` | Çoklu Piyasa Rejimleri ve Aşırı Öğrenme Alarmı | ✅ GEÇTİ | Katlamalar arası performans oynaklığı ve aşırı öğrenme (overfitting) uyarı mekanizması başarıyla tetiklendi ve raporlandı. |
| 6 | `REL4-BWF-06-AUDIT-DISCLAIMER` | SPK Yasal Uyarı ve Denetim Raporu Uyumu | ✅ GEÇTİ | SPK mevzuatına uygun yasal uyarı doğrulandı; denetim raporu `RELEASE_4_BASELINE_WALK_FORWARD_REPORT.md` yayına hazır. |

## 3. Doğrulama ve Metodoloji Prensipleri

- **Naif Kıyas Üstünlüğü:** Hiçbir model, `LastValue`, `HistoricalMean` ve `SimpleMomentum` modellerini hem MAE/RMSE hem de yön doğruluğunda geçmeden kabul edilmez.
- **Walk-Forward İzolasyonu:** Rejim kaymasını tespit etmek için katlamalı (expanding/rolling) doğrulama uygulanır; test fold'ları train fold'larından mutlak surette sonradır.
- **Embargo Penceresi:** Otokorelasyon ve etiket ufku örtüşmesini önlemek için her katlama arasında katı `embargo_size` boşluğu bulunur.
- **Sürtünme Maliyetleri:** Strateji simülasyonlarında aracı kurum komisyonu (%0.10), kayma (%0.05) ve vergi yükü (BSMV) dahil net getiri hesaplanır.
- **Aşırı Öğrenme (Overfitting) Tespiti:** Katlamalar arası kayıp varyansı (Loss Std/Mean > %60) tespit edildiğinde sistem alarm üretir ve modeli kilitler.
- **Yatırım Tavsiyesi Yasağı:** Finansal göstergeler ve model çıktıları kesinlikle kişiye özel al-sat veya getiri vaadi içermez.

---
*Rapor, `stock_analysis.release.release_four_baseline_walk_forward` tarafından otomatik oluşturulmuştur.*