import streamlit as st
import pandas as pd
from streamlit_paste_button import paste_image_button
import io
import json
import requests
import base64

# --- 1. 系統設定 ---
st.set_page_config(page_title="AI 藥品計算機 (REST API版)", page_icon="👨‍⚕️", layout="wide")

st.markdown("""
    <style>
    .big-font { font-size:24px !important; font-weight:bold; }
    .report-box { background-color:#f0f2f6; padding:20px; border-radius:10px; border-left: 6px solid #007bff; font-family: monospace; white-space: pre-wrap; font-size: 16px;}
    .danger-box { background-color:#f8d7da; padding:10px; border-radius:5px; border:1px solid #721c24; color:#721c24; font-weight:bold; }
    </style>
""", unsafe_allow_html=True)

# --- 2. 資料庫讀取 ---
@st.cache_data
def load_database():
    try:
        df = pd.read_csv('drug_database.csv')
        df.columns = [c.strip() for c in df.columns]
        df['藥代'] = df['藥代'].astype(str).str.strip().str.upper()
        df['健保價'] = pd.to_numeric(df['健保價'], errors='coerce').fillna(0)
        if '警語' not in df.columns: df['警語'] = ""
        df['警語'] = df['警語'].fillna('')
        return df.set_index('藥代')['健保價'].to_dict(), \
               df.set_index('藥代')['藥名'].to_dict(), \
               df.set_index('藥代')['警語'].to_dict()
    except Exception as e:
        return {}, {}, {}

PRICE_DB, NAME_DB, WARN_DB = load_database()

# --- 3. 核心計算引擎 (維持原樣) ---
def format_prescription(weight, drugs_list, analysis, note):
    drug_lines = []
    total_price = 0
    has_warning = False
    
    for d in drugs_list:
        p = PRICE_DB.get(d['code'], 0)
        cost = p * d['qty']
        total_price += cost
        w = WARN_DB.get(d['code'], "")
        warn_text = f"⛔ {w}" if w else ""
        if w: has_warning = True
        drug_lines.append(f"- **{d['name']}**: {d['qty']} 顆  {warn_text}")
    
    warning_block = ""
    if has_warning:
        warning_block = "\n<div class='danger-box'>⚠️ 注意：本處方包含警示藥物 (如G6PD/兒童禁用)！</div>\n"
    
    return f"""### 💊 處方建議 (3天份)
**體重：** {weight} kg

**1. 藥物總量 (請藥師磨粉分包)：**
{chr(10).join(drug_lines)}
**(總藥費預估: ${int(total_price)})**
{warning_block}
**2. 劑量驗算：**
{analysis}

**3. 醫師備註：**
{note}
"""

def calc_amo1_complex(weight, mode='high'):
    if mode == 'high':
        target_amox_kg, target_ratio, mode_name = 80, 14, "急性鼻竇炎 (80mg/kg)"
    else:
        target_amox_kg, target_ratio, mode_name = 45, 0, "標準劑量 (45mg/kg)"

    limit_clav_kg, limit_amox_max, adult_weight_cutoff = 10, 2000, 22

    if weight >= adult_weight_cutoff:
        return format_prescription(weight, 
            [{'name': 'Curam (500/125)', 'qty': 6, 'code': 'AMO1'}, {'name': 'Amoxicillin (500mg)', 'qty': 6, 'code': 'AX'}],
            f"- Amox: 2000 mg/day (成人封頂)\n- Clav: 250 mg/day", "已達成人封頂劑量")

    daily_amox_req = min(target_amox_kg * weight, limit_amox_max)
    daily_clav_limit = limit_clav_kg * weight
    
    if mode == 'high':
        daily_clav_final = min(daily_amox_req / target_ratio, daily_clav_limit)
    else:
        daily_clav_final = min(6.4 * weight, daily_clav_limit)

    curam_qty = int(round((daily_clav_final * 3) / 125))
    provided_amox = curam_qty * 500
    provided_clav = curam_qty * 125
    
    rem_amox = max(0, (daily_amox_req * 3) - provided_amox)
    qty_500 = int(round(rem_amox / 500))
    qty_250 = int(round(rem_amox / 250))
    
    if abs(qty_250*250 - rem_amox) < abs(qty_500*500 - rem_amox) and abs(qty_500*500 - rem_amox) > 100:
        amox_qty, amox_code, amox_name = qty_250, 'AM', "Amoxicillin (250mg)"
    else:
        amox_qty, amox_code, amox_name = qty_500, 'AX', "Amoxicillin (500mg)"
        
    real_amox = (provided_amox + (amox_qty * (250 if amox_code=='AM' else 500))) / 3
    real_clav = provided_clav / 3
    ratio = round(real_amox / real_clav, 1) if real_clav > 0 else 0
    
    note = f"符合 {mode_name}"
    if real_clav / weight > 9.0: note += "。Clav 劑量略高，建議搭配益生菌。"
    
    return format_prescription(weight, 
        [{'name': 'Curam (500/125)', 'qty': curam_qty, 'code': 'AMO1'}, {'name': amox_name, 'qty': amox_qty, 'code': amox_code}],
        f"- Amox: {int(real_amox)} mg/day ({round(real_amox/weight,1)} mg/kg)\n- Clav: {int(real_clav)} mg/day ({round(real_clav/weight,1)} mg/kg)\n- 比例: {ratio} : 1", note)

