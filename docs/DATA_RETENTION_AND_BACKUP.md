# Veri Saklama Süresi ve Ham Veri Yedekleme Politikası

Bu belge, **Borsa Analiz Sistemi** veri hattında toplanan ham piyasa verilerinin, türetilmiş analiz serilerinin, denetim kayıtlarının ve önbellek mekanizmalarının saklama sürelerini (retention), değişmezlik (immutability) kurallarını ve felaket kurtarma/yedekleme (backup & disaster recovery) standartlarını belirler.

---

## 1. Veri Saklama Katmanları ve Süreleri (Data Retention Tiers)

| Katman | Tanım | Saklama Süresi | Değişmezlik (WORM) | Otomatik Budama (Prune) |
|---|---|---|---|---|
| **`RAW_EOD`** (Ham Gün Sonu) | Sağlayıcıdan gelen el değmemiş ham OHLCV barları | **Süresiz** (En az 10 yıl mevzuat ve model geriye dönük test uyumu) | **Evet** (Append-only, SHA-256) | **İzin verilmez** |
| **`DERIVED_SERIES`** (Türetilmiş Seri) | Doğrulanmış ve kurumsal işlem düzeltmeli seriler | **1 Yıl** (Sıcak/Warm depolama; ham veriden her zaman yeniden türetilebilir) | Hayır (`derivation_version` ile versiyonlanır) | **Evet** |
| **`AUDIT_LOGS`** (Bütünlük ve Denetim) | Günlük bütünlük raporları, veri anomalileri ve lisans logları | **3 Yıl** (Sermaye piyasası denetim uyumluluğu) | **Evet** | Hayır |
| **`CACHE`** (Önbellek) | Sağlayıcı yanıtları, anlık fiyat kuoteleri ve kota sayaçları | **24 Saat** (TTL: 5 dk – 24 sa) | Hayır | **Evet** |

### 1.1. Ham Veri Değişmezliği (WORM & Provenance)
- `RawBarRecord` nesneleri `SHA-256` parmak izi (`raw_payload_hash`) ile mühürlenir.
- Aynı gün ve sembol için gelen mükerrer veriler idempotent olarak işlenir; ancak farklı fiyata sahip çelişkili veriler `ImmutableDataError` fırlatılarak reddedilir.
- Ham veride doğrudan güncelleme (`UPDATE`) veya silme (`DELETE`) yapılamaz.

---

## 2. Ham Veri Yedekleme Politikası (Backup Policy)

Piyasa verisi hattının sürekliliği için ham verilerin güvenli, kriptografik ve soğuk/ikincil depolama ortamlarında yedeklenmesi esastır:

1. **Yedekleme Sıklığı ve Türü:**
   - **Günlük Artımlı (Incremental):** Her BIST seans kapanışı sonrasında (18:15 TSI) günün yeni eklenen ham barları yedeklenir.
   - **Haftalık Tam (Full Snapshot):** Her Pazar günü tüm geçmiş ham veri havuzunun tam anlık görüntüsü alınır.
2. **Kriptografik Bütünlük ve Manifest:**
   - Her yedek paketi ile birlikte deterministik `BackupManifest` üretilir.
   - Manifest; içerilen sembolleri, tarih aralığını, kayıt adedini ve kayıtların sıralı SHA-256 sağlama toplamını (`manifest_checksum`) içerir.
   - Geri yükleme sırasında `verify_raw_backup_integrity` çalıştırılmadan veri tabanına aktarım yapılmaz.
3. **Şifreleme Standartları:**
   - Bekleyen veriler (data-at-rest) ve harici nesne depolama (S3/GCS/soğuk arşiv) yedekleri için zorunlu **AES-256-GCM** şifrelemesi uygulanır.

---

## 3. Kurtarma Hedefleri (RPO ve RTO)

- **RPO (Recovery Point Objective): En fazla 24 Saat**
  - EOD verileri seans bitiminde arşivlendiği için herhangi bir veri kaybı felaketinde azami kabul edilebilir veri kaybı son işlem gününün ham verisidir.
- **RTO (Recovery Time Objective): En fazla 1 Saat**
  - Ham veri yedekten yüklendikten sonra `MarketDataPipelineService` üzerinden türetilmiş veri katmanının yeniden inşası en geç 1 saat içinde tamamlanır.

---

## 4. Tatbikat ve Denetim (Recovery Drills)

- **Üç Aylık Tatbikat (Quarterly Drill):** En geç 90 günde bir rastgele seçilen geçmiş bir tarihin yedek paketi test ortamına geri yüklenir ve `verify_raw_backup_integrity` ile kriptografik doğrulaması yapılır.
- Başarısız tatbikatlar doğrudan güvenlik ve veri bütünlüğü alarmı olarak kaydedilir.

---

## 5. Yedeklerde Silme Etkisi ve Geri Yükleme Sonrası Kişisel Veri Temizliği (KVKK Uyum)

Kişisel Verilerin Korunması Kanunu (KVKK m. 7) ve Kişisel Verilerin Silinmesi, Yok Edilmesi veya Anonim Hale Getirilmesi Hakkında Yönetmelik uyarınca, kriptografik ve salt-okunur (WORM) yedekleme ortamlarındaki verilerin anında imhası teknik olarak mümkün olmadığında şu mekanizma işletilir:

1. **Kişisel Veri Mezar Taşı Defteri (`TombstoneRegistry` & `DeletedUserTombstone`):**
   - Kullanıcı hesabı kalıcı olarak silindiğinde (hard-delete), sistem doğrudan kullanıcının e-posta adresini saklamak yerine SHA-256 ile özetlenmiş halini ve `user_id` bilgisini kriptografik mühürle (`tombstone_signature`) değişmez mezar taşı kütüğüne işler.
2. **Yedeklerin Döngüsel Rotasyonu:**
   - Şifreli veritabanı anlık görüntüleri en fazla 90 gün boyunca saklanır. Süresi dolan yedekler otomatik imha edilerek silinmiş kullanıcı verisi yedek havuzundan da doğal olarak temizlenir.
3. **Geri Yükleme Sonrası Otomatik Arındırma (`PostRestoreSanitizer`):**
   - Herhangi bir felaket kurtarma veya tatbikat senaryosunda geçmiş bir şifreli yedek veritabanına geri yüklendiğinde, `PostRestoreSanitizer` derhal devreye girer.
   - Yedekten geri gelen kullanıcılar `TombstoneRegistry` ile taranır; daha önce silinmiş kullanıcılar ve bunlara bağlı tüm oturum, alarm ve izleme listesi kayıtları hedef veritabanından kalıcı olarak derhal silinir (`POST_RESTORE_PURGE`).
   - Bu sayede yedekten geri yükleme yapılsa dahi silinmiş hiçbir kişisel veri sisteme tekrar sızamaz veya yeniden canlanamaz.

