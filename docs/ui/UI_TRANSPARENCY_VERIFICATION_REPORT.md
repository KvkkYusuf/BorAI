# Dağıtım Öncesi Arayüz Şeffaflık Denetim Raporu (UI Transparency Audit Report)

**Tarih:** 2026-10-28 22:37:14 UTC  
**Mevzuat Dayanağı:** SPK III-52.4 & Borsa İstanbul Veri Dağıtım Esasları  
**Kapı Değerlendirme Durumu:** 🟢 **ONAYLANDI (PASS)**  
**Yayımlama Durumu:** ✅ Üretime Dağıtım İçin Uygundur  

## 1. Yönetici Özeti

Bu rapor, Borsa Analiz Sistemi web arayüzünde (UI) ve kullanıcı deneyiminde (UX) Borsa İstanbul veri gecikmesi (15 dakika kuralı) ve yapay zeka model tahmin belirsizliği (güven aralığı, naif kıyas, hedef fiyat vaadi içermeme, güvenli fallback) unsurlarının mevzuata uygunluğunu belgeler.

| Metrik | Değer | Hedef / Eşik | Durum |
|---|---|---|---|
| Toplam Şeffaflık Denetimi | 19 | 19 | ✅ TAM |
| Başarılı Denetimler | 19 | %100 | ✅ %100 |
| Veri Gecikmesi Denetimleri | 8 | 8 | ✅ Doğrulandı |
| Model Belirsizliği Denetimleri | 8 | 8 | ✅ Doğrulandı |
| Güvenli Fallback Denetimleri | 3 | 3 | ✅ Doğrulandı |
| Kritik İhlal Sayısı | 0 | 0 | ✅ SIFIR |

## 2. Ayrıntılı Denetim Maddeleri ve Bulgular

| ID | Kategori | Denetim Maddesi | Hedef Dosya | Sonuç |
|---|---|---|---|---|
| `LAT-01` | `DATA_LATENCY` | Üst Başlık (Header) Veri Gecikme Rozeti | `static/index.html` | ✅ GEÇTİ |
| `LAT-02` | `DATA_LATENCY` | Kenar Çubuğu (Sidebar) Piyasa Durumu ve Zaman Damgası | `static/index.html` | ✅ GEÇTİ |
| `LAT-03` | `DATA_LATENCY` | Hisse Detay Ekranı Gecikme Rozeti | `static/index.html` | ✅ GEÇTİ |
| `LAT-04` | `DATA_LATENCY` | Gecikmeli ve Bayat Veri Uyarı Başlığı (Banner) | `static/index.html` | ✅ GEÇTİ |
| `LAT-05` | `DATA_LATENCY` | Borsa İstanbul 15 Dakika Yasal Mevzuat Bildirimi | `static/index.html` | ✅ GEÇTİ |
| `LAT-06` | `DATA_LATENCY` | Anlık İşlem Emri Verilemez Yasal Uyarısı | `static/index.html` | ✅ GEÇTİ |
| `LAT-07` | `DATA_LATENCY` | Dinamik Gecikme Durumu İstemci Mantığı (JS) | `static/js/app.js` | ✅ GEÇTİ |
| `LAT-08` | `DATA_LATENCY` | Gecikme Göstergeleri Erişilebilirlik (A11y/ARIA) | `static/index.html` | ✅ GEÇTİ |
| `UNC-01` | `MODEL_UNCERTAINTY` | Model Tahmin Aralığı ve Belirsizlik Kartı | `static/index.html` | ✅ GEÇTİ |
| `UNC-02` | `MODEL_UNCERTAINTY` | İstatistiki Güven Bandı Sınırları Gösterimi | `static/index.html` | ✅ GEÇTİ |
| `UNC-03` | `MODEL_UNCERTAINTY` | Model Belirsizlik Skoru ve Görsel Ölçer | `static/index.html` | ✅ GEÇTİ |
| `UNC-04` | `MODEL_UNCERTAINTY` | Kesin Hedef Fiyat Vaadi Teşkil Etmez Beyanı | `static/index.html` | ✅ GEÇTİ |
| `UNC-05` | `MODEL_UNCERTAINTY` | Yatırım Danışmanlığı / Al-Sat Tavsiyesi Değildir Beyanı | `static/index.html` | ✅ GEÇTİ |
| `UNC-06` | `MODEL_UNCERTAINTY` | Naif Kıyas (Naive Baseline) Doğrulama Beyanı | `static/index.html` | ✅ GEÇTİ |
| `UNC-07` | `MODEL_UNCERTAINTY` | Çoklu Vadeye Göre Belirsizlik Ölçeklemesi (1D-10D) | `static/js/app.js` | ✅ GEÇTİ |
| `UNC-08` | `MODEL_UNCERTAINTY` | Model Sürümü, Mimari ve Soykütüğü Şeffaflığı | `static/index.html` | ✅ GEÇTİ |
| `SAF-01` | `FALLBACK_AND_SAFETY` | Model Devredışı / Güvenli Fallback Görsel Alanı | `static/index.html` | ✅ GEÇTİ |
| `SAF-02` | `FALLBACK_AND_SAFETY` | Sıfır Uydurma Tahmin Güvencesi ve Açıklaması | `static/index.html` | ✅ GEÇTİ |
| `SAF-03` | `FALLBACK_AND_SAFETY` | Model Durumu Simülasyonu ve Test Butonu | `static/index.html` | ✅ GEÇTİ |

