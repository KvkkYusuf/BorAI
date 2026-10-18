## 9. Klasik analiz ve trend tarama

- [ ] İlk etapta yapay zekâ olmadan temel analitik hesaplamaları kur.
- [ ] Basit göstergeler ekle: hareketli ortalamalar, RSI, MACD, ATR, hacim ortalaması ve oynaklık.
- [ ] Her gösterge için formül, parametre ve testler yaz.
- [ ] Trend koşullarını açık ve yapılandırılabilir kurallarla tanımla; tek bir göstergeyi kesin sinyal sayma.
- [ ] Likidite, işlem hacmi, oynaklık ve veri kalitesi filtreleri ekle.
- [ ] İşlem maliyeti, spread ve kayma olmadan strateji performansını gerçekçi kabul etme.
- [ ] Sinyalin hangi koşullarla üretildiğini ve ne zaman geçersizleşeceğini açıkla.
- [ ] Kullanıcıya “yükseliş başladı” gibi kesin hüküm yerine “tanımlı koşullara göre yükseliş eğilimi olasılığı arttı” gibi ölçülü dil kullan.
- [ ] Sinyal tekrarlarını azaltmak için cooldown ve deduplication uygula.
- [ ] Analiz sonuçlarını aynı veri kesitiyle tekrar üretilebilir kıl.

**Çıktı:** Test edilmiş indikatörler ve açıklanabilir trend tarayıcısı.

### v2 eklemeleri
- [ ] Göstergeleri bağımsız referansla (ör. bilinen kütüphane çıktısı/örnek veri) karşılaştıran test ekle.
- [ ] Sinyal metinleri `docs/DISCLAIMER_TR.md` biçimine uysun; yasaklı ifade testi (`/hukuk-kapisi`).
