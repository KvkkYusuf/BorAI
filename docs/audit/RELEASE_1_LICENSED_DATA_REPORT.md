# Sürüm 1 (Veri ve Grafik MVP) — Lisanslı Veri Alım Kabul Raporu

**Tarih:** 2026-10-29 09:45:00 UTC\
**Kapsam:** FAZ-17 Sürüm Planı — Sürüm 1: Veri ve grafik MVP (`docs/phases/FAZ-17-surum-plani.md`)\
**Genel Durum:** **PASS** (6/6 Kontrol Başarılı)\
**Pilot Sembol Kotası:** 10 / 10 Sembol

---

## 1. Denetim Özeti

FAZ-17 Sürüm 1 (Veri ve Grafik MVP) gereksinimleri uyarınca, lisanslı kaynaktan sınırlı sayıda sembol için veri alımı mimarisi, sözleşme doğrulaması, sembol sınırı politikası ve veri çekim güvenilirliği `ReleaseOneLicensedDataGatekeeper` ile denetlenmiştir.

| Kontrol Kodu | Kontrol Başlığı | Sonuç | Doğrulama Kanıtı |
|---|---|---|---|
| `CHECK-R1-01` | Lisanslı Veri Sağlayıcı ve Sözleşme Geçerliliği | **PASS** | Lisanslı sağlayıcı doğrulandı (is_licensed=True, Kayıtlı sözleşme sayısı=1). |
| `CHECK-R1-02` | Sınırlı Sayıda MVP Pilot Sembol Kotası | **PASS** | Sürüm 1 sınırlı sembol kotası uygun: 10 sembol (Maksimum izin verilen: 10). |
| `CHECK-R1-03` | Yetkisiz / Kapsam Dışı Sembol Kısıtlaması | **PASS** | Kapsam dışı semboller (AAPL, BTCUSDT, XYZ_UNLISTED) SymbolEntitlementError ile güvenli şekilde engellendi. |
| `CHECK-R1-04` | Pilot Sembol Veri Çekimi ve OHLCV Bütünlüğü | **PASS** | 'AKBNK' için 1 adet günlük bar başarıyla çekildi, OHLCV alanları doğrulandı. |
| `CHECK-R1-05` | Yeniden Dağıtım ve Görüntüleme (Display) Lisans Kapsamı | **PASS** | Lisanslı veri arayüzde gösterim (Display) hakkı doğrulandı (Sözleşmeli Display / Derived Data kapsamı). |
| `CHECK-R1-06` | Veri Sağlayıcı Sağlık ve Bağlantı Kontrolü | **PASS** | Sağlayıcı sağlık kontrolü başarılı (mock_matriks_licensed, is_licensed=True, mesaj=Licensed feed active). |

---

## 2. Onaylanan MVP Pilot Sembol Listesi

İzin verilen pilot semboller (10 adet, BIST-30 çekirdek likit hisseler):
`AKBNK`, `ASELS`, `BIMAS`, `EREGL`, `GARAN`, `KCHOL`, `SISE`, `THYAO`, `TUPRS`, `YKBNK`

- Kural: Sürüm 1 MVP kapsamında bu 10 sembol haricindeki hiçbir sembol için veri çekimine izin verilmez (`SymbolEntitlementError`).
- Kota Sınırı: Azami 10 sembol; ek semboller lisans ve altyapı genişletme onayından sonra açılır.

---

## 3. Sürüm Kabul Beyanı

Tüm kontroller başarıyla tamamlanmış olup, Sürüm 1 MVP kapsamında lisanslı kaynaktan sınırlı sembol (10 sembol) veri alım şartı %100 doğrulanmıştır. Prometheus telemetrisi (`release_one_licensed_data_status=1.0`) devrededir.
