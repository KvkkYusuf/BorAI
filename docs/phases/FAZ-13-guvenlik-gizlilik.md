## 13. Güvenlik ve gizlilik

- [ ] Tehdit modellemesi yap: hesap ele geçirme, veri sızıntısı, API abuse, yetki yükseltme, tedarik zinciri saldırısı.
- [ ] TLS, güvenli başlıklar, CORS allowlist ve uygun CSP yapılandır.
- [ ] Gizli bilgileri secret manager'da tut; anahtarları düzenli döndür.
- [ ] Üretim/test ortamlarını ve erişim rollerini ayır.
- [ ] En az ayrıcalık ilkesi, RBAC ve yönetici işlemleri için denetim kaydı uygula.
- [ ] OWASP Top 10 ve API Security Top 10 kontrollerini uygula.
- [ ] CSRF, XSS, SQL injection, SSRF, IDOR, rate-limit ve dosya yükleme risklerini test et.
- [ ] Bağımlılık, container ve kod güvenlik taramaları çalıştır.
- [ ] Loglarda parola, token, tam e-posta, kişisel veri veya API anahtarı tutma.
- [ ] Veritabanı yedeklerini şifrele; geri yükleme tatbikatı yap.
- [ ] Veri saklama ve silme politikasını uygula; yedeklerde silme etkisini belgeye bağla.
- [ ] Güvenlik olayı müdahale planı, iletişim sorumluları ve kurtarma adımları yaz.
- [ ] Yayına çıkmadan bağımsız güvenlik incelemesi/penetrasyon testi yaptır.

**Çıktı:** `docs/SECURITY.md`, tehdit modeli, test raporu ve olay müdahale planı.

### v2 eklemeleri
- [ ] `docs/SECURITY.md` v2 tehdit tablosunu genişlet; ajan kaynaklı bağımlılık riskini (hayali paket) CI'da kontrol et.
- [ ] LLM tehditleri için `docs/AI_LLM_SAFETY.md` test setini çalıştır.
- [ ] Hata izleme servisine giden verinin kişisel veriden temizlendiğini doğrula.
