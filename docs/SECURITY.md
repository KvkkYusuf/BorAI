# Güvenlik Taban Çizgisi

## Kimlik ve erişim
- E-posta doğrulama ve güvenli parola sıfırlama
- MFA/passkey değerlendirmesi
- Oturum iptali ve güvenli çerezler
- Kullanıcı sahipliği kontrolü (IDOR önleme)
- Yönetici rolleri ve MFA
- Giriş ve hassas işlemlerde hız sınırlaması

## Uygulama güvenliği
- TLS ve güvenli HTTP başlıkları
- CORS allowlist, uygun CSP, CSRF koruması
- Girdi doğrulama ve çıktı kodlama
- Parametreli sorgular/ORM
- SSRF ve dosya yükleme yüzeyini azaltma
- Sır yönetimi ve anahtar rotasyonu
- Bağımlılık, container ve secret taraması

## Veri ve gizlilik
- Loglarda parola/token/API anahtarı tutma.
- Gerektiği kadar veri topla; saklama süresini tanımla.
- Şifreli yedekleme ve geri yükleme tatbikatı yap.
- Hesap silme ve veri dışa aktarma sürecini test et.
- Üretim verisini geliştirme/test ortamında kullanma.

## Operasyon
- Tehdit modeli ve olay müdahale planı
- Güvenlik olayları için alarm ve denetim kaydı
- Düzenli yama ve bağımlılık güncelleme süreci
- Üretim öncesi bağımsız güvenlik incelemesi

## v2 eklemeleri
### Başlangıç tehdit modeli (Faz 13'te genişlet)
| Varlık | Tehdit | Önlem |
|---|---|---|
| Kullanıcı hesabı | Kaba kuvvet, credential stuffing, oturum çalma | Oran sınırı, MFA/passkey, HttpOnly+Secure çerez, oturum iptali |
| Kullanıcı verisi | IDOR, SQL injection, log sızıntısı | Sahiplik kontrolü, parametreli sorgu, log maskeleme |
| Sağlayıcı/LLM anahtarları | Repo/log/prompt sızıntısı | Secret manager, rotasyon, secret taraması, `.gitignore` |
| Piyasa verisi hattı | Sağlayıcı kesintisi, bozuk/zehirli veri | Doğrulama, karantina, kaynak imzası/şema sözleşmesi |
| Model/ağırlıklar | Kötü amaçlı dosya, sessiz bozulma | `pickle` yok, hash doğrulama, onay kapısı |
| LLM katmanı | Prompt injection, maliyet suistimali | `docs/AI_LLM_SAFETY.md` |
| Geliştirme (ajan) | Hayali/zararlı paket, tehlikeli komut, sır okuma | `.agent/rules/guvenlik.md`, bağımlılık onayı, komut onayı |
| Admin paneli | Yetki yükseltme | RBAC, MFA, denetim kaydı, ağ kısıtı |

### Ek taban çizgisi
- Güvenlik başlıkları: HSTS, CSP, X-Content-Type-Options, Referrer-Policy; `security.txt` ile açık bildirimi yolu.
- SBOM üretimi ve bağımlılık güncelleme otomasyonu; kilit dosyası zorunlu.
- Veritabanı ve uygulama ayrı düşük yetkili hesaplar; üretim erişimi ayrı ve denetimli.
- Hata izleme servisine kişisel veri gitmeden önce temizleme (scrub) uygulanır.
- Yedekten geri yükleme tatbikatı en az üç ayda bir.