def calc_simple_antibiotic(weight, drug_code):
    if drug_code == 'CIP0':
        if weight < 40:
            min_d, max_d = weight * 10, min(weight * 20, 750)
            min_t, max_t = round(min_d/500, 2), round(max_d/500, 2)
            avg_tab_bid = round((min_t + max_t) / 2 * 2) / 2
            total = avg_tab_bid * 2 * 3
            return format_prescription(weight, [{'name': 'Ciprofloxacin (500mg)', 'qty': total, 'code': 'CIP0'}], 
                f"劑量: {min_d}-{max_d} mg/dose (BID)\n每次約 {min_t}-{max_t} 顆", 
                "⚠️ 兒童使用需評估關節風險。")
        else:
            return format_prescription(weight, [{'name': 'Ciprofloxacin (500mg)', 'qty': 6, 'code': 'CIP0'}], "成人劑量: 500mg (1#) BID", "⚠️ 蠶豆症禁用")
    elif drug_code == 'AZI2':
        d = round((weight*10/250)*2)/2 if weight<20 else (1.5 if weight<=40 else 2)
        note = "成人劑量" if weight > 40 else "用法：QD (每日一次)"
        return format_prescription(weight, [{'name': 'Azithromycin (250mg)', 'qty': d*3, 'code': 'AZI2'}], f"每日 {d} 顆 (10mg/kg)", note)
    elif drug_code in ['AM', 'AX']: 
        target = weight * 45 * 3
        qty_ax = int(round(target/500))
        qty_am = int(round(target/250))
        if abs(qty_am*250 - target) < abs(qty_ax*500 - target):
            return format_prescription(weight, [{'name': 'Amoxicillin (250mg)', 'qty': qty_am, 'code': 'AM'}], f"目標 45mg/kg", "標準劑量")
        else:
            return format_prescription(weight, [{'name': 'Amoxicillin (500mg)', 'qty': qty_ax, 'code': 'AX'}], f"目標 45mg/kg", "標準劑量")
    elif drug_code in ['K5', 'CEP']: 
        target = weight * 40 * 3
        qty_k5, qty_cep = int(round(target/500)), int(round(target/250))
        if abs(qty_cep*250 - target) < abs(qty_k5*500 - target):
            return format_prescription(weight, [{'name': 'Cephalexin (250mg)', 'qty': qty_cep, 'code': 'CEP'}], f"目標 40mg/kg", "建議分 4 次 (QID)")
        else:
            return format_prescription(weight, [{'name': 'Cephalexin (500mg)', 'qty': qty_k5, 'code': 'K5'}], f"目標 40mg/kg", "建議分 4 次 (QID)")
    elif drug_code == 'MOR': 
        if weight >= 40: return format_prescription(weight, [{'name': 'Baktar (MOR)', 'qty': 12, 'code': 'MOR'}], "成人: 2# BID", "⚠️ 蠶豆症禁用")
        else:
            dose = round((weight/20)*2)/2 or 0.5
            return format_prescription(weight, [{'name': 'Baktar (MOR)', 'qty': dose*2*3, 'code': 'MOR'}], f"公式 BW/20 = {dose}顆/次", "⚠️ 蠶豆症禁用")
    elif drug_code == 'DOX0':
        return format_prescription(weight, [{'name': 'Doxycycline (100mg)', 'qty': 6, 'code': 'DOX0'}], "成人: 1# BID", "⚠️ 8歲以下不建議")
    return "Error: Unknown Drug"

