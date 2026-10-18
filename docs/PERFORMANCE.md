# Performans Rehberi

## Tasarım ilkeleri
1. **Hesabı istek anında değil, önceden yap.** EOD veride göstergeler ve tahminler seans sonrası toplu (batch) hesaplanır; API yalnızca okur.
2. **Önbellek katmanları:** tarayıcı (ETag/Cache-Control) → CDN/API önbelleği → Redis (anahtar: sembol+aralık+veri sürümü) → DB. Veri sürümü değişince anahtar kendiliğinden geçersizleşir.
3. **DB:** sorgu kalıplarına göre bileşik indeks (instrument_id, ts); sayfalama zorunlu; büyük zaman serisi için bölümleme/TimescaleDB değerlendirmesi hacim büyüyünce; N+1 yok.
4. **Grafik:** istemciye uzun dönemde aşağı örneklenmiş (downsampled) seri gönder; ayrıntı yakınlaştırınca yüklenir.
5. **Frontend:** kod bölme (route bazlı), görsel/font optimizasyonu, iskelet (skeleton) yükleme, gereksiz yeniden render yok. Bundle bütçesi: ilk yük JS ≤ ~200 KB sıkıştırılmış (ölç ve güncelle).
6. **Arka plan işleri:** kuyruk + idempotent işler + üst sınırlı retry; kullanıcı isteğini bloklamaz.
7. **Çıkarım:** model CPU'da küçük ve toplu çalışır; kullanıcı başına canlı tahmin yok.
8. **Sağlayıcı kotaları:** oran sınırlayıcı + yerel önbellek; aynı veriyi iki kez çekme.

## Ölçüm (yapılmadan "hızlı" denmez)
- Hedefler: `docs/ACCEPTANCE_CRITERIA.md`.
- Yük testi (k6/Locust) Faz 14'te; sağlayıcı kesintisi simülasyonu dahil.
- Her sürümde Lighthouse/Web Vitals ve API p95 karşılaştırması; gerileme CI'da uyarı verir.
- Profil çıkar, sonra optimize et; tahminle optimizasyon yapma.
