# Gizlilik ve Çerez Politikası

**Yürürlük Tarihi:** 28 Ekim 2026  
**Sürüm:** 1.0.0

İşbu Gizlilik ve Çerez Politikası, Borsa Analiz Sistemi platformunu ziyaret eden kullanıcıların gizliliğini korumak ve kullanılan çerezler hakkında şeffaf bilgilendirme sağlamak amacıyla hazırlanmıştır.

## 1. Sıfır Üçüncü Taraf Takipçi İlkesi
Platformumuzda Google Analytics, Meta Pixel, Hotjar veya herhangi bir ticari reklam/pazarlama takip mekanizması kesinlikle yer almamaktadır. Kullanıcıların internet gezintileri profillenmez ve davranışsal reklamcılık yürütülmez.

## 2. Kullanılan Çerezler (Teknik ve Zorunlu Çerezler)
Platform yalnızca hizmetin teknik olarak sunulabilmesi için zaruri olan güvenli oturum çerezlerini kullanır:

| Çerez Adı | Tür | Amaç | Saklama Süresi | Güvenlik Nitelikleri |
|---|---|---|---|---|
| `session_token` | Zorunlu (First-Party) | Kullanıcı oturumunu güvenli şekilde sürdürmek | Oturum süresince | `HttpOnly`, `Secure`, `SameSite=Lax` |
| `csrf_token` | Zorunlu (First-Party) | Siteler arası istek sahteciliğine (CSRF) karşı koruma | Oturum süresince | `Secure`, `SameSite=Strict` |
| `theme_preference` | Fonksiyonel | Kullanıcının koyu/açık tema tercihini hatırlamak | 1 Yıl | `Secure`, `SameSite=Lax` |

## 3. Loglama ve Maskeleme Standartları
Sistem hata ve erişim loglarında hiçbir kişisel veri (parola, API tokenı, açık e-posta) açık biçimde tutulmaz. E-posta adresleri log seviyesinde tek yönlü maskelenir (`k***@e***.com`).

## 4. Çerez Tercihlerinin Yönetimi
Kullanıcılar tarayıcı ayarları üzerinden çerezleri diledikleri zaman silebilir veya engelleyebilir. Ancak zorunlu oturum çerezlerinin engellenmesi durumunda kullanıcı girişi ve hesap paneli işlevleri çalışmayacaktır.
