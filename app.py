import datetime
import json
import ssl
import time
import urllib.request
import pandas as pd
import requests
import streamlit as st

# 1. 頁面基本設定
st.set_page_config(
    page_title="台股全市場 KD, RSI & 大戶籌碼篩選器",
    page_icon="📈",
    layout="wide",
)

st.title("🌐 台股全市場 KD、RSI & 集保大戶籌碼彈性篩選器")
st.caption(
    "資料來源：FinMind 台灣金融資料庫 ｜ 支援集保大戶持股比例、近週增減與連續增減週數計算"
)

# 2. 常用核心個股中文名稱字典
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
    "FinMind API Token (建議填寫，可大幅提升速額度):",
    type="password",
    help=(
        "免費申請：可在 FinMind 官網註冊取得 Token，上限可提升至 600 次/分！"
    ),
)

st.sidebar.header("⚙️ 2. 技術指標週期設定")
with st.sidebar.expander("📊 KD 指標週期設定", expanded=False):
  kd_period = st.number_input(
      "KD 週期 (N日)", min_value=3, max_value=60, value=9, step=1
  )
  k_smooth = st.number_input(
      "K 平滑分母 (預設 3 即 1/3 權重)",
      min_value=2,
      max_value=10,
      value=3,
      step=1,
  )
  d_smooth = st.number_input(
      "D 平滑分母 (預設 3 即 1/3 權重)",
      min_value=2,
      max_value=10,
      value=3,
      step=1,
  )

with st.sidebar.expander("📈 RSI 指標週期設定", expanded=False):
  rsi_short = st.number_input(
      "短天期 RSI 週期", min_value=2, max_value=30, value=5, step=1
  )
  rsi_long = st.number_input(
      "長天期 RSI 週期", min_value=2, max_value=60, value=10, step=1
  )

st.sidebar.header("🐋 3. 集保大戶籌碼門檻")
enable_big_holder_filter = st.sidebar.checkbox(
    "啟用大戶條件過濾", value=False
)
big_holder_level = st.sidebar.selectbox(
    "大戶持股等級定義：", ["1,000張以上", "800張以上", "400張以上"], index=0
)

min_big_pct = st.sidebar.slider(
    "大戶持股比例高於 (%)", min_value=0, max_value=90, value=40, step=5
)
min_weekly_change = st.sidebar.slider(
    "近週持股增加至少 (%)",
    min_value=-5.0,
    max_value=5.0,
    value=0.0,
    step=0.1,
)
min_streak_weeks = st.sidebar.slider(
    "大戶連續增加至少 (週)", min_value=0, max_value=8, value=0, step=1
)

st.sidebar.header("🎛️ 4. 技術指標門檻條件")
k_threshold = st.sidebar.slider(
    "K 值低於 (超跌門檻)", min_value=5, max_value=95, value=25, step=1
)
rsi_s_threshold = st.sidebar.slider(
    f"RSI({rsi_short}) 低於", min_value=5, max_value=95, value=25, step=1
)
rsi_l_threshold = st.sidebar.slider(
    f"RSI({rsi_long}) 低於", min_value=5, max_value=95, value=25, step=1
)

only_matched = st.sidebar.checkbox("僅顯示符合條件的股票", value=True)


# 4. 取得股票清單 (串接證交所 OpenAPI)
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
      name = twse_dict.get(code, POPULAR_STOCKS_MAP.get(code, f"股票{code}"))
      popular_stocks.append({"Code": code, "Name": name})
    return popular_stocks

  if twse_dict:
    return [{"Code": code, "Name": name} for code, name in twse_dict.items()]
  else:
    return [
        {"Code": code, "Name": name}
        for code, name in POPULAR_STOCKS_MAP.items()
    ]


