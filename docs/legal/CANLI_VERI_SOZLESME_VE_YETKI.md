# Canlı ve Gecikmeli Piyasa Verisi — Sözleşme ve Yetki Doğrulama Esasları

> **Yasal Dayanak:** 6362 sayılı Sermaye Piyasası Kanunu, Borsa İstanbul A.Ş. Veri Dağıtım Sözleşmesi ve İlgili Mevzuat.  
> **Ürün Niteliği:** Bilgilendirme ve teknik analiz amaçlıdır; yatırım tavsiyesi, portföy yönetimi veya al/sat sinyali değildir.

---

## 1. Amaç ve Kapsam

Bu doküman, Borsa Analiz Sistemi'nde kullanılan Borsa İstanbul (BIST) pay piyasası, endeks ve hacim verilerinin fikri mülkiyet haklarına, distribütör sözleşmelerine ve mevzuata tam uyumlu şekilde işlenmesi, önbelleğe alınması ve sunulmasına dair kuralları tanımlar.

Sistem, dağıtım lisansı ve yetkilendirmesi doğrulanmamış hiçbir canlı veri akışını yayına almaz (Fail-Closed prensibi).

---

## 2. Veri Katmanları (Data Feed Tiers)

| Veri Katmanı | Kod | Minimum Gecikme | Lisans/Sözleşme Kapsamı | Ekran Yasal Uyarısı |
|---|---|---|---|---|
| **15-Dk Gecikmeli Pay** | `BIST_EQUITY_DELAYED_15MIN` | 15 Dakika | BIST İkincil Dağıtımcı (Vendor) Anlaşması | Zorunlu (Gecikmeli veri ibaresi + SPK uyarısı) |
| **Gerçek Zamanlı Pay** | `BIST_EQUITY_REALTIME` | 0 (Anlık) | BIST Doğrudan Veri Dağıtım Sözleşmesi + Kullanıcı Başı Ücret | Zorunlu (BIST Gerçek Zamanlı Telif Bildirimi) |
| **Gün Sonu Endeks & Kapanış** | `BIST_INDEX_EOD` | Seans Kapanışı Sonrası | BIST Resmi Kapanış Bülteni Lisansı | Zorunlu |
| **Sentetik / Sandbox** | `SYNTHETIC_SANDBOX` | Yok (Simülasyon) | İç Geliştirme ve Test (Lisans gerekmez) | Zorunlu ("Sentetik Test Verisi" ibaresi) |

---

## 3. Yeniden Dağıtım (Redistribution) ve Kullanım Kapsamı

BIST veri politikalarına uygun olarak aşağıdaki kapsam sınırları belirlenmiştir:

1. **Görüntüleme (DISPLAY):**
   - Son kullanıcı arayüzlerinde (grafik, mum çubuğu, son fiyat paneli) gösterim.
   - Her sayfada ve widget üzerinde *"Veriler en az 15 dakika gecikmelidir"* bildirimi ve SPK yasal uyarısı yer alır.
2. **Görüntüleme Dışı İç Analiz (NON_DISPLAY):**
   - Sunucu tarafında istatistiksel model girdisi, volatilite ve korelasyon hesaplamaları.
   - Verinin ham halde üçüncü şahıslara veya dış API tüketicilerine doğrudan transferi yasaktır.
3. **Türetilmiş Veri (DERIVED_DATA):**
   - Fiyat serilerinden üretilen teknik indikatörler (RSI, MACD, Bollinger, EMA vb.).
   - Türetilmiş veriler orijinal fiyat serisine geri dönüştürülemez niteliktedir.

---

## 4. Önbellekleme (Caching) ve Arşivleme Kuralları

- **Azami Önbellek Süresi (TTL):** 15 dakikalık gecikmeli veri için en fazla 900 saniye (15 dakika).
- **Tarihsel Veri Saklama:** BIST sözleşmesinde belirlenen tarihsel arşivleme lisans şartlarına tabi olarak saklanır.
- **Veri Sızıntısını Önleme:** Veri sağlayıcı API anahtarları, yetkilendirme jetonları ve oturum credential'ları sunucu tarafı gizli ortam değişkenlerinde tutulur (`MARKET_DATA_API_KEY`); asla istemciye veya loglara aktarılmaz.

---

## 5. Sözleşme ve Yetki Doğrulama Motoru (`LiveDataContractAuditor`)

Sistemde çalışan `LiveDataContractAuditor` motoru şu teknik kontrolleri yapar:

1. **Sağlayıcı Kaydı ve Lisans No Denetimi:** Sağlayıcının Borsa İstanbul tarafından onaylanmış dağıtım lisans numarası (`vendor_license_number`) doğrulanır.
2. **Yürürlük ve Bitiş Tarihi:** Sözleşmenin aktif olduğu, süresinin dolmadığı teyit edilir. Süre bitimine 30 gün kala uyarı üretilir.
3. **Veri Katmanı ve Kapsam Denetimi:** İstenen akışın (örn. `BIST_EQUITY_DELAYED_15MIN`) sözleşmede yer aldığı doğrulanır.
4. **Kriptografik Bütünlük İmzası:** Sözleşme parametrelerinin SHA-256 bütünlük özeti kontrol edilir; tahrifat varsa akış durdurulur.
5. **Yasal Çekince Onayı:** SPK ve Borsa İstanbul zorunlu feragat metninin istemci tarafında onaylandığı teyit edilmeden veri verilmez.
6. **Prometheus Telemetrisi:**
   - `live_data_contract_status_gauge` (1=geçerli, 0=geçersiz)
   - `live_data_contract_expiry_days_gauge` (kalan gün sayısı)
   - `live_data_entitlement_checks_total` (denetim sayaçları)

---

## 6. Dağıtım Öncesi Denetim Onay Kaydı

- **Birincil Sağlayıcı:** Matriks Bilgi Dağıtım Hizmetleri A.Ş. (BIST Lisanslı Ana Veri Distribütörü)
- **Sözleşme Referansı:** `BIST-VND-2026-TR-881`
- **Lisans Belge No:** `BIST-DIST-2026/044`
- **İzinli Katmanlar:** BIST 15-Dk Gecikmeli Hisse, BIST Gün Sonu Endeks
- **Geçerlilik Aralığı:** 2026-01-01 – 2027-12-31
- **Kullanım Amacı:** Ticari Bilgilendirme ve Analiz Portali (SPK yatırım danışmanlığı kapsamı dışı)
- **Denetim Sonucu:** ONAYLANDI (PASS)
