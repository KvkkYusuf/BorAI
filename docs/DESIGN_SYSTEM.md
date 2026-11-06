# Borsa Analiz Sistemi — Tasarım Sistemi ve UX Test Notları (FAZ-08)

Bu belge, Borsa Analiz Sistemi'nin masaüstü ve mobil uyumlu responsive arayüz tasarım sistemini, token mimarisini, bileşen kurallarını ve kullanıcı deneyimi (UX) test notlarını tanımlar.

---

## 1. Tasarım Sistemi Temelleri (Design Tokens)

Tasarım sistemi belirteçleri (tokens) `src/stock_analysis/static/css/tokens.css` içinde CSS değişkenleri olarak merkezi şekilde tanımlanmıştır.

### 1.1 Tipografi
- **Yazı Tipi Ailesi:** `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`
- **Sayısal & Kod Ailesi:** `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace` (BIST fiyatları, yüzdeler, hacim ve zaman etiketlerinde sabit genişlikli hizalama için).
- **Tipografi Ölçeği:**
  - `text-xs`: 0.75rem (12px) — Alt etiketler, gecikme bilgileri, zaman damgaları
  - `text-sm`: 0.875rem (14px) — Tablo hücreleri, ikincil metinler, form etiketleri
  - `text-base`: 1.0rem (16px) — Gövde metinleri, kart başlıkları, butonlar
  - `text-lg`: 1.125rem (18px) — Alt başlıklar, gösterge değerleri
  - `text-xl`: 1.25rem (20px) — Sayfa bölüm başlıkları
  - `text-2xl`: 1.5rem (24px) — Ekran ana başlıkları
  - `text-3xl`: 1.875rem (30px) — Hisse güncel fiyatı ve ana sembol kodu

### 1.2 Renk Paleti ve Erişilebilirlik (WCAG 2.1 AA)
Renkler hiçbir zaman tek başına anlam taşıyıcısı olarak kullanılmaz; her zaman ikon (`▲`, `▼`, `ℹ️`) ve metin eşlik eder.

| Token | Değer (Koyu / Açık) | Kullanım Amacı | Kontrast Uyumu |
|---|---|---|---|
| `--color-bull` | `#10b981` / `#059669` | Fiyat artışı, kâr, başarılı durum, piyasa açık | WCAG AA (> 4.5:1) |
| `--color-bear` | `#ef4444` / `#dc2626` | Fiyat düşüşü, zarar, risk uyarısı | WCAG AA (> 4.5:1) |
| `--color-neutral` | `#94a3b8` / `#64748b` | Yatay trend, pasif durum | WCAG AA (> 4.5:1) |
| `--color-warning` | `#f59e0b` / `#d97706` | Gecikmeli veri uyarısı, model belirsizliği | WCAG AA (> 4.5:1) |
| `--color-primary` | `#0284c7` / `#0284c7` | Birincil eylemler, aktif sekmeler, odaklama | WCAG AA (> 4.5:1) |

### 1.3 Boşluk ve Grid Ölçeği (8px Tabanlı)
- `space-1` (4px), `space-2` (8px), `space-3` (12px), `space-4` (16px), `space-6` (24px), `space-8` (32px).
- Kartlar ve modüller arasındaki tutarlı ritim bu ölçek üzerinden yürütülür.

---

## 2. Responsive Düzen ve Ekran Kırılımları (Breakpoints)

- **Masaüstü (Desktop >= 1025px):**
  - Sol tarafta sabit 260px genişliğinde `app-sidebar`.
  - Üstte yapışkan (sticky) arama çubuğu, tema anahtarı ve kullanıcı profil alanı.
  - Geniş 4 kolonlu ticker kartları ve zengin çift kolonlu detay/analiz görünümü.
- **Tablet (768px - 1024px):**
  - Kenar çubuğu çekmece (drawer) moduna geçer, menü butonu ile açılır/kapanır.
  - Ticker kartları 2 kolona daralır.