# 5. 從 FinMind API 擷取日 K 線與大戶持股資料 (加上 Cache 防止連線爆掉)
def fetch_finmind_daily(stock_id, token=""):
  start_date = (
      datetime.datetime.now() - datetime.timedelta(days=90)
  ).strftime("%Y-%m-%d")
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
      return df.sort_values("date").reset_index(drop=True)
  except Exception:
    pass
  return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_finmind_big_holders(stock_id, token="", level_str="1,000張以上"):
  """擷取 FinMind 集保戶股數分級統計表，計算大戶比例、近週增減與連續增減週數"""
  start_date = (
      datetime.datetime.now() - datetime.timedelta(days=120)
  ).strftime("%Y-%m-%d")
  url = "https://api.finmindtrade.com/api/v4/data"
  params = {
      "dataset": "TaiwanStockHoldingSharesPer",
      "data_id": stock_id,
      "start_date": start_date,
  }
  if token:
    params["token"] = token

  # FinMind HoldingPLevel 分級對應: 15 (1000張以上), 14 (800-1000張), 12~13 (400-800張)
  if level_str == "1,000張以上":
    target_levels = [15]
  elif level_str == "800張以上":
    target_levels = [14, 15]
  elif level_str == "400張以上":
    target_levels = [12, 13, 14, 15]
  else:
    target_levels = [15]

  try:
    res = requests.get(url, params=params, timeout=5)
    data = res.json()
    if data.get("status") == 200 and data.get("data"):
      df = pd.DataFrame(data["data"])
      if df.empty or "HoldingPLevel" not in df.columns:
        return {"latest_pct": 0.0, "weekly_change": 0.0, "streak": 0}

      # 修正型態相容問題
      df["HoldingPLevel"] = pd.to_numeric(
          df["HoldingPLevel"], errors="coerce"
      )
      df["percent"] = pd.to_numeric(df["percent"], errors="coerce")

      df_big = df[df["HoldingPLevel"].isin(target_levels)]
      if df_big.empty:
        return {"latest_pct": 0.0, "weekly_change": 0.0, "streak": 0}

      # 依日期加總該等級大戶總持股比例
      df_weekly = df_big.groupby("date")["percent"].sum().reset_index()
      df_weekly = df_weekly.sort_values("date").reset_index(drop=True)

      if len(df_weekly) == 0:
        return {"latest_pct": 0.0, "weekly_change": 0.0, "streak": 0}

      latest_pct = float(df_weekly.iloc[-1]["percent"])
      if len(df_weekly) < 2:
        return {
            "latest_pct": round(latest_pct, 2),
            "weekly_change": 0.0,
            "streak": 0,
        }

      prev_pct = float(df_weekly.iloc[-2]["percent"])
      weekly_change = latest_pct - prev_pct

      # 計算連續增減週數
      df_weekly["diff"] = df_weekly["percent"].diff()
      diffs = df_weekly["diff"].dropna().tolist()

      streak = 0
      if len(diffs) > 0:
        last_diff = diffs[-1]
        if last_diff > 0:
          for d in reversed(diffs):
            if d > 0:
              streak += 1
            else:
              break
        elif last_diff < 0:
          for d in reversed(diffs):
            if d < 0:
              streak -= 1
            else:
              break

      return {
          "latest_pct": round(latest_pct, 2),
          "weekly_change": round(weekly_change, 2),
          "streak": streak,
      }
  except Exception:
    pass
  return {"latest_pct": 0.0, "weekly_change": 0.0, "streak": 0}


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