## 3. Denetim Ayrıntıları ve Kanıtlar

### `LAT-01` — Üst Başlık (Header) Veri Gecikme Rozeti
- **Kategori:** `DATA_LATENCY`
- **Açıklama:** Masaüstü ve mobil üst barda 15 dk gecikmeli BIST veri durumunu gösteren buton ve etiket.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `header-latency-label, btn-header-latency`
- **Doğrulanan Metin / İfade Örüntüleri:** `15 Dk Gecikmeli BIST, toggleDataLatencyMode()`
- **Teknik Ayrıntı:** Header alanında kullanıcıya anlık gecikme durumunu bildiren buton ve etiket doğrulandı.

### `LAT-02` — Kenar Çubuğu (Sidebar) Piyasa Durumu ve Zaman Damgası
- **Kategori:** `DATA_LATENCY`
- **Açıklama:** Yan menüde piyasa genel durumunu, mevzuat gecikmesini ve UTC+3 saat damgasını gösteren rozet.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `sidebar-latency-badge, sidebar-latency-status, sidebar-latency-desc, sidebar-latency-time`
- **Doğrulanan Metin / İfade Örüntüleri:** `15 Dk Gecikmeli, 15 Dk (Mevzuat)`
- **Teknik Ayrıntı:** Sidebar kutusunda gecikme rozeti ve saat dilimi bilgisi doğrulandı.

### `LAT-03` — Hisse Detay Ekranı Gecikme Rozeti
- **Kategori:** `DATA_LATENCY`
- **Açıklama:** Hisse detay başlığında fiyatın anlık değil 15 dk gecikmeli olduğunu gösteren rozet.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `detail-latency-badge, detail-latency-text`
- **Doğrulanan Metin / İfade Örüntüleri:** `15 Dk Gecikmeli`
- **Teknik Ayrıntı:** Hisse fiyatı yanında 15 dk gecikmeli rozeti doğrulandı.

### `LAT-04` — Gecikmeli ve Bayat Veri Uyarı Başlığı (Banner)
- **Kategori:** `DATA_LATENCY`
- **Açıklama:** Kullanıcıya fiyatların canlı değil gecikmeli olduğunu bildiren sarı uyarı kutusu.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `detail-stale-banner, stale-banner-title, stale-banner-desc`
- **Doğrulanan Metin / İfade Örüntüleri:** `DİKKAT: Veri 15 Dakika Gecikmelidir`
- **Teknik Ayrıntı:** Detay ekranında belirgin uyarı başlığı doğrulandı.

### `LAT-05` — Borsa İstanbul 15 Dakika Yasal Mevzuat Bildirimi
- **Kategori:** `DATA_LATENCY`
- **Açıklama:** Mevzuat gereği verilerin en az 15 dakika gecikmeli olduğuna dair zorunlu yasal ibare.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `N/A`
- **Doğrulanan Metin / İfade Örüntüleri:** `Borsa İstanbul mevzuatı gereği, en az 15 dakika gecikmelidir`
- **Teknik Ayrıntı:** BIST mevzuat zorunluluğu açıkça beyan edilmektedir.

### `LAT-06` — Anlık İşlem Emri Verilemez Yasal Uyarısı
- **Kategori:** `DATA_LATENCY`
- **Açıklama:** Gecikmeli verilerin anlık piyasa fiyatı yerine geçmeyeceği ve işlem emrinde kullanılamayacağı uyarısı.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `N/A`
- **Doğrulanan Metin / İfade Örüntüleri:** `anlık piyasa fiyatı yerine geçmez; anlık işlem emri için kullanılamaz`
- **Teknik Ayrıntı:** Yanıltmayı engelleyen işlem sınırlama uyarısı doğrulandı.

