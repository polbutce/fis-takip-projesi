import os, base64, sqlite3, io, re, time
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, request, session
from PIL import Image
from groq import Groq
from rapidfuzz import fuzz
from werkzeug.security import check_password_hash

app = Flask(__name__)
app.secret_key = "cok-gizli-bir-anahtar-kelime"

# Güvenli tanım: API anahtarını kodda açık bırakmıyoruz, sistemden okutuyoruz
client = Groq(api_key=os.environ.get("GROQ_API_KEY"), timeout=60.0)

DB_FILE = "veritabanı.db"

STANDART_BIRIMLER = [
    "Adet", "Kilogram", "Gram", "Litre", "Mililitre", 
    "Paket", "Kutu", "Koli", "Rulo", "Demet", "Şişe", "Metre"
]

KATEGORILER = [
    "Gıda", "Temizlik", "Kozmetik", "Kırtasiye", "Hırdavat", 
    "Giyim", "Sağlık", "Ulaşım", "Plastik/Züccaciye", "Elektronik", "Finans/Sigorta"
]

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS fisler (id INTEGER PRIMARY KEY AUTOINCREMENT, dosya_adi TEXT, firma_adi TEXT, ham_firma_adi TEXT, sube_adi TEXT, tarih TEXT, fis_no TEXT, toplam_tutar TEXT)')
    
    c.execute("PRAGMA table_info(fisler)")
    columns = [column[1] for column in c.fetchall()]
    if 'sube_adi' not in columns:
        c.execute('ALTER TABLE fisler ADD COLUMN sube_adi TEXT')
    if 'ham_firma_adi' not in columns:
        c.execute('ALTER TABLE fisler ADD COLUMN ham_firma_adi TEXT')

    c.execute('CREATE TABLE IF NOT EXISTS ana_urunler (id INTEGER PRIMARY KEY AUTOINCREMENT, temiz_urun_adi TEXT UNIQUE, kategori TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS urun_kalemleri (id INTEGER PRIMARY KEY AUTOINCREMENT, fis_id INTEGER, ham_urun_adi TEXT, ana_urun_id INTEGER, miktar REAL, birim TEXT, birim_fiyat REAL, satir_toplam REAL, tahmini_kategori TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS urun_sozlugu (id INTEGER PRIMARY KEY AUTOINCREMENT, ham_isim TEXT UNIQUE, ana_urun_id INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS firma_sozlugu (id INTEGER PRIMARY KEY AUTOINCREMENT, ocr_firma TEXT UNIQUE, temiz_firma TEXT, sube_adi TEXT)')

    conn.commit()
    conn.close()

def encode_and_resize_image(p, max_size=(800, 800)):
    with Image.open(p) as img:
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.convert('RGB').save(buf, format="JPEG", quality=75)
        return base64.b64encode(buf.getvalue()).decode('utf-8')

def get_content_safely(res):
    if not res or not hasattr(res, 'choices'): return ""
    ch = res.choices
    if isinstance(ch, list) and len(ch) > 0:
        first = ch[0]
        if hasattr(first, 'message'): return first.message.content
        if isinstance(first, dict) and 'message' in first: return first['message']['content']
    return ch.message.content if hasattr(ch, 'message') else ""

def temiz_sayi(m):
    if not m: return 0.0
    m = str(m).upper().replace("TL", "").strip()
    if "," in m and "." in m: 
        m = m.replace(".", "").replace(",", ".")
    elif "," in m: 
        m = m.replace(",", ".")
    try: 
        return float(re.sub(r'[^\d\.]', '', m))
    except: 
        return 0.0

def format_virgul(sayi):
    if sayi is None: return "0,00"
    try:
        val = float(sayi)
        return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        s = str(sayi).upper().replace("TL", "").strip()
        num = temiz_sayi(s)
        if num > 0:
            return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return str(sayi)

