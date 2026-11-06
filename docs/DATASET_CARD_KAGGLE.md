# Borsa İstanbul (BIST) Kaggle Veri Kartı ve Doğrulama Raporu
## (Dataset Card & Provenance Audit)

> **Belge Sürümü:** 1.0.0  
> **İlgili Faz:** FAZ-10 (Kaggle veri setleri ve veri bilimi disiplini)  
> **Tarih:** 2026-10-24  
> **Durum:** Onaylandı / Aktif  

---

## 1. Veri Seti Künyesi (Metadata)

| Alan | Bilgi / Değer |
|---|---|
| **Veri Seti Kimliği (ID)** | `bist-daily-stock-historical-data` |
| **Başlık** | Borsa Istanbul (BIST) Daily Equity OHLCV & Corporate Actions (2010–2025) |
| **Sürüm** | `2025.1` (Yıllık dondurulmuş araştırma sürümü) |
| **Yayıncı / Yükleyici** | Açık Finans Veri İnisiyatifi (Kaggle Community Dataset) |
| **Kaynak URL** | `https://www.kaggle.com/datasets/borai/bist-daily-stock-historical-data` |
| **Hedef Pazar** | Borsa İstanbul Pay Piyasası (BIST 30, BIST 100 ve Tüm Hisseler) |
| **Temel Para Birimi** | `TRY` (Türk Lirası) |
| **Zaman Dilimi** | `Europe/Istanbul` (Kayıtlar UTC standardında saklanır) |
| **Gözlem Aralığı** | `2010-01-04` — `2025-01-03` |
| **Sembol Sayısı** | 542 sembol (aktif ve delist olmuş hisseler dahil) |
| **Toplam Kayıt Sayısı** | ~1.450.000 günlük bar |

---

## 2. Lisans, Kaynak Zinciri (Provenance) ve Kullanım Kapsamı

### 2.1 Lisans Türü ve Uygunluk Kararı
- **Tanımlı Lisans:** `CC-BY-4.0` (Creative Commons Attribution 4.0 International).
- **Lisans İncelemesi:**
  - Veri setinin ticari ve araştırma amaçlı kullanımına atıf (attribution) şartıyla izin verilmektedir.
  - Lisansı doğrulanmış ve kamuya açık kaynaklardan derlenmiş sürümler için `PRODUCTION_ALLOWED` (üretim destekli analiz) kapsamı geçerlidir.
- **v2 Kuralı (Lisansı Belirsiz Veriler):**
  - Kaggle üzerinde lisansı belirtilmemiş, lisansı "Unknown" veya şüpheli/kazınmış (scraped) olan veri setleri kesinlikle üretim veya canlı API hattına alınamaz.
  - Bu tür veri setleri sistemde otomatik olarak `LOCAL_EXPERIMENT_ONLY` (Yalnızca Yerel Deney) olarak etiketlenir ve üretim derlemelerinden dışlanır.

### 2.2 Kaynak Zinciri (Provenance Trail)
1. **Birincil Kaynak:** Kamuyu Aydınlatma Platformu (KAP) günlük bültenleri ve Borsa İstanbul resmi kapanış verileri.
2. **Derleme Katmanı:** Kamusal bülten verilerinin OHLCV ve bölünme faktörleriyle temizlenip CSV/Parquet formatına dönüştürülmesi.
3. **Kaggle Arşivi:** Topluluk veri havuzunda SHA-256 sağlama toplamı (checksum) ile dondurulmuş veri deposu.
4. **Sistem İçeri Alımı (Ingestion Pipeline):** `src/stock_analysis/pipeline/kaggle_verifier.py` denetiminden geçen ve UTC formatına dönüştürülen izole araştırma deposu.

---

## 3. Kurumsal İşlemler ve Fiyat Düzeltmeleri

