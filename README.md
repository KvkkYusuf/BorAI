# Borsa Analiz Sistemi — Başlangıç Paketi v2

Antigravity ile geliştirilecek, güvenli, yasal sınırlar içinde, kullanıcı dostu, token/maliyet bilinçli ve performans hedefli bir borsa analiz uygulaması için yol haritası.

## Yapı
- `AGENTS.md` — her zaman yüklenen kısa çekirdek kurallar (Antigravity bunu okur)
- `.agent/rules/` — konu bazlı kurallar (finans-hukuk, güvenlik, veri-ml, kod-kalitesi, token-ve-llm)
- `.agent/workflows/` — tekrar eden işler için kısa komutlar (`/yeni-gorev`, `/gorev-bitir`, `/guvenlik-incelemesi`, `/hukuk-kapisi`)
- `TODO.md` — kısa indeks; ayrıntılar `docs/phases/FAZ-XX-*.md` içinde (ajan yalnızca aktif fazı okur)
- `docs/` — kapsam, kabul ölçütleri, hukuk, mimari, güvenlik, performans, token, LLM güvenliği, metinler, kurulum rehberi
- `PROGRESS.md`, `DECISIONS.md` — ilerleme ve karar kaydı (TODO'dan ayrıldı; tek satırlık ekleme)

## Başlangıç (5 adım)
1. Klasörü Antigravity'de workspace olarak aç (`docs/ANTIGRAVITY_SETUP.md` adımlarını uygula; arayüz ayarlarını kendi sürümünde doğrula).
2. `docs/FIRST_PROMPT.md` içindeki ilk komutu yapıştır; Faz 0 ile başla.
3. Bir oturum = bir görev. Görev bitince `/gorev-bitir`.
4. Faz kapılarını atlama (özellikle Faz 1: hukuk/veri lisansı).
5. Canlıya çıkmadan `docs/phases/FAZ-16-dagitim-oncesi.md` listesi tamam olmalı.

## Gerçekçi beklenti
Piyasayı kusursuz tahmin eden model yoktur. Ürün; analiz, açıklanabilir kurallar, olasılıksal/deneysel tahmin ve uyarı sistemidir. Hukuki statü netleşmeden kişiye özel öneri yoktur.
