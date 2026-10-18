## 14. Test stratejisi

- [ ] Birim testleri: indikatörler, fiyat dönüşümleri, veri temizleme, yetkilendirme ve uyarı koşulları.
- [ ] Entegrasyon testleri: veri sağlayıcı adaptörü, veritabanı, e-posta, kuyruk ve API.
- [ ] Sözleşme testleri: sağlayıcı/API şema değişiklikleri.
- [ ] Zaman serisi testleri: sıra, eksik veri, kurumsal işlem, piyasa tatili ve saat dilimi.
- [ ] Model testleri: veri sızıntısı, tekrarlanabilirlik, baseline kıyası ve uç durumlar.
- [ ] Güvenlik testleri: erişim kontrolü, oturum, hız sınırı, input validation.
- [ ] E2E testleri: kayıt/giriş, hisse arama, grafik açma, izleme listesi, uyarı kurma, hesap silme.
- [ ] Yük ve dayanıklılık testi yap; API sağlayıcı kesintisini simüle et.
- [ ] Test verilerinde gerçek kullanıcı bilgisi kullanma.
- [ ] Kritik akışlar için minimum kapsama hedefi koy; kapsama oranını kalite kanıtı olarak tek başına görme.

### v2 eklemeleri
- [ ] Yük testi (k6/Locust): `docs/ACCEPTANCE_CRITERIA.md` p95 hedefleriyle karşılaştır.
- [ ] Yasaklı ifade ve prompt injection test setlerini otomatik teste dönüştür.
- [ ] Önbellek isabet oranı ve LLM çağrı tekrarı testleri.
