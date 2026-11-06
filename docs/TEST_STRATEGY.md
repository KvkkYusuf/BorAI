# Test Stratejisi ve Kalite Standartları (FAZ-14)

Bu belge, **Borsa Analiz Sistemi** için test piramidi mimarisini, birim/entegrasyon/sözleşme/E2E test sınırlarını, veri hijyeni kurallarını ve kabul ölçütlerini tanımlar.

---

## 1. Test Piramidi Mimarisi

Sistem, hızlı geri bildirim ve yüksek güvenilirlik sağlamak amacıyla 4 katmanlı test piramidi modelini uygular:

```
          / \
         /   \      [Katman 4: E2E & Kabul Testleri]
        / E2E \     - Kritik kullanıcı akışları (Kayıt, Arama, Grafik, Uyarı, Silme)
       /-------\    - docs/ACCEPTANCE_CRITERIA.md SLA hedefleri
      / Sözleşme\   [Katman 3: Sözleşme ve Güvenlik Testleri]
     / & Güvenlik\  - OpenAPI / Pydantic şema uyumu, OWASP Top 10, LLM Safety
    /-------------\ [Katman 2: Entegrasyon Testleri]
   /  Entegrasyon  \- DB (PostgreSQL/SQLite), Redis önbellek, EOD kuyruğu, API
  /-----------------\[Katman 1: Saf Birim Testleri (Temel Katman)]
 /   Birim Testler   \- İndikatörler, Fiyat Dönüşümleri, Veri Temizleme, RBAC, Uyarılar
/---------------------\- Sıfır I/O, %100 bellek içi, < 5 sn toplam yürütme
```

---

## 2. Birim Test Katmanı ve İlkeleri (Unit Testing)

Birim testler sistemin en geniş tabanını oluşturur ve aşağıdaki katı kurallarla yürütülür:

1. **Sıfır Dış Bağımlılık (No I/O):**
   - Disk dosyası okuma/yazma, SQLite/PostgreSQL veritabanı, Redis önbellek veya dış ağ çağrıları kesinlikle yapılamaz.
   - Tüm girdiler bellek içi deterministik diziler, pandas Serileri veya Pydantic modelleridir.
2. **Yürütme Hızı Hedefi:**
   - Tüm birim test paketi yerel geliştirici makinesinde **< 5 saniyede** tamamlanmalıdır (`uv run pytest -m unit`).
3. **Determinizm ve Tekrarlanabilirlik:**
   - Rastgelelik gerektiren sentetik veri üretiminde rastgele seed sabitlenir (`seed=42`). Test sonuçları zamandan veya ortamdan bağımsız olarak her çalıştırmada aynı çıktıyı vermelidir.
4. **Gerçek Kullanıcı Verisi Yasağı:**
   - Test fixture'larında gerçek kişi adları, gerçek e-posta adresleri veya üretim sırları/tokenları bulunamaz.

---

## 3. Beş Çekirdek Birim Alanı ve Doğrulama Matrisi

FAZ-14 gereğince sistemin 5 kritik matematiksel ve iş mantığı bileşeni saf birim testleriyle doğrulanır:

| Çekirdek Alan | Modül | Test Edilen Uç Durumlar ve Doğrulamalar | İlgili Test Modülü |
|---|---|---|---|
| **1. İndikatörler** | `stock_analysis.indicators.technical` | SMA, EMA, RSI (0-100 sınırı, sürekli artış/düşüş), MACD (çizgi/sinyal/histogram farkı), ATR (True Range gap kontrolü, pozitiflik), Volatilite (yıllıklandırma, min periyot) | `tests/test_indicators.py`, `tests/test_unit_core_suite.py` |
| **2. Fiyat Dönüşümleri** | `stock_analysis.pipeline.corporate_actions`, `price_scaling` | Bedelsiz/bedelli bölünme (SPLIT) geriye dönük fiyat yarılanması, nakit temettü düzeltmesi, Min-Max / standart normalizasyon ile nominal fiyat farkının (10 TL vs 1000 TL) giderilmesi | `tests/test_corporate_actions.py`, `tests/test_unit_core_suite.py` |
| **3. Veri Temizleme** | `stock_analysis.pipeline.validator` | Sıfır/negatif fiyat engeli, High < Low mantıksal bozulma reddi, mükerrer seans tarihi ayıklama, kronolojik sıra bozulması (out-of-order) tespiti, hacim sıçrama anomalisi uyarısı | `tests/test_data_integrity.py`, `tests/test_unit_core_suite.py` |
| **4. Yetkilendirme (RBAC)** | `stock_analysis.security.audit`, `environments` | `RoleHierarchy` rank sıralaması (`GUEST < USER < ANALYST < COMPLIANCE == ML < ADMIN < SUPER_ADMIN`), `can_assign_role` yetki tavanı, yetki yükseltme engeli (`PrivilegeEscalationError`) | `tests/test_admin_audit_rbac.py`, `tests/test_unit_core_suite.py` |
| **5. Uyarı Koşulları** | `stock_analysis.alerts.rules` | Eşik sınır denetimleri (`RuleValidationError`), `AlertRuleConfig` VE/VEYA mantığı, yatırım tavsiyesi içermeyen Türkçe açıklanabilir metin (`explain()`), DataFrame üzerinde tetiklenme doğrulaması | `tests/test_alert_rules.py`, `tests/test_unit_core_suite.py` |

