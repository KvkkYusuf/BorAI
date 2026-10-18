# Finans ve hukuk sınırları

Not: Bu kurallar hukuki görüş değildir; yayına çıkmadan önce sermaye piyasası hukuku bilen bir avukatla doğrulanmalıdır.

## Neden sınır var
Türkiye'de yatırım danışmanlığı SPK mevzuatında düzenlenmiş bir faaliyettir ve yalnızca izin verilmiş kuruluşlar yapabilir. Ayırıcı unsur **kişiselleştirmedir**: kişinin mali durumu, risk ve getiri tercihleri dikkate alınarak yönlendirici tavsiye verilmesi. Bu yüzden ürün **herkese aynı sunulan, kişiselleştirilmemiş bilgi ve analiz** olarak kalmalıdır.

## Yapma
- "Al", "sat", "tut", "şimdi gir", "hedef fiyat", "garanti", "kesin yükselir" gibi ifadeler üretme (kod, UI metni, e-posta, LLM çıktısı, test verisi dahil).
- Kullanıcıdan risk profili, yatırım hedefi, gelir, portföy büyüklüğü, "ne kadar kaybedebilirsin" gibi bilgileri toplama ve buna göre sonuç değiştirme.
- Kullanıcının izleme listesine/portföyüne göre kişiye özel yönlendirme üretme. İzleme listesi yalnızca takip ve uyarı kurma amaçlıdır.
- Otomatik emir, aracı kurum entegrasyonu veya portföy yönetimi ekleme.
- Veriyi lisansı olmayan kaynaktan kazıma/yeniden yayınlama. Veri gecikmişse güncelmiş gibi gösterme.

## Yap
- Her fiyat ve sinyalde kaynak, veri zamanı, gecikme durumu ve hesaplama/model sürümü göster.
- Sinyal dili ölçülü olsun: "tanımlı koşullara göre yükseliş eğilimi koşulu sağlandı" gibi. Bu, yatırım tavsiyesi değildir uyarısı kısa ve görünür yerde dursun (metin: `docs/DISCLAIMER_TR.md`).
- Tahminleri aralık/olasılık + belirsizlik + ufuk ile göster; tek sayıyı "hedef" gibi sunma.
- Naif baseline'ı geçmeyen modeli kullanıcıya gösterme (özellik bayrağı kapalı).
- Yapay zekâ ile üretilen her metni "otomatik üretilmiştir" diye etiketle.
- Ticari kullanım, ücretli plan, reklam, aracı kurum yönlendirmesi eklenecekse önce `docs/COMPLIANCE_TR.md` kapı kontrolünü geçir.
- Şirket adı/logosu/marka kullanımı için hak durumu belirsizse logo kullanma, yalnızca sembol/ad göster.
