# Sürüm 3 (Klasik Analiz ve Uyarı) — Trend Kuralları Raporu

**Değerlendirme Tarihi:** `2026-10-29T15:03:23.881859+00:00`  
**Kabul Kapısı Durumu:** **PASS**  
**Genel Sonuç:** `BAŞARILI`  

## 1. Yönetici Özeti

Sürüm 3 Trend Kuralları Doğrulama Kapısı: 6/6 adım başarılı. Kabul durumu: PASS.

## 2. Denetim Adımları ve Teknik İnceleme Detayları

| No | Denetim Kodu | Denetim Başlığı | Durum | Teknik Ayrıntı ve Gözlemler |
|---|---|---|:---:|---|
| 1 | `REL3-TREND-01-MULTI-IND-DEFENSE` | Tek Gösterge Savunması ve Bağımsız Kategori Teyidi | ✅ PASS | min_rules < 2 ValueError koruması: True; min_cat < 2 ValueError koruması: True; Yetersiz teyitte yön: NEUTRAL; Başlık: 'Yetersiz teyit: Tek veya sınırlı gösterge sinyal sayılmaz.' |
| 2 | `REL3-TREND-02-EXPLAINABILITY-INVALIDATION` | Kural Açıklanabilirliği ve Geçersizleşme (Invalidation) Şeffaflığı | ✅ PASS | Tetiklenen kurallar (4): ['PRICE_ABOVE_EMA20', 'EMA20_ABOVE_SMA50', 'RSI_HEALTHY_BULLISH_MOMENTUM', 'MACD_POSITIVE_EXPANSION']; Kategoriler: ['MOMENTUM', 'TREND']; Geçersizleşme sayısı: 4; Durum: Eksiksiz açıklandı |
| 3 | `REL3-TREND-03-LEGAL-GUARDRAILS` | SPK Uyum ve Yasaklı Terim Kırmızı Çizgi Koruması | ✅ PASS | Çıktı metinleri temiz: True; 'al' tuzağı yakalandı: True; 'hedef fiyat' tuzağı yakalandı: True; 'risksiz' tuzağı yakalandı: True |
| 4 | `REL3-TREND-04-CRYPTOGRAPHIC-REPRODUCIBILITY` | Kriptografik Tekrarlanabilirlik ve Determinizm (SHA-256) | ✅ PASS | 10 bağımsız koşuda hash tekilliği: True (hash=9fe76c5df941db9e); Yön determinizmi: True (BULLISH_TENDENCY); Kural seti determinizmi: True |
| 5 | `REL3-TREND-05-COOLDOWN-DEDUPLICATION` | Soğuma (Cooldown) ve Tekilleştirme Mekanizması | ✅ PASS | 3 bar önceki sinyalde cooldown_active=True (beklenen: True); 10 bar önceki sinyalde cooldown_active=False (beklenen: False) |
| 6 | `REL3-TREND-06-LIQUIDITY-QUALITY-FILTERS` | Likidite ve Veri Kalitesi Filtresi Şeffaflığı | ✅ PASS | Kısa seri filtresi: True; Düşük hacim filtresi: True; Düşük ciro filtresi: True; Bozuk OHLC filtresi: True |

## 3. Metodoloji ve Uyum Standartları

- **Tek Gösterge Savunması:** Tek bir gösterge kuralı kesinlikle sinyal sayılmaz. En az 3 kural ve 2 bağımsız gösterge kategorisi (Trend, Momentum, Hacim) zorunludur.
- **Açıklanabilirlik ve Geçersizleşme (Invalidation):** Üretilen her eğilim sinyalinin hangi kurallarla oluştuğu, sayısal eşikleri ve koşul geçersizleşmesi şeffaftır.
- **SPK ve Hukuki Kırmızı Çizgi Güvencesi:** 'Al', 'sat', 'hedef fiyat', 'kesinlikle', 'risksiz' gibi ifadeler PROHIBITED_TERMS ile taranır; aykırılıkta ValueError fırlatılır.
- **Determinizm ve Tekrar Üretilebilirlik:** Her eğilim çıktısı sembol, zaman damgası, yön ve kural kümesini içeren 16 karakterlik SHA-256 `reproducibility_hash` taşır.
- **Soğuma ve Tekilleştirme (Cooldown):** Tekrarlayan bildirim kirliliğini önlemek adına belirtilen çubuk sayısı (cooldown_bars) boyunca sinyal cooldown_active işaretlenir.
- **Veri Kalitesi ve Likidite Filtresi:** Hacim, TL işlem tutarı, veri bütünlüğü ve volatilite eşiklerini sağlamayan sığ veya bozuk tahtalar doğrudan elenir.

## 4. Kabul ve Sonuç Kararı

- [x] Trend kuralları açıklanabilir ve tekrar üretilebilir.
- Sürüm 3 kabul kriterlerinin 2. maddesi eksiksiz tamamlanmış olup onaylanmıştır.
