## 6. Veritabanı ve API

- [x] Tabloları tasarla: users, sessions/identity mapping, watchlists, watchlist_items, alert_rules, alert_events, market_bars, instruments, corporate_actions, model_versions, forecasts, audit_events.
- [x] Kullanıcı parolasını kendin yönetmek yerine mümkünse güvenilir kimlik sağlayıcısı kullan; yönetiyorsan modern parola hash'i ve güvenli sıfırlama akışı kullan.
- [x] Veritabanı şeması için migration sistemi kur.
- [x] API uç noktalarını tasarla: arama, hisse listesi, fiyat geçmişi, göstergeler, izleme listesi, uyarı ayarları, tahminler, hesap ayarları.
- [x] Her uç noktaya doğrulama, yetkilendirme, sayfalama ve oran sınırlaması ekle.

- [x] Hatalarda sır, SQL, kişisel veri veya stack trace sızdırma.
- [x] Veri erişiminde parametreli sorgu/ORM kullan; kullanıcı girdisini SQL'e birleştirme.
- [x] API sürümleme ve geriye uyumluluk politikasını belirle.

**Çıktı:** OpenAPI dokümantasyonu ve entegrasyon testleri.

### v2 eklemeleri
- [x] Sorgu kalıplarına göre indeks planı (örn. `(instrument_id, ts)`); yavaş sorgu günlüğü aç.
- [x] HTTP önbelleği (ETag/Cache-Control) ve Redis önbelleği; sayfalama zorunlu.
- [x] Grafik uçları için aşağı örnekleme (downsampling) seçeneği.
- [x] Anonim salt okunur uçlar (örnek sembol arama/grafik) ayrı ve sıkı oran sınırlı.