---

## 4. Pytest Marker ve Koşum Düzeni

Testlerin hız ve kapsamına göre filtrelenebilmesi için `pyproject.toml` altında aşağıdaki marker'lar tanımlanmıştır:

```bash
# Sadece saf ve hızlı birim testlerini koşmak için (< 5 sn):
uv run pytest -m unit

# Yalnızca sözleşme ve şema drift testlerini koşmak için:
uv run pytest -m contract

# Yalnızca entegrasyon testlerini koşmak için:
uv run pytest -m integration

# Güvenlik ve RBAC testlerini koşmak için:
uv run pytest -m security

# Kritik E2E kullanıcı akışlarını koşmak için:
uv run pytest -m e2e

# Yük, eşzamanlılık ve sağlayıcı kesintisi dayanıklılık testlerini koşmak için:
uv run pytest -m resilience

# Tüm test paketini sessiz modda koşmak için:
uv run pytest -q
```

---

## 5. Sözleşme Test Katmanı ve Şema Değişiklikleri (Contract Testing & Schema Drift)

Sözleşme testleri (`tests/test_contract_schemas_suite.py`, `pytest -m contract`), veri sağlayıcıları ile API istemcileri arasındaki şema uyumluluğunu ve geriye dönük uyumluluğu garanti altına alır:

1. **Sağlayıcı Veri Sözleşmesi (Provider Contract):**
   - `MarketBarRecord` ve `CorporateAction` zorunlu alanları, fiyat pozitiflik sınırları, High/Low/Open/Close mantıksal sınırları ve Decimal duyarlılığı test edilir.
   - Sağlayıcıdan gelen beklenmeyen/eksik alanlar `LiveSchemaTransitionValidator` ile tespit edilir; eksik zorunlu alanlar `CRITICAL` seviyede engellenirken ek alanlar `INFO` seviyesinde tolere edilir (tolerant reader).
2. **OpenAPI 3.1.0 Şema Doğruluğu ve Model Kararlılığı:**
   - FastAPI OpenAPI JSON spesifikasyonunun bütünlüğü, yasal bilgilendirme metninin (`DISCLAIMER_TEXT`) varlığı ve tüm kritik endpoint'lerin (`/api/v1/market/symbols`, `/api/v1/market/history/{symbol}`, vb.) OpenAPI şemasıyla uyumu denetlenir.
   - Hassas veri sızıntı koruması: Hiçbir response modelinde parola, hash, tuz (salt) veya gizli anahtar alanı yer alamaz.
3. **Kırıcı Değişiklik Denetçisi (Breaking Changes Auditor):**
   - `OpenApiContractAuditor` motoru ile baseline ve mevcut spesifikasyon karşılaştırılır.
   - Endpoint silinmesi, response modelinden alan kaldırılması, alan tipinin değişmesi veya request modellerine yeni zorunlu alan eklenmesi `BREAKING` olarak engellenir.
   - Yeni endpoint eklenmesi veya isteğe bağlı (opsiyonel) alanlar geriye dönük uyumlu kabul edilir.

---

---

## 6. Kabul Kriterleri Bağlantısı (`docs/ACCEPTANCE_CRITERIA.md`)

- **Kapsama Oranı:** Kritik hesaplama (indikatörler, fiyat düzeltmeleri) ve güvenlik/RBAC dallarında hedef **%100 dal (branch) kapsamasıdır**. Ancak kapsama oranı tek başına kalite kanıtı sayılmaz; negatif testler (hata fırlatma, sınır aşımı) zorunludur.
- **Hata ve Güvenlik:** Birim testlerde hiçbir istisna yutulmaz (swallowed exception yasaktır). Güvenlik yetkilendirmesi başarısız olduğunda sistem sessizce varsayılan izin vermez, kesinlikle reddeder (fail-secure).

---

## 7. Zaman Serisi Test Katmanı (`tests/test_timeseries_suite.py`, `pytest -m timeseries`)

