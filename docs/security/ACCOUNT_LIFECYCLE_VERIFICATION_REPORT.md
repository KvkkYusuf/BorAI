# Dagitim Oncesi E-posta Dogrulama, Parola Sifirlama ve Hesap Silme Guvenlik Raporu

**Tarih:** 2026-10-28T22:11:16.885310+00:00  
**Genel Kapi Durumu:** [x] GECTI (PASS)  
**Dagitim / Yayim Onayi (Sign-off Ready):** EVET  

## 1. Denetlenen Kontrol Maddeleri Ozeti

- **Toplam Kontrol Sayisi:** 12
- **Basarili:** 12
- **Basarisiz:** 0

| Kontrol ID | Kategori | Baslik | Standart Referansi | Durum |
|---|---|---|---|---|
| `CHK-EMAIL-001` | email_verification | Kriptografik Token Uretimi ve Ozet Depolama | OWASP ASVS v4.0.3 - 2.8.1 Cryptographic Identifiers | **PASS** |
| `CHK-EMAIL-002` | email_verification | Suresi Dolan Belirteclerin Reddi | OWASP ASVS v4.0.3 - 2.8.2 Token Expiration Enforcement | **PASS** |
| `CHK-EMAIL-003` | email_verification | Tek Kullanimlik Tuketim ve Tekrar (Replay) Korumasi | OWASP ASVS v4.0.3 - 2.8.3 Single-Use Token Invalidation | **PASS** |
| `CHK-EMAIL-004` | email_verification | Kullanici Tespiti (Anti-Enumeration) ve PII Maskeleme | OWASP WSTG-IDNT-04 / KVKK m. 12 Veri Guvenligi | **PASS** |
| `CHK-PASS-001` | password_reset | Parola Sifirlama 15 Dakika Asgari TTL Siniri | NIST SP 800-63B / OWASP ASVS v4.0.3 - 2.8.4 | **PASS** |
| `CHK-PASS-002` | password_reset | Scrypt Kriptografik Karma ve Parola Politikasi Denetimi | RFC 7914 (scrypt) / NIST SP 800-63B Section 5.1.1.2 | **PASS** |
| `CHK-PASS-003` | session_revocation | Parola Degisimi Sonrasi Tum Cihaz Oturumlarinin Sonlandirilmasi | OWASP ASVS v4.0.3 - 3.3.4 Session Invalidation on Change | **PASS** |
| `CHK-PASS-004` | password_reset | Parola Belirteci Tek Seferlik Kullanim ve Iptal Guvencesi | OWASP ASVS v4.0.3 - 2.8.5 Token Revocation | **PASS** |
| `CHK-DEL-001` | account_deletion | KVKK m. 7 Kalici Silme ve Iliskili Verilerin Temizlenmesi | KVKK m. 7 / GDPR Article 17 (Right to Erasure) | **PASS** |
| `CHK-DEL-002` | account_deletion | 5651 Sayili Kanun Kapsaminda Denetim Izi Saklama ve Anonimlestirme | 5651 Sayılı Kanun m. 4 / KVKK m. 11 Yasal Saklama | **PASS** |
| `CHK-DEL-003` | account_deletion | Mezar Tasi (Tombstone) ve Yedekten Geri Donuste Dirilme Engeli | KVKK Veri Silme ve Yok Etme Rehberi / ISO 27701 | **PASS** |
| `CHK-DEL-004` | session_revocation | Silinen Hesaba Ait Erisimin Aninda Kesilmesi | OWASP ASVS v4.0.3 - 3.7.1 Immediate Deprovisioning | **PASS** |

## 2. Ayrintili Denetim Bulgulari

### CHK-EMAIL-001 — Kriptografik Token Uretimi ve Ozet Depolama
- **Kategori:** email_verification
- **Standart:** OWASP ASVS v4.0.3 - 2.8.1 Cryptographic Identifiers
- **Durum:** PASS
- **Detay:** E-posta dogrulama belirteci kriptografik guvenli rastgelelik ile uretilmekte ve bellekte/veritabaninda yalnizca SHA-256 ozeti saklanmaktadir.

### CHK-EMAIL-002 — Suresi Dolan Belirteclerin Reddi
- **Kategori:** email_verification
- **Standart:** OWASP ASVS v4.0.3 - 2.8.2 Token Expiration Enforcement
- **Durum:** PASS
- **Detay:** Gecerlilik suresi dolan e-posta belirtecleri kesin olarak reddedilmektedir.

