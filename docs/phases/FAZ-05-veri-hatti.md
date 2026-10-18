## 5. Piyasa verisi hattı — güvenilirliğin temeli

- [ ] Lisanslı veri sağlayıcı seç ve bir adaptör arayüzü tasarla.
- [ ] Her kayıt için sembol, borsa, zaman damgası, saat dilimi, OHLCV, para birimi, kaynak ve veri durumu alanlarını belirle.
- [ ] Eksik bar, yinelenen kayıt, sıfır/negatif fiyat, tutarsız yüksek-düşük, hacim anomalisi ve zaman sırası kontrolleri ekle.
- [ ] Tatil, seans, yaz saati, işlem durdurma ve farklı borsa takvimlerini ele al.
- [ ] Bölünme, temettü ve diğer kurumsal işlemlerin fiyat serisine etkisini tanımla.
- [ ] Ham veriyi değişmez biçimde sakla; düzeltilmiş/temizlenmiş veriyi ayrı türet.
- [ ] Sağlayıcı kesintisi, gecikme, kota aşımı ve API şema değişikliklerinde kontrollü hata ve yeniden deneme uygula.
- [ ] Verinin “son güncelleme zamanı” ve gecikme durumunu kullanıcıya göster.
- [ ] Verinin kaynağını ve gecikme seviyesini hisse detayında görünür yap.
- [ ] Günlük veri bütünlüğü raporu ve alarmı oluştur.
- [ ] Sağlayıcı kullanım koşullarının izin vermediği veriyi kazıma veya yeniden yayınlama.

**Çıktı:** Doğrulanmış, izlenebilir ve tekrar üretilebilir veri hattı.

### v2 eklemeleri
- [ ] Veri alımını idempotent yap (aynı gün tekrar çalışınca çift kayıt yok).
- [ ] Sağlayıcı yanıtlarını yerel önbellekle; aynı veriyi iki kez çekme; kota sayacı tut.
- [ ] Veri sürümü (`data_version`) üret; önbellek anahtarları buna bağlansın.
- [ ] Saklama süresi ve ham veri yedekleme politikasını yaz.
