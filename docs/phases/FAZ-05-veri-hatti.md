## 5. Piyasa verisi hattı — güvenilirliğin temeli

- [x] Lisanslı veri sağlayıcı seç ve bir adaptör arayüzü tasarla (`BaseMarketDataProvider`, `SyntheticMarketDataProvider`, `LicensedEODDataProvider`).
- [x] Her kayıt için sembol, borsa, zaman damgası, saat dilimi, OHLCV, para birimi, kaynak ve veri durumu alanlarını belirle (`MarketBarRecord`, `DataStatus`).
- [x] Eksik bar, yinelenen kayıt, sıfır/negatif fiyat, tutarsız yüksek-düşük, hacim anomalisi ve zaman sırası kontrolleri ekle (`validate_market_data_series`, `DataIntegrityReport`).
- [x] Tatil, seans, yaz saati, işlem durdurma ve farklı borsa takvimlerini ele al (`BISTCalendar`, `TradingHalt`, `validate_market_data_series`).
- [x] Bölünme, temettü ve diğer kurumsal işlemlerin fiyat serisine etkisini tanımla (`CorporateAction`, `CorporateActionType`, `apply_corporate_actions`).
- [x] Ham veriyi değişmez biçimde sakla; düzeltilmiş/temizlenmiş veriyi ayrı türet (`RawBarRecord`, `ImmutableRawStore`, `DerivedMarketSeries`, `MarketDataPipelineService`).
- [x] Sağlayıcı kesintisi, gecikme, kota aşımı ve API şema değişikliklerinde kontrollü hata ve yeniden deneme uygula (`RetryPolicy`, `ResilientMarketDataProvider`, `execute_with_retry`, `ProviderTimeoutError`, `ProviderSchemaError`).
- [x] Verinin “son güncelleme zamanı” ve gecikme durumunu kullanıcıya göster (`DataFreshnessInfo`, `DelayCategory`, `compute_data_freshness`, `format_timestamp_trt`).
- [x] Verinin kaynağını ve gecikme seviyesini hisse detayında görünür yap (`StockDetailAttribution`, `StockDetailView`, `build_stock_detail_attribution`, `build_stock_detail_view`).
- [x] Günlük veri bütünlüğü raporu ve alarmı oluştur (`DailyDataIntegrityReport`, `DailyIntegrityAuditor`, `IntegrityAlert`, `AlertSeverity`, `LogAlertHandler`, `CallbackAlertHandler`).
- [x] Sağlayıcı kullanım koşullarının izin vermediği veriyi kazıma veya yeniden yayınlama (`ScrapingGuard`, `RedistributionGuard`, `DataUsagePolicy`, `UnauthorizedScrapingError`, `RedistributionPolicyViolationError`).

**Çıktı:** Doğrulanmış, izlenebilir ve tekrar üretilebilir veri hattı.

### v2 eklemeleri
- [x] Veri alımını idempotent yap (aynı gün tekrar çalışınca çift kayıt yok) (`IngestionResult`, `append_batch_detailed`, `ingest_raw_bars`).
- [x] Sağlayıcı yanıtlarını yerel önbellekle; aynı veriyi iki kez çekme; kota sayacı tut (`CachedMarketDataProvider`, `MarketDataCache`, `ProviderQuotaTracker`).
- [x] Veri sürümü (`data_version`) üret; önbellek anahtarları buna bağlansın (`generate_data_version`, `MarketDataCache`, `CachedMarketDataProvider`).
- [x] Saklama süresi ve ham veri yedekleme politikasını yaz (`DataRetentionTier`, `RetentionPolicy`, `RawDataBackupPolicy`, `BackupManifest`, `docs/DATA_RETENTION_AND_BACKUP.md`).
