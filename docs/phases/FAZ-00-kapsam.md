## 0. Ürün kapsamı ve ilk kararlar

### İlk sürümün kapsamı (MVP)
- [x] İlk pazar/varlık sınıfını seç: başlangıçta yalnızca Borsa İstanbul hisse senetleri veya yalnızca ABD hisseleri. İkisini aynı anda başlatma.
- [x] Uygulamanın yalnızca bilgilendirme/analiz aracı olduğunu ve yatırım tavsiyesi vermediğini ürün kapsamına yaz.
- [x] İlk kullanıcı senaryolarını tanımla: piyasayı görüntüleme, hisse arama, hisse detayını inceleme, izleme listesi, trend uyarısı.
- [x] Gerçek zamanlı mı, gecikmeli mi, gün sonu verisi mi kullanılacağını belirle.
- [x] Veri sağlayıcının lisansını, kullanım şartlarını, yeniden gösterim ve saklama haklarını doğrula.
- [x] Başarı ölçütlerini belirle: veri güncelliği, hata oranı, sayfa yanıt süresi, uyarı gecikmesi, modelin basit kıyas ölçütünü geçmesi.
- [x] “Mükemmel performans” yerine ölçülebilir SLO/SLA ve kabul kriterleri tanımla.

**Çıktı:** `docs/PRODUCT_SCOPE.md`, `docs/ACCEPTANCE_CRITERIA.md`

### v2 eklemeleri
- [x] `docs/PRODUCT_SCOPE.md` içindeki [ÖNERİ] kararlarını onayla veya değiştir; `DECISIONS.md`'ye yaz.
- [x] `docs/ACCEPTANCE_CRITERIA.md` hedeflerini gözden geçir (ölçüp güncelleyeceksin).
- [x] Kullanıcı kolaylığı ilkelerini (60 sn ilk değer, 3 tık ana eylemler) onayla.
- [x] LLM açıklama katmanı v1'de var mı yok mu karar ver (öneri: v1'de yok, deterministik analiz önce).
