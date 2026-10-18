# AGENTS.md — Borsa Analiz Sistemi (her zaman yüklenen çekirdek kurallar)

Dil: Kullanıcıyla Türkçe konuş. Kod, değişken ve commit mesajları İngilizce olabilir.
Bu dosya bilinçli olarak kısa tutulur (token tasarrufu). Ayrıntılar `.agent/rules/` ve `docs/` altındadır; yalnızca görevle ilgili olanı oku.

## Çalışma düzeni (token dostu)
1. Görev başında yalnızca `TODO.md` (kısa indeks) ve **aktif faz dosyasını** (`docs/phases/FAZ-XX-*.md`) oku. Tüm fazları, tüm `docs/` klasörünü veya tüm repoyu okuma.
2. Önce 5 satırı geçmeyen plan yaz: amaç, dokunulacak dosyalar, risk, test. Büyük/geri dönüşsüz iş için onay iste.
3. Küçük adım, küçük diff. Bir oturum = bir görev. Görev bitince özet yaz ve yeni sohbet/oturum öner.
4. Dosyaları kısmen oku (arama/satır aralığı); büyük log, lock dosyası, veri ve çıktıyı bağlama yapıştırma; yalnızca ilgili kısmı özetle.
5. Aynı bilgiyi tekrar yazma: kural, ilke ve karar için dosyaya referans ver.

## Dürüstlük
- Çalışmayan veya test edilmemiş şeyi çalışıyor gibi gösterme. Mock / gerçek veri ayrımını açık tut.
- Testi çalıştırmadan "tamam" deme; gerçek çıktıyı raporla. Başarısız testi gizleme veya devre dışı bırakma.
- Emin olmadığın API/sürüm/mevzuat bilgisini uydurma; "doğrulanmalı" diye işaretle.

## Kırmızı çizgiler (ihlal etme, tartışmasız)
- Kişiye özel al/sat önerisi, hedef fiyat, risk profiline göre yönlendirme, otomatik emir, portföy yönetimi **yok** (hukuki onay gelene kadar). Ayrıntı: `.agent/rules/finans-hukuk.md`
- Sır/anahtar/token/gerçek kullanıcı verisi koda, loga, prompta, commit'e **girmez**. Ayrıntı: `.agent/rules/guvenlik.md`
- Onaysız: yıkıcı komut, veri silme, `git push --force`, üretim erişimi, dış sisteme veri gönderme, yeni bağımlılık ekleme.
- Lisansı doğrulanmamış veri/kütüphane/model kullanma.

## Görev bitişi raporu (en fazla 10 satır)
Değişen dosyalar · yapılan iş · çalışan testler ve gerçek sonuç · bilinen risk · elle yapılacak adım · sonraki en küçük görev. Ardından `PROGRESS.md`'ye tek satır ekle.

## Hangi kural dosyası ne zaman
- Her görev: bu dosya. Kod yazarken: `kod-kalitesi.md`. Veri/model: `veri-ml.md`. Hukuk/metin/UI yazısı: `finans-hukuk.md`. Kimlik/API/altyapı: `guvenlik.md`. LLM çağrısı veya maliyet: `token-ve-llm.md`.
