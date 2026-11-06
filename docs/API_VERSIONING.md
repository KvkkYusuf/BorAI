# API Sürümleme ve Geriye Uyumluluk Politikası

Bu belge, Borsa Analiz Sistemi API'sinin sürümleme stratejisini, geriye uyumluluk kurallarını ve kullanım ömrü (lifecycle / deprecation / sunset) süreçlerini tanımlar.

---

## 1. Sürümleme Stratejisi

### 1.1 URL Tabanlı Ana Sürümleme (URI Versioning)
- API ana sürümleri URL yolunda açıkça belirtilir:
  - `/api/v1/...` (Mevcut kararlı sürüm)
  - `/api/v2/...` (Gelecekteki olası kırıcı değişiklikler için)
- Kırıcı (breaking) değişiklikler **asla** mevcut sürüm yolu altında yayınlanmaz; yeni bir ana sürüm yolu (`/api/v2`) açılır.

### 1.2 Semantik Sürümleme (SemVer 2.0.0)
API spesifikasyonu ve sürüm etiketi `MAJOR.MINOR.PATCH` biçimindedir (Örn. `1.0.0`):
- **MAJOR (Ana Sürüm):** Geriye uyumsuz (breaking) API değişiklikleri. URL yolu değişir (`/api/v1` -> `/api/v2`).
- **MINOR (İkincil Sürüm):** Geriye uyumlu yeni özellikler, yeni uç noktalar, yeni isteğe bağlı parametreler veya yanıta yeni alan eklemeleri. URL yolu değişmez.
- **PATCH (Yama Sürümü):** Geriye uyumlu hata düzeltmeleri, performans iyileştirmeleri ve dokümantasyon güncellemeleri.

---

## 2. Geriye Uyumluluk Kuralları

### 2.1 Kırıcı Olmayan Değişiklikler (Geriye Uyumlu)
Aşağıdaki değişiklikler mevcut ana sürüm (`/api/v1`) içinde yapılabilir:
1. **Yeni uç noktalar ekleme:** Mevcut istemcileri etkilemeyen yeni REST endpoint'leri.
2. **Yanıta yeni alanlar ekleme:** İstemciler Postel Kuralı'na (Robustness Principle) uygun olarak bilmedikleri alanları yok saymalıdır.
3. **İsteğe bağlı (optional) parametreler ekleme:** Varsayılan değeri olan veya zorunlu olmayan yeni sorgu/gövde parametreleri.
4. **Kısıtlamaları gevşetme:** Örneğin önceden maksimum 50 olan limitin 100'e çıkarılması.

### 2.2 Kırıcı Değişiklikler (Breaking Changes)
Aşağıdaki değişiklikler kesinlikle yeni bir ana sürüm gerektirir:
1. Mevcut uç noktaların kaldırılması veya URL yolunun değiştirilmesi.
2. Yanıt şemasından mevcut alanların silinmesi veya alan adlarının değiştirilmesi.
3. Yanıt veya istek alanlarının veri tiplerinin değiştirilmesi (örn. string -> integer).
4. İsteğe yeni zorunlu (required) parametre veya başlık eklenmesi.
5. Hata kodlarının veya HTTP durum kodlarının mevcut istemcileri yanıltacak şekilde değiştirilmesi.

---

## 3. Kullanımdan Kaldırma ve Kapatma Süreci (Deprecation & Sunset)

RFC 8594 ve RFC 9745 standartlarına uygun olarak kullanımdan kaldırma süreci işletilir:

### 3.1 Süreç Takvimi
1. **Duyuru ve Deprecation:** Bir uç nokta veya ana sürüm kullanımdan kaldırılmaya karar verildiğinde en az **180 gün (6 ay)** öncesinden duyurulur.
2. **Standart HTTP Başlıkları:**
   - `Deprecation: true` (veya RFC 9745 tarih/zaman damgası)
   - `Sunset: <HTTP-date>` (Örn. `Wed, 22 Apr 2027 00:00:00 GMT` — RFC 7231 formatında kapatılma tarihi)
   - `Link: <https://api.borsa.local/docs/migration>; rel="deprecation"` (Geçiş kılavuzuna yönlendiren bağlantı)
3. **Kapatma (Sunset):** Belirtilen sunset tarihi dolduktan sonra eski sürüm `410 Gone` veya `404 Not Found` yanıtı verir.

### 3.2 Eşzamanlı Sürüm Desteği (N ve N-1)
Sistem en fazla **iki** ana sürümü (`vCurrent` ve `vPrevious`) eşzamanlı olarak destekler. `v3` çıktığında `v1` için sunset süresi başlatılır ve `v1` emekliye ayrılır.

---

## 4. İstemci Sözleşmesi ve Güvenceler

1. **OpenAPI Şeması:** Her sürüm için OpenAPI 3.1 şeması (`/openapi.json`) sözleşme garantisidir. CI hattında şema regresyon testleri çalışır.
2. **Yasal Sorumluluk Reddi:** Tüm sürümlerde `X-Disclaimer` başlığı zorunludur.
3. **Versiyon Sorgulama Uç Noktası:** `/api/v1/version` ucu ile istemciler anlık sürüm, durum ve deprecation bilgilerini programatik olarak sorgulayabilir.
