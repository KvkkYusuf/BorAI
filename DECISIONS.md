# Karar kaydı (kısa tut: karar + gerekçe + alternatif)

| Tarih | Karar | Gerekçe | Alternatif |
|---|---|---|---|
| 2026-10-18 | İlk hedef pazar: Yalnızca BIST hisseleri | Odaklanmış MVP, yerel piyasa uzmanlığı, tek mevzuat alanı | ABD hisseleri (aynı anda başlamama kuralı) |
| 2026-10-18 | Veri türü: Gün sonu (EOD) verisi | Düşük lisans ve altyapı maliyeti, mevzuat kolaylığı | Gecikmeli veya gerçek zamanlı veri |
| 2026-10-18 | Başlangıç evreni: BIST 30 / BIST 100 hisseleri | Yüksek likidite, kontrollü veri hacmi ve temiz veri takibi | Tüm BIST hisseleri |
| 2026-10-18 | LLM açıklama katmanı: v1'de yok | Deterministik analiz önceliği, maliyet ve tavsiye riskini önleme | LLM özet katmanı |
| 2026-10-18 | Yasal uyarı sunumu: Grafik altı sabit metin + tek seferlik giriş onayı | Yüksek görünürlük, SPK uyumu, kullanıcı deneyimini bozmama | Yalnızca footer veya her ekranda modal |
| 2026-10-18 | Gösterge dili: Kural tabanlı nötr dil (al/sat yok) | Yatırım danışmanlığı sınırını kesin koruma | Standart al/sat sinyal terimleri |
| 2026-10-18 | Platform ve kitle: Responsive web, kişisel kullanım + kapalı beta | Hızlı iterasyon, cihaz esnekliği, kontrollü kullanıcı büyümesi | Sadece masaüstü veya yerel mobil uygulama |
| 2026-10-18 | Erişim ve giriş: Kayıtsız anonim arama/grafik, izleme/uyarı için e-posta | 60 sn içinde ilk değeri gösterme, bariyersiz deneyim | İlk andan zorunlu üyelik veya sırf yerel depolama |
| 2026-10-18 | UX ilkeleri: 3 tık kuralı, sade Türkçe, uyarı yorgunluğu önleme onaylandı | Kullanıcı dostu, erişilebilir ve sade finansal araç tasarımı | Karmaşık profesyonel terminal arayüzü |
| 2026-10-18 | Kabul ölçütleri ve SLA: BIST EOD ≤ 30 dk (18:40), API p95 < 300ms, Web LCP < 2,5s | Ölçülebilir kalite kapısı, net ve gerçekçi performans çıtası | Belirsiz/ölçülemeyen hedefler veya aşırı esnek SLA |
| 2026-10-18 | Veri lisansı ve kaynağı: Resmi/lisanslı EOD API (izinsiz scraping kesinlikle yasak) | Hukuki ve telif risklerini sıfırlama, veri bütünlüğünü sağlama | Lisanssız web kazıma veya şüpheli veri kaynakları |
| 2026-10-18 | Faz 1 Veri Stratejisi: Prototipte doğrulanmış Kaggle/tarihsel veri, canlı öncesi resmi EOD sağlayıcı | Erken geliştirme maliyetini sıfırlama, algoritma doğrulama, telif/lisans ihlalini kesin önleme | Geliştirme başında maliyetli borsa lisansı alma veya lisanssız scraping |
| 2026-10-18 | SPK Yatırım Danışmanlığı Sınırı: Sıfır kişiselleştirme, izleme listesinde maliyet/kâr-zarar toplanmaz | SPK yetkisiz yatırım danışmanlığı suçunu ve hukuki yaptırımı kesin engelleme | Kullanıcı maliyet/portföy takibi veya yönlendirici al/sat önerileri |
| 2026-10-18 | Marka ve Logo: Logo kullanılmayacak; yalnızca sembol kodu ve bülten şirket unvanı | Telif/marka hakkı ihlali risklerini sıfırlama | Webden çekilen resmi olmayan şirket logoları |
| 2026-10-18 | Kaggle/Tarihsel Veri: Her set için "Veri Kartı" şartı, lisansı belirsiz veri depoya giremez | Veri izlenebilirliği, lisans denetimi ve model veri hijyeni | Kaynağı belirsiz ham csv dosyaları |
| 2026-10-18 | KVKK Hakları: Tek tıkla doğrudan kalıcı silme (hard delete) ve JSON dışa aktarma | Kullanıcı gizliliği, veri minimizasyonu ve yasal haklara tam saygı | Silmeyi zorlaştıran uzun süreçler veya belirsiz soft delete |
| 2026-10-18 | E-posta Politikası: Yalnızca işlemsel (transactional) e-posta, pazarlama/bülten yok | İYS yükümlülüğü ve istenmeyen posta riskini sıfırlama | Pazarlama e-postaları veya zorunlu tanıtım bültenleri |
| 2026-10-18 | FAZ-01 Hukuk/Veri Lisansı Kapısı: COMPLIANCE_TR tam onaylandı, AI v1 kapalı, avukat kapısı bağlandı | Veri kullanım hakları ve yasal sınırlar kesinleşmeden kod yazımını ve canlı yayını engelleme | Hukuki zemin olmadan geliştirmeye başlama |
| 2026-10-18 | Git ve CI Düzeni: Yerel git (main dalı) başlatıldı, .github/workflows/ci.yml ile secret tarama eklendi | Kod/dokümantasyon bütünlüğü, secret sızıntısını otomatik engelleme | Versiyon kontrolsüz geliştirme |