Zaman serisi doğrulamaları, piyasa takvimi ve zaman sıralamasından kaynaklanan kritik hataları engeller:
- **BIST Takvimi ve Seans Kuralları:** İş günleri, hafta sonları, resmi tatiller, yarım seanslar ve borsa devre kesici (circuit breaker/halt) durumlarında veri akış bütünlüğü.
- **Zaman Dilimi Dönüşümleri:** Veritabanındaki UTC zaman damgaları ile BIST yerel saati (TRT, UTC+3) arasında kayıpsız dönüşüm ve yaz/kış saati (sabit UTC+3) kararlılığı.
- **Kronolojik Sıra ve Boşluk Tespiti:** Zaman serisinde tarih sıçramaları, mükerrer tarihler ve eksik seans günlerinin (gap) katı tespiti.
- **Kurumsal İşlemler:** Bedelli/bedelsiz sermaye artırımları, temettü düşüşleri ve hisse bölünmelerinde geriye dönük fiyat serisi düzeltmelerinin doğruluğu.

---

## 8. Model Test Katmanı (`tests/test_model_suite.py`, `pytest -m models`)

Makine öğrenimi ve derin öğrenme modellerinin güvenilirliği için katı model test paketi:
1. **Veri ve Zaman Sızıntısı (Data Leakage & Lookahead Bias):**
   - Ön işleme ve normalizasyon istatistiklerinin (`PointInTimeNormalizer`) yalnızca eğitim setine fit edilmesi (`PreprocessingLeakageError`).
   - `LookaheadBiasDetector` ile negatif shift, merkezli pencereler ve karar anından sonraki öznitelik zaman damgalarının engellenmesi.
   - Kronolojik bölümleme (`TimeSeriesSplitter`) ve purge/embargo tamponlarının ihlal edilmediğinin teyidi.
2. **Deterministik Tekrarlanabilirlik (Reproducibility):**
   - `seed_everything` ile Python, NumPy, PyTorch CPU/CUDA seed sabitleme.
   - Model ağırlıklarının deterministik ilklendirilmesi ve `safe_save_weights` / `safe_load_weights` ile SHA-256 bütünlük kontrolü.
   - Sabit tensörlerde bit-exact veya tolerans dahilinde kararlı çıkarım üretimi.
3. **Baseline Kıyası ve Model Kapısı (Benchmarking & Gatekeeper):**
   - Naif kıyas modelleri (`LastValue`, `HistoricalMean`, `SimpleMomentum`) hesaplamaları.
   - `ModelPublishingGate` ile naif baseline'ı geçemeyen modellerin canlıya alınmasının engellenmesi (`ModelNotApprovedForPublishingError`).
   - Aşırı performans düşüşünde çıkarım devre kesicisinin (`InferenceCircuitBreaker`) tetiklenmesi ve güvenli fallback sunumu.
4. **Uç Durumlar ve Sayısal Dayanıklılık (Edge Cases & Robustness):**
   - NaN, Inf, -Inf girdilerinin reddedilmesi ve sıfır varyans serilerinde sıfıra bölme koruması.
   - `GradientController` ile patlayan gradyan tespiti ve kırpma (clipping).
   - Ufuk ayrımı (`HorizonMismatchError`) ve tekil çıplak tahmin sunumunun kesinlikle yasaklanması (`NakedPointEstimateDisallowedError`).
   - Ekstrem çöküş senaryolarında (`MarketRegimeType.BEAR`, `HIGH_VOLATILITY`) piyasa rejim sınıflandırması.

---

## 9. Güvenlik Test Katmanı (`tests/test_security_suite.py`, `pytest -m security`)

Sistemin 4 kritik güvenlik sütununu doğrulayan kapsamlı güvenlik test paketi:
1. **Erişim Kontrolü (Access Control & RBAC & BOLA/IDOR):**
   - `RoleHierarchy` rank seviyeleri (`Guest: 0 < User: 10 < Analyst: 20 < Compliance/ML: 30 < Admin: 40 < SuperAdmin: 50`).
   - Yetki yükseltme engeli (`PrivilegeEscalationError`): Yöneticiler kendisiyle eşit/üst rolleri atayamaz ve kendi rolünü doğrudan değiştiremez (self-promotion block).
   - BOLA/IDOR nesne sahiplik koruması (`BolaOwnershipGuard`): Kullanıcıların başkalarına ait izleme listesi vb. kaynaklara yetkisiz erişimi engellenir (`BolaAccessDeniedError`).
   - Yönetici denetim izi (`record_admin_audit_event`): Parola, token, oturum sırrı ve e-posta/IP maskeleme (`hash_client_ip`, `sanitize_audit_details`).
