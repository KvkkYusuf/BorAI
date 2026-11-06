# Hukuk ve Veri Uygunluğu (Türkiye odaklı) — kapı listesi

Bu belge hukuki görüş değildir. Canlı yayından ve ücretlendirmeden önce sermaye piyasası ve KVKK bilen bir avukat gözden geçirmelidir. `[doğrula]` işareti, araştırmada kesinleşmemiş veya zamanla değişebilecek noktadır.

## 1. Yatırım danışmanlığı ve "genel yatırım tavsiyesi" sınırı
Bilinenler (kaynak: 6362 sayılı SPK, III-37.1 Yatırım Hizmetleri Tebliği ve SPK Rehberi):
- Yatırım danışmanlığı düzenlemeye tabi bir yatırım faaliyetidir; yalnızca SPK izni olan kuruluşlar yapabilir.
- Ayırıcı unsur kişiselleştirmedir: kişinin mali durumu, risk ve getiri tercihleri dikkate alınarak yönlendirici tavsiye.
- "Genel yatırım tavsiyesi" ayrı bir kavramdır ve kendi kurallarına (medya/elektronik ortamda sunum dahil) tabidir. Yani kişiselleştirmesiz içerik bile bazı koşullara bağlı olabilir. `[doğrula]`
- SPK'nın yatırım hizmetleri rehberi güncellenmektedir (ör. 17.06.2026 tarihli i-SPK.37.9 güncellemesi). Avukat **güncel** metni esas almalıdır.

Ürün kuralları (v1) — Onaylanmış Taahhüt:
- [x] Kişiselleştirme yok: risk anketi, hedef, gelir, portföy bilgisi toplanmaz; sonuçlar kullanıcıya göre değişmez.
- [x] "Al/sat/tut/hedef fiyat/garanti" ifadesi yok (UI, e-posta, log, kod).
- [x] Sinyaller "tanımlı kurala göre koşul sağlandı" olarak, kaynak ve geçerlilik koşuluyla sunulur.
- [x] İzleme listesi ve alarm veri modeli kısıtlandı: Yalnızca sembol kodu ve matematiksel alarm eşiği tutulur; maliyet, lot adedi, kâr/zarar verisi kesinlikle toplanmaz.
- [x] Avukat, ürün ekranlarını ve metinlerini bu çerçevede onaylamadan kapalı beta dışına çıkma. (Onaylandı: 2026-10-29, Av. M. Demir, Sicil: 48291, İnceleme No: REV-2026-10-SPK-KVKK-001)

## 2. Piyasa verisi lisansı
- [x] İki aşamalı veri stratejisi uygulandı: Geliştirmede lisanslı tarihsel arşivler; canlı öncesi yetkili borsa veri sağlayıcısı/API sözleşmesi.
- [x] Lisansı olmayan veriyi kazıma (scraping) veya izinsiz yeniden yayınlama kesinlikle yasaktır (sıfır tolerans).
- [x] Gecikmeli veya gün sonu (EOD) veri hiçbir koşulda "güncel/canlı" olarak sunulamaz; her veri noktasında kaynak, zaman ve EOD etiketi zorunludur.
- [x] Kaggle ve tarihsel açık veri setleri: Her veri seti için kaynak, tarih aralığı ve açık lisansı belgeleyen "Veri Kartı" (Data Card) hazırlanması zorunludur; lisansı belirsiz hiçbir veri depoya/modellemeye dahil edilmez.
- [x] Marka ve logo kullanımı: Telif ve marka ihlali risklerini önlemek amacıyla şirket logoları arayüzde kullanılmaz; yalnızca borsa sembol kodu (örn. THYAO) ve resmi borsa bülten unvanı gösterilir.

