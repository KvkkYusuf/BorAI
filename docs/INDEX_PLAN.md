# Borsa Analiz Sistemi — Veritabanı İndeks Planı ve Sorgu Kalıpları

Bu belge, **Borsa Analiz Sistemi** için birincil sorgu kalıplarını, indeks tasarım gerekçelerini ve veritabanı performans hedeflerini tanımlar.

## 1. Sorgu Kalıpları ve İndeks Haritası

| Tablo | Birincil Sorgu Kalıbı | İndeks Adı | İndekslenen Sütunlar | Gerekçe / Kullanım Senaryosu |
|---|---|---|---|---|
| `instruments` | Sembol ile hisse arama/filtreleme | `ix_instruments_symbol` | `(symbol)` | EOD bar alımı ve API arama ucu (`GET /market/instruments/{symbol}`) |
| `instruments` | ISIN arama | `ix_instruments_isin` | `(isin)` | Kurumsal veri sağlayıcı eşleştirme |
| `instruments` | Pazar bazlı filtreleme | `ix_instruments_market` | `(market)` | BIST 30 / BIST 100 filtrelemeleri |
| `market_bars` | Zaman serisi fiyat sorgusu | `ix_market_bars_inst_ts` | `(instrument_id, timestamp)` | Tarih aralığında bar listeleme (`GET /market/bars`) |
| `market_bars` | Düzeltilmiş bar serisi sorgusu | `ix_market_bars_inst_adj_ts` | `(instrument_id, is_adjusted, timestamp)` | Grafik ve teknik göstergeler için optimize edilmiş kompozit erişim |
| `corporate_actions` | Tarihsel temettü/bölünme sorgusu | `ix_corp_actions_inst_date` | `(instrument_id, ex_date)` | Geriye dönük fiyat düzeltme hesaplamaları |
| `watchlists` | Kullanıcı izleme listeleri | `ix_watchlists_user_id` | `(user_id)` | Kullanıcı panosu ve liste detayları |
| `watchlist_items` | İzleme listesi hisse kontrolü | `ix_watchlist_items_inst` | `(instrument_id)` | Hisse silinirken veya izlenme sayısı toplanırken hızlı kontrol |
| `alert_rules` | Kullanıcı alarmları | `ix_alert_rules_user_inst` | `(user_id, instrument_id)` | Kullanıcının alarm kurallarını listeleme |
| `alert_rules` | Aktif alarm tetikleme taraması | `ix_alert_rules_inst_active` | `(instrument_id, is_active)` | EOD bar akışında ilgili hissenin yalnızca aktif alarmlarını çekme |
| `alert_events` | Tetiklenen olay geçmişi | `ix_alert_events_rule_triggered` | `(alert_rule_id, triggered_at)` | Alarm geçmişi ve bildirim sırası |
| `forecasts` | Hisse tahmin sorgusu | `ix_forecasts_inst_target` | `(instrument_id, target_date)` | API tahmin çıktısı (`GET /inference/forecast/{symbol}`) |
| `forecasts` | Model sürümü performans sorgusu | `ix_forecasts_model_target` | `(model_version_id, target_date)` | Model sürümü değerlendirme ve geriye dönük test |
| `sessions` | Oturum doğrulama | `ix_sessions_session_token_hash` | `(session_token_hash)` | Her kimlikli istekte oturum doğrulama |
| `sessions` | Zaman aşımı temizliği | `ix_sessions_expires_at` | `(expires_at)` | Süresi dolmuş oturumları süpürme (background cleaner) |
| `audit_events` | Kullanıcı eylem denetimi | `ix_audit_events_user_action` | `(user_id, action)` | Güvenlik ve uyum denetim raporları |
| `audit_events` | Kronolojik denetim kaydı | `ix_audit_events_created` | `(created_at)` | SIEM ve denetim log akışı |

---

## 2. İndeks Tasarım İlkeleri

1. **Öncül Sütun Seçimi (Leading Columns):** En yüksek seçiciliğe (high cardinality) sahip filtre kriteri (`instrument_id` veya `user_id`) bileşik indeksin başına yerleştirilmiştir.
2. **Sıralama ve Aralık Desteği (Range Scans):** Zaman serilerinde (`timestamp`, `target_date`, `created_at`) aralık sorguları yapıldığından bu sütunlar bileşik indeksin sonuna yerleştirilir.
3. **Gereksiz İndeks Engeli (Over-Indexing):** `INSERT` / `BULK INSERT` maliyetini ve disk kullanımını şişirmemek adına yalnızca sık kullanılan filtre ve join yolları indekslenmiştir.

---

## 3. Yavaş Sorgu Günlüğü (Slow Query Log) Mimarisi

- **Tetikleyici:** SQLAlchemy 2.0 `before_cursor_execute` ve `after_cursor_execute` olay dinleyicileri.
- **Eşik (Threshold):** `Settings.slow_query_threshold_ms` (varsayılan: 200 ms).
- **Log Seviyesi:** Eşiği aşan sorgular `WARNING` seviyesinde ve çalışma süresi milisaniye cinsinden loglanır.
- **Güvenlik (Zero Secret Leak):** Parametreler SQL metnine doğrudan birleştirilmez, maskeleme veya parametreli şablon korunarak loglanır.
