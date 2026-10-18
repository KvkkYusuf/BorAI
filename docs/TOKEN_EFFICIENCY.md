# Token Verimliliği

## A) Geliştirme sırasında (Antigravity)
Amaç: aynı işi daha az bağlamla, daha az tekrar ve daha az hatayla yapmak.
- **Bağlamı küçük tut:** `AGENTS.md` kısa (≈2,5 KB); konu kuralları `.agent/rules/` altında ayrı. TODO tek dev dosya değil: indeks + faz dosyaları. Ajan yalnızca aktif fazı okur.
- **Bir oturum = bir görev.** Uzayan sohbetler her mesajda eski bağlamı taşır; görev bitince yeni oturum aç, durumu `PROGRESS.md`'den devral.
- **Tekrarlayan prompt yerine workflow:** `/yeni-gorev`, `/gorev-bitir`, `/guvenlik-incelemesi`, `/hukuk-kapisi`.
- **Hedefli komut ver:** "FAZ-05 madde 3'ü uygula, yalnızca `services/market_data` içinde" gibi. "Tüm projeyi gözden geçir" demekten kaçın.
- **Çıktıyı sınırla:** testi `-q`/kısa özetle çalıştır; uzun log, lock dosyası, veri dosyasını bağlama yapıştırma; `head/tail/grep` kullan.
- **Model seçimi:** sıradan şablon/test işinde hızlı-ucuz model, mimari/güvenlik/hukuki metin incelemesinde güçlü model (Antigravity'de seçenekleri ve kotaları kendi hesabında doğrula).
- **Boşa dönüşü azalt:** önce plan, sonra uygula; yanlış varsayımı erken yakalamak, yeniden yazdırmaktan ucuzdur.
- **Kural dosyalarını şişirme:** rule/workflow dosyası başına yaklaşık 12.000 karakter sınırı bildirilmiştir (sürümünde doğrula); her satırın bir davranışı değiştirmesi gerekir.

## B) Uygulama çalışırken (LLM kullanılırsa)
Ayrıntılı ilkeler: `.agent/rules/token-ve-llm.md`. Özet: önce kodla hesapla; LLM'e yalnızca kısa yapılandırılmış JSON ver; `max_tokens` + JSON şema; sonuç önbelleği (sembol+veri sürümü+prompt sürümü); küçük model varsayılan; kullanıcı ve global bütçe; bütçe dolunca LLM'siz kural tabanlı özet; her çağrının token/maliyet kaydı.

## Ölçüm
- Panoda: özellik başına token, çağrı sayısı, önbellek isabet oranı, kullanıcı başına maliyet.
- Hedef: LLM çağrılarının ≥ %80'i önbellekten karşılansın (başlangıç hedefi; ölç ve güncelle).