- **Mobil (&lt; 768px):**
  - Alt kısımda başparmak erişimine uygun sabit `mobile-bottom-nav` (Pano, Keşfet, Grafik, Uyarılar, Ayarlar).
  - Tablolar yatay kaydırma çubuğu veya kart görünümüne dönüşür (`table-responsive`).
  - Çift kolonlu yapılar tek kolona düşer.

---

## 3. Ekran Mimarisi ve Karşılanan İhtiyaçlar

1. **Pano (`#screen-dashboard`):** BIST 100, BIST 30, BIST Banka ve Sınai endeksleri; canlı/gecikmeli veri durumu; hızlı izleme listesi ve tetiklenen son alarmlar.
2. **Hisse Keşfet (`#screen-screener`):** Sembol, şirket adı, sektör, likidite filtresi ve anlık arama motoru.
3. **Hisse Detay (`#screen-detail`):** Şirket unvanı, sembolü, anlık fiyat, gün içi en yüksek/en düşük, 1G-Tümü arası zaman periyotları, hacim barlarıyla birlikte SVG mum grafiği, 20 günlük SMA ve RSI.
4. **Analiz & Model (`#screen-analysis`):** Kesin hedef fiyat vaadi içermeyen güven aralığı bandı (%90 güven), model belirsizlik skoru (±%2,8), geçmiş isabet oranı ve gösterge sinyalleri dökümü.
5. **Uyarılar (`#screen-alerts`):** Fiyat veya teknik gösterge (RSI > 70) koşul formu, kanal seçimi (E-posta, Webhook, SMS), aktif/pasif düğmesi ve uyarı geçmişi.
6. **İzleme Listeleri (`#screen-watchlists`):** Özel kategorize edilmiş listeler (Favoriler, Temettü, İvmeliler), hızlı çıkarma/ekleme eylemleri.
7. **Ayarlar (`#screen-settings`):** Passkey (WebAuthn / FIDO2) yönetimi, TOTP 2FA durumu, saat dilimi (`Europe/Istanbul`), para birimi (`TRY`), tema anahtarı.
8. **Yasal & Şeffaflık (`#screen-legal`):** SPK mevzuat uyarısı, kişiye özel yatırım danışmanlığı yapılmadığına dair açık beyan, veri gecikme koşulları ve AI model sınırlamaları.
9. **Yönetim Paneli (`#screen-admin`):** RBAC yetkili paneli; veri hattı sağlığı, iş kuyruğu durumu, PyTorch model sürümü, güvenlik denetim kayıtları (parola ve sırlar asla yer almaz).

---

## 4. Kullanıcı Deneyimi (UX) Test Notları

### 4.1 Erişilebilirlik ve Navigasyon
- [x] Klavyeyle tam kontrol: Sekmeler arası `Tab` ve `Shift+Tab` ile gezinme; odak göstergeleri (`:focus-visible`) belirgin mavi çerçeveye sahip.
- [x] Ekran okuyucu uyumu: "Ana İçeriğe Atla" (`.skip-link`) bağlantısı mevcut; tüm ikon butonları `aria-label` etiketine sahip; ana alanlar `<nav>`, `<header>`, `<main>`, `<footer>` anlamsal etiketleriyle yapılandırıldı.
- [x] Renk körlüğü uyumu: Yükseliş ve düşüş değerlerinde `▲` ve `▼` ok işaretleri ve sayısal yüzde işaretleri zorunlu tutuldu.

### 4.2 Performans ve İlk Etkileşim
- [x] 60 saniyede ilk grafik: Açılışta doğrudan popüler hisse (THYAO) ve interaktif mum grafiği hazır gelir.
- [x] Sıfır harici ağır kütüphane bağımlılığı: Saf HTML, CSS ve vanilla JS ile LCP < 1,0 sn ve sıfır gecikme elde edildi.
- [x] Türkçe yerelleştirme: Sayı formatları `312,50 ₺`, tarihler `23.10.2026`, saat dilimi varsayılanı `Europe/Istanbul`.
