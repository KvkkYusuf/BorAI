## 10. Kaggle veri setleri ve veri bilimi disiplini

- [x] Kaggle'da hedef borsa, semboller, tarih aralığı, OHLCV ve kurumsal işlem bilgileri açısından uygun veri setleri ara.
- [x] Veri setinin lisansını, kaynak zincirini, güncelliğini, para birimini, saat dilimini ve kapsamını doğrula.
- [x] Lisans ve kalite kontrolünden geçmeyen veri setini kullanma.
- [x] Eğitim, doğrulama ve test dönemlerini zamana göre ayır; rastgele satır bölmesiyle zaman serisi sızıntısı oluşturma.
- [x] Gelecek verisinin özelliklere sızmadığını kontrol et (look-ahead bias).
- [x] Farklı sembollerin fiyat ölçeklerini ve kurumsal işlem etkilerini ele al.
- [x] Hayatta kalma yanlılığını (survivorship bias), delist olmuş hisseleri ve seçilmiş sembol yanlılığını değerlendir.
- [x] Eğitim/test ayrımını ve veri sürümünü kaydet.
- [x] Kaggle verisini canlı veri sağlayıcısıyla aynı kabul etme; canlıya geçişte kaynak ve şema farklarını test et.
- [x] Ham veriyi Git'e koyma; DVC veya uygun veri sürümleme/nesne depolama yaklaşımı kullan.

**Çıktı:** Veri kartı (dataset card), veri doğrulama raporu ve tekrar üretilebilir veri hazırlama kodu.

### v2 eklemeleri
- [x] Lisansı belirsiz veri setini üründe kullanma; yalnızca yerel deney olarak işaretle.
- [x] Veri kartına lisans, kaynak, tarih aralığı, delist/survivorship notu ekle.