# --- 4. AI 視覺辨識 (改用 REST API 直連) ---
def analyze_image_rest(img_bytes, api_key):
    if not api_key: return "ERROR: API Key Missing"
    
    # 轉為 Base64
    base64_data = base64.b64encode(img_bytes).decode('utf-8')
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt_text = """
    你是專業的藥品辨識系統。請分析這張藥單圖片。
    請直接回傳純 JSON List，不要有任何 markdown 標記。
    格式：[{"code":"藥品代碼大寫", "frequency":次數數字, "total_amount":總量數字}]
    範例：[{"code":"AZI2", "frequency":1, "total_amount":3}]
    """
    
    data = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {"inline_data": {"mime_type": "image/png", "data": base64_data}}
            ]
        }]
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code != 200:
            st.error(f"Google API 回傳錯誤: {response.text}")
            return []
            
        result = response.json()
        # 解析回傳內容
        raw_text = result['candidates'][0]['content']['parts'][0]['text']
        clean_text = raw_text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
        
    except Exception as e:
        st.error(f"連線或解析失敗: {str(e)}")
        return []

# --- 5. 前端介面 ---
st.sidebar.title("☁️ 雲端藥品計算機")
st.sidebar.info("Ver 4.0 - REST API 終極版")
api_key = st.sidebar.text_input("Gemini API Key", type="password")
weight = st.sidebar.number_input("體重 (kg)", value=20.0, step=0.5)

if not PRICE_DB: st.sidebar.error("⚠️ 請確認 drug_database.csv 已上傳")
else: st.sidebar.success(f"📚 藥品庫：{len(PRICE_DB)} 筆")

tab1, tab2 = st.tabs(["🧮 抗生素精算", "📷 截圖辨識"])

with tab1:
    st.subheader("抗生素處方開立")
    abx = st.selectbox("選擇抗生素", ["AMO1 (Curam/Amox 混藥)", "AZI2 (Azithromycin)", "CIP0 (Ciprofloxacin)", "AM/AX (純 Amox)", "K5/CEP (Cephalexin)", "MOR (Baktar)", "DOX0 (Doxycycline)"])
    
    if st.button("計算處方", type="primary", use_container_width=True):
        if "AMO1" in abx:
            mode = st.radio("治療目標", ["急性鼻竇炎 (80mg/kg)", "標準劑量 (45mg/kg)"])
            mk = 'high' if '80' in mode else 'std'
            st.markdown(f"""<div class="report-box" unsafe_allow_html=True>{calc_amo1_complex(weight, mk)}</div>""", unsafe_allow_html=True)
        else:
            code_map = {"AZI2":"AZI2", "CIP0":"CIP0", "AM/AX":"AX", "K5/CEP":"K5", "MOR":"MOR", "DOX0":"DOX0"}
            st.markdown(f"""<div class="report-box" unsafe_allow_html=True>{calc_simple_antibiotic(weight, code_map[abx.split()[0]])}</div>""", unsafe_allow_html=True)

with tab2:
    st.subheader("AI 藥單辨識")
    paste_res = paste_image_button("📋 貼上截圖", background_color="#6c757d", text_color="#FFF")
    
    if paste_res.image_data:
        st.image(paste_res.image_data, caption="預覽圖片")
        
        if st.button("🚀 開始 AI 分析 (REST API)", type="primary"):
            if not api_key:
                st.error("❌ 請先在左側欄位輸入 Gemini API Key")
            else:
                with st.spinner("AI 正在分析中..."):
                    bytes_io = io.BytesIO()
                    paste_res.image_data.save(bytes_io, format='PNG')
                    items = analyze_image_rest(bytes_io.getvalue(), api_key)
                
                if items:
                    results = []
                    for item in items:
                        code = item.get('code', 'UNKNOWN')
                        qty = item.get('total_amount', 0)
                        name = NAME_DB.get(code, code)
                        price = PRICE_DB.get(code, 0)
                        results.append({"代碼": code, "藥名": name, "總量": qty, "小計": price*qty})
                    
                    st.dataframe(pd.DataFrame(results))
                else:
                    st.warning("AI 無法辨識內容")
