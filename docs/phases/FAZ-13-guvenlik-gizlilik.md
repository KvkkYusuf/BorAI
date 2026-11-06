## 13. Güvenlik ve gizlilik

- [x] Tehdit modellemesi yap: hesap ele geçirme, veri sızıntısı, API abuse, yetki yükseltme, tedarik zinciri saldırısı.
- [x] TLS, güvenli başlıklar, CORS allowlist ve uygun CSP yapılandır.
- [x] Gizli bilgileri secret manager'da tut; anahtarları düzenli döndür.
- [x] Üretim/test ortamlarını ve erişim rollerini ayır.
- [x] En az ayrıcalık ilkesi, RBAC ve yönetici işlemleri için denetim kaydı uygula.
- [x] OWASP Top 10 ve API Security Top 10 kontrollerini uygula.
- [x] CSRF, XSS, SQL injection, SSRF, IDOR, rate-limit ve dosya yükleme risklerini test et.
- [x] Bağımlılık, container ve kod güvenlik taramaları çalıştır.
- [x] Loglarda parola, token, tam e-posta, kişisel veri veya API anahtarı tutma.
- [x] Veritabanı yedeklerini şifrele; geri yükleme tatbikatı yap.
- [x] Veri saklama ve silme politikasını uygula; yedeklerde silme etkisini belgeye bağla.
- [x] Güvenlik olayı müdahale planı, iletişim sorumluları ve kurtarma adımları yaz.
- [x] Yayına çıkmadan bağımsız güvenlik incelemesi/penetrasyon testi yaptır.

**Çıktı:** `docs/SECURITY.md`, tehdit modeli, test raporu ve olay müdahale planı.

### v2 eklemeleri
- [x] `docs/SECURITY.md` v2 tehdit tablosunu genişlet; ajan kaynaklı bağımlılık riskini (hayali paket) CI'da kontrol et.
- [x] LLM tehditleri için `docs/AI_LLM_SAFETY.md` test setini çalıştır.
- [x] Hata izleme servisine giden verinin kişisel veriden temizlendiğini doğrula.
