## 4. Geliştirme ortamı ve kalite kapıları

- [ ] Python sürümünü ve bağımlılık yöneticisini sabitle.
- [ ] Sanal ortam kur; geliştirme, test ve üretim bağımlılıklarını ayır.
- [ ] PyTorch'u hedef donanım/işletim sistemi için resmî kurulum seçicisinden kur ve sürümü kaydet.
- [ ] Kod biçimlendirme, lint, tip kontrolü ve test komutlarını tanımla.
- [ ] Pre-commit veya eşdeğer kalite kontrollerini ekle.
- [ ] GitHub Actions veya eşdeğer CI hattında testleri çalıştır.
- [ ] `.env.example` oluştur; gerçek sırları örnek dosyaya yazma.
- [ ] Yerel geliştirme için sentetik/örnek veri kullan; lisanslı canlı veriyi testlere gömme.

**Çıktı:** Yeni bir bilgisayarda dokümana göre tekrarlanabilir kurulum.

### v2 eklemeleri
- [ ] `.gitignore` ve `.env.example` dosyalarını kullan; secret taraması ve bağımlılık taramasını pre-commit/CI'a ekle.
- [ ] Kilit dosyası (lockfile) zorunlu; yeni bağımlılık için onay kuralı (`guvenlik.md`).
