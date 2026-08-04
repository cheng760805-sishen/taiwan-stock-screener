import datetime
import json
import ssl
import urllib.request
import pandas as pd
import requests
import streamlit as st

# 1. 頁面基本設定
st.set_page_config(
    page_title="台股全市場 FinMind KD & RSI 彈性篩選器",
    page_icon="📈",
    layout="wide",
)

st.title("🌐 台股上市全市場 KD & RSI 彈性自訂條件篩選器")
st.caption(
    "資料來源：FinMind 台灣金融資料庫 ｜ 專為台股設計，支援中文股票名稱顯示"
)

# 2. 常用核心個股的中文名稱字典（確保即使沒抓到 API 也能精準顯示中文）
POPULAR_STOCKS_MAP = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電", "2382": "廣達",
    "2881": "富邦金", "2882": "國泰金", "2412": "中華電", "2303": "聯電", "3008": "大立光",
    "2603": "長榮", "2609": "陽明", "2615": "萬海", "2002": "中鋼", "1101": "台泥",
    "1301": "台塑", "1303": "南亞", "2886": "兆豐金", "2891": "中信金", "5880": "合庫金",
    "3231": "緯創", "6669": "緯穎", "2357": "華碩", "2379": "瑞昱", "3034": "聯詠",
    "2377": "微星", "2301": "光寶科", "2345": "智邦", "3711": "日月光投控", "2408": "南亞科",
    "3037": "欣興", "2376": "技嘉", "2356": "英業達", "2409": "友達", "3481": "群創",
    "1216": "統一", "2912": "統一超", "9910": "豐泰", "1402": "遠東新", "2105": "正新",
    "2207": "和泰車", "2618": "長榮航", "2610": "華航", "2880": "華南金", "2883": "開發金",
    "2884": "玉山金", "2885": "元大金", "2887": "台新金", "2890": "永豐金", "2892": "第一金",
    "5876": "上海商銀", "6005": "群益證"
}

# 初始化 Session State (記憶庫)
if "scan_data" not in st.session_state:
    st.session_state.scan_data = None


# 3. 側邊欄控制區
st.sidebar.header("🎯 1. 掃描範圍與 Token 設定")
scan_mode = st.sidebar.radio(
    "選擇掃描模式：",
    ["⚡ 核心熱門個股 (~50+ 檔, 掃描極快)", "🌐 全上市市場 (~1,000+ 檔)"],
    index=0,
)

finmind_token = st.sidebar.text_input(
    "FinMind API Token (選填，填入可提升抓取速度):",
    type="password",
    help="可在 FinMind 官網免費申請 Token，未填寫亦可正常使用免費額度。",
)

st.sidebar.header("⚙️ 2. 技術指標週期設定")
with st.sidebar.expander("📊 KD 指標週期設定", expanded=False):
    kd_period = st.number_input("KD 週期 (N日)", min_value=3, max_value=60, value=9, step=1)
    k_smooth = st.number_input("K 平滑分母 (預設 3 即 1/3 權重)", min_value=2, max_value=10, value=3, step=1)
    d_smooth = st.number_input("D 平滑分母 (預設 3 即 1/3 權重)", min_value=2, max_value=10, value=3, step=1)

with st.sidebar.expander("📈 RSI 指標週期設定", expanded=False):
    rsi_short = st.number_input("短天期 RSI 週期", min_value=2, max_value=30, value=5, step=1)
    rsi_long = st.number_input("長天期 RSI 週期", min_value=2, max_value=60, value=10, step=1)

st.sidebar.header("🎛️ 3. 篩選門檻條件設定")
k_threshold = st.sidebar.slider("K 值低於 (超跌門檻)", min_value=5, max_value=95, value=25, step=1)
rsi_s_threshold = st.sidebar.slider(f"RSI({rsi_short}) 低於", min_value=5, max_value=95, value=25, step=1)
rsi_l_threshold = st.sidebar.slider(f"RSI({rsi_long}) 低於", min_value=5, max_value=95, value=25, step=1)

only_matched = st.sidebar.checkbox("僅顯示符合條件的股票", value=True)


# 4. 取得股票中文名稱與清單 (串接證交所 OpenAPI)
@st.cache_data(ttl=14400)
def get_twse_stocks(full_market=True):
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    
    twse_dict = {}
    try:
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response:
            data = json.loads(response.read().decode())
            df = pd.DataFrame(data)
            df = df[df["Code"].str.match(r"^\d{4}$")]
            twse_dict = dict(zip(df["Code"], df["Name"]))
    except Exception:
        pass

    if not full_market:
        popular_stocks = []
        for code in POPULAR_STOCKS_MAP.keys():
            # 優先使用證交所即時抓到的中文名稱，若無則使用內建中文字典
            name = twse_dict.get(code, POPULAR_STOCKS_MAP.get(code, f"股票{code}"))
            popular_stocks.append({"Code": code, "Name": name})
        return popular_stocks

    if twse_dict:
        return [{"Code": code, "Name": name} for code, name in twse_dict.items()]
    else:
        return [{"Code": code, "Name": name} for code, name in POPULAR_STOCKS_MAP.items()]


