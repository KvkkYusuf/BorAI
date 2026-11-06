# E-posta Teslim Edilebilirliği ve Kimlik Doğrulama (SPF, DKIM, DMARC)

Bu belge, Borsa Analiz Sistemi'nin işlemsel e-postaları (e-posta doğrulama, parola sıfırlama, güvenlik uyarıları vb.) için alan adı kimlik doğrulaması (domain authentication), kimlik sahteciliği önleme (anti-spoofing) ve teslim edilebilirlik standartlarını tanımlar.

---

## 1. Genel Bakış ve Güvenlik Hedefleri

E-posta güvenliğinde temel riskler:
- **E-posta Sahteciliği (Spoofing):** Saldırganların sistem alan adını taklit ederek kullanıcılara sahte şifre sıfırlama bağlantıları göndermesi.
- **Kimlik Avı (Phishing):** Kullanıcıların yanıltılarak hesap bilgilerinin çalınması.
- **Spam Filtrelerine Takılma:** Eksik DNS kayıtları nedeniyle doğrulama e-postalarının Spam/Junk klasörüne düşmesi veya tamamen reddedilmesi.

Bu riskleri bertaraf etmek için **SPF**, **DKIM** ve **DMARC** standartları eksiksiz uygulanır.

---

## 2. DNS Kayıtları Mimarisi

Birincil alan adı örneği: `borsaanaliz.com`  
E-posta gönderim alt alanı (isteğe bağlı ve önerilen): `mail.borsaanaliz.com`

### 2.1. SPF (Sender Policy Framework - RFC 7208)
SPF, alan adınız adına hangi sunucuların/sağlayıcıların e-posta gönderebileceğini belirten TXT kaydıdır.

- **Kayıt Türü:** `TXT`
- **Host / İsim:** `@` (veya `mail.borsaanaliz.com`)
- **Değer / İçerik:**
  ```text
  v=spf1 include:_spf.emailprovider.com -all
  ```
- **Kurallar:**
  - `include:_spf.emailprovider.com`: Yetkili e-posta sağlayıcısının SPF şablonunu dahil eder.
  - `-all` (Hard Fail): Yetkilendirilmemiş herhangi bir IP'den gelen e-postanın alıcı sunucu tarafından **kesin olarak reddedilmesini** talep eder.
  - `+all` kullanımı **kesinlikle yasaktır** (tüm internete sahtecilik izni verir).
  - RFC 7208 gereği maksimum **10 DNS lookup** sınırı aşılmamalıdır.

---

### 2.2. DKIM (DomainKeys Identified Mail - RFC 6376)
DKIM, e-posta içeriğinin ve başlıklarının yolda değiştirilmediğini ve alan adı sahibi tarafından imzalandığını kriptografik olarak kanıtlar.

- **Anahtar Boyutu:** En az 2048-bit RSA veya Ed25519.
- **Seçici (Selector):** `default` veya sağlayıcıya özel (`s1`, `resend`, `k1`).
- **Kayıt Türü:** `TXT` (veya sağlayıcı CNAME veriyorsa CNAME)
- **Host / İsim:** `<selector>._domainkey.borsaanaliz.com` (Örn: `default._domainkey.borsaanaliz.com`)
- **Değer / İçerik:**
  ```text
  v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...[PUBLIC_KEY]...QIDAQAB
  ```
- **İmza Kapsamı:** `From`, `To`, `Subject`, `Date`, `Message-ID`, e-posta gövdesi (`bh` - body hash).

---

### 2.3. DMARC (Domain-based Message Authentication, Reporting, and Conformance - RFC 7489)
DMARC, SPF ve DKIM sonuçlarını birbirine bağlar ve e-posta başlığındaki `From` adresi ile doğrulanan alan adlarının hizalanmasını (alignment) zorunlu kılar.

- **Kayıt Türü:** `TXT`
- **Host / İsim:** `_dmarc.borsaanaliz.com`
- **Değer / İçerik:**
  ```text
  v=DMARC1; p=reject; sp=reject; pct=100; aspf=r; adkim=r; rua=mailto:dmarc-rua@borsaanaliz.com; ruf=mailto:dmarc-ruf@borsaanaliz.com
  ```

#### DMARC Parametreleri:
| Parametre | Değer | Anlamı |
|---|---|---|
| `v` | `DMARC1` | DMARC protokol sürümü. |
| `p` | `reject` | İlke: Doğrulamayı geçemeyen e-postalar doğrudan çöpe atılır / reddedilir. |
| `sp` | `reject` | Alt alan adları için de aynı `reject` ilkesi geçerlidir. |
| `pct` | `100` | İlkenin e-postaların %100'üne uygulanacağını belirtir. |
| `aspf` | `r` (relaxed) | SPF hizalama modu: Alt alan adı veya ana alan adı uyumlu sayılır. |
| `adkim` | `r` (relaxed) | DKIM hizalama modu: `d=` domaini From domainiyle organizasyonel düzeyde eşleşmelidir. |
| `rua` | `mailto:...` | Toplu DMARC günlüklerinin gönderileceği adres (Agrege Raporlar). |
| `ruf` | `mailto:...` | Başarısızlık/sahtecilik denemeleri için adli bildirimler (Forensic Reports). |

---

## 3. Alan Adı Hizalaması (Domain Alignment)

DMARC'ın geçerli olması için yalnızca SPF veya DKIM'in "PASS" vermesi yetmez; `From` başlığındaki alan adı ile doğrulanmış alan adının hizalanması şarttır:

1. **SPF Hizalaması:**
   - E-posta başlığındaki `From: noreply@borsaanaliz.com` ile SMTP zarfındaki `MAIL FROM` (Return-Path: `bounces@borsaanaliz.com` veya `bounces@mail.borsaanaliz.com`) aynı organizasyonel alana (`borsaanaliz.com`) ait olmalıdır.
2. **DKIM Hizalaması:**
   - `DKIM-Signature` başlığındaki `d=borsaanaliz.com` (veya `d=mail.borsaanaliz.com`) ile `From` başlığındaki alan adı örtüşmelidir.

---

## 4. İletim Güvenliği ve TLS (MTA-STS & TLS-RPT)

- **Zorunlu Şifreleme:** Giden tüm SMTP bağlantılarında TLS 1.2+ zorunludur (`require_tls=True`).
- **MTA-STS (RFC 8461):** E-postaların şifreli iletimini zorunlu kılmak için MTA Strict Transport Security desteği planlanmıştır.
- **BIMI (Brand Indicators for Message Identification):** DMARC `p=reject` sağlandıktan sonra, e-posta istemcilerinde resmi kurum logosunun görünmesi için BIMI DNS kaydı eklenir.

---

## 5. Uygulama İçi Doğrulama ve Güvenlik

Uygulama kodu (`stock_analysis.auth.email_security`):
1. Gönderilen tüm işlemsel e-postalarda `From` adresi ve `Sender Domain` hizalamasını doğrular.
2. DNS kayıtlarının (SPF, DKIM, DMARC) geçerliliğini ve RFC kurallarına uygunluğunu denetler.
3. Hata veya log çıktılarında kullanıcı e-postalarını maskeleyerek (`a***@x.com`) KVKK/güvenlik sızıntılarını önler.
