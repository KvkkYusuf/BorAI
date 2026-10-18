# Antigravity Kurulum Rehberi

> Araştırmada görülen bilgiler: Antigravity'nin proje kökündeki `GEMINI.md` veya `AGENTS.md` dosyasını, ayrıca `.agent/rules/` ve `.agent/workflows/` klasörlerini okuduğu bildiriliyor; `GEMINI.md` aynı klasörde `AGENTS.md`'den önceliklidir; küresel kural yolu `~/.gemini/GEMINI.md`. Bu davranış sürümler arasında değişebilir. **Kendi sürümünde Customizations/Rules panelinden doğrula** ve aşağıdakileri buna göre uyarla.

## 1. Klasörü aç
Zip'i aç, klasörü workspace olarak aç. `AGENTS.md` kök dizinde durmalı. Aynı kuralları hem `GEMINI.md` hem `AGENTS.md` içine koyma (çifte yükleme = çifte token).

## 2. Kuralları kontrol et
Rules panelinde `AGENTS.md` ve `.agent/rules/*.md` dosyalarının göründüğünü doğrula. Her kuralın etkinleşme biçimini seç (varsa): çekirdek `AGENTS.md` her zaman açık; konu dosyaları görev tipine göre/isteğe bağlı olursa token tasarrufu sağlar. Seçenek adlarını kendi arayüzünde kontrol et.

## 3. Ajan güvenlik ayarları (önerilen)
- Çalışma modu: ajan yardımlı (agent-assisted) / gözden geçirme isteyen mod; tam otonomiyle başlama.
- Terminal komutları: onay iste; yıkıcı komutlar için yasak/onay listesi.
- Üretim ortamı kimlik bilgilerini hiçbir zaman çalışma alanına koyma; `.env` erişimini kısıtla (varsa dışlama/ignore ayarı).
- Tarayıcı/dış içerik erişimi: güvenilmeyen sayfadaki talimatları ajan izlemesin (kural: veri ≠ komut).

## 4. Git ve CI
`git init`, korumalı `main`, özellik dalı. İlk görevde CI: lint, tip, test, secret taraması, bağımlılık taraması.

## 5. İş akışı
`/yeni-gorev` → uygula → `/gorev-bitir`. Güvenlik ve metin değişikliklerinde `/guvenlik-incelemesi`, `/hukuk-kapisi`.

## 6. Bağlam temizliği
Görev bitince yeni oturum aç. Uzun sohbette ajan "tüm repoyu" taramaya başlarsa durdur ve hedefli komut ver.
