# Operasyonel Olay Müdahale ve Rollback El Kitabı (Runbook)

Bu doküman, Borsa Analiz Sistemi'nin canlı ortamında ortaya çıkabilecek kritik altyapı, veri bozulması, güvenlik ihlali ve performans gerilemesi vakalarında operasyon, SRE ve güvenlik ekipleri tarafından izlenecek standart operasyonel prosedürleri (SOP) belirler.

---

## 1. Olay Sınıflandırması ve Eskalasyon Matrisi

| Şiddet | Kriter | İlk Müdahale Hedefi (MTTA) | Çözüm/Hafifletme Hedefi (MTTR) | Eskalasyon |
|---|---|---|---|---|
| **SEV-1 (Kritik)** | Tam servis kesintisi, KVKK veri sızıntısı, DB bozulması, veri sağlayıcı kesintisi | < 15 dakika | < 60 dakika | CISO, CTO, Incident Commander, SRE Lideri |
| **SEV-2 (Yüksek)** | p95 yanıt süresi > 2s, Hata oranı > %1, Model tahmin anomalisi, Sağlayıcı gecikmesi > 15 dk | < 30 dakika | < 120 dakika | SecOps Mühendisi, Backend Lideri |
| **SEV-3 (Orta)** | Tekil kullanıcı anomalileri, önbellek düşüşü, bütçe kotası %80 aşımı | < 2 saat | < 8 saat | Nöbetçi Mühendis |
| **SEV-4 (Düşük)** | Hatalı pozitif alarmlar, arayüz kozmetik uyarıları | < 24 saat | < 48 saat | Backlog Triyajı |

---

## 2. PICERL Olay Müdahale Döngüsü

1. **Hazırlık (Preparation):** Alarmların ve log aggregator'ların (Prometheus/Grafana/Loki) aktifliği doğrulanır.
2. **Tanımlama (Identification):** Anomali kaynağı (API, DB, ML Model, Dış Sağlayıcı) korelasyon kimliğiyle tespit edilir.
3. **Sınırlama (Containment):** Etkilenen alt sistem izole edilir, yazma trafiği geçici olarak `READ_ONLY` moduna alınır veya Cloudflare/WAF kuralları devreye sokulur.
4. **Ortadan Kaldırma (Eradication):** Hatalı dağıtım geri alınır (Rollback), sızan anahtarlar iptal edilir veya bozuk veri işaretlenir.
5. **Kurtarma (Recovery):** Sağlıklı yedekten sandbox geri yükleme yapılır, sistem doğrulanır ve kademeli trafik açılır.
6. **Çıkarılan Dersler (Lessons Learned):** 72 saat içinde Post-Mortem (Kök Neden Analizi) raporu yayımlanır.

---

## 3. Otomatik ve Yarı-Otomatik Geri Dönüş (Rollback) Prosedürü

Herhangi bir sürüm dağıtımında gerileme (hata oranı > %1 veya p95 gecikme > 800ms) tespit edildiğinde:

### Adım 1: Trafik Geri Çekme
- Container imajı önceki kararlı sürüme (`previous_stable_version`) yönlendirilir.
- Canary ağırlığı derhal `%0` seviyesine çekilir (`ROLLBACK_COORDINATOR.assess_and_trigger`).

### Adım 2: Veritabanı Şema Geri Alma (Gerekiyorsa)
- Veritabanı şeması son dağıtımda değiştiyse:
  ```bash
  alembic downgrade -1
  ```
- SQLite bütünlük kontrolü çalıştırılır:
  ```sql
  PRAGMA integrity_check;
  ```

### Adım 3: Model Tahmin Fallback
- Model sürümü önceki onaylı safetensors ağırlıklarına çekilir.
- Baseline kontrolü başarısız ise güvenli fallback (`SafeForecastFallbackManager`) devreye sokulur; kullanıcıya sessiz tahmin üretilmez.

### Adım 4: Önbellek ve Oturum Geçersiz Kılma
- Redis / Ephemeral bellek temizlenir (`CACHE.flush()`).
- Versiyon uyumsuzluğu riski taşıyan aktif oturumlar sonlandırılır.

### Adım 5: Sağlık Doğrulaması (Smoke Test)
- `/health/ready` ve `/health/live` uç noktalarının HTTP 200 verdiği doğrulanır.
- RTO hedefi: Geri dönüşün baştan sona tamamlanma süresi **< 300 saniye (5 dakika)** olmalıdır.

---

## 4. İletişim ve Yasal Bildirimler

- **KVKK (Kişisel Verileri Koruma Kurumu):** Doğrulanmış veri sızıntılarında en geç **72 saat** içinde `https://ihlal.kvkk.gov.tr` üzerinden bildirim yapılır.
- **SPK (Sermaye Piyasası Kurulu):** Bilgi sistemleri kesintilerinde **24 saat içinde** ivedilikle bildirim sağlanır.
- **Kullanıcı Bilgilendirmesi:** Durum sayfası (`/status`) üzerinden şeffaf bilgilendirme yapılır; asılsız vaat veya kişiye özel yatırım telkini yapılmaz.
