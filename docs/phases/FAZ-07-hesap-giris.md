## 7. E-posta ile giriş ve hesap ayarları

- [x] E-posta adresi doğrulama akışı ekle.
- [x] Güvenli, tek kullanımlık ve süreli parola sıfırlama bağlantısı kur.

- [x] Oturum çerezlerinde `HttpOnly`, `Secure`, uygun `SameSite`; CSRF koruması ve oturum süresi politikası uygula.
- [x] Mümkünse MFA/passkey seçeneği ekle.
- [x] Giriş denemelerini sınırla; kaba kuvvet ve credential stuffing savunması ekle.
- [x] Kullanıcıya aktif oturumları görme ve diğer oturumları kapatma olanağı ver.
- [x] E-posta bildirim tercihleri, saat dilimi, tema, dil, izleme listesi ve uyarı sıklığı ayarlarını ekle.
- [x] Hesap dışa aktarma ve hesap silme akışlarını tasarla; yasal saklama yükümlülüklerini gözet.
- [x] E-posta sağlayıcısında SPF, DKIM ve DMARC yapılandır.
- [x] E-posta adresinin varlığını gereksiz yere ifşa eden yanıtlar verme.
- [x] Üretimde yönetici hesaplarına MFA ve en az ayrıcalık uygula.

**Çıktı:** Güvenli hesap/ayar ekranı ve kimlik doğrulama testleri.

### v2 eklemeleri
- [x] Kayıt olmadan ilk değer akışı (anonim örnek) ile kayıtlı akış arasında net geçiş.
- [x] Giriş formunda sade hata mesajları; şifre yöneticisi uyumu; passkey seçeneği.