2. **Oturum Yönetimi (Session Management & Cookies & CSRF):**
   - Çerez güvenlik bayrakları (`SessionSecurityPolicy`): Oturum çerezi `HttpOnly=True`, `Secure=True`, `SameSite="lax"`.
   - Double-Submit CSRF koruması: `borai_csrf` çerezi ve `x-csrf-token` başlığı doğrulaması. Eksik veya uyumsuz belirteçlerde HTTP 403 reddi.
   - Güvenli HTTP metotları (GET, HEAD, OPTIONS) için CSRF atlama garantisi.
   - Oturum sonlandırma (logout): Çerezlerin sıfırlanması (`Max-Age=0`) ve veritabanında SHA-256 hash ile saklanan oturumların iptali.
3. **Hız Sınırı (Rate Limiting & DoS Kalkanı):**
   - Kayan pencere (`SlidingWindowRateLimiter`): Eşiği aşan isteklerde HTTP 429 Too Many Requests ve geçerli `Retry-After` başlığı.
   - İstemci izolasyonu: Bir istemcinin kotayı tüketmesi diğer istemcileri etkilemez.
   - Katmanlı hız sınırı (`TieredRateLimiter`): Anonim (katı, IP bazlı) ve yetkilendirilmiş (daha esnek, kullanıcı bazlı) kota ayrımı.
4. **Girdi Doğrulama & Enjeksiyon Koruması (Input Validation & Injection Defense):**
   - SQL Enjeksiyonu koruması (`InjectionGuard.assert_safe_sql_param`): `' OR '1'='1`, `UNION SELECT`, `DROP TABLE` ve yorum karakterleri tespiti.
   - XSS koruması (`InjectionGuard.assert_safe_xss_content`): `<script>`, `onerror=`, `javascript:`, `<iframe>` engeli.
   - SSRF koruması (`SsrfProtectionGuard.validate_target_url`): Loopback (127.0.0.1, localhost), AWS metadata (169.254.169.254) ve özel alt ağların engellenmesi; sadece kamuya açık HTTPS adreslerine izin verilmesi.
   - Dosya yükleme koruması (`FileUploadGuard`): Dizin gezintisi (`../../`), tehlikeli uzantılar (`.exe`, `.sh`, `.php`), dosya boyutu tavanı ve ELF/PE sihirli bayt MIME sahteciliği tespiti.
   - Kitle Atama (Mass Assignment / BOPLA) koruması (`ObjectPropertyGuard`): İstemciden gelen `is_admin`, `role`, `password_hash` vb. alanların katı reddi/arındırılması ve giden yanıtlardan sırların ayıklanması.

---

## 10. E2E Test Katmanı (`tests/test_e2e_suite.py`, `pytest -m e2e`)

Gerçek kullanıcı etkileşimlerini simüle eden ve sistemin 6 kritik ana akışını uçtan uca doğrulayan kabul test paketi:
1. **Kayıt ve Giriş Akışı (Registration & Login Lifecycle):**
   - Şifre karmaşıklık politikası denetimi (uzunluk < 10 için HTTP 422, büyük harf/rakam/özel karakter eksikliği için HTTP 400 `PASSWORD_POLICY_VIOLATION`).
   - Hesap kaydı (`POST /api/v1/auth/register`) ve e-posta doğrulama token onaylaması (`POST /api/v1/auth/verify-email/confirm`).
   - Parolalı giriş (`POST /api/v1/auth/login`), `borai_session` (HttpOnly/Secure) ve `borai_csrf` çerezlerinin verilmesi.
   - Oturum kapatma (`POST /api/v1/auth/session/logout`), çerezlerin sıfırlanması ve eski oturumla gelen isteklerin HTTP 401 ile reddedilmesi.
2. **Hisse Arama ve Keşif Akışı (Stock Search & Discovery):**
   - Varsayılan BIST sembol listesi (`GET /api/v1/market/symbols`).
   - Sembol koduyla arama (`?q=THYAO`) ve şirket unvanıyla arama (`?q=Aselsan`).
   - Bulunamayan hisseler için zarif boş liste (`200 OK`, `count: 0`, `symbols: []`) dönüşü.
   - Sayfalama parametreleri (`page=1&page_size=2`) ile doğru dilimleme.
3. **Grafik Açma ve Aşağı Örnekleme Akışı (Chart View & Downsampling):**
   - EOD OHLCV çubuk serisinin getirilmesi (`GET /api/v1/market/history/{symbol}?range=1y&interval=1d&price_type=adjusted`).
   - SPK yasal uyarı başlığının (`X-Disclaimer`) zorunlu varlığı ve `CandleItem` (`date`, `open`, `high`, `low`, `close`, `volume`) veri bütünlüğü.
   - Grafik optimizasyonu için `downsample=bucket&max_points=15` ile veri seyreltme doğrulaması.
   - Düzeltilmemiş (raw) fiyat serisi seçeneği (`price_type=raw`).
