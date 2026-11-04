import os
import base64
import json
import io
import pandas as pd
from PIL import Image
from groq import Groq

client = Groq()

# Görseli optimize edip (küçültüp) Base64 formatına çeviren fonksiyon
def encode_and_resize_image(image_path, max_size=(1024, 1024)):
    with Image.open(image_path) as img:
        # Eğer görsel çok büyükse oranını koruyarak küçültüyoruz
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Resmi bellekte JPEG formatında sıkıştırıyoruz
        buffer = io.BytesIO()
        img.convert('RGB').save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')

# Klasördeki tüm jpeg fişleri otomatik buluyoruz
fis_listesi = [f for f in os.listdir(".") if f.startswith("deneme_fis") and f.lower().endswith((".jpeg", ".jpg"))]
tum_veriler = []

print(f"Toplam {len(fis_listesi)} adet fiş bulundu. Optimizasyonlu işlem başlıyor...\n")

for fis in fis_listesi:
    print(f"-> {fis} optimize ediliyor ve analiz ediliyor...")
    try:
        # Görseli küçülterek Base64 alıyoruz (Bağlantı hatasını önlemek için)
        base64_image = encode_and_resize_image(fis)
        
        completion = client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": (
                                "Bu bir alışveriş fişidir. Bilgileri şu JSON formatında çıkar: "
                                '{"firma_adi": "string", "tarih": "string", "fis_no": "string", "toplam_tutar": "string"}. '
                                "Sadece JSON objesini dön, başka hiçbir açıklama yazma."
                            )
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                        }
                    ]
                }
            ],
            temperature=0.1,
            timeout=60.0 # İstek için 60 saniye zaman tanıyoruz
        )
        
        # Güvenli yanıt okuma
        if hasattr(completion.choices, 'message'):
            yanit_metni = completion.choices.message.content
        elif isinstance(completion.choices, list) and len(completion.choices) > 0:
            choice = completion.choices[0]
            yanit_metni = choice.message.content if hasattr(choice, 'message') else choice.get('message', {}).get('content', '')
        else:
            yanit_metni = completion.choices[0].message.content

        veri = json.loads(yanit_metni)
        veri["dosya_adi"] = fis
        tum_veriler.append(veri)
        print(f"✓ {fis} başarıyla okundu.")
        
    except Exception as e:
        print(f"X {fis} işlenirken hata oluştu: {e}")

# Verileri Excel ve CSV formatında kaydediyoruz
if tum_veriler:
    df = pd.DataFrame(tum_veriler)
    df = df[["dosya_adi", "firma_adi", "tarih", "fis_no", "toplam_tutar"]]
    
    df.to_excel("fis_raporu.xlsx", index=False)
    df.to_csv("fis_raporu.csv", index=False, encoding="utf-8-sig")
    print("\n=========================================")
    print("İşlem tamamlandı! 'fis_raporu.xlsx' dosyası oluşturuldu.")
    print("=========================================")
else:
    print("\nOkunacak veri bulunamadı. Lütfen internet bağlantınızı veya API kotanızı kontrol edin.")
