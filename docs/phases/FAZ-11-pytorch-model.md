## 11. PyTorch model geliştirme — basitten karmaşığa

### Baseline önce
- [x] Önce “son değer”, tarihsel ortalama veya basit momentum gibi naif kıyas modelleri oluştur.
- [x] Tahmin hedefini netleştir: ör. belirli ufukta getiri, yön sınıflandırması veya volatilite. Aynı anda çok hedef ekleme.
- [x] Hedef ufuklarını belirle (ör. 1 gün, 5 gün, 20 işlem günü); sonuçları birbirine karıştırma.
- [x] Özellikleri yalnızca tahmin anında bilinen bilgilerden üret.
- [x] Başlangıç modeli olarak küçük MLP veya 1D CNN; veri ve deney gerekçelendirirse LSTM/GRU/Transformer değerlendir.
- [x] Rastgele seed, yapılandırma, veri sürümü, metrikler ve model ağırlıklarını kaydet.
- [x] Eğitim döngüsünde early stopping, gradient kontrolü ve checkpoint kullan.
- [x] Modeli eğitimde kullanılan normalizasyon/özellik dönüşümleriyle birlikte sürümle.

### Değerlendirme ve backtest
- [x] Walk-forward veya expanding-window değerlendirmesi kur.
- [x] Finansal metrikleri doğru hesapla: MAE/RMSE yönü değerlendirmek için tek başına yeterli değildir.
- [x] Yön doğruluğu, kalibrasyon, tahmin aralığı kapsaması ve uygun kıyas metriklerini raporla.
- [x] Strateji simülasyonu yapılıyorsa işlem maliyeti, spread, kayma, gecikme ve pozisyon sınırlarını ekle.
- [x] Sharpe, maksimum düşüş ve getiri gibi metrikleri örneklem/varsayım açıklamasıyla raporla; geçmiş sonuçların geleceği garanti etmediğini belirt.
- [x] Farklı piyasa rejimlerinde, kriz dönemlerinde ve sembol gruplarında stres testi yap.
- [x] Birden çok deneme nedeniyle oluşabilecek backtest overfitting riskini kayda al.
- [x] Naif baseline'ı ve işlem maliyetli gerçekçi kıyası geçemeyen modeli kullanıcıya tahmin üretmek için yayınlama.
- [x] Tahminleri tek sayı yerine uygun olduğunda aralık/olasılık ve belirsizlik bilgisiyle göster.
- [x] Canlıya geçmeden önce gölge modunda (shadow mode) bir süre izle.

### Üretim ve izleme
- [x] Model kayıt defteri ve model onay süreci kur.
- [x] Model sürümü, eğitim tarihi, veri aralığı ve özellik setini her tahminle ilişkilendir.
- [x] Veri kayması, performans düşüşü, gecikme ve tahmin dağılımı için alarm tanımla.
- [x] Otomatik yeniden eğitimden önce kalite kapısı ve insan onayı koy.
- [x] Model başarısızsa güvenli fallback göster; sessizce eski veya hatalı tahmin üretme.

**Çıktı:** Tekrarlanabilir eğitim hattı, model kartı, backtest raporu ve gölge mod raporu.

### v2 eklemeleri
- [x] Çıkarımı toplu (batch) ve CPU'da hızlı olacak şekilde tasarla; TorchScript/ONNX dışa aktarma değerlendir.
- [x] Model yükleme güvenliği: `pickle` yok, hash doğrulama, `weights_only`/safetensors.
- [x] `FORECAST_ENABLED` bayrağı; baseline'ı geçmezse kapalı ve arayüzde "tahmin kapalı" durumu.
- [x] İsteğe bağlı LLM açıklama katmanı: `docs/AI_LLM_SAFETY.md` kabul testleri geçmeden açma.
