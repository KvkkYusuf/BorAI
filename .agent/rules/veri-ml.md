# Veri bilimi ve model kuralları

- Zaman serisinde rastgele train/test bölmesi yok; zamana göre ayır, walk-forward/expanding-window kullan.
- Look-ahead bias, survivorship bias, veri sızıntısını her deneyde kontrol et ve raporla. Ön işleme yalnızca eğitim verisine fit edilir.
- Önce naif baseline (son değer, hareketli ortalama, basit momentum). Baseline'ı yenmeyen karmaşık modeli ekleme.
- Test setini model seçimi için tekrar tekrar kullanma; çok deneme yapıldıysa backtest overfitting riskini kayda geçir.
- Seed, veri sürümü, hiperparametre, metrik, ağırlık dosyası, eğitim zamanı kaydedilir (`ml/model_registry/`).
- Metrikler: yön doğruluğu, kalibrasyon, aralık kapsaması, işlem maliyetli kıyas. Tek metrik (MAE/RMSE) yeterli değildir.
- Model kapısı: baseline'ı + maliyetli gerçekçi kıyası geçmezse `forecast_enabled=false`. Sessiz fallback yok; kullanıcıya "tahmin şu an kapalı" göster.
- Çıkarım (inference) maliyeti: günlük/saatlik **toplu (batch)** tahmin üret, sonucu önbellekte tut; her kullanıcı isteğinde model çalıştırma. CPU'da hızlı çalışsın diye model küçük tutulur; TorchScript/ONNX dışa aktarımı değerlendirilir.
- Kaggle verisi: lisans, kaynak zinciri, tarih aralığı, para birimi ve saat dilimi doğrulanmadan kullanılmaz. Ham veri Git'e girmez (DVC/nesne depolama).
- Model dosyalarını güvenilmeyen kaynaktan `pickle` ile yükleme; mümkünse `safetensors`/TorchScript ve `weights_only=True`.