### CHK-EMAIL-003 — Tek Kullanimlik Tuketim ve Tekrar (Replay) Korumasi
- **Kategori:** email_verification
- **Standart:** OWASP ASVS v4.0.3 - 2.8.3 Single-Use Token Invalidation
- **Durum:** PASS
- **Detay:** Kullanilan e-posta dogrulama belirteci aninda yok edilmekte, tekrar kullanilmasi engellenmekte ve hesap dogrulanmis olarak isaretlenmektedir.

### CHK-EMAIL-004 — Kullanici Tespiti (Anti-Enumeration) ve PII Maskeleme
- **Kategori:** email_verification
- **Standart:** OWASP WSTG-IDNT-04 / KVKK m. 12 Veri Guvenligi
- **Durum:** PASS
- **Detay:** Denetim izlerinde e-posta adresleri PII maskelemesine tabi tutulmakta ve var olmayan hesaplar icin genel sabit yanit donulmektedir.

### CHK-PASS-001 — Parola Sifirlama 15 Dakika Asgari TTL Siniri
- **Kategori:** password_reset
- **Standart:** NIST SP 800-63B / OWASP ASVS v4.0.3 - 2.8.4
- **Durum:** PASS
- **Detay:** Parola sifirlama token'i azami 15 dakika gecerlilik suresiyle uretilmektedir.

### CHK-PASS-002 — Scrypt Kriptografik Karma ve Parola Politikasi Denetimi
- **Kategori:** password_reset
- **Standart:** RFC 7914 (scrypt) / NIST SP 800-63B Section 5.1.1.2
- **Durum:** PASS
- **Detay:** Yeni parola scrypt algoritmasi ile N=16384, r=8, p=1 guvenli parametreleriyle ozetlenmekte ve asgari karmasiklik kosullarina uymayan parolalar reddedilmektedir.

### CHK-PASS-003 — Parola Degisimi Sonrasi Tum Cihaz Oturumlarinin Sonlandirilmasi
- **Kategori:** session_revocation
- **Standart:** OWASP ASVS v4.0.3 - 3.3.4 Session Invalidation on Change
- **Durum:** PASS
- **Detay:** Parola guncellendiginde kullaniciya ait tum acik oturumlar aninda veritabanindan silinmekte ve belirtecler gecersiz kilinmaktadir.

### CHK-PASS-004 — Parola Belirteci Tek Seferlik Kullanim ve Iptal Guvencesi
- **Kategori:** password_reset
- **Standart:** OWASP ASVS v4.0.3 - 2.8.5 Token Revocation
- **Durum:** PASS
- **Detay:** Parola degisimi sonrasi kullanicinin bekleyen tum sifirlama token'lari iptal edilmektedir.

### CHK-DEL-001 — KVKK m. 7 Kalici Silme ve Iliskili Verilerin Temizlenmesi
- **Kategori:** account_deletion
- **Standart:** KVKK m. 7 / GDPR Article 17 (Right to Erasure)
- **Durum:** PASS
- **Detay:** Kullanici hesabi silindiginde profil, oturumlar, izleme listeleri, fiyat uyarilari ve tercihler veritabanindan kalici silinmektedir.

### CHK-DEL-002 — 5651 Sayili Kanun Kapsaminda Denetim Izi Saklama ve Anonimlestirme
- **Kategori:** account_deletion
- **Standart:** 5651 Sayılı Kanun m. 4 / KVKK m. 11 Yasal Saklama
- **Durum:** PASS
- **Detay:** 5651 Sayili Kanun geregi denetim izleri korunmakta, silinen kullanici kimligi user_id=None olarak anonimlestirilmektedir.

### CHK-DEL-003 — Mezar Tasi (Tombstone) ve Yedekten Geri Donuste Dirilme Engeli
- **Kategori:** account_deletion
- **Standart:** KVKK Veri Silme ve Yok Etme Rehberi / ISO 27701
- **Durum:** PASS
- **Detay:** Silinen kullanicilar TombstoneRegistry'de ozetlerle kaydedilmekte, yedekten geri yuklemede dirilme engellenmektedir.

### CHK-DEL-004 — Silinen Hesaba Ait Erisimin Aninda Kesilmesi
- **Kategori:** session_revocation
- **Standart:** OWASP ASVS v4.0.3 - 3.7.1 Immediate Deprovisioning
- **Durum:** PASS
- **Detay:** Silinen kullanicinin eski oturum token'lari ile yapilan tum istekler aninda HTTP 401 ile sonuclanmaktadir.

## 3. Dagitimi Engelleyen Sorunlar ve Oneriler

Hicbir engelleyici bulgu tespit edilmemistir. E-posta dogrulama, parola sifirlama ve hesap silme uctan uca yasam dongusu kontrolleri guvenlik standartlarina tam uyumludur.