4. **İzleme Listesi Akışı (Watchlist Lifecycle):**
   - Kullanıcının izleme listesini sorgulaması (`GET /api/v1/user/watchlist`).
   - Listeye sembol ekleme (`POST /api/v1/user/watchlist/{symbol}`), mükerrer eklemede idempotent kararlılık.
   - Listeden sembol silme (`DELETE /api/v1/user/watchlist/{symbol}`).
   - SPK kurallarına uyum: Yanıtta kesinlikle portföy maliyeti, alış fiyatı veya lot adedi bulunmaması.
5. **Uyarı Kurma ve Kota Akışı (Alert Rules & Quotas):**
   - Fiyat eşik uyarısı kurma (`POST /api/v1/user/alerts`, `price_above` ve `price_below`).
   - Kullanıcı başına azami 5 aktif uyarı kuralı kotası (`MAX_ALERTS_PER_USER=5`) ve 6. uyarıda HTTP 400 `ALERT_QUOTA_EXCEEDED` engeli.
   - Aktif uyarıları listeleme ve uyarı silme (`DELETE /api/v1/user/alerts/{alert_id}`).
6. **KVKK Veri İhracı ve Hesap Silme Akışı (Account Deletion & Data Purge):**
   - KVKK veri taşınabilirlik ihracı (`GET /api/v1/user/export`) ile tüm kullanıcı ilişkili varlıkların dökümü.
   - Hesabı kalıcı silme (`DELETE /api/v1/user/account`).
   - Silinen kullanıcının oturumlarının geçersiz kılınması, izleme listesi/uyarı kurallarının kaskat silinmesi.
   - 5651 Sayılı Kanun ve KVKK gereğince yasal denetim kaydının (`ACCOUNT_DELETED`) kullanıcı kimliği anonimleştirilerek (`user_id=None`) saklanması.
7. **Birleşik Eksiksiz Kullanıcı Yolculuğu (Unified Full Lifecycle Journey):**
   - Tek oturumda kayıt, e-posta onaylama, giriş, hisse arama, grafik inceleme, izleme listesine ekleme, uyarı kurma, tercih güncelleme, veri ihracı alma, hesap silme ve oturumun geçersiz kaldığını teyit eden uçtan uca kesintisiz senaryo.

---

## 11. Yük ve Dayanıklılık Test Katmanı (`tests/test_load_resilience_suite.py`, `pytest -m resilience`)

Sistemin yüksek eşzamanlılık, sağlayıcı arızaları ve olağandışı yük senaryolarındaki kararlılığını garanti altına alan dayanıklılık test paketi:
1. **API Sağlayıcı Kesintisi Simülasyonu (Provider Outage Simulation):**
   - **Tam Kesinti (Total Outage & 503):** Sağlayıcı çöktüğünde sonsuz döngü engeli, sınırlı üstel geri çekilme (`max_retries=3`, `initial_delay=0.1s`), gecikme tavanı ve kontrollü `ProviderUnavailableError` fırlatılması.
   - **Geçici Kesinti ve Kendiliğinden İyileşme (Transient Outage Self-Healing):** İlk denemelerde kesinti/zaman aşımı yaşayan ancak son denemede ayağa kalkan sağlayıcıdan verinin kayıpsız toparlanması, telemetri sayacında `failed_invocations == 0`.
   - **Kota Tüketiminde Hızlı Hata (Quota Exhaustion Fail-Fast):** Sağlayıcı HTTP 429 döndüğünde kota harcamamak ve vakit kaybetmemek adına retry yapılmadan anında `ProviderQuotaExceededError` ile fail-fast davranışı.
   - **Şema Bozulması Kalkanı (Schema Corruption Defense):** Eksik kolon veya bozuk tipli sağlayıcı çıktılarında veri zehirlenmesini önlemek için doğrudan `ProviderSchemaError` üretilmesi.
2. **Yük ve Eşzamanlılık Stresi (Concurrent Load & Throughput Stress):**
   - **p95 Gecikme SLA Hedefleri:** Eşzamanlı istemci iş parçacıkları (thread pool) altında önbellekli okumalarda p95 < 300 ms SLA tavanı doğrulaması.
   - **Aşağı Örnekleme (Downsampling) Yükü:** Eşzamanlı 1 yıllık grafik ve downsampling sorgularında CPU ve bellek kararlılığı.
   - **Eşzamanlılık Altında Hız Sınırı (Rate Limiting Concurrency):** Tek bir istemcinin sınırın (20 req/dk) üzerine çıktığı eşzamanlı istek patlamasında race condition olmadan tam olarak 20 başarılı (200 OK) ve 5 sınırlama (429 Too Many Requests) yanıtı dönmesi.
   - **Önbellek Yığılması Koruması (Cache Stampede / Thundering Herd):** Önbellekte henüz bulunmayan bir sorguya aynı anda gelen eşzamanlı isteklerin veritabanında kilitlenme (deadlock) veya çökme yaratmadan zarifçe işlenmesi.