def tarih_parse_et(tarih_str):
    if not tarih_str: return datetime.min
    try:
        match = re.search(r'(\d{2})[\.\/-](\d{2})[\.\/-](\d{4})', str(tarih_str))
        if match:
            d, m, y = match.groups()
            return datetime(int(y), int(m), int(d))
    except:
        pass
    return datetime.min

def birim_standartlastir(birim_raw):
    b = str(birim_raw).upper().strip()
    if b in ["KG", "KILOGRAM", "KİLO"]: return "Kilogram"
    if b in ["GR", "GRAM"]: return "Gram"
    if b in ["LT", "LİTRE", "LITRE"]: return "Litre"
    if b in ["ML", "MİLİLİTRE"]: return "Mililitre"
    if b in ["PK", "PAKET"]: return "Paket"
    if b in ["KT", "KUTU"]: return "Kutu"
    if b in ["KL", "KOLİ", "KOLI"]: return "Koli"
    if b in ["RL", "RULO"]: return "Rulo"
    if b in ["DM", "DEMET"]: return "Demet"
    if b in ["ŞŞ", "ŞİŞE"]: return "Şişe"
    if b in ["MT", "METRE"]: return "Metre"
    return "Adet"

def kategori_tahmin_et(urun_adi):
    u = urun_adi.upper()
    havuz = {
        "Kozmetik": ["DEODORANT", "JEL", "SHAMPOO", "SAMPUAN", "CREME", "KREM", "PARFUM", "RUJ", "AGIZ", "PANTENE"],
        "Temizlik": ["SABUN", "DETERJAN", "DOMESTOS", "PEROS", "MATIK", "YUZEY", "HAVLU", "SPREY", "MARC", "BULASIK", "CAMASIR"],
        "Gıda": ["SUT", "KIYMA", "KOFT", "BONFILE", "ET", "PILIÇ", "YAG", "ZEYTIN", "MISIR", "CAFE", "KAHVE", "SU", "AYRAN", "CIPS", "PEYNIR", "EKMEK", "DARDANEL", "ÇEKİRDEK", "YUKSUK", "MAKARNA"],
        "Kırtasiye": ["KALEM", "DEFTER", "SILGI", "KAGIT", "AJANDA", "BANT", "DOSYA", "MAKAS", "FOTOKOPI"],
        "Hırdavat": ["VIDA", "CIVATA", "MATKOP", "CATER", "ÇEKİÇ", "TORNAVIDA", "SILIKON", "TENT", "KONTROL", "PENSE", "MISIN"],
        "Giyim": ["GÖMLEK", "PANTOLON", "CORAP", "AYAKKABI", "T-SHIRT", "KAZAK", "MONT"],
        "Sağlık": ["ILAC", "ECZANE", "BANDAN", "VITAMIN", "ASPIRIN", "MASK", "KOLONYA"],
        "Ulaşım": ["AKBIL", "BENZIN", "MAZOT", "OTOPARK", "TAKSI", "METRO"],
        "Plastik/Züccaciye": ["POŞET", "KASA", "TABAK", "BARDAK", "PLASTIK", "BIÇAK"],
        "Elektronik": ["AMPUL", "PIL", "KABLO", "SARJ", "TV", "LED"],
        "Finans/Sigorta": ["SİGORTA", "POLİÇE", "TEKLİF", "ARACILIK"]
    }
    for kat, kelimeler in havuz.items():
        if any(k in u for k in kelimeler): return kat
    return "Gıda"

