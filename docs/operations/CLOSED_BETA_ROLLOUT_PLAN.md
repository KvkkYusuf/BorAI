# Kapalı Beta ve Kademeli Dağıtım Planı (Closed Beta & Phased Rollout Plan)

**Doküman Kodu:** `DOC-OPS-ROLLOUT-001`  
**Sürüm:** `v1.0.0`  
**Yasal & Operasyonel Dayanak:** SPK Bilgi Sistemleri Tebliği (VII-128.9), KVKK m. 12, FAZ-16 Dağıtım Öncesi Kontrol Listesi  
**Yetkili:** SRE, Ürün ve Uyum Ekibi  

---

## 1. Amaç ve Kapsam

Bu plan, **Borsa Analiz Sistemi**'nin canlı ortama dağıtımı sırasında sistem güvenilirliğini, veri hattı tutarlılığını ve mevzuata uyumu sağlamak amacıyla uygulanacak kontrollü kapalı beta ve kademeli dağıtım (canary rollout) prosedürlerini tanımlar.

Sistem bir bilgilendirme aracıdır; kesinlikle yatırım tavsiyesi vermez. Bu yasal sınırın korunması ve altyapı yükünün kademeli test edilmesi için tüm adımlar otomasyon kapılarıyla denetlenir.

---

## 2. Dağıtım Merdiveni ve Aşamalar

| Aşama | Kod | Kitle / Hedef Oran | Erişim Kuralı | Minimum Gözlem (Soak) | Çıkış Kriteri |
|---|---|---|---|---|---|
| **Aşama 1** | `CLOSED_BETA` | 50 Seçilmiş Kullanıcı | Yalnızca Davetiye & Allowlist | 72 Saat | Sıfır P1/P2 hata, SPK beyan kabulü %100 |
| **Aşama 2** | `CANARY_5` | %5 Rastgele Kullanıcı | Deterministik SHA-256 Cohort | 48 Saat | Hata oranı < %0.5, p95 gecikme < 300ms |
| **Aşama 3** | `CANARY_25` | %25 Kullanıcı | Deterministik SHA-256 Cohort | 48 Saat | Hata oranı < %0.8, veri hattı gecikmesi 0 |
| **Aşama 4** | `CANARY_50` | %50 Kullanıcı | Deterministik SHA-256 Cohort | 48 Saat | Hata oranı < %0.8, altyapı kaynakları stabil |
| **Aşama 5** | `GA_100` | %100 Genel Dağıtım | Tüm kayıtlı kullanıcılar | Sürekli | Kararlı genel sürüm |

---

## 3. Deterministik Kullanıcı Havuzu (Cohort Hashing)

Kademeli geçişlerde kullanıcıların her istekte farklı gruba düşmesini engellemek için deterministik SHA-256 algoritması kullanılır:

$$\text{Bucket} = \text{SHA256}(\text{user\_id} + \text{salt}) \pmod{100}$$

- `Bucket < 5` ise kullanıcı `%5` canary grubuna atanır.
- `Bucket < 25` ise kullanıcı `%25` grubuna atanır.
- `Bucket < 50` ise kullanıcı `%50` grubuna atanır.
- Allowlist'te yer alan kapalı beta kullanıcıları aşamadan bağımsız olarak her zaman öncelikli tam erişime sahiptir.

---

## 4. Otomatik Devre Kesici (Circuit Breaker) ve Rollback Tetikleyicileri

Sistem telemetrisi Prometheus üzerinden gerçek zamanlı izlenir. Aşağıdaki eşiklerden herhangi biri aşıldığında **otomatik devre kesici tetiklenir**:

1. **Hata Oranı:** 5 dakikalık kayan pencerede `HTTP 5xx` hata oranı **%1.0** üzerine çıkarsa.
2. **Kritik Alarm:** Herhangi bir aktif **P1** veya **P2** operasyonel alarm oluşursa.
3. **Veri Bütünlüğü Bozulması:** BIST veri boru hattında doğrulanamayan seans verisi veya split/temettü anomalisi yakalanırsa.
4. **Acil Durum Manuel Müdahalesi:** Uyum veya güvenlik yöneticisi acil durdurma emri verirse.

### Devre Kesici Eylemleri
- Kademeli dağıtım derhal `%0` canary seviyesine (yalnızca kapalı beta allowlist) çekilir.
- RTO hedefi: Otomatik geri alma süresi **< 300 saniye**.
- Operasyon ve SIRT ekiplerine otomatik P1 bildirim gönderilir.

---

## 5. Yasal ve Uyum Şartları (SPK & KVKK)

Kapalı beta veya kademeli dağıtım havuzuna dahil olan her kullanıcı için:
1. **SPK Uyarısı:** "Bu sistem bilgilendirme amaçlıdır; yatırım tavsiyesi verilmez." beyanının açıkça onaylanması şarttır.
2. **KVKK Aydınlatması:** Çerez, telemetri ve hata günlüğü işleme açık rıza metninin kabul edilmesi zorunludur.
3. Onayı bulunmayan veya davetiye kodu doğrulanmamış kullanıcıların analitik ve tahmin sayfalarına erişimi engellenir.

---

## 6. Dağıtım Öncesi Hazır Bulunuşluk Kapısı (BetaRolloutGatekeeper)

`BetaRolloutGatekeeper` aşağıdaki 6 kritik kontrol maddesini otomatik denetler:
- `BETA-01-ALLOWLIST-ACCESS-CONTROL`: Davetiye ve allowlist mekanizması devrede mi?
- `BETA-02-PHASED-ROLLOUT-STAGES`: 5 aşamalı dağıtım merdiveni ve deterministik cohort algoritması hazır mı?
- `BETA-03-CIRCUIT-BREAKER-ROLLBACK`: Otomatik devre kesici (%1.0 hata eşiği) ve geri alma senaryosu tatbikatı yapıldı mı?
- `BETA-04-LEGAL-SPK-KVKK`: SPK yatırım tavsiyesi kalkanı ve KVKK şartları davet akışına entegre mi?
- `BETA-05-PROMETHEUS-TELEMETRY`: Prometheus metrikleri (`beta_rollout_*`) tanımlı ve veri üretiyor mu?
- `BETA-06-OPERATIONAL-DOCUMENTATION`: Resmi dağıtım planı belgesi onaylandı mı?

Tüm kontroller eksiksiz **PASS** vermeden sürüm yayınlanamaz.
