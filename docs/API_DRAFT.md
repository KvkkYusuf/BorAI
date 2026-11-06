# Borsa Analiz Sistemi — API Sözleşme Taslağı (OpenAPI 3.0 Özeti)

Bu belge, Borsa Analiz Sistemi'nin RESTful API sözleşmelerini, uç noktalarını (endpoints), istek/yanıt modellerini ve güvenlik kurallarını tanımlar.

> **Kırmızı Çizgi Hatırlatması:**  
> - Bütün piyasa yanıtlarında `disclaimer` (Yatırım tavsiyesi değildir) metadata veya HTTP başlığı yer alır.  
> - İzleme listesi uç noktalarında maliyet, lot, kâr/zarar gibi kişisel portföy alanları **kesinlikle bulunmaz**.  
> - Zaman damgaları `UTC (ISO 8601)` standardındadır.

---

## 1. Genel Bilgiler & Güvenlik

- **Temel Yol:** `/api/v1`
- **İçerik Türü:** `application/json`
- **Kimlik Doğrulama:** Bearer JWT Token (`Authorization: Bearer <token>`)
- **Genel Hata Modeli:**
```json
{
  "error": {
    "code": "SYMBOL_NOT_FOUND",
    "message": "Belirtilen sembol BIST evreninde bulunamadı.",
    "timestamp": "2026-10-18T15:30:00Z"
  }
}
```
- **Zorunlu Yanıt Başlığı (Response Header):**  
  `X-Disclaimer: Bu sistem bilgilendirme amaclidir, yatirim tavsiyesi degildir.`

---

## 2. Uç Noktalar (Endpoints)

### 2.1. Sağlık & Sistem Bilgisi (Anonim)
* `GET /health`  
  * **Açıklama:** Servis, veri tabanı ve önbellek sağlık durumu.
  * **Yanıt (200 OK):**
    ```json
    {
      "status": "healthy",
      "version": "1.0.0",
      "timestamp": "2026-10-18T15:30:00Z",
      "flags": {
        "forecast_enabled": false,
        "llm_enabled": false
      }
    }
    ```

---

### 2.2. Piyasa Verisi (Anonim / Salt Okunur)
* `GET /market/symbols`  
  * **Açıklama:** Desteklenen BIST hisseleri listesi (BIST 30 / BIST 100).
  * **Yanıt (200 OK):**
    ```json
    {
      "universe": "BIST_100",
      "count": 100,
      "updated_at": "2026-10-18T18:40:00Z",
      "symbols": [
        {"symbol": "THYAO", "title": "Türk Hava Yolları A.O.", "indices": ["BIST30", "BIST100"]},
        {"symbol": "ASELS", "title": "Aselsan Elektronik San. ve Tic. A.Ş.", "indices": ["BIST30", "BIST100"]}
      ]
    }
    ```

* `GET /market/history/{symbol}`  
  * **Sorgu Parametreleri:**
    * `interval`: `1d` (Varsayılan gün sonu EOD)
    * `range`: `1m`, `3m`, `6m`, `1y`, `5y`, `all` (Varsayılan `1y`)
    * `price_type`: `adjusted` (Varsayılan: temettü/sermaye düzeltilmiş) veya `raw` (ham)
  * **Yanıt (200 OK):**
    ```json
    {
      "symbol": "THYAO",
      "currency": "TRY",
      "timezone": "UTC",
      "price_type": "adjusted",
      "data_source": "official_eod_feed",
      "disclaimer": "Bilgilendirme amaclidir, yatirim tavsiyesi degildir.",
      "candles": [
        {
          "date": "2026-10-16T15:00:00Z",
          "open": 305.50,
          "high": 312.00,
          "low": 304.00,
          "close": 310.25,
          "volume": 14502300,
          "turnover_try": 4485230000
        }
      ]
    }
    ```

---

### 2.3. Analitik & İndikatörler (Anonim / Salt Okunur)
* `GET /analytics/indicators/{symbol}`  
  * **Sorgu Parametreleri:** `indicators=rsi,sma20,sma50,macd`
  * **Yanıt (200 OK):**
    ```json
    {
      "symbol": "THYAO",
      "calculated_at": "2026-10-18T18:45:00Z",
      "indicators": {
        "rsi_14": 58.4,
        "sma_20": 302.10,
        "sma_50": 295.40,
        "macd": {
          "line": 2.45,
          "signal": 1.90,
          "histogram": 0.55
        }
      },
      "neutral_summary": "Fiyat 20 ve 50 gunluk hareketli ortalamalarin uzerindedir. RSI 58.4 seviyesindedir.",
      "disclaimer": "Teknik gostergeler deterministik hesaplamadir, al-sat onerisi degildir."
    }
    ```