3. **Model Çıkarım Devre Kesici ve Güvenli Fallback (Inference Circuit Breaker):**
   - **Devre Kesici Tetikleme (Breaker Tripping to OPEN):** Peş peşe gelen çıkarım arızalarında eşiğin (`failure_threshold=3`) aşılmasıyla devrenin derhal OPEN durumuna geçmesi ve yeni çıkarım çağrılarını durdurması (`is_call_allowed == False`).
   - **Yarı Açık Durum ve İyileşme (HALF_OPEN Recovery):** Kurtarma bekleme süresi dolduğunda devrenin tek bir deneme çağrısına izin vermesi (`HALF_OPEN`), başarılı olursa `CLOSED` durumuna dönmesi.
   - **Mevzuata Uygun Güvenli Fallback (Statutory Safe Fallback):** Model devre dışı veya arızalıyken asla uydurma yön/güven skoru sunulmaması (`forecast_available=False`), alternatif teknik göstergeler ve SPK yasal uyarısı (`yatırım tavsiyesi değildir`) ile şeffaf yanıt üretilmesi.
4. **Altyapı Arıza Arındırma Güvencesi (Failure Sanitization):**
   - Veritabanı bağlantı kopması veya beklenmeyen sistem istisnalarında API yanıtlarında bağlantı sırları, parola, IP adresi veya SQL yığın izi (stack trace) sızdırılmaması.

---

## 12. Test Verisi Hijyeni ve Gerçek Kullanıcı Bilgisi İzolasyonu (`tests/test_test_data_privacy.py`)

KVKK m. 7 / m. 12, ISO/IEC 27001:2022 A.8.33 (Test verilerinin korunması) ve `guvenlik.md` ilkeleri doğrultusunda, test ortamlarında gerçek kullanıcı bilgisi kullanımı kesinlikle yasaklanmış olup aşağıdaki denetimler kurulmuştur:

1. **Sentetik Test Kimliği Fabrikası (`SyntheticUserDataFactory`):**
   - **RFC 2606 / RFC 6761 Ayrılmış Alan Adları:** Test e-postalarında yalnızca `@example.com`, `@example.org`, `@example.net`, `@test.local` alan adları kullanılır; `gmail.com`, `hotmail.com`, `yahoo.com` gibi gerçek servis sağlayıcılar yasaktır.
   - **Sentetik Telefon Aralığı:** Gerçek operatör blokları yerine yalnızca testler için ayrılmış güvenli önekler (`+90 555 000 0000`, `+90 555 01XX`) üretilir.
   - **Algoritmik Sentetik TCKN:** Yaşayan vatandaşlarla çakışmayı önlemek için yalnızca `9` serisi ile başlayan, sağlama algoritması (mod 10/11) geçerli sentetik kimlik numaraları kullanılır.
   - **Mock Parola ve Tuz (Salt):** Test kullanıcıları için izole test tuzları ve mock hash kalıpları (`$argon2id$...$mock_hash`) üretilir.
2. **Kişisel Veri (PII) Güvenlik Denetçisi (`TestDataPrivacyAuditor`):**
   - Tüm test dizinini (`tests/test_*.py`), fixture'ları ve dinamik payload'ları tarar.
   - Gerçek e-posta servisleri, aktif mobil abone blokları, canlı TCKN'ler veya geçerli kredi kartı numaraları (Luhn algoritması) tespit edildiğinde `CRITICAL` seviyede testleri düşürür.
   - İstisnai durumlar (PII temizleme motorunun kendisini test eden güvenlik testleri) `# pragma: allow-pii-test-suite` veya `# pragma: allow-pii-test` etiketleriyle kontrol altına alınır.
3. **Çalışma Zamanı Kalkanı (`assert_safe_test_data`):**
   - Test aşamalarında API uçlarına gönderilen veya mock edilen gömülü veri yapılarında PII sızıntısı tespit edildiğinde `PrivacyViolationError` fırlatarak kontrolsüz test verisi girişini engeller.

---

## 13. Kritik Akışlar İçin Minimum Kapsama Hedefleri ve Kalite Güvencesi (`tests/test_coverage_quality_suite.py`)

Kapsama oranının (line coverage) tek başına bir kalite kanıtı olamayacağı (“hollow coverage / assertion-free test fallacy”) temel ilkesi benimsenmiştir. Salt satır üzerinden geçen ancak iddia (`assert`) içermeyen veya negatif yolları sınamayan test paketleri kalite güvencesi sağlamaz. Bu doğrultuda çok boyutlu denetim motoru (`CoverageQualityAuditor`) kurulmuştur:

