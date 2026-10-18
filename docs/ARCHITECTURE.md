# Mimari Taslak

## Veri akışı
1. Lisanslı sağlayıcı adaptörü piyasa verisini alır.
2. Doğrulama katmanı kayıtları kontrol eder ve veri kalitesini işaretler.
3. Ham ve normalize edilmiş veri uygun depolara yazılır.
4. Analitik servisi göstergeleri hesaplar.
5. Trend tarayıcı tanımlı kuralları değerlendirir.
6. PyTorch tahmin servisi yalnızca onaylanmış model sürümünü kullanır.
7. API, veri kaynağı/zamanı ve belirsizlik metadatasıyla sonucu arayüze sunar.
8. Uyarı servisi kullanıcı tercihine göre bildirim gönderir.

## Sınırlar
- Piyasa verisi kullanıcı kimlik verisinden mantıksal olarak ayrılır.
- Tarayıcıya sağlayıcı gizli anahtarları gönderilmez.
- Eğitim işleri kullanıcıya açık API'den ayrıdır.
- Modelin yayınlanması bir onay kapısından geçer.
- Admin paneli en az ayrıcalıkla korunur.

## Önerilen servis sorumlulukları
- `market_data`: sağlayıcı adaptörleri, doğrulama, normalizasyon
- `analytics`: teknik göstergeler, açıklanabilir kurallar
- `inference`: sürümlenmiş PyTorch modeli ile tahmin
- `alerts`: kurallar, tekrar kontrolü, teslimat
- `api`: kimlik doğrulama, yetkilendirme, kullanıcıya dönük sözleşme
- `web`: erişilebilir, duyarlı kullanıcı arayüzü

## Kritik tasarım kararları
- Kaynak ve zaman damgası her fiyat serisinde bulunmalı.
- Ham ve düzeltilmiş fiyat ayrılmalı.
- Tüm zamanlar UTC'de saklanmalı; kullanıcıya yerel saat diliminde gösterilmeli.
- Veri gecikmesi açıkça görünmeli.
- Model tahmini ve klasik teknik analiz ayrı etiketlenmeli.

## v2 eklemeleri
- **Önbellek katmanı:** Redis; anahtar `sembol:aralık:veri_sürümü`. API okuma yolu: önbellek → DB.
- **Toplu (batch) üretim:** seans sonrası göstergeler, tarama ve tahmin önceden hesaplanır; kullanıcı isteğinde ağır hesap yok.
- **Özellik bayrakları:** `FORECAST_ENABLED`, `LLM_ENABLED`; kapalıyken arayüz güvenli alternatif gösterir.
- **LLM açıklama servisi (isteğe bağlı):** yalnızca hazır JSON sonuçtan metin üretir; kota/bütçe/önbellek/filtre arkasında (`docs/AI_LLM_SAFETY.md`).
- **Maliyet ve kullanım ölçümü:** token, çağrı, önbellek isabeti, kullanıcı başına maliyet metriği.
- **Anonim salt okunur mod:** kayıt olmadan örnek sembol arama/grafik (hızlı ilk değer); yazma işlemleri kimlik doğrulamalı.
- **Zaman ve yerelleştirme:** UTC saklama, Europe/Istanbul gösterim, Türkçe sayı/tarih biçimi.
