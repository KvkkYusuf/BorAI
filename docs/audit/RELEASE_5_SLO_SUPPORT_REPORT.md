# Sürüm 5 — SLO ve Destek Süreci Kabul Denetim Raporu

> **Değerlendirme Tarihi:** 2026-10-29 19:18:19 UTC  
> **Genel Kabul Kapısı Durumu:** **PASS** (6/6 Kontrol Geçti)  
> **İlgili Sürüm Kapsamı:** `FAZ-17 Sürüm 5 (Beta ve Üretim)`  
> **Referans Standartlar:** SPK Bilgi Sistemleri Tebliği (VII-128.9), KVKK Kanunu m. 12, Google SRE SLO/SLA Standartları  

---

## 1. Yönetici Özeti

Bu denetim raporu, Borsa Analiz Sistemi'nin Sürüm 5 (Beta ve Üretim) aşaması kapsamında 'SLO ve destek süreci aktif' kabul kriterinin eksiksiz sağlandığını onaylar. Sistem; %99.5 aylık kullanılabilirlik SLO'su ve %0.1'in altındaki 5xx hata oranı ile %0.5'lik hata bütçesini (error budget) izlemekte; önbellekli p95 < 300ms ve önbelleksiz p95 < 800ms yanıt süresi ile seans bitimi <= 30 dk EOD veri güncelliğini sağlamaktadır. Kullanıcı destek süreçleri resmi e-posta ve API uç noktaları üzerinden devreye alınmış, acil vakalarda 4 saat ve standart vakalarda 24 saat SLA garantisi tanımlanmış; yatırım tavsiyesi arayışlarını engelleyen SPK kalkanı, PII maskeleme ve kritik arızaların IncidentResponseCoordinator'a otomatik eskalasyonu uçtan uca doğrulanmıştır.

## 2. Kabul Kontrolleri Matrisi

| Kontrol ID | Kontrol Adı | Durum | Doğrulama Detayı |
|---|---|:---:|---|
| `REL5-SLO-01-AVAILABILITY-AND-ERROR-BUDGET` | **Sistem Kullanılabilirliği ve Hata Bütçesi SLO'su** | ✅ PASS | Ölçülen Kullanılabilirlik: %99.95 (Hedef >= %99.5), 5xx Hata Oranı: %0.045 (Tavan <= %0.100), Kalan Hata Bütçesi: %91.0 (Tüketim: %9.0). |
| `REL5-SLO-02-API-LATENCY-AND-DATA-FRESHNESS` | **API Yanıt Süresi ve Veri Güncelliği SLO'su** | ✅ PASS | Önbellekli API p95: 245.0 ms (< 300 ms), Önbelleksiz API p95: 710.0 ms (< 800 ms), Veri Güncelliği: fresh (Gecikme: 60.0 sn). |
| `REL5-SLO-03-SUPPORT-CHANNELS-AND-SLA-ROUTING` | **Destek Kanalları, Triage ve SLA Yönlendirmesi** | ✅ PASS | Resmi E-Posta: destek@bora-analiz.local, Güvenlik Kanalı: guvenlik@bora-analiz.local, Acil SLA: 4 saat, Standart SLA: 24 saat, Hazır Bulunuşluk: PASS (5/5). |
| `REL5-SLO-04-SPK-INVESTMENT-ADVICE-SHIELD` | **SPK Yatırım Tavsiyesi Otomatik Kalkanı** | ✅ PASS | Tavsiye Arayışı Tespiti: TETİKLENDİ, Otomatik SPK Kalkan Notu: EKLENDİ, Genel Çekince Beyanı: MEVCUT. |
| `REL5-SLO-05-SUPPORT-PII-PRIVACY-SHIELD` | **Destek Veri Gizliliği ve Hassas Veri (PII) Kalkanı** | ✅ PASS | Maskelenen PII Öğe Sayısı: 4, TCKN Maskeleme: BAŞARILI, Kredi Kartı Maskeleme: BAŞARILI, IBAN Maskeleme: BAŞARILI, Parola Maskeleme: BAŞARILI. |
| `REL5-SLO-06-INCIDENT-ESCALATION-INTEGRATION` | **Destek Taleplerinin Olay Yönetimine Eskalasyonu** | ✅ PASS | Olay ID: INC-20261029-4CC861, Ciddiyet Seviyesi: SEV_1_CRITICAL, Bilet Bağlantısı: SUP-202610-3A9E25AD -> INC-20261029-4CC861, Bilet Durumu: under_review. |

## 3. SLO ve Destek Hizmet Seviyesi Metrikleri

- **Hedef Kullanılabilirlik (SLO):** >= %99.5 (Ölçülen: ~%99.95) — ✅ UYUMLU
- **HTTP 5xx Hata Oranı:** <= %0.100 (Ölçülen: ~%0.045) — ✅ UYUMLU
- **Kalan Hata Bütçesi (Error Budget):** >= %50.0 (Kalan: ~%91.0) — ✅ UYUMLU
- **Önbellekli API p95 Yanıt Süresi:** < 300 ms (Ölçülen: ~245 ms) — ✅ UYUMLU
- **Önbelleksiz API p95 Yanıt Süresi:** < 800 ms (Ölçülen: ~710 ms) — ✅ UYUMLU
- **Veri Güncelliği (EOD):** <= 30 dk — ✅ UYUMLU
- **Acil Destek Yanıt SLA'sı:** <= 4 saat — ✅ UYUMLU
- **Standart Destek Yanıt SLA'sı:** <= 24 saat — ✅ UYUMLU
- **SPK Yatırım Tavsiyesi Filtresi:** %100 Otomatik Çekince — ✅ UYUMLU
- **Destek PII Gizlilik Maskelemesi:** TCKN/Kart/IBAN/Şifre Tam Koruma — ✅ UYUMLU

## 4. Yasal SPK Uyarısı

> SPK Mevzuati Uyarinca: Bu sistem bir bilgi ve analiz aracidir; kisisellestirilmis yatirim danismanligi, hisse oneri veya portfoy yonetimi hizmeti saglamaz. Sistem SLO hedefleri ve destek surecleri teknik hizmet kalitesini, sistem guvenligini ve kullanici memnuniyetini temin etmek amaciyla isletilmektedir.

---
*Bu denetim raporu sistem tarafından otomatik olarak üretilmiştir.*