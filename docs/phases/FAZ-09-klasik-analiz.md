## 9. Klasik analiz ve trend tarama

- [x] İlk etapta yapay zekâ olmadan temel analitik hesaplamaları kur. (Bkz: `src/stock_analysis/indicators/technical.py`, `tests/test_indicators.py`)
- [x] Basit göstergeler ekle: hareketli ortalamalar, RSI, MACD, ATR, hacim ortalaması ve oynaklık. (Bkz: `calculate_sma`, `calculate_ema`, `calculate_rsi`, `calculate_macd`, `calculate_atr`, `calculate_volume_sma`, `calculate_volatility`)
- [x] Her gösterge için formül, parametre ve testler yaz. (Bkz: `tests/test_indicators.py`)
- [x] Trend koşullarını açık ve yapılandırılabilir kurallarla tanımla; tek bir göstergeyi kesin sinyal sayma. (Bkz: `src/stock_analysis/indicators/trend.py`, `TrendEngine`)
- [x] İşlem maliyeti, spread ve kayma olmadan strateji performansını gerçekçi kabul etme. (Bkz: `src/stock_analysis/indicators/cost_model.py`, `ExecutionSimulator`, `is_realistic=False`)

- [x] Sinyalin hangi koşullarla üretildiğini ve ne zaman geçersizleşeceğini açıkla. (Bkz: `invalidation_criteria`, `TrendSignalResult`)
- [x] Kullanıcıya “yükseliş başladı” gibi kesin hüküm yerine “tanımlı koşullara göre yükseliş eğilimi olasılığı arttı” gibi ölçülü dil kullan. (Bkz: `PROHIBITED_TERMS`, `validate_compliant_text`)
- [x] Sinyal tekrarlarını azaltmak için cooldown ve deduplication uygula. (Bkz: `cooldown_bars`, `reproducibility_hash`)
- [x] Analiz sonuçlarını aynı veri kesitiyle tekrar üretilebilir kıl. (Bkz: `reproducibility_hash`, deterministik `evaluate`)

**Çıktı:** Test edilmiş indikatörler ve açıklanabilir trend tarayıcısı.

### v2 eklemeleri
- [x] Göstergeleri bağımsız referansla (ör. bilinen kütüphane çıktısı/örnek veri) karşılaştıran test ekle. (Bkz: `tests/test_indicators_reference.py`)
- [x] Sinyal metinleri `docs/DISCLAIMER_TR.md` biçimine uysun; yasaklı ifade testi (`/hukuk-kapisi`). (Bkz: `TestLegalComplianceAndLanguage`)
