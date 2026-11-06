# Dagitim Oncesi Model Baseline ve Tahmin Ozelligi Dogrulama Raporu

**Tarih:** 2026-10-29T01:15:00.000000+00:00  
**Kapi Durumu:** [x] GECTI (PASS)  
**Tahmin Ozelligi (FORECAST_ENABLED):** KAPALI (DISABLED)  
**Guvenli Fallback Guvencesi:** DOGRULANDI (Sessiz uydurma engellendi)  
**Dagitim / Surum Onayi (Sign-off Ready):** EVET  

## 1. Model Baseline Degerlendirme Ozeti

- **Degerlendirilen Model Sayisi:** 3
- **Baseline ve Friksiyonu Gecen (Onayli):** 2
- **Baseline Altinda Kalan (Reddedilen):** 1

| Model | Sembol | Ufuk | Model MAE | En Iyi Baseline MAE | MAE Fark % | Yon Dogrulugu | Net Sharpe | Net Getiri % | Kapi Karari |
|---|---|---|---|---|---|---|---|---|---|
| `bist_lstm_v1` | THYAO | 1G | 0.0125 | 0.0165 (LastValueBaseline) | +24.24% | %56.5 | 1.42 | %14.80 | **ONAYLANDI** |
| `bist_cnn1d_v1` | GARAN | 5G | 0.0210 | 0.0260 (SimpleMomentumBaseline) | +19.23% | %54.8 | 1.18 | %10.25 | **ONAYLANDI** |
| `bist_mlp_candidate_v2` | ASELS | 1G | 0.0195 | 0.0180 (LastValueBaseline) | -8.33% | %48.0 | -0.35 | %-2.40 | **REDDEDILDI** |

## 2. Tahmin Ozelligi ve Guvenli Fallback Denetimi

Kural geregi (`docs/phases/FAZ-11-pytorch-model.md` & `FAZ-16-dagitim-oncesi.md`):
1. Naif baseline'i ve islem maliyetli gercekci kiyasi gecemeyen hicbir model kullaniciya tahmin uretemez.
2. Baseline'i gecen aktif model bulunmadiginda veya model yayin onayindan gecmedigi surece `FORECAST_ENABLED=False` yapilmasi zorunludur.
3. Sessiz uydurma (varsayilan yon tahmin uydurma veya eski bayat model ciktisi sunma) kesinlikle yasaktir;
   kullaniciya acikca `Tahmin kapali / Model kalite sartlarini saglamadi` statusu ve klasik indikatorler sunulur.

- **Tahmin Servisi Durumu:** `KAPALI (DISABLED)` (Guvenli varsayilan: Canli oncesi kapali baslatma prensibi)
- **Fallback Davranis Kontrolu:** `GECTI` (SafeForecastFallbackManager devrede, tahmin ciktilari katı sekilde `None`, yalnizca klasik indikatörler sunuluyor)

## 3. Dagitimi Engelleyen Sorunlar (Blocking Issues)

Hicbir engelleyici sorun tespit edilmemistir. Sistem dagitim sartlarini karsilamaktadir.

## 4. Yasal Uyari ve Mevzuat Bildirimi

> Bu model baseline değerlendirme ve kapı kontrol raporu, geriye dönük naif kıyaslama (son değer, tarihsel ortalama, basit momentum) ve işlem maliyetli strateji simülasyonu sonuçlarına dayanır. Geçmiş verideki üstünlük veya kapı onayı gelecekteki sonuçların aynen gerçekleşeceğini ifade etmez veya bir getiri vaadi oluşturmaz. SPK mevzuatı uyarınca yatırım danışmanlığı kapsamında olmayıp yalnızca model yayınlama kalite güvencesi ve dağıtım öncesi güvenlik amacıyla sunulmuştur.
