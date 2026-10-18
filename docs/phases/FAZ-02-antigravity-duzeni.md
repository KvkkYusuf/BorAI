## 2. Antigravity çalışma düzeni ve proje kuralları

- [x] Antigravity içinde yeni bir proje/workspace oluştur.
- [x] Git deposu başlat; `main` dalını korumalı yap ve özellik dalları kullan.
- [x] Aşağıdaki klasör yapısını oluştur.
- [x] `AGENTS.md` ve `.agent/rules/` kurallarının Antigravity tarafından yüklendiğini Rules panelinde doğrula (`docs/ANTIGRAVITY_SETUP.md`).
- [x] Ajanın her değişiklikte plan, etkilenen dosyalar, testler ve riskleri belirtmesini iste.
- [x] Ajanın izinsiz şekilde sırları, kişisel verileri veya gerçek kullanıcı verilerini kullanmasını engelle.
- [x] Her görevde küçük ve gözden geçirilebilir değişiklikler yaptır; testleri çalıştırmadan görevi tamamlandı sayma.
- [x] Bağımlılıkları sürüm aralıkları yerine mümkün olduğunca kilit dosyasıyla sabitle.
- [x] `.env`, API anahtarları, erişim belirteçleri ve özel anahtarları Git'e ekleme.
- [x] CI'da biçimlendirme, lint, test, tip kontrolü, bağımlılık taraması ve secret scanning kur.

**Çıktı:** Çalışan Git deposu ve ajan talimatları.

### v2 eklemeleri
- [x] `docs/ANTIGRAVITY_SETUP.md` adımlarını uygula; `AGENTS.md` ve `.agent/rules/*.md` dosyalarının yüklendiğini Rules panelinde doğrula.
- [x] Kuralların etkinleşme biçimini ayarla (çekirdek her zaman açık, konu kuralları göreve göre).
- [x] Terminal komut onayı ve yasak komut listesini aç; `.env` erişimini kısıtla.
- [x] `/yeni-gorev` ve `/gorev-bitir` workflow'larını bir deneme görevinde sına.
- [x] Not: bu fazdaki `ANTIGRAVITY_RULES.md` ifadeleri v2'de `AGENTS.md` + `.agent/rules/` ile karşılanır.