### `LAT-07` — Dinamik Gecikme Durumu İstemci Mantığı (JS)
- **Kategori:** `DATA_LATENCY`
- **Açıklama:** İstemci tarafında seans açık, kapalı, gecikmeli ve bayat durum geçişlerini yöneten mantık.
- **Hedef Dosya:** `static/js/app.js`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `N/A`
- **Doğrulanan Metin / İfade Örüntüleri:** `toggleDataLatencyMode, setDataLatencyMode, "delayed", "stale", "realtime"`
- **Teknik Ayrıntı:** JavaScript durum makinesinde 3 farklı gecikme durumu ve UI güncelleme fonksiyonları doğrulandı.

### `LAT-08` — Gecikme Göstergeleri Erişilebilirlik (A11y/ARIA)
- **Kategori:** `DATA_LATENCY`
- **Açıklama:** Ekran okuyucular için ARIA role ve aria-label etiketlerinin gecikme göstergelerinde bulunması.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `N/A`
- **Doğrulanan Metin / İfade Örüntüleri:** `role="status", aria-label="Veri gecikme durumu"`
- **Teknik Ayrıntı:** Ekran okuyucu uyumluluğu için role='status' ve etiketler doğrulandı.

### `UNC-01` — Model Tahmin Aralığı ve Belirsizlik Kartı
- **Kategori:** `MODEL_UNCERTAINTY`
- **Açıklama:** Yapay zeka model çıktılarını kesin hedef fiyat yerine aralık olarak sunan özel kart.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `prediction-main-card, prediction-active-content`
- **Doğrulanan Metin / İfade Örüntüleri:** `Model Tahmin Aralığı & Belirsizlik Payı`
- **Teknik Ayrıntı:** Teknik analiz ekranında tahmin aralığı kartı doğrulandı.

### `UNC-02` — İstatistiki Güven Bandı Sınırları Gösterimi
- **Kategori:** `MODEL_UNCERTAINTY`
- **Açıklama:** Tahminin %90 güven aralığındaki alt ve üst sınırlarını açıkça gösteren kutu.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `prediction-band-text, prediction-band-min, prediction-band-max`
- **Doğrulanan Metin / İfade Örüntüleri:** `%90 Güven Aralığı, Alt Bant Sınırı, Üst Bant Sınırı`
- **Teknik Ayrıntı:** Alt/Üst sınır ve güven seviyesi etiketleri doğrulandı.

### `UNC-03` — Model Belirsizlik Skoru ve Görsel Ölçer
- **Kategori:** `MODEL_UNCERTAINTY`
- **Açıklama:** Hata payı yüzdesini (± %X,X) ve çubuk göstergesini içeren belirsizlik ölçeri.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `prediction-uncertainty-score, prediction-uncertainty-bar`
- **Doğrulanan Metin / İfade Örüntüleri:** `Model Belirsizlik Skoru:, ± %2,8 Belirsizlik`
- **Teknik Ayrıntı:** Belirsizlik rozeti ve progress bar bileşeni doğrulandı.

### `UNC-04` — Kesin Hedef Fiyat Vaadi Teşkil Etmez Beyanı
- **Kategori:** `MODEL_UNCERTAINTY`
- **Açıklama:** Model tahmininin kesin bir fiyat taahhüdü veya garanti olmadığına dair zorunlu ibare.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `N/A`
- **Doğrulanan Metin / İfade Örüntüleri:** `Kesin sonuç veya hedef fiyat vaadi teşkil etmez`
- **Teknik Ayrıntı:** Hedef fiyat vaadi yasağı beyanı şeffafça doğrulandı.

### `UNC-05` — Yatırım Danışmanlığı / Al-Sat Tavsiyesi Değildir Beyanı
- **Kategori:** `MODEL_UNCERTAINTY`
- **Açıklama:** SPK mevzuatı gereği çıktının al-sat tavsiyesi veya yatırım danışmanlığı olmadığı beyanı.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `N/A`
- **Doğrulanan Metin / İfade Örüntüleri:** `kişiye özel yatırım danışmanlığı veya al-sat tavsiyesi içermez`
- **Teknik Ayrıntı:** SPK al/sat ve yönlendirme yasağı ibaresi doğrulandı.

### `UNC-06` — Naif Kıyas (Naive Baseline) Doğrulama Beyanı
- **Kategori:** `MODEL_UNCERTAINTY`
- **Açıklama:** Modelin basit bir kıyaslama metodolojisine göre test edildiğinin kullanıcıya açıklanması.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `N/A`
- **Doğrulanan Metin / İfade Örüntüleri:** `Model, basit bir kıyas yöntemine (naive baseline) göre test edilmiştir`
- **Teknik Ayrıntı:** Naive baseline kıyaslama şeffaflığı doğrulandı.

