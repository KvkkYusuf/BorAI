# Ürün Kapsamı

> [ÖNERİ] ile işaretli satırlar başlangıç önerisidir; Faz 0'da kullanıcı onaylar ve `DECISIONS.md`'ye yazılır.

## Amaç
Kullanıcının piyasa verilerini incelemesine, hisseleri karşılaştırmasına, geçmiş grafiklere bakmasına ve tanımlı koşullara göre uyarı almasına yardımcı olmak. Herkese aynı sunulan, kişiselleştirilmemiş bilgi/analiz aracıdır.

## İlk sürüm kararları
- Hedef borsa: Yalnızca Borsa İstanbul hisseleri (BIST 30 ve BIST 100 endeks payları odaklı; yüksek likidite, kontrollü evren; ABD borsaları ile aynı anda başlanmaz)
- Veri türü: Gün sonu (EOD / End-of-Day) verisi (canlı/gecikmeli yayın maliyet ve lisans yükü sebebiyle ilk sürümde yok)
- LLM katmanı: v1'de LLM yok; yalnızca deterministik göstergeler ve teknik analiz
- Kullanıcı kitlesi: Kişisel kullanım + küçük kapalı beta
- Platform: Duyarlı (responsive) web (mobil uyumlu web arayüzü; yerel mobil uygulama sonraki fazlarda)
- Erişim modeli: Kayıt olmadan anonim salt okunur arama ve grafik görüntüleme (60 sn ilk değer); izleme listesi ve uyarılar için e-posta ile giriş
- Dil: Türkçe (sayı biçimi 1.234,56; para birimi TRY; saat dilimi Europe/Istanbul)
- Para kazanma: ilk sürümde yok (ücretli plan hukuki kapıdan geçmeden eklenmez)
- Kapsam dışı: al/sat tavsiyesi, hedef fiyat, risk profiline göre yönlendirme, otomatik emir, portföy yönetimi, aracı kurum entegrasyonu

## Yasal Sınırlar ve Bilgilendirme Niteliği
- **Yatırım Tavsiyesi Değildir:** Uygulama SPK mevzuatı uyarınca bir yatırım danışmanlığı aracı değildir; yalnızca kişiselleştirilmemiş, herkese eşit sunulan genel bilgi ve analiz sağlar.
- **Yasal Uyarı Sunumu:** Her grafik ve analiz ekranının altında `docs/DISCLAIMER_TR.md` standart uyarısı sabit ve görünür olarak yer alır; uygulamaya ilk girişte kullanıcıya tek seferlik bilgilendirme onayı sunulur.
- **Sinyal ve Gösterge Dili:** Tavsiye veya yönlendirme çağrıştıran ifadeler ("al", "sat", "hedef fiyat") kesinlikle kullanılmaz; tamamen nötr ve kural tabanlı dil kullanılır (örn: "Tanımlı yükseliş eğilimi koşulu sağlandı", "Al/sat önerisi değildir").
- **Veri Lisansı ve Kaynak Politikası:** İki aşamalı veri stratejisi izlenir:
  1. *Geliştirme / Prototip / Test:* Doğrulanmış, lisansı ticari olmayan araştırmaya uygun tarihsel veri setleri (Kaggle / açık kaynaklı doğrulanmış arşivler) yalnızca modelleme ve algoritma testinde kullanılır; canlı veya güncel veri gibi sunulmaz.
  2. *Canlı / Üretim Ortamı:* Canlı yayına ve kapalı betaya geçmeden önce yalnızca resmi/lisanslı EOD veri sağlayıcısı/API sözleşmesiyle veri alınır. İzinsiz web kazıma (scraping) ve lisanssız yeniden dağıtım kesinlikle yasaktır; her verinin kaynağı, tarihi ve zamanı şeffafça gösterilir.

## Kullanıcı hikâyeleri (v1)
1. Sembol/şirket adıyla ararım.
2. Seçtiğim aralıkta geçmiş fiyat grafiğini görürüm.
3. Hisseyi izleme listeme eklerim.
4. Açıklanabilir teknik koşul için uyarı kurarım.
5. Veri kaynağını, zamanını, gecikmesini ve tahmin belirsizliğini görürüm.
6. E-posta ile güvenli giriş yapar, ayarlarımı, dışa aktarma ve hesap silmeyi yönetirim.

## Kullanıcı kolaylığı ilkeleri
- İlk değer 60 saniye içinde: kayıt olmadan örnek sembol arama + grafik (anonim, salt okunur).
- Ana eylemler 3 dokunuş/tıklama içinde: ara → hisse aç → izle/uyarı kur.
- Sade Türkçe; kısaltma (RSI, MACD) yanında tek cümlelik açıklama.
- Renk tek başına anlam taşımaz (ikon + metin). Klavye ve ekran okuyucu desteği.
- Boş/yükleniyor/hata/gecikmiş veri durumları tasarlanmıştır; hata mesajı ne olduğunu ve ne yapılacağını söyler.
- Uyarı yorgunluğu yok: varsayılan düşük sıklık, sessiz saatler, tek tıkla abonelikten çıkma.

## Başarı ölçütleri
`docs/ACCEPTANCE_CRITERIA.md` ve `docs/PERFORMANCE.md` içinde sayısal hedefler vardır.