1. **Kritik Mimari Akışlar ve Hedef Eşikleri:**
   - **Çekirdek İndikatörler ve Dönüşümler (`CORE_INDICATORS_AND_TRANSFORMS`):** Satır Kapsaması $\ge \%90$, Dal (Branch) $\ge \%85$, İddia Yoğunluğu $\ge 2.0$ iddia/test, Negatif Test Oranı $\ge \%25$.
   - **Kimlik, Oturum ve Güvenlik (`AUTH_SESSION_AND_SECURITY`):** Satır Kapsaması $\ge \%95$, Dal $\ge \%90$, İddia Yoğunluğu $\ge 2.5$ iddia/test, Negatif Test Oranı $\ge \%30$.
   - **Veri Hattı ve Zaman Serisi (`DATA_PIPELINE_AND_TIMESERIES`):** Satır Kapsaması $\ge \%88$, Dal $\ge \%82$, İddia Yoğunluğu $\ge 1.8$ iddia/test, Negatif Test Oranı $\ge \%20$.
   - **Uyarılar ve Bildirimler (`ALERTS_AND_NOTIFICATIONS`):** Satır Kapsaması $\ge \%88$, Dal $\ge \%82$, İddia Yoğunluğu $\ge 2.0$ iddia/test, Negatif Test Oranı $\ge \%25$.
   - **Model Yönetişimi ve Güvenli Fallback (`MODEL_GOVERNANCE_AND_FALLBACK`):** Satır Kapsaması $\ge \%90$, Dal $\ge \%85$, İddia Yoğunluğu $\ge 2.0$ iddia/test, Negatif Test Oranı $\ge \%25$.
   - **KVKK Gizlilik ve Silme (`KVKK_PRIVACY_AND_PURGE`):** Satır Kapsaması $\ge \%95$, Dal $\ge \%90$, İddia Yoğunluğu $\ge 2.5$ iddia/test, Negatif Test Oranı $\ge \%30$.
   - **API Yönlendirme ve Ağ Geçidi (`API_ROUTING_AND_GATEWAY`):** Satır Kapsaması $\ge \%85$, Dal $\ge \%78$, İddia Yoğunluğu $\ge 1.8$ iddia/test, Negatif Test Oranı $\ge \%20$.

2. **Çok Boyutlu Kalite Değerlendirmesi:**
   - **Dal (Branch) Kapsaması Ayrımı:** `if/else` veya istisna dallarının her iki yönünün de test edildiği denetlenir; yetersiz dal kapsaması durumunda `REJECTED_LOW_BRANCH_COVERAGE` verilir.
   - **Yalancı Kapsama (Hollow Coverage) Savunması:** Satır kapsaması yüksek görünse dahi, test başına düşen ortalama iddia sayısı 1.0'ın altındaysa veya sıfırsa test paketi `REJECTED_HOLLOW_COVERAGE` ile derhal reddedilir.
   - **Sınır Değer ve Negatif Yol Güvencesi:** Yalnızca "happy path" senaryolarını test eden paketler engellenir; asgari negatif yol eşiğinin altındaki durumlar `REJECTED_LACK_OF_NEGATIVE_PATHS` ile işaretlenir.
   - **Statik AST İncelemesi (`analyze_test_file_ast`):** Test dosyaları AST düzeyinde taranarak gerçek `assert` ve `pytest.raises` blokları objektif olarak sayılır ve iddia yoğunluğu hesaplanır.

---

## 14. Yük Testi ve Kabul Ölçütleri p95 Kıyaslaması (`tests/load/` & `tests/test_load_p95_benchmarks.py`)

`docs/ACCEPTANCE_CRITERIA.md` dosyasında tanımlanan gecikme ve hata eşikleri, hem Locust/k6 yük senaryoları hem de otomatik `pytest` benchmark paketi ile sürekli doğrulanır:

1. **Kabul Ölçütleri SLA Eşikleri:**
   - **API (Önbellekli Okuma):** $\text{p95 yanıt} < 300\text{ ms}$ (`SLA_P95_CACHED_MAX_MS = 300.0`).
   - **API (Önbelleksiz Okuma):** $\text{p95 yanıt} < 800\text{ ms}$ (`SLA_P95_UNCACHED_MAX_MS = 800.0`).
   - **Hata ve Dayanıklılık:** 500 İç Sunucu Hatası $= 0$ (`error_rate = 0.0`), unhandled exception sızıntısı yasaktır.

