## 7. E-posta ile giriş ve hesap ayarları

- [ ] E-posta adresi doğrulama akışı ekle.
- [ ] Güvenli, tek kullanımlık ve süreli parola sıfırlama bağlantısı kur.
- [ ] Oturum çerezlerinde `HttpOnly`, `Secure`, uygun `SameSite`; CSRF koruması ve oturum süresi politikası uygula.
- [ ] Mümkünse MFA/passkey seçeneği ekle.
- [ ] Giriş denemelerini sınırla; kaba kuvvet ve credential stuffing savunması ekle.
- [ ] Kullanıcıya aktif oturumları görme ve diğer oturumları kapatma olanağı ver.
- [ ] E-posta bildirim tercihleri, saat dilimi, tema, dil, izleme listesi ve uyarı sıklığı ayarlarını ekle.
- [ ] Hesap dışa aktarma ve hesap silme akışlarını tasarla; yasal saklama yükümlülüklerini gözet.
- [ ] E-posta sağlayıcısında SPF, DKIM ve DMARC yapılandır.
- [ ] E-posta adresinin varlığını gereksiz yere ifşa eden yanıtlar verme.
- [ ] Üretimde yönetici hesaplarına MFA ve en az ayrıcalık uygula.

**Çıktı:** Güvenli hesap/ayar ekranı ve kimlik doğrulama testleri.

### v2 eklemeleri
- [ ] Kayıt olmadan ilk değer akışı (anonim örnek) ile kayıtlı akış arasında net geçiş.
- [ ] Giriş formunda sade hata mesajları; şifre yöneticisi uyumu; passkey seçeneği.