if st.button(
    f"🚀 開始 FinMind 數據掃描 ({len(all_stocks)} 檔標的)",
    use_container_width=True,
):
  raw_results = []
  progress_bar = st.progress(0)
  status_text = st.empty()

  total_stocks = len(all_stocks)

  for idx, s in enumerate(all_stocks):
    code = s["Code"]
    name = s["Name"]

    progress = min((idx + 1) / total_stocks, 1.0)
    progress_bar.progress(progress)
    status_text.text(
        f"⏳ [FinMind 讀取中] ({idx+1}/{total_stocks})：{code} {name}..."
    )

    df_stock = fetch_finmind_daily(code, token=finmind_token)
    big_holder_info = fetch_finmind_big_holders(
        code, token=finmind_token, level_str=big_holder_level
    )

    if (
        not df_stock.empty
        and len(df_stock) >= max(kd_period, rsi_long) + 5
        and "close" in df_stock.columns
    ):
      try:
        df_calc = calculate_kd_rsi(
            df_stock,
            kd_period,
            k_smooth,
            d_smooth,
            rsi_short,
            rsi_long,
        )
        latest = df_calc.iloc[-1]

        raw_results.append({
            "股票代號": code,
            "股票名稱": name,
            "收盤價": round(float(latest["close"]), 2),
            "K": round(float(latest["K"]), 2),
            "D": round(float(latest["D"]), 2),
            "RSI_S": round(float(latest[f"RSI_{rsi_short}"]), 2),
            "RSI_L": round(float(latest[f"RSI_{rsi_long}"]), 2),
            "大戶持股比例(%)": big_holder_info["latest_pct"],
            "近週持股增減(%)": big_holder_info["weekly_change"],
            "大戶連續週數": big_holder_info["streak"],
        })
      except Exception:
        continue

    # 無 Token 時微調連線間隔，防止頻率過快被鎖
    if not finmind_token:
      time.sleep(0.12)

  progress_bar.empty()

  if len(raw_results) > 0:
    status_text.success(
        f"🎉 FinMind 資料庫掃描完成！成功分析 {len(raw_results)}"
        " 檔股票技術面與大戶籌碼。"
    )
    st.session_state.scan_data = pd.DataFrame(raw_results)
  else:
    status_text.error("⚠️ 未能成功取得數據，請確認網路連線狀況。")

# 8. 即時動態過濾區 (記憶庫快取)
if st.session_state.scan_data is not None and not st.session_state.scan_data.empty:
  df_all = st.session_state.scan_data.copy()

  tech_mask = (
      (df_all["K"] < k_threshold)
      & (df_all["RSI_S"] < rsi_s_threshold)
      & (df_all["RSI_L"] < rsi_l_threshold)
  )

  if enable_big_holder_filter:
    chip_mask = (
        (df_all["大戶持股比例(%)"] >= min_big_pct)
        & (df_all["近週持股增減(%)"] >= min_weekly_change)
        & (df_all["大戶連續週數"] >= min_streak_weeks)
    )
    matched_mask = tech_mask & chip_mask
  else:
    matched_mask = tech_mask

  df_all["狀態"] = matched_mask.map(
      {True: "🎯 符合自訂條件", False: "觀察中"}
  )

  def format_streak(s):
    if s > 0:
      return f"連增 {s} 週 🟢"
    elif s < 0:
      return f"連減 {abs(s)} 週 🔴"
    else:
      return "持平 ⚪"

  df_all["連續增減週數"] = df_all["大戶連續週數"].apply(format_streak)

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
          "大戶持股比例(%)": f"大戶比例({big_holder_level})",
      }
  )

  col_order = [
      "股票代號",
      "股票名稱",
      "收盤價",
      f"K({kd_period})",
      f"D({kd_period})",
      f"RSI({rsi_short})",
      f"RSI({rsi_long})",
      f"大戶比例({big_holder_level})",
      "近週持股增減(%)",
      "連續增減週數",
      "狀態",
  ]
  display_cols = [c for c in col_order if c in display_df.columns]
  display_df = display_df[display_cols]

  col1, col2 = st.columns(2)
  col1.metric("已成功分析總檔數", f"{len(df_all)} 檔")
  matched_count = int(matched_mask.sum())
  col2.metric("符合目前篩選條件個股", f"{matched_count} 檔")

  st.subheader("📊 即時篩選結果明細")
  st.dataframe(display_df, use_container_width=True)
else:
  st.info(
      "💡 請點擊上方『🚀 開始 FinMind"
      " 數據掃描』按鈕載入最新技術面與大戶籌碼數據。"
  )