2. **Yük Testi Varlıkları ve Senaryoları:**
   - **Locust Senaryosu (`tests/load/locustfile.py`):** `BorsaAnalizUser` profili; ağırlıklı görevler (5x sembol listesi, 3x önbellekli grafik, 2x önbelleksiz çok yıllık geçmiş, 2x downsampled mum, 1x sistem sağlık kontrolü).
   - **k6 Senaryosu (`tests/load/k6_test_script.js`):** Ramp-up (5s 10 VU), steady state (20s 20 VU), ramp-down (5s 0 VU) ve yerleşik `thresholds` (`p(95)<300`, `p(95)<800`, `rate==0`).
   - **SLA Uyumluluk Karar Motoru (`stock_analysis.testing.load_sla`):** `calculate_percentile` ile p50, p90, p95, p99 hesaplama; `evaluate_sla_compliance` fonksiyonu ile `LoadTestVerdict` ve `EndpointBenchmarkResult` raporlama, eşik aşımında CI sürecini düşürme.

---

## 15. Yasaklı İfade ve Prompt Injection Otomatik Test Paketi (`tests/test_banned_and_injection_suite.py`)

`docs/AI_LLM_SAFETY.md` ve `docs/COMPLIANCE_TR.md` standartları doğrultusunda, LLM ve açıklama katmanları için sistematik ve kategorize edilmiş otomatik güvenlik test kalkanı (`SafetyBenchmarkCatalog`, `SafetyBenchmarkAuditor`) kurulmuştur:

1. **Yasaklı İfade Taksonomisi ve Sıfır Sızıntı İlkesi (Banned Phrases):**
   - **Doğrudan Al/Sat Emirleri (`DIRECT_ADVICE`):** "hemen al", "kesinlikle sat", "portföye ekle", "mutlaka al" vb.
   - **Hedef Fiyat ve Değer Biçme (`PRICE_TARGET`):** "hedef fiyat 500 TL", "teknik hedefimiz", "12 aylık hedef" vb.
   - **Garanti Kazanç Vaatleri (`GUARANTEED_RETURNS`):** "garanti getiri", "garanti kazanç", "bedava para", "risksiz", "zarar etme ihtimali sıfır" vb.
   - **Kaldıraç ve Riskli Türevler (`LEVERAGE_AND_MARGIN`):** "10x kaldıraç aç", "long aç", "short aç", "zarar kes yapma" vb.
   - **Spekülatif Pompalama (`SPECULATIVE_PUMP`):** "uçacak", "patlayacak", "tavan yapacak", "roket", "10x yapacak", "pump başladı" vb.
   - **SPK İhlalleri (`LEGAL_DISCLAIMER_VIOLATION`):** "bu analiz yatırım tavsiyesidir", "şiddetle tavsiye edilir" vb.
   - **Gizleme ve Noktalama Evasyonu (`OBFUSCATED_EVASION`):** Araya giren noktalama ve ayırıcı işaretlerin (`.`, `-`, `_`, `*`, `~`, `/`) temizlendiği de-obfuscation denetimi (`h-e-d-e-f f-i-y-a-t`, `k*e*s*i*n*l*i*k*l*e a*l`).
   - **İngilizce Yönlendirmeler (`ENGLISH_DIRECTIVE`):** "buy now", "sell immediately", "target price", "strong buy" vb.

2. **Prompt Injection & Jailbreak Nötralizasyon Taksonomisi:**
   - **Doğrudan Sistem Ezme (`SYSTEM_OVERRIDE`):** "Sistem talimatını unut", "Ignore previous directions" vb.
   - **Sınırlandırıcı Etiket Delme (`DELIMITER_HIJACK`):** `</veri>`, `<veri>`, Markdown blok sızıntısı ve admin etiketi denemeleri.
   - **Rol Yapma & Jailbreak (`ROLEPLAY_JAILBREAK`):** DAN modu, "Serbest Danışman", varsayımsal dünya ve tiyatro senaryosu taklitleri.
   - **Dolaylı Haber ve Not Zehirleme (`INDIRECT_NEWS_POISONING`):** Dış haber akışları, analist notları veya kullanıcı notlarına gizlenmiş talimatlar.
   - **Sır ve Sistem İstemi Sızdırma (`DATA_EXFILTRATION`):** Sistem istemi, gizli anahtarlar veya ortam değişkenlerini dışarı sızdırma teşebbüsleri.

3. **Otomatik Güvenlik Denetçisi (`SafetyBenchmarkAuditor`):**
   - Yüzde yüz (%100) blok oranı zorunluluğu (`banned_block_rate_pct == 100.0`, `injection_defense_rate_pct == 100.0`).
   - Sıfır sızıntı (`banned_leaked == 0`, `injection_breached == 0`).
   - Herhangi bir sızıntı durumunda `is_compliant=False` ve `failure_samples` raporlaması ile CI geçiş kapısı.



