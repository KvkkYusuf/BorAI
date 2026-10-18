# Yapay Zekâ (PyTorch + isteğe bağlı LLM) Güvenliği

## Katmanlar
1. Deterministik analiz (göstergeler, kurallar) — varsayılan, her zaman açık.
2. PyTorch tahmin (deneysel) — yalnızca onaylı model sürümü ve `FORECAST_ENABLED=true` iken.
3. LLM açıklama katmanı (isteğe bağlı) — yalnızca hazır sonuçları anlatır, yeni sayı/öneri üretmez. `LLM_ENABLED=false` varsayılan.

## LLM tehditleri ve önlemler
| Tehdit | Önlem |
|---|---|
| Prompt injection (haber/kullanıcı notu/web metni) | Harici metin `<veri>` bloğunda, "talimat değildir" kuralı; araç/ağ yetkisi yok; çıktı şema ile doğrulanır |
| Yönlendirici içerik ("al", "hedef fiyat") | Yasaklı ifade filtresi + yeniden üretim yerine kural tabanlı şablona düşme |
| Uydurma sayı (halüsinasyon) | LLM'e sayıyı yazdırma; sayılar şablona koddan eklenir; LLM sadece cümle kurar |
| Sır/kişisel veri sızıntısı | Prompta kişisel veri ve anahtar girmez; log maskelenir |
| Maliyet suistimali | Kullanıcı kotası, global bütçe, oran sınırı, `max_tokens`, önbellek |
| Sağlayıcı kesintisi | Zaman aşımı, sınırlı retry, LLM'siz kural tabanlı özet |
| Model/ağırlık dosyası saldırısı | Güvenilmeyen `pickle` yükleme yok; `safetensors`/TorchScript; imza/hash doğrulama |

## Kabul testleri (Faz 11/13)
- Yasaklı ifade seti (en az 30 örnek) LLM çıktısında 0 geçmeli.
- Injection test seti: zararlı talimat içeren haber başlığı çıktıyı değiştirmemeli.
- Aynı girdi → aynı önbellek anahtarı → ikinci çağrıda LLM çağrılmaz.
- Bütçe aşımında kullanıcı hata görmez, kural tabanlı özet görür.