* `GET /analytics/screener`  
  * **Sorgu Parametreleri:**  
    * `rsi_max=35` (Aşırı satım bölgesine yakın olanlar)
    * `sma_cross=above_sma50`
  * **Yanıt (200 OK):**
    ```json
    {
      "filter_applied": {"rsi_max": 35},
      "matches": [
        {"symbol": "EKGYO", "close": 9.42, "rsi_14": 31.2}
      ],
      "disclaimer": "Filtreleme sonuclari teknik kriter taramasidir; yatirim tavsiyesi degildir."
    }
    ```

---

### 2.4. Model Çıkarımı / Tahmin (Özellik Bayrağı Kontrollü)
* `GET /inference/forecast/{symbol}`  
  * **Ön Koşul:** `FORECAST_ENABLED=true`
  * **Kapalı Durum Yanıtı (503 Service Unavailable):**
    ```json
    {
      "error": {
        "code": "FORECAST_DISABLED",
        "message": "Model tahmin servisi su anda aktif degildir.",
        "timestamp": "2026-10-18T15:30:00Z"
      }
    }
    ```
  * **Açık Durum Yanıtı (200 OK):**
    ```json
    {
      "symbol": "THYAO",
      "model_version": "bist-trend-v1.0.2",
      "evaluation_date": "2026-10-18T18:50:00Z",
      "horizon_days": 5,
      "predicted_direction": "upward",
      "confidence_score": 0.62,
      "uncertainty_interval": [305.00, 318.00],
      "baseline_comparison": "Model test seti dogruluk skoru: %58 (Rastgele baseline: %50)",
      "disclaimer": "Model cikti ve tahminleri kesinlik icermez; istatistiki bir yaklasimdir, yatirim tavsiyesi degildir."
    }
    ```

---

### 2.5. Kimlik Doğrulama & Oturum (Parolasız Sihirli Bağlantı)
* `POST /auth/magic-link`  
  * **Gövde:** `{"email": "kullanici@example.com"}`
  * **Yanıt (200 OK):** `{"message": "Giris baglantisi e-posta adresinize gonderildi."}`

* `POST /auth/verify`  
  * **Gövde:** `{"token": "magic_token_string"}`
  * **Yanıt (200 OK):**  
    ```json
    {
      "access_token": "eyJhbGciOi...",
      "token_type": "bearer",
      "expires_in": 86400
    }
    ```

---

### 2.6. Kullanıcı Tercihleri & İzleme Listesi (Kimlik Doğrulamalı)
* `GET /user/watchlist`  
  * **Yetki:** Bearer Token
  * **Yanıt (200 OK):**
    ```json
    {
      "watchlist": ["THYAO", "ASELS", "KCHOL"]
    }
    ```
  *(Not: Kesinlikle hisse maliyeti, alış fiyatı veya lot tutulmaz).*

* `POST /user/watchlist/{symbol}`  
  * **Yetki:** Bearer Token  
  * **Yanıt (201 Created):** `{"message": "Sembol izleme listesine eklendi."}`

* `DELETE /user/watchlist/{symbol}`  
  * **Yetki:** Bearer Token  
  * **Yanıt (200 OK):** `{"message": "Sembol listeden cikarildi."}`

---

### 2.7. Uyarı Yönetimi (Kimlik Doğrulamalı)
* `GET /user/alerts`  
  * **Yetki:** Bearer Token  
  * **Yanıt (200 OK):** En fazla 5 aktif EOD uyarı kuralı listelenir.

* `POST /user/alerts`  
  * **Yetki:** Bearer Token  
  * **Gövde:**
    ```json
    {
      "symbol": "THYAO",
      "condition_type": "PRICE_ABOVE",
      "target_value": 320.00,
      "channel": "EMAIL"
    }
    ```
  * **Yanıt (201 Created):** `{"alert_id": "alt_12345", "status": "active"}`

---

### 2.8. KVKK Hakları & Veri Yönetimi (Kimlik Doğrulamalı)
* `GET /user/export`  
  * **Yetki:** Bearer Token  
  * **Açıklama:** KVKK kapsamında kullanıcıya ait tüm verilerin (hesap, izleme listesi, uyarılar) JSON çıktısı.

* `DELETE /user/me`  
  * **Yetki:** Bearer Token  
  * **Açıklama:** Kullanıcı hesabını, izleme listesini ve uyarılarını derhal ve kalıcı olarak (hard delete) siler.
  * **Yanıt (200 OK):** `{"message": "Hesabiniz ve tum verileriniz kalici olarak silindi."}`
