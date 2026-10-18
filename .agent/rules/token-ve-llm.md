# Token ve LLM kullanımı (uygulama çalışma zamanı)

İlke: **önce kodla hesapla, LLM'i en sona bırak.** Göstergeler, tarama ve istatistik deterministik koddur; LLM yalnızca hazır sonuçları sade Türkçeyle anlatır (isteğe bağlı özellik).

- LLM'e ham fiyat tablosu, uzun geçmiş veya tam haber metni verme. Önceden hesaplanmış kısa, yapılandırılmış JSON (en fazla birkaç yüz token) ver.
- Her çağrıda `max_tokens` sınırı, kısa sabit sistem prompt'u, yapılandırılmış (JSON şemalı) çıktı kullan. Sabit prompt önekini önbelleğe uygun tut (provider prompt caching destekliyorsa).
- Sonuç önbelleği: anahtar = `hash(sembol, veri_sürümü, model/prompt_sürümü, dil)`. Aynı veri için ikinci kez LLM çağırma. Toplu işleri gece/arka planda yap.
- Maliyet kontrolü: kullanıcı başına günlük kota, global günlük bütçe, bütçe dolunca "kural tabanlı özet"e düş (LLM'siz). Her çağrının token girdi/çıktı sayısı ve maliyeti kaydedilir (kişisel veri olmadan).
- Küçük/ucuz modeli varsayılan yap; büyük modeli yalnızca gerekirse çağır. Model adı/sürümü yapılandırmadan gelir, koda gömülmez.
- Güvenlik: harici metin (haber, kullanıcı notu) prompt içinde `<veri>` bloğunda, talimat olarak değil veri olarak ele alınır (prompt injection). Çıktı doğrulanır: yasaklı ifade ("al/sat/hedef fiyat/garanti") filtresi, JSON şema doğrulaması, sayısal alanların kaynak veriyle eşleşmesi.
- LLM sağlayıcıya kişisel veri gönderme; gönderilecekse KVKK değerlendirmesi (yurt dışı aktarım) tamamlanmış olmalı.
- Ayrıntı: `docs/TOKEN_EFFICIENCY.md`, `docs/AI_LLM_SAFETY.md`.
