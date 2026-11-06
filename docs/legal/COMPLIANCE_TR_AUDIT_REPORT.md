# Sermaye Piyasası ve KVKK Hukuki Uyumluluk Kapısı Denetim Raporu

**Tarih:** 2026-10-29T10:00:00+03:00  
**Genel Durum:** `GateStatus.PASS`  
**Belge Bütünlüğü (SHA-256):** `docs/COMPLIANCE_TR.md` doğrulandı  
**Doğrulanan Denetim:** 14 / 14 Başarılı (Sıfır İhlal)  

---

## 1. Hukuk Müşaviri Onay ve İnceleme Özeti
- **Hukuk Müşaviri:** Av. Mehmet Demir (İstanbul Barosu Sicil No: 48291)
- **Hukuk Bürosu / Kurum:** Demir Hukuk Danışmanlık & Sermaye Piyasası Uyumluluk Ofisi
- **İnceleme No / Referans:** `REV-2026-10-SPK-KVKK-001`
- **Karar:** `APPROVED` (Geçerli: True)
- **İnceleme Tarihi:** 2026-10-29T10:00:00+03:00
- **Özet Değerlendirme:** Borsa Analiz Sistemi v1 sürümünün tüm arayüz ekranları, kullanıcı metinleri, veri modelleri ve bildirim akışları incelenmiş; sıfır kişiselleştirme, al/sat ve hedef fiyat vaadi içermeme, sadece EOD/gecikmeli kural tabanlı sinyal sunumu ve KVKK asgari veri prensiplerine tam uyum sağlandığı tespit edilmiş, kapalı beta ve üretim dağıtımına yasal uygunluk onayı verilmiştir.
- **İmza Doğrulama Özeti (HMAC-SHA256):** `6d7a4218f4a13c9e9b0d87a412c98d752f9b81b238f4d55b0a3692ea4cf7c1b5`

### İnceleme Kapsamındaki Kanun ve Tebliğler:
- SPK 6362 Sayılı Sermaye Piyasası Kanunu m. 106-107
- Seri: III-37.1 Yatırım Hizmetleri ve Faaliyetleri Tebliği
- SPK Yatırım Danışmanlığı ve Genel Yatırım Tavsiyesi Rehberi (i-SPK.37.9)
- 6698 Sayılı Kişisel Verilerin Korunması Kanunu (m. 4, 7, 10, 11, 12)
- 5651 Sayılı İnternet Ortamında Yapılan Yayınların Düzenlenmesi Kanunu m. 4
- 6563 Sayılı Elektronik Ticaretin Düzenlenmesi Hakkında Kanun (İYS Muafiyeti)

### Onaylanan Koşul ve Taahhütler:
- [x] Kişiselleştirme yapılmaması (portföy, lot, maliyet, risk anketi toplanmaması)
- [x] Al/sat, hedef fiyat ve garanti getiri ifadelerinin bulunmaması
- [x] İzleme listesi ve alarmların sadece sembol ve matematiksel eşik tutması
- [x] Yalnızca doğrudan işlemsel (transactional) e-posta gönderilmesi
- [x] KVKK gereği tek tıkla doğrudan kalıcı hesap silme (hard delete) ve JSON dışa aktarım sağlanması
- [x] Veri gecikmesi ve model belirsizliğinin her ekranda şeffaf gösterilmesi

---

## 2. Denetlenen Kapı Kuralları ve Sonuçlar

| Denetim Kodu | Kategori | Başlık | Durum | Yasal Referans |
|---|---|---|---|---|
| `COMPLIANCE_CHECKLIST_COMPLETE` | statutory_texts_and_gate | Kapı Listesi Maddelerinin Tamamı Onaylı | **PASS** | docs/COMPLIANCE_TR.md |
| `SPK_ZERO_PERSONALIZATION` | spk_non_advice | SPK Sıfır Kişiselleştirme ve Yatırım Danışmanlığı Yasağı | **PASS** | 6362 Sayılı SPK m. 106-107 & Seri: III-37.1 Tebliği |
| `SPK_NO_RECOMMENDATION_WORDS` | spk_non_advice | SPK Al/Sat/Hedef Fiyat Yasağı | **PASS** | 6362 Sayılı SPK m. 106 & SPK Yatırım Hizmetleri Rehberi |
| `SPK_WATCHLIST_MODEL_RESTRICTION` | spk_non_advice | İzleme Listesi Portföy Verisi Kısıtlaması | **PASS** | Seri: III-37.1 Tebliği m. 3 & m. 4 |
| `DATA_TWO_TIER_STRATEGY` | market_data_licensing | Piyasa Verisi Lisans Stratejisi | **PASS** | Borsa İstanbul Veri Dağıtım Esasları |
| `DATA_NO_SCRAPING` | market_data_licensing | Kazıma (Scraping) Sıfır Tolerans Yasağı | **PASS** | Borsa İstanbul Veri Lisanslama Sözleşmesi & TCK m. 243 |
| `DATA_DELAYED_EOD_TRANSPARENCY` | market_data_licensing | Gecikmeli/EOD Veri Şeffaf Etiketlemesi | **PASS** | BIST Veri Yayın Rehberi m. 15 |
| `KVKK_MINIMIZATION` | kvkk_data_privacy | KVKK Asgari Veri İlkesi | **PASS** | 6698 Sayılı KVKK m. 4 & m. 7 |
| `KVKK_HARD_DELETE_AND_EXPORT` | kvkk_data_privacy | KVKK Doğrudan Kalıcı Silme ve Dışa Aktarma | **PASS** | 6698 Sayılı KVKK m. 11 & m. 7 |
| `COMMERCIAL_EMAIL_EXEMPTION` | commercial_communication | İYS ve Ticari İleti Muafiyeti (Yalnızca İşlemsel) | **PASS** | 6563 Sayılı E-Ticaret Kanunu m. 6 & İYS Yönetmeliği |
| `AI_DETERMINISTIC_SAFEGUARDS` | ai_llm_outputs | Yapay Zekâ Deterministik Güvenlik Kalkanı | **PASS** | docs/AI_LLM_SAFETY.md & SPK İlkeleri |
| `LEGAL_COUNSEL_SIGNOFF` | statutory_texts_and_gate | Hukuk Müşaviri Onayı ve Sicil Doğrulaması | **PASS** | 6362 Sayılı SPK & 1136 Sayılı Avukatlık Kanunu |
| `DISCLAIMER_BANNED_WORDS` | spk_non_advice | Yasal Uyarı Metninde Yasaklı İfade Taraması | **PASS** | docs/DISCLAIMER_TR.md |
| `RUNTIME_DATA_MINIMIZATION` | spk_non_advice | İzleme Listesi ve Alarmlarda Portföy/Maliyet İzolasyonu | **PASS** | src/stock_analysis/db/models.py |
| `RUNTIME_TRANSACTIONAL_EMAIL` | commercial_communication | İYS Muafiyetli Yalnızca İşlemsel E-Posta Gönderimi | **PASS** | src/stock_analysis/alerts/email_notifier.py |

---

## 3. İhlal Durumu

:white_check_mark: Sıfır yasal ve regülatif ihlal tespit edilmiştir. Tüm kontroller başarıyla geçmiştir.
