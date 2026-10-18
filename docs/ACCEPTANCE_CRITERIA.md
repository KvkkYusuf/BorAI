# Kabul Ölçütleri (başlangıç hedefleri — ölçüp gerçekçi değere güncelle)

| Alan | Ölçüt | Hedef |
|---|---|---|
| Veri güncelliği (EOD) | BIST seans bitimi (18:10) sonrası veri hazır ve doğrulanmış | ≤ 30 dk (en geç 18:40) |
| Veri kalitesi | Doğrulamayı geçmeyen kayıt oranı | < %0,5, hepsi işaretli |
| API (önbellekli okuma) | p95 yanıt | < 300 ms |
| API (önbelleksiz) | p95 yanıt | < 800 ms |
| Web | LCP / INP / CLS | < 2,5 sn / < 200 ms / < 0,1 |
| Uyarı gecikmesi | Veri hazır → bildirim | < 5 dk (EOD) |
| Kullanılabilirlik (beta) | Aylık | ≥ %99,5 |
| Güvenlik | Kritik/yüksek açık bulgu | 0 (yayın kapısı) |
| Gizlilik | Loglarda sır/kişisel veri | 0 (otomatik tarama) |
| Model | Baseline + maliyetli kıyası geçme | Geçmezse özellik kapalı |
| LLM maliyeti | v1'de LLM devre dışı | 0 TL (çağrı yok, deterministik analiz) |
| Erişilebilirlik | Otomatik denetim (örn. axe) kritik hata | 0 |
| Yeni kullanıcı | Yardımsız ilk grafik açma | ≥ %80 (5+ kişilik test) |

Bir faz, yalnızca ilgili ölçütlerin kanıtı (test çıktısı, rapor) `PROGRESS.md`'de varsa kapanır.
