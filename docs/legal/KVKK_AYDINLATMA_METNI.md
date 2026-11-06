# 6698 Sayılı KVKK Kapsamında Aydınlatma Metni

**Yürürlük Tarihi:** 28 Ekim 2026  
**Sürüm:** 1.0.0

İşbu Aydınlatma Metni, 6698 sayılı Kişisel Verilerin Korunması Kanunu ("KVKK") madde 10 uyarınca, veri sorumlusu sıfatıyla Borsa Analiz Sistemi ("Veri Sorumlusu") tarafından kişisel verilerinizin işlenmesine ilişkin usul ve esaslar hakkında sizleri bilgilendirmek amacıyla hazırlanmıştır.

## 1. Veri Sorumlusunun Kimliği
Borsa Analiz Sistemi Altyapı Yönetimi, veri sorumlusu olarak kişisel verilerinizi Anayasa ve KVKK hükümlerine tam uyumlu şekilde saklamayı ve işlemeyi taahhüt eder.

## 2. İşlenen Kişisel Veriler ve Asgari Veri İlkesi (Data Minimization)
Platformumuz "asgari veri" (veri minimizasyonu) ilkesini benimsemiştir. Yalnızca hizmetin ifası için zorunlu olan teknik veriler toplanır:
- **Kimlik ve İletişim Verisi:** Kullanıcı tarafından sağlanan e-posta adresi.
- **Güvenlik Verisi:** Güçlü tuzlama (salt) ve Argon2id/Bcrypt algoritmalarıyla tek yönlü hash'lenmiş parola.
- **Tercih ve İşlem Verisi:** Kullanıcının takip ettiği hisse senedi sembolleri (izleme listesi) ve matematiksel teknik alarm eşikleri.

*Kesinlikle Toplanmayan Veriler:* TC Kimlik Numarası, ad, soyad, telefon numarası, finansal durum, gelir bilgisi, banka/kredi kartı bilgileri, portföy tutarı, lot adedi, kâr/zarar rakamları hiçbir surette toplanmaz ve talep edilmez.

## 3. Kişisel Verilerin İşlenme Amaçları ve Hukuki Sebepleri
Kişisel verileriniz KVKK m. 5/2-c (sözleşmenin kurulması veya ifası) ve m. 5/2-f (veri sorumlusunun meşru menfaati) hukuki sebeplerine dayalı olarak:
- Kullanıcı hesabı oluşturulması ve oturum açma güvenliğinin sağlanması,
- Kullanıcının bizzat tanımladığı matematiksel fiyat uyarılarının e-posta ile bildirilmesi,
- Sistem performansının ve güvenliğinin korunması amaçlarıyla işlenmektedir.

Pazarlama, ticari tanıtım veya reklam amaçlı veri işleme yapılmaz.

## 4. Kişisel Verilerin Aktarılması ve Yurt Dışı Sınırı
Kişisel verileriniz hiçbir reklam veren, veri simsarı veya ticari üçüncü tarafla paylaşılmaz. Kullanıcı verileri hiçbir harici yapay zekâ (LLM) sağlayıcısına veya yurt dışı izleme servisine gönderilmez; tüm analizler yerel/kapalı sunucu mimarisinde icra edilir.

## 5. Saklama Süresi ve Kalıcı Silme (Unutulma Hakkı)
Kullanıcı hesabı aktif olduğu müddetçe veriler saklanır. Kullanıcı profil panelinden "Hesabımı Sil" talebinde bulunduğu anda e-posta, parola, izleme listesi ve alarm verileri veritabanından kalıcı olarak (hard delete) anında imha edilir.

## 6. İlgili Kişinin Hakları (KVKK Madde 11)
KVKK m. 11 uyarınca her kullanıcı:
- Kişisel verisinin işlenip işlenmediğini öğrenme,
- İşlenmişse buna ilişkin bilgi talep etme ve verilerini JSON formatında dışa aktarma (export),
- Verilerin silinmesini veya yok edilmesini talep etme (tek tıkla silme),
- Kanuna aykırı işleme sebebiyle zarara uğraması halinde zararın giderilmesini talep etme haklarına sahiptir.
