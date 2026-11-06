## 4. Geliştirme ortamı ve kalite kapıları

- [x] Python sürümünü ve bağımlılık yöneticisini sabitle (`.python-version`: 3.12, `uv`).
- [x] Sanal ortam kur; geliştirme, test ve üretim bağımlılıklarını ayır.
- [x] PyTorch'u hedef donanım/işletim sistemi için resmî kurulum seçicisinden kur ve sürümü kaydet (PyTorch 2.6.0+cu124, CUDA aktif).
- [x] Kod biçimlendirme, lint, tip kontrolü ve test komutlarını tanımla (ruff, mypy, pytest).
- [x] Pre-commit veya eşdeğer kalite kontrollerini ekle.
- [x] GitHub Actions veya eşdeğer CI hattında testleri çalıştır.
- [x] `.env.example` oluştur; gerçek sırları örnek dosyaya yazma.
- [ ] Yerel geliştirme için sentetik/örnek veri kullan; lisanslı canlı veriyi testlere gömme.

**Çıktı:** Yeni bir bilgisayarda dokümana göre tekrarlanabilir kurulum.

### v2 eklemeleri
- [x] `.gitignore` ve `.env.example` dosyalarını kullan; secret taraması ve bağımlılık taramasını pre-commit/CI'a ekle.
- [x] Kilit dosyası (lockfile) zorunlu; yeni bağımlılık için onay kuralı (`guvenlik.md`).