def fisleri_tara_ve_kaydet():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    fisler = [f for f in os.listdir(".") if f.startswith("deneme_fis") and f.lower().endswith((".jpeg", ".jpg"))]
    
    c.execute("SELECT ocr_firma, temiz_firma, sube_adi FROM firma_sozlugu")
    market_hafizasi = c.fetchall()

    for fis in fisler:
        c.execute("SELECT id FROM fisler WHERE dosya_adi = ?", (fis,))
        if c.fetchone(): continue
        print(f"\n-> {fis} TARANIYOR...")
        try:
            b64 = encode_and_resize_image(fis)
            
            res1 = client.chat.completions.create(
                model="qwen/qwen3.8-27b", max_tokens=150,
                messages=[{"role": "user", "content": [{"type": "text", "text": "Bu fişin üst bilgilerini aralarına '###' koyarak tam olarak şu sırada tek satırda yaz: Firma Adı ### Şube Adı ### Tarih ### Fiş No ### Toplam Tutar. Eğer şube adı yoksa '-' yaz. Başka hiçbir şey yazma."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}])
            
            p1 = [x.strip() for x in get_content_safely(res1).split("###") if x.strip()]
            ham_f_adi = p1[0] if len(p1) > 0 else "Bilinmiyor"
            sube = p1[1] if len(p1) > 1 and p1[1] != "-" else ""
            tarih = p1[2] if len(p1) > 2 else (p1[1] if len(p1) > 1 else "-")
            f_no = p1[3] if len(p1) > 3 else "-"
            t_tutar = p1[4] if len(p1) > 4 else (p1[3] if len(p1) > 3 else "-")
            time.sleep(2)
            
            f_adi = ham_f_adi
            for k_ocr, k_temiz, k_sube in market_hafizasi:
                if fuzz.token_sort_ratio(ham_f_adi.upper(), k_ocr.upper()) >= 80:
                    f_adi = k_temiz
                    if k_sube: sube = k_sube
                    break
            
            res2 = client.chat.completions.create(
                model="qwen/qwen3.8-27b", max_tokens=600,
                messages=[{"role": "user", "content": [{"type": "text", "text": "Bu fişteki tüm ürünleri satır satır bul. Her satırı aralarında sadece '*' koyarak tam olarak şu düzende yaz: Miktar * Birim * Ürün Adı * Satır Toplam Tutar. Örnek: 1 * AD * PANTENE SAMPUAN * 300,00. Başka açıklama ekleme, direkt listeyle başla."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}])
            
            c.execute("INSERT INTO fisler (dosya_adi, firma_adi, ham_firma_adi, sube_adi, tarih, fis_no, toplam_tutar) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                      (fis, f_adi, ham_f_adi, sube, tarih, f_no, t_tutar))
            fid = c.lastrowid
            
            urun_metni = get_content_safely(res2)
            for satir in urun_metni.strip().split("\n"):
                p = [x.strip() for x in satir.split("*") if x.strip()]
                if len(p) >= 4:
                    h_isim = p[2]
                    if any(x in h_isim.upper() for x in ["TOPLAM", "KDV", "ARA", "YALNIZ"]): continue
                    mlar = temiz_sayi(p[0])
                    birim = birim_standartlastir(p[1])
                    tot = temiz_sayi(p[3])
                    if mlar == 0: mlar = 1.0
                    if tot == 0: continue
                    
                    bf = round(tot / mlar, 2) if mlar > 0 else tot
                    tahmini_kat = kategori_tahmin_et(h_isim)
                    
                    c.execute("SELECT ana_urun_id FROM urun_sozlugu WHERE ham_isim = ?", (h_isim,))
                    row = c.fetchone()
                    au_id = row[0] if row else None
                    
                    c.execute("INSERT INTO urun_kalemleri (fis_id, ham_urun_adi, ana_urun_id, miktar, birim, birim_fiyat, satir_toplam, tahmini_kategori) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                              (fid, h_isim, au_id, mlar, birim, bf, tot, tahmini_kat))
            
            conn.commit()
            print(f" ✓ {fis} işlendi.")
            time.sleep(2)
        except Exception as e:
            print(f" X {fis} hata: {e}")
    conn.close()

@app.route('/')
def ana_sayfa():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute("SELECT * FROM fisler")
    fisler_raw = c.fetchall()
    fis_listesi = []
    
    for f in fisler_raw:
        c.execute("SELECT SUM(satir_toplam) as toplam FROM urun_kalemleri WHERE fis_id = ?", (f['id'],))
        row = c.fetchone()
        hesaplanan_toplam = row['toplam'] if row['toplam'] else 0.0
        
        orijinal_toplam = temiz_sayi(f['toplam_tutar'])
        fark = round(hesaplanan_toplam - orijinal_toplam, 2)
        
        fis_dict = dict(f)
        fis_dict['toplam_tutar_formatted'] = format_virgul(f['toplam_tutar'])
        fis_dict['hesaplanan_toplam_str'] = format_virgul(hesaplanan_toplam)
        fis_dict['fark'] = fark
        fis_dict['fark_str'] = format_virgul(fark)
        fis_dict['sort_date'] = tarih_parse_et(f['tarih'])
        fis_listesi.append(fis_dict)
        
    fis_listesi.sort(key=lambda x: x['sort_date'], reverse=True)
    conn.close()
    return render_template('ana_sayfa.html', fisler=fis_listesi)

@app.route('/fis/<int:fis_id>')
def fis_detay(fis_id):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute("SELECT * FROM ana_urunler")
    mevcut = {r['temiz_urun_adi']: (r['id'], r['kategori']) for r in c.fetchall()}
    
    c.execute("SELECT * FROM fisler WHERE id = ?", (fis_id,))
    f = c.fetchone()
    if not f: return redirect(url_for('ana_sayfa'))
    
    c.execute("SELECT uk.*, au.temiz_urun_adi, au.kategori FROM urun_kalemleri uk LEFT JOIN ana_urunler au ON uk.ana_urun_id = au.id WHERE uk.fis_id = ?", (fis_id,))
    kalemler = []
    hesaplanan_toplam = 0.0
    
    for r in c.fetchall():
        d = dict(r)
        d['temiz_isim'] = d['temiz_urun_adi']
        d['miktar_str'] = format_virgul(d['miktar'])
        d['birim_fiyat_str'] = format_virgul(d['birim_fiyat'])
        d['satir_toplam_str'] = format_virgul(d['satir_toplam'])
        
        hesaplanan_toplam += float(d['satir_toplam']) if d['satir_toplam'] else 0.0
        
        if not d['temiz_isim'] and mevcut:
            en_yakin, maks_skor = "", 0
            for k in mevcut.keys():
                skor = fuzz.token_sort_ratio(d['ham_urun_adi'].upper(), k.upper())
                if skor > maks_skor: maks_skor = skor; en_yakin = k
            d['oneri'] = en_yakin if maks_skor >= 60 else d['ham_urun_adi'].title()
        else:
            d['oneri'] = d['ham_urun_adi'].title()
        kalemler.append(d)
        
    orijinal_toplam = temiz_sayi(f['toplam_tutar'])
    fark = round(hesaplanan_toplam - orijinal_toplam, 2)
    
    fis_data = dict(f)
    if not fis_data.get('sube_adi'): fis_data['sube_adi'] = ""
    fis_data['urunler'] = kalemler
    fis_data['toplam_tutar_formatted'] = format_virgul(f['toplam_tutar'])
    fis_data['hesaplanan_toplam_str'] = format_virgul(hesaplanan_toplam)
    fis_data['fark'] = fark
    fis_data['fark_str'] = format_virgul(fark)
    
    conn.close()
    return render_template('detay.html', fis=fis_data, birimler=STANDART_BIRIMLER)

@app.route('/toplu-guncelle', methods=['POST'])
def toplu_guncelle():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    fis_id = request.form.get('fis_id')
    
    yeni_firma = request.form.get('firma_adi', '').strip()
    yeni_sube = request.form.get('sube_adi', '').strip()
    yeni_tarih = request.form.get('tarih', '').strip()
    yeni_fis_no = request.form.get('fis_no', '').strip()
    
    c.execute("UPDATE fisler SET firma_adi=?, sube_adi=?, tarih=?, fis_no=? WHERE id=?", 
              (yeni_firma, yeni_sube, yeni_tarih, yeni_fis_no, fis_id))
    
    c.execute("SELECT ham_firma_adi FROM fisler WHERE id = ?", (fis_id,))
    row_f = c.fetchone()
    ham_f = row_f[0] if row_f and row_f[0] else yeni_firma
    
    c.execute("""
        INSERT INTO firma_sozlugu (ocr_firma, temiz_firma, sube_adi) 
        VALUES (?, ?, ?) 
        ON CONFLICT(ocr_firma) 
        DO UPDATE SET temiz_firma = ?, sube_adi = ?
    """, (ham_f, yeni_firma, yeni_sube, yeni_firma, yeni_sube))

    kalem_ids = request.form.getlist('kalem_ids')
    for kid in kalem_ids:
        m = temiz_sayi(request.form.get(f'miktar_{kid}'))
        b = request.form.get(f'birim_{kid}', 'Adet')
        t = temiz_sayi(request.form.get(f'satir_toplam_{kid}'))
        bf = round(t / m, 2) if m > 0 else t
        
        c.execute("UPDATE urun_kalemleri SET miktar=?, birim=?, birim_fiyat=?, satir_toplam=? WHERE id=?", (m, b, bf, t, kid))
        
        yeni_ana_key = f'yeni_ana_{kid}'
        secilen_kategori = request.form.get(f'kategori_{kid}', 'Gıda')
        ham_isim = request.form.get(f'ham_isimler_{kid}', '').strip()
        
        if yeni_ana_key in request.form:
            yeni_ana_adi = request.form.get(yeni_ana_key, '').strip()
            
            if yeni_ana_adi and ham_isim:
                c.execute("""
                    INSERT INTO ana_urunler (temiz_urun_adi, kategori) 
                    VALUES (?, ?) 
                    ON CONFLICT(temiz_urun_adi) 
                    DO UPDATE SET kategori = ?
                """, (yeni_ana_adi, secilen_kategori, secilen_kategori))
                
                c.execute("SELECT id FROM ana_urunler WHERE temiz_urun_adi = ?", (yeni_ana_adi,))
                row_au = c.fetchone()
                if row_au:
                    au_id = row_au[0]
                    c.execute("INSERT OR REPLACE INTO urun_sozlugu (ham_isim, ana_urun_id) VALUES (?, ?)", (ham_isim, au_id))
                    c.execute("UPDATE urun_kalemleri SET ana_urun_id = ? WHERE ham_urun_adi = ?", (au_id, ham_isim))
            
    conn.commit()
    conn.close()
    return redirect(url_for('fis_detay', fis_id=fis_id))

# YENİ: DİNAMİK RAPORLAMA VE AI ANALİZ ROTASI
@app.route('/raporlar')
def raporlar():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Dropdown seçenekleri için verileri çek
    c.execute("SELECT DISTINCT firma_adi FROM fisler WHERE firma_adi IS NOT NULL AND firma_adi != ''")
    firmalar = [r['firma_adi'] for r in c.fetchall()]

    c.execute("SELECT DISTINCT sube_adi FROM fisler WHERE sube_adi IS NOT NULL AND sube_adi != ''")
    subeler = [r['sube_adi'] for r in c.fetchall()]

    # Filtre Parametrelerini Al
    f_firma = request.args.get('firma', '').strip()
    f_sube = request.args.get('sube', '').strip()
    f_kategori = request.args.get('kategori', '').strip()
    f_urun = request.args.get('urun_adi', '').strip()
    f_bas = request.args.get('bas_tarih', '').strip()
    f_bit = request.args.get('bit_tarih', '').strip()
    f_min = request.args.get('min_tutar', '').strip()
    f_maks = request.args.get('maks_tutar', '').strip()

    # SQL Sorgusunu Oluştur
    query = """
        SELECT f.firma_adi, f.sube_adi, f.tarih, f.fis_no, 
               uk.ham_urun_adi, uk.satir_toplam, uk.tahmini_kategori,
               au.temiz_urun_adi, au.kategori as ana_kategori
        FROM urun_kalemleri uk
        JOIN fisler f ON uk.fis_id = f.id
        LEFT JOIN ana_urunler au ON uk.ana_urun_id = au.id
        WHERE 1=1
    """
    params = []

    if f_firma:
        query += " AND f.firma_adi = ?"
        params.append(f_firma)
    if f_sube:
        query += " AND f.sube_adi = ?"
        params.append(f_sube)
    if f_kategori:
        query += " AND (au.kategori = ? OR (au.kategori IS NULL AND uk.tahmini_kategori = ?))"
        params.extend([f_kategori, f_kategori])
    if f_urun:
        query += " AND (uk.ham_urun_adi LIKE ? OR au.temiz_urun_adi LIKE ?)"
        params.extend([f'%{f_urun}%', f'%{f_urun}%'])
    if f_min:
        query += " AND uk.satir_toplam >= ?"
        params.append(temiz_sayi(f_min))
    if f_maks:
        query += " AND uk.satir_toplam <= ?"
        params.append(temiz_sayi(f_maks))

    c.execute(query, params)
    rows = c.fetchall()

    # İstatistik Hesaplamaları
    toplam_tutar = 0.0
    toplam_kalem = len(rows)
    kategori_toplamlari = {}
    market_toplamlari = {}
    fis_set = set()

    for r in rows:
        tot = r['satir_toplam'] if r['satir_toplam'] else 0.0
        toplam_tutar += tot
        fis_set.add(r['fis_no'])

        kat = r['ana_kategori'] if r['ana_kategori'] else r['tahmini_kategori']
        if not kat: kat = "Gıda"
        kategori_toplamlari[kat] = kategori_toplamlari.get(kat, 0.0) + tot

        mkt = r['firma_adi'] if r['firma_adi'] else "Bilinmiyor"
        market_toplamlari[mkt] = market_toplamlari.get(mkt, 0.0) + tot

    toplam_fis_sayisi = len(fis_set)
    ort_fis = (toplam_tutar / toplam_fis_sayisi) if toplam_fis_sayisi > 0 else 0.0

    ai_yorum = ""
    if toplam_kalem > 0:
        prompt_ozet = f"""
        Aşağıda bir kullanıcının harcama verileri yer almaktadır:
        - Toplam Harcama: {toplam_tutar:.2f} TL
        - Toplam Fiş Sayısı: {toplam_fis_sayisi}
        - Toplam Aldığı Ürün Kalemi: {toplam_kalem}
        - Kategorilere Göre Dağılım: {kategori_toplamlari}
        - Marketlere Göre Dağılım: {market_toplamlari}

        Lütfen bir finans uzmanı gibi davranarak bu kullanıcının harcama alışkanlıklarını değerlendir.
        En çok bütçe ayırdığı kategorileri, gittiği marketleri yorumla. 
        Tasarruf edebileceği alanları ve bütçe tavsiyelerini 3-4 paragrafta samimi, yapıcı ve profesyonel bir Türkçe ile yaz. 
        Cevabını HTML formatında (<p>, <strong>, <ul>, <li> etiketleri kullanarak) ver.
        """
        try:
            res = client.chat.completions.create(
                model="qwen/qwen3.8-27b", max_tokens=700,
                messages=[{"role": "user", "content": prompt_ozet}]
            )
            ai_yorum = get_content_safely(res)
        except Exception as e:
            ai_yorum = f"<p class='text-danger'>AI Yorumu üretilirken hata oluştu: {e}</p>"
    else:
        ai_yorum = "<p class='text-muted'>Filtrelere uygun harcama kaydı bulunamadığı için analiz üretilemedi.</p>"

    conn.close()

    filtreler = {
        'firma': f_firma, 'sube': f_sube, 'kategori': f_kategori,
        'urun_adi': f_urun, 'bas_tarih': f_bas, 'bit_tarih': f_bit,
        'min_tutar': f_min, 'maks_tutar': f_maks
    }

    return render_template(
        'raporlar.html',
        firmalar=firmalar, subeler=subeler, kategoriler=KATEGORILER,
        filtreler=filtreler,
        toplam_tutar_str=format_virgul(toplam_tutar),
        toplam_fis_sayisi=toplam_fis_sayisi,
        toplam_kalem_sayisi=toplam_kalem,
        ortalama_fis_str=format_virgul(ort_fis),
        kat_labels=list(kategori_toplamlari.keys()),
        kat_data=list(kategori_toplamlari.values()),
        market_labels=list(market_toplamlari.keys()),
        market_data=list(market_toplamlari.values()),
        ai_yorum=ai_yorum
    )

@app.route('/kalem-ekle', methods=['POST'])
def kalem_ekle():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    fid = request.form.get('fis_id')
    h_isim = request.form.get('ham_urun_adi', '').strip()
    m = temiz_sayi(request.form.get('miktar', '1'))
    b = request.form.get('birim', 'Adet')
    tot = temiz_sayi(request.form.get('satir_toplam', '0'))
    
    if h_isim and tot > 0:
        bf = round(tot / m, 2) if m > 0 else tot
        tahmini_kat = kategori_tahmin_et(h_isim)
        
        c.execute("SELECT ana_urun_id FROM urun_sozlugu WHERE ham_isim = ?", (h_isim,))
        row = c.fetchone()
        au_id = row[0] if row else None
        
        c.execute("INSERT INTO urun_kalemleri (fis_id, ham_urun_adi, ana_urun_id, miktar, birim, birim_fiyat, satir_toplam, tahmini_kategori) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                  (fid, h_isim, au_id, m, b, bf, tot, tahmini_kat))
        conn.commit()
        
    conn.close()
    return redirect(url_for('fis_detay', fis_id=fid))

@app.route('/fis-sil/<int:fis_id>')
def fis_sil(fis_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM urun_kalemleri WHERE fis_id = ?", (fis_id,))
    c.execute("DELETE FROM fisler WHERE id = ?", (fis_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('ana_sayfa'))

@app.route('/sozluk-sil')
def sozluk_sil():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    ham = request.args.get('ham')
    fis_id = request.args.get('fis_id')
    c.execute("DELETE FROM urun_sozlugu WHERE ham_isim = ?", (ham,))
    c.execute("UPDATE urun_kalemleri SET ana_urun_id = NULL WHERE ham_urun_adi = ?", (ham,))
    conn.commit()
    conn.close()
    return redirect(url_for('fis_detay', fis_id=fis_id))

@app.route('/tara')
def tara():
    fisleri_tara_ve_kaydet()
    return redirect(url_for('ana_sayfa'))

# KULLANICI GİRİŞ SAYFASI
@app.route('/giris', methods=['GET', 'POST'])
def giris():
    hata = ""
    if request.method == 'POST':
        kullanici = request.form.get('kullanici')
        sifre = request.form.get('sifre')
        
        # .env dosyasından veya Render Environment değişkenlerinden okur
        admin_kullanici = os.environ.get("ADMIN_USER", "admin")
        admin_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
        
        # Kullanıcı adı doğru ve şifre hash eşleşmesi başarılıysa
        if kullanici == admin_kullanici and check_password_hash(admin_hash, sifre):
            session['giris_yapildi'] = True
            return redirect(url_for('ana_sayfa'))
        else:
            hata = "Kullanıcı adı veya şifre hatalı!"
            
    return render_template('giris.html', hata=hata)

# ÇIKIŞ YAPMA
@app.route('/cikis')
def cikis():
    session.pop('giris_yapildi', None)
    return redirect(url_for('giris'))

# HER SAYFADA OTURUM KONTROLÜ (Decorator)
@app.before_request
def oturum_kontrol():
    serbest_rotalar = ['giris']
    if request.endpoint not in serbest_rotalar and not session.get('giris_yapildi'):
        return redirect(url_for('giris'))
    

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
else:
    # Gunicorn ile çalışırken tabloların hazır olmasını sağlar
    init_db()