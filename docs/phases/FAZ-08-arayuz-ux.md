## 8. Kullanıcı arayüzü ve kullanıcı deneyimi

### Önerilen ekranlar
1. **Pano:** seçilen piyasa endeksleri, güncel veri durumu, izleme listesi, son uyarılar.
2. **Hisse keşfet:** sembol/şirket arama, borsa, sektör ve likidite filtreleri.
3. **Hisse detay:** şirket adı/sembolü, son fiyat, değişim, veri zamanı/kaynağı, zaman aralığı seçilebilen fiyat grafiği, hacim, teknik göstergeler, kurumsal işlemler.
4. **Analiz:** trend durumu, kullanılan göstergeler, model tahmin aralığı, belirsizlik ve geçmiş model performansı.
5. **Uyarılar:** koşul oluşturma, aktif/pasif, kanal, sessize alma ve geçmiş uyarılar.
6. **İzleme listeleri:** kullanıcıya özel listeler.
7. **Ayarlar:** e-posta/hesap güvenliği, bildirim, tema, dil, saat dilimi, veri tercihleri ve gizlilik.
8. **Yasal ve şeffaflık:** veri kaynağı/lisans bilgisi, gecikme, risk bildirimi, model sınırlamaları, gizlilik ve kullanım koşulları.
9. **Yönetim paneli (yalnızca yetkili):** veri sağlığı, iş kuyruğu, model sürümleri, hata ve güvenlik olayları; kullanıcı parolaları veya sırlar gösterilmez.

### UX TODO
- [ ] Önce masaüstü ve mobil uyumlu responsive tasarım prototipi hazırla.
- [ ] En önemli eylemleri netleştir: ara, izle, grafik aç, uyarı kur.
- [ ] Renkleri tek başına anlam göstergesi yapma; artış/azalış yanında ikon ve metin kullan.
- [ ] Grafiklerde yakınlaştırma, dönem seçimi, imleç bilgisi, hacim ve veri boşluğu gösterimi ekle.
- [ ] Veri gecikmişse açık bir uyarı göster; eski fiyatı güncelmiş gibi sunma.
- [ ] Boş durum, yükleniyor durumu, hata durumu ve tekrar deneme davranışlarını tasarla.
- [ ] Klavye ile kullanım, ekran okuyucu etiketleri, kontrast ve erişilebilirlik kontrolleri yap.
- [ ] Telefon ekranında tablo yerine okunabilir kartlar veya yatay kaydırma kullan.
- [ ] Tahmin ekranında “kesin hedef” izlenimi vermekten kaçın; tahmin ufku, belirsizlik ve model sürümünü göster.
- [ ] Kullanıcı testleri yap; yeni kullanıcıların temel görevi yardımsız tamamlayıp tamamlamadığını ölç.

**Çıktı:** Figma veya eşdeğer prototip, tasarım sistemi ve UX test notları.

### v2 eklemeleri
- [ ] 60 saniyede ilk grafik: açılış ekranında örnek sembol ve arama kutusu.
- [ ] Türkçe sayı/tarih biçimi (1.234,56; gg.aa.yyyy), TRY, Europe/Istanbul.
- [ ] Kısaltma yanında tek cümle açıklama (RSI, MACD, ATR) — yardım balonu.
- [ ] `docs/DISCLAIMER_TR.md` metinlerini grafik/analiz altında sabit ve rahatsız etmeyen biçimde göster.
- [ ] Performans bütçesi: LCP < 2,5 sn, INP < 200 ms, ilk yük JS bütçesi; CI'da Lighthouse kontrolü.
- [ ] Karanlık tema, kontrast ve klavye gezinmesi; axe ile otomatik erişilebilirlik denetimi.
