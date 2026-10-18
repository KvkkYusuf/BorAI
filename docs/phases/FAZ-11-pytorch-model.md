## 11. PyTorch model geliştirme — basitten karmaşığa

### Baseline önce
- [ ] Önce “son değer”, tarihsel ortalama veya basit momentum gibi naif kıyas modelleri oluştur.
- [ ] Tahmin hedefini netleştir: ör. belirli ufukta getiri, yön sınıflandırması veya volatilite. Aynı anda çok hedef ekleme.
- [ ] Hedef ufuklarını belirle (ör. 1 gün, 5 gün, 20 işlem günü); sonuçları birbirine karıştırma.
- [ ] Özellikleri yalnızca tahmin anında bilinen bilgilerden üret.
- [ ] Başlangıç modeli olarak küçük MLP veya 1D CNN; veri ve deney gerekçelendirirse LSTM/GRU/Transformer değerlendir.
- [ ] Rastgele seed, yapılandırma, veri sürümü, metrikler ve model ağırlıklarını kaydet.
- [ ] Eğitim döngüsünde early stopping, gradient kontrolü ve checkpoint kullan.
- [ ] Modeli eğitimde kullanılan normalizasyon/özellik dönüşümleriyle birlikte sürümle.

### Değerlendirme ve backtest
- [ ] Walk-forward veya expanding-window değerlendirmesi kur.
- [ ] Finansal metrikleri doğru hesapla: MAE/RMSE yönü değerlendirmek için tek başına yeterli değildir.
- [ ] Yön doğruluğu, kalibrasyon, tahmin aralığı kapsaması ve uygun kıyas metriklerini raporla.
- [ ] Strateji simülasyonu yapılıyorsa işlem maliyeti, spread, kayma, gecikme ve pozisyon sınırlarını ekle.
- [ ] Sharpe, maksimum düşüş ve getiri gibi metrikleri örneklem/varsayım açıklamasıyla raporla; geçmiş sonuçların geleceği garanti etmediğini belirt.
- [ ] Farklı piyasa rejimlerinde, kriz dönemlerinde ve sembol gruplarında stres testi yap.
- [ ] Birden çok deneme nedeniyle oluşabilecek backtest overfitting riskini kayda al.
- [ ] Naif baseline'ı ve işlem maliyetli gerçekçi kıyası geçemeyen modeli kullanıcıya tahmin üretmek için yayınlama.
- [ ] Tahminleri tek sayı yerine uygun olduğunda aralık/olasılık ve belirsizlik bilgisiyle göster.
- [ ] Canlıya geçmeden önce gölge modunda (shadow mode) bir süre izle.

### Üretim ve izleme
- [ ] Model kayıt defteri ve model onay süreci kur.
- [ ] Model sürümü, eğitim tarihi, veri aralığı ve özellik setini her tahminle ilişkilendir.
- [ ] Veri kayması, performans düşüşü, gecikme ve tahmin dağılımı için alarm tanımla.
- [ ] Otomatik yeniden eğitimden önce kalite kapısı ve insan onayı koy.
- [ ] Model başarısızsa güvenli fallback göster; sessizce eski veya hatalı tahmin üretme.

**Çıktı:** Tekrarlanabilir eğitim hattı, model kartı, backtest raporu ve gölge mod raporu.

### v2 eklemeleri
- [ ] Çıkarımı toplu (batch) ve CPU'da hızlı olacak şekilde tasarla; TorchScript/ONNX dışa aktarma değerlendir.
- [ ] Model yükleme güvenliği: `pickle` yok, hash doğrulama, `weights_only`/safetensors.
- [ ] `FORECAST_ENABLED` bayrağı; baseline'ı geçmezse kapalı ve arayüzde "tahmin kapalı" durumu.
- [ ] İsteğe bağlı LLM açıklama katmanı: `docs/AI_LLM_SAFETY.md` kabul testleri geçmeden açma.