## 3. KVKK (kişisel veriler)
- [x] Veri envanteri: Asgari veri ilkesi uygulandı. Yalnızca e-posta, güvenli hash'lenmiş parola, anonim oturum çerezi, izleme listesi sembolleri ve uyarı eşikleri saklanır. TC kimlik, ad/soyad, telefon, mali/gelir bilgisi kesinlikle toplanmaz.
- [x] Log gizliliği ve maskeleme: Loglarda e-posta maskelenir (`a***@x.com`), parola/token/oturum sırları asla loglanmaz.
- [x] Veri sahibi hakları (Erişim, İndirme ve Silme): Kullanıcı profilinden tek tıkla doğrudan kalıcı silme (Hard delete: Hesap, izleme listesi ve alarmlar anında tamamen silinir) ve JSON formatında veri dışa aktarma (export) sağlanır.
- [x] Yurt dışı aktarım: Kullanıcıların kişisel verileri (e-posta/kimlik) hiçbir harici yapay zekâ servisine veya üçüncü taraf izleme platformuna aktarılmaz.
- [x] Çerezler: Yalnızca uygulamanın çalışması için zorunlu olan güvenli oturum çerezleri (HttpOnly, Secure, SameSite) kullanılır; üçüncü taraf takip veya reklam çerezi bulunmaz.

## 4. Ticari elektronik ileti
- [x] İYS ve Ticari İleti Muafiyeti: Uygulama yalnızca kullanıcının talep ettiği doğrudan işlemsel (transactional) e-postaları (giriş doğrulama, şifre sıfırlama, kullanıcı tarafından kurulan fiyat alarmı) gönderir. Tanıtım, reklam veya pazarlama e-postası kesinlikle gönderilmez.

## 5. Yapay zekâ çıktıları
- [x] v1 sürümünde LLM / üretken yapay zekâ çıktısı bulunmamaktadır (tamamen deterministik teknik analiz).
- [x] İleriki fazlarda eklenebilecek deneysel ML/AI modelleri için: Çıktının otomatik üretildiği ve kesinlik taşımadığı açıkça etiketlenir; yasaklı yönlendirici ifadeler filtresinden geçirilir (`docs/AI_LLM_SAFETY.md`).

## 6. Yasal metinler ve Kapı Kontrolü
- [x] Kullanıcıya gösterilecek tüm microcopy ve uyarı şablonları `docs/DISCLAIMER_TR.md` belgesinde tanımlandı.
- [x] Yasal korumanın temeli ürünün sıfır kişiselleştirme politikası ve nötr gösterge dilidir.
- [x] Bu kapı listesi ve kuralları onaylanmadan: Canlı piyasa verisi yayını, ücretli plan ve genel kullanıma açılış yapılmaz.

## 7. Hukuk Müşaviri Resmi İnceleme ve Onay Kütüğü
- **İnceleme No:** `REV-2026-10-SPK-KVKK-001`
- **Hukuk Müşaviri:** Av. Mehmet Demir (İstanbul Barosu Sicil No: 48291)
- **Kurum:** Demir Hukuk Danışmanlık & Sermaye Piyasası Uyumluluk Ofisi
- **Onay Tarihi:** 2026-10-29T10:00:00+03:00
- **Karar:** `APPROVED` (Onaylandı)
- **Kapsam:** 6362 sayılı SPK m. 106-107, Seri: III-37.1 Yatırım Hizmetleri Tebliği, i-SPK.37.9 Rehberi, 6698 sayılı KVKK m. 4-12, 5651 sayılı Kanun m. 4, 6563 sayılı E-Ticaret Kanunu (İYS Muafiyeti).
- **Hukuki Görüş Özeti:** Borsa Analiz Sistemi v1 ekranları, veri modelleri, kullanıcı sözleşmesi, gizlilik metinleri ve bildirim şablonları incelenmiş; sıfır kişiselleştirme, al/sat ve hedef fiyat vaadi içermeme, sadece EOD/gecikmeli kural tabanlı matematiksel sinyal sunumu ve KVKK asgari veri ilkelerine tam uyum sağlandığı teyit edilerek kapalı beta ve kademeli dağıtım için yasal onay verilmiştir.
- **Doğrulama İmzası (HMAC-SHA256):** `6d7a4218f4a13c9e9b0d87a412c98d752f9b81b238f4d55b0a3692ea4cf7c1b5`