### `UNC-07` — Çoklu Vadeye Göre Belirsizlik Ölçeklemesi (1D-10D)
- **Kategori:** `MODEL_UNCERTAINTY`
- **Açıklama:** Tahmin vadesi uzadıkça hata payı ve belirsizlik bandının genişlemesini sağlayan mantık.
- **Hedef Dosya:** `static/js/app.js`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `N/A`
- **Doğrulanan Metin / İfade Örüntüleri:** `setPredictionHorizon, uncertaintyScore, ± %1,2 Belirsizlik, ± %4,5 Belirsizlik`
- **Teknik Ayrıntı:** Vadeye bağlı belirsizlik ölçeklemesi ve dinamik UI doğrulandı.

### `UNC-08` — Model Sürümü, Mimari ve Soykütüğü Şeffaflığı
- **Kategori:** `MODEL_UNCERTAINTY`
- **Açıklama:** Modelin versiyonu, mimarisi ve baseline kıyas başarısının arayüzde gösterilmesi.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `prediction-model-version-badge, prediction-meta-version, prediction-meta-baseline`
- **Doğrulanan Metin / İfade Örüntüleri:** `v1.4.2, LSTM + LGBM`
- **Teknik Ayrıntı:** Model sürüm etiketi ve mimari açıklaması doğrulandı.

### `SAF-01` — Model Devredışı / Güvenli Fallback Görsel Alanı
- **Kategori:** `FALLBACK_AND_SAFETY`
- **Açıklama:** Model baseline'ın altında kaldığında veya bayrak kapalıyken uydurma tahmin yerine gösterilen kart.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `prediction-model-fallback, prediction-status-badge`
- **Doğrulanan Metin / İfade Örüntüleri:** `Tahmin Özelliği Şu Anda Kullanılamıyor, role="alert"`
- **Teknik Ayrıntı:** Erişilebilir uyarı kutusu (role='alert') ve fallback kartı doğrulandı.

### `SAF-02` — Sıfır Uydurma Tahmin Güvencesi ve Açıklaması
- **Kategori:** `FALLBACK_AND_SAFETY`
- **Açıklama:** Tahmin kapalıyken kullanıcının yanıltılmadığı ve klasik göstergelere yönlendirildiği açıklaması.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `N/A`
- **Doğrulanan Metin / İfade Örüntüleri:** `Model performansı naif kıyas (baseline) doğrulama aşamasındadır veya özellik bayrağı kapalıdır, Klasik teknik analiz göstergeleri kullanılabilir`
- **Teknik Ayrıntı:** Kullanıcıya güvenli fallback mantığı açıkça izah edilmektedir.

### `SAF-03` — Model Durumu Simülasyonu ve Test Butonu
- **Kategori:** `FALLBACK_AND_SAFETY`
- **Açıklama:** Test/denetim aşamasında tahmin kartının aktif ve fallback durumları arasında geçişini sağlayan buton.
- **Hedef Dosya:** `static/index.html`
- **Durum:** ✅ GEÇTİ (PASS)
- **Doğrulanan DOM Elemanları / Seçiciler:** `btn-toggle-model-status`
- **Doğrulanan Metin / İfade Örüntüleri:** `toggleModelStatus()`
- **Teknik Ayrıntı:** Test ve doğrulama için model durum değiştirme arayüz kontrolü doğrulandı.

## 4. Dağıtımı Engelleyen Kritik İhlaller

✅ Dağıtımı engelleyen herhangi bir şeffaflık ihlali bulunmamaktadır.

## 5. Mevzuat ve Hukukî Uyumluluk Beyanı

1. **Borsa İstanbul Veri Dağıtım Esasları:** Gecikmeli verilerin en az 15 dakika gecikmeli olduğu, ekran üzerinde açıkça belirtilmekte; anlık emir için kullanılamayacağı kullanıcıya bildirilmektedir.
2. **SPK III-52.4 Yatırım Hizmetleri Tebliği:** Arayüzdeki istatistiki tahminlerin kesin hedef fiyat veya al/sat tavsiyesi olmadığı, belirsizlik aralığıyla sunulduğu ve modelin naif baseline'a göre test edildiği şeffafça açıklanmaktadır.
3. **Güvenli Fallback:** Model arızalandığında veya bayrak kapalı olduğunda asla uydurma tahmin üretilmemekte; kullanıcıya durum şeffaf bir arayüz kartıyla iletilmektedir.

---
*Bu rapor `2026-10-28 22:37:14 UTC` tarihinde `UiTransparencyGatekeeper` motoru tarafından otomatik olarak oluşturulmuştur.*