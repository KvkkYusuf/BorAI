# /guvenlik-incelemesi — Değişiklik güvenlik gözden geçirmesi
Yalnızca `git diff` üzerinde çalış. Kontrol et: sır sızıntısı, kimlik/yetki (IDOR), girdi doğrulama, SQL birleştirme, CSRF/XSS/SSRF, oran sınırı, hata yanıtında bilgi sızıntısı, yeni bağımlılık (var mı, bakımlı mı, lisans), log içeriği (kişisel veri), LLM çağrılarında prompt injection ve çıktı filtresi.
Çıktı: bulgu listesi (kritik/yüksek/orta/düşük) + önerilen düzeltme. Düzeltmeyi onaysız uygulama.
