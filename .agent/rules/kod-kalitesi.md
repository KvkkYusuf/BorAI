# Kod kalitesi ve performans

- Python: type hints, `ruff` + `mypy`/`pyright`, `pytest`. TypeScript: `strict`, ESLint. Biçimlendirme otomatik.
- İş mantığı arayüzden ayrı; veri sağlayıcı adaptör arayüzü arkasında (değiştirilebilir). Hata durumları açık; sınırsız retry ve sessiz fallback yok (üstel geri çekilme + üst sınır).
- Değişiklikle birlikte test + doküman güncelle. Şema/API değişirse migration ve sözleşme testi ekle.
- Tüm zamanlar UTC saklanır, kullanıcıya `Europe/Istanbul` (veya kullanıcı ayarı) ile gösterilir. Para birimi ve ham/düzeltilmiş fiyat ayrımı veri modelinde açık.
- Performans varsayılanları: sayfalama zorunlu; N+1 sorgu yok; sık okunan veriye önbellek (anahtar: sembol+aralık+veri sürümü); ağır hesap istek anında değil arka plan işinde; DB'de sorgu kalıplarına göre indeks; gereksiz yeniden render ve büyük bundle yok.
- Bütçeler (başlangıç hedefi, `docs/PERFORMANCE.md`): önbellekli API p95 < 300 ms, LCP < 2,5 sn, INP < 200 ms.
- Yeni bağımlılık: gerekçe + lisans + bakım + güvenlik kontrolü, onay.