- **Kurumsal İşlem Türleri:** Bedelli/bedelsiz sermaye artırımları, temettü dağıtımları, pay birleştirme/bölünme işlemleri.
- **Uygulanan Yöntem:** `DUAL_STREAM` (İkili Akış)
  - **Ham Fiyatlar (`raw_unadjusted`):** Gerçek işlem görmüş piyasa fiyatları (arşiv ve denetim amaçlı).
  - **Düzeltilmiş Fiyatlar (`split_adjusted`):** İndikatör hesaplamaları (RSI, MACD, MA) ve ML model eğitimi için sermaye bölünmeleri geriye dönük düzeltilmiştir.
- **Temettü Muamelesi:** Fiyat serisini bozmamak için temettü getirisi getiri endeksinde (Total Return) izlenir, ham kapanış fiyatı doğrudan iskonto edilmez.

---

## 4. Yanlılık (Bias) ve Veri Bütünlüğü Değerlendirmesi

### 4.1 Hayatta Kalma Yanlılığı (Survivorship Bias)
- **Problem:** Yalnızca bugün işlem görmeye devam eden hisseler (ör. BIST 100 güncel bileşenleri) eğitilirse, iflas eden, tahtası kapanan veya kottan çıkarılan (delist) şirketler yok sayılarak yapay şekilde yüksek getiriler ortaya çıkar.
- **Çözüm & Kapsam:**
  - Veri setine gözlem penceresinde işlem görmüş ancak daha sonra kottan çıkmış **48 adet delist sembol** (tarihsel barlarıyla birlikte) dahil edilmiştir.
  - Hayatta kalma yanlılığı risk seviyesi: **DÜŞÜK (LOW)** olarak tescil edilmiştir.

### 4.2 Gelecek Bilgisi Sızıntısı (Look-Ahead Bias)
- **Problem:** Zaman serisinde rastgele (random shuffle) `train_test_split` yapılması, modelin gelecekteki fiyat ve volatilite bilgisini öğrenmesine neden olur.
- **Çözüm & Bölümleme Kuralı:**
  - Rastgele satır bölmesi **kesinlikle yasaktır**.
  - **Kronolojik Bölümleme (Walk-Forward / Expanding Window):**
    - **Eğitim (Train):** `2010-01-04` — `2021-12-31` (12 yıl)
    - **Doğrulama (Val):** `2022-01-03` — `2023-06-30` (1.5 yıl)
    - **Test (Out-of-Sample Test):** `2023-07-03` — `2025-01-03` (1.5 yıl)
  - Herhangi bir örtüşme (overlap) `KaggleDatasetVerifier` tarafından engellenir (`look_ahead_risk_level = NONE`).

---

## 5. Canlı Veri ve Kaggle Verisi Arasındaki Şema Uyumlaştırma

| Alan | Kaggle Ham Format | Canlı Sağlayıcı Formatı | Sistem İçi Standart (`MarketBarRecord`) |
|---|---|---|---|
| Tarih/Zaman | `YYYY-MM-DD` (Date string) | ISO 8601 UTC timestamp | UTC `datetime` (`Europe/Istanbul` duyarlı) |
| Sembol | `THYAO.IS` veya `THYAO` | `THYAO` | `THYAO` (BIST borsa eki normalize edilmiş) |
| Hacim (Volume) | Adet veya Lot karışık | Nominal Pay Adedi | Pay Adedi (`volume`) + Hacim TL (`turnover_try`) |
| Para Birimi | Belirtilmemiş / TRY | `TRY` | `TRY` |

---

## 6. Güvenlik, Depolama ve Git Kuralları

1. **Git Koruması:** Kaggle'dan indirilen hiçbir ham veri dosyası (`.csv`, `.parquet`, `.feather`, `.zip`) **asla Git reposuna eklenmez** (repoyu şişirmeme ve lisans dağıtım ihlalini önleme kuralı).
2. **Veri Sürümleme:** Yerel deneyler için veri dizini `.gitignore` altında tutulur ve DVC / nesne depolama manifestleri ile sürümlenir.
3. **Mali Sorumluluk:** Bu veri seti yalnızca teknik analiz ve makine öğrenimi modellerinin doğrulanması içindir; yatırım tavsiyesi veya kişiye özel portföy yönetimi amacıyla kullanılamaz (`docs/COMPLIANCE_TR.md`).