# 5. 從 FinMind API 擷取個股日 K 線
def fetch_finmind_daily(stock_id, token=""):
    start_date = (datetime.datetime.now() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start_date,
    }
    if token:
        params["token"] = token

    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        if data.get("status") == 200 and data.get("data"):
            df = pd.DataFrame(data["data"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
    except Exception:
        pass
    return pd.DataFrame()


# 6. 技術指標計算邏輯
def calculate_kd_rsi(df, kd_p, k_s, d_s, r_short, r_long):
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain_s = gain.ewm(alpha=1 / r_short, adjust=False).mean()
    avg_loss_s = loss.ewm(alpha=1 / r_short, adjust=False).mean()
    df[f"RSI_{r_short}"] = 100 - (100 / (1 + (avg_gain_s / avg_loss_s)))

    avg_gain_l = gain.ewm(alpha=1 / r_long, adjust=False).mean()
    avg_loss_l = loss.ewm(alpha=1 / r_long, adjust=False).mean()
    df[f"RSI_{r_long}"] = 100 - (100 / (1 + (avg_gain_l / avg_loss_l)))

    low_min = df["min"].rolling(window=kd_p).min()
    high_max = df["max"].rolling(window=kd_p).max()

    df["RSV"] = (df["close"] - low_min) / (high_max - low_min) * 100
    df["RSV"] = df["RSV"].fillna(50)

    k_w1, k_w2 = (k_s - 1) / k_s, 1 / k_s
    d_w1, d_w2 = (d_s - 1) / d_s, 1 / d_s

    k_list, d_list = [50.0], [50.0]
    for rsv in df["RSV"].iloc[1:]:
        k = k_w1 * k_list[-1] + k_w2 * rsv
        d = d_w1 * d_list[-1] + d_w2 * k
        k_list.append(k)
        d_list.append(d)

    df["K"] = k_list
    df["D"] = d_list
    return df


# 7. 主程式掃描邏輯
is_full = "全上市市場" in scan_mode
all_stocks = get_twse_stocks(full_market=is_full)
st.sidebar.info(f"當前模式載入標的：**{len(all_stocks)}** 檔")

if st.button(f"🚀 開始 FinMind 資料庫掃描 ({len(all_stocks)} 檔標的)", use_container_width=True):
    raw_results = []
    progress_bar = st.progress(0)
    status_text = st.empty()

    total_stocks = len(all_stocks)

    for idx, s in enumerate(all_stocks):
        code = s["Code"]
        name = s["Name"]

        progress = min((idx + 1) / total_stocks, 1.0)
        progress_bar.progress(progress)
        status_text.text(f"⏳ [FinMind 讀取中] ({idx+1}/{total_stocks})：{code} {name}...")

        df_stock = fetch_finmind_daily(code, token=finmind_token)

        if not df_stock.empty and len(df_stock) >= max(kd_period, rsi_long) + 5 and "close" in df_stock.columns:
            try:
                df_calc = calculate_kd_rsi(df_stock, kd_period, k_smooth, d_smooth, rsi_short, rsi_long)
                latest = df_calc.iloc[-1]

                raw_results.append({
                    "股票代號": code,
                    "股票名稱": name,
                    "收盤價": round(float(latest["close"]), 2),
                    "K": round(float(latest["K"]), 2),
                    "D": round(float(latest["D"]), 2),
                    "RSI_S": round(float(latest[f"RSI_{rsi_short}"]), 2),
                    "RSI_L": round(float(latest[f"RSI_{rsi_long}"]), 2),
                })
            except Exception:
                continue

    progress_bar.empty()

    if len(raw_results) > 0:
        status_text.success(f"🎉 FinMind 資料庫掃描完成！成功分析 {len(raw_results)} 檔股票數據。")
        st.session_state.scan_data = pd.DataFrame(raw_results)
    else:
        status_text.error("⚠️ 未能成功取得數據，請確認網路連線狀況。")

# 8. 即時過濾區 (記憶庫快取)
if st.session_state.scan_data is not None and not st.session_state.scan_data.empty:
    df_all = st.session_state.scan_data.copy()

    matched_mask = (
        (df_all["K"] < k_threshold)
        & (df_all["RSI_S"] < rsi_s_threshold)
        & (df_all["RSI_L"] < rsi_l_threshold)
    )

    df_all["狀態"] = matched_mask.map({True: "🎯 符合自訂條件", False: "觀察中"})

    if only_matched:
        display_df = df_all[df_all["狀態"] == "🎯 符合自訂條件"].copy()
    else:
        display_df = df_all.copy()

    display_df = display_df.rename(
        columns={
            "K": f"K({kd_period})",
            "D": f"D({kd_period})",
            "RSI_S": f"RSI({rsi_short})",
            "RSI_L": f"RSI({rsi_long})",
        }
    )

    col1, col2 = st.columns(2)
    col1.metric("已成功分析總檔數", f"{len(df_all)} 檔")
    matched_count = int(matched_mask.sum())
    col2.metric("符合目前滑桿條件個股", f"{matched_count} 檔")

    st.subheader(f"📊 即時篩選結果 (條件：K < {k_threshold}, RSI({rsi_short}) < {rsi_s_threshold}, RSI({rsi_long}) < {rsi_l_threshold})")
    st.dataframe(display_df, use_container_width=True)
else:
    st.info("💡 請點擊上方『🚀 開始 FinMind 資料庫掃描』按鈕載入最新市場數據。載入後拉動滑桿可 0 秒即時篩選！")
