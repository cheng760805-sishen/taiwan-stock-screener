import json
import ssl
import time
import urllib.request
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# 1. 頁面基本設定
st.set_page_config(
    page_title="台股全市場 KD & RSI 彈性自訂篩選器",
    page_icon="📈",
    layout="wide",
)

st.title("🌐 台股上市全市場 KD & RSI 彈性自訂條件篩選器")
st.caption("支援記憶庫快取與防連線封鎖機制 ｜ 拉動滑桿即可即時過濾")

# 初始化 Session State (記憶庫)
if "scan_data" not in st.session_state:
  st.session_state.scan_data = None

# 2. 側邊欄：掃描模式與指標設定
st.sidebar.header("🎯 1. 掃描範圍設定")
scan_mode = st.sidebar.radio(
    "選擇掃描模式：",
    ["⚡ 核心熱門個股 (~100 檔, 掃描極快/不封鎖)", "🌐 全上市市場 (~1,000+ 檔)"],
    index=0,
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

st.sidebar.header("🎛️ 3. 篩選門檻條件設定")
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


# 3. 取得股票清單 (證交所 API)
@st.cache_data(ttl=14400)
def get_twse_stocks(full_market=True):
  if not full_market:
    # 常用熱門核心股清單 (約 100 檔)
    popular_codes = [
        "2330",
        "2317",
        "2454",
        "2308",
        "2382",
        "2881",
        "2882",
        "2412",
        "2303",
        "3008",
        "2603",
        "2609",
        "2615",
        "2002",
        "1101",
        "1301",
        "1303",
        "2886",
        "2891",
        "5880",
        "3231",
        "6669",
        "2357",
        "2379",
        "3034",
        "2377",
        "2301",
        "2345",
        "3711",
        "2408",
        "3037",
        "2376",
        "2301",
        "2356",
        "2409",
        "3481",
        "1216",
        "2912",
        "9910",
        "1402",
        "2105",
        "2207",
        "2618",
        "2610",
        "2880",
        "2883",
        "2884",
        "2885",
        "2887",
        "2890",
        "2892",
        "5876",
        "6005",
    ]
    return [{"Code": c, "Name": f"股票{c}"} for c in popular_codes]

  url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
  req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
  try:
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(req, context=context) as response:
      data = json.loads(response.read().decode())
      df = pd.DataFrame(data)
      df = df[df["Code"].str.match(r"^\d{4}$")]
      return df[["Code", "Name"]].to_dict("records")
  except Exception as e:
    st.error(f"連線證交所失敗: {e}")
    return [{"Code": "2330", "Name": "台積電"}]


# 4. 技術指標計算邏輯
def calculate_kd_rsi(df, kd_p, k_s, d_s, r_short, r_long):
  delta = df["Close"].diff()
  gain = delta.where(delta > 0, 0)
  loss = -delta.where(delta < 0, 0)

  avg_gain_s = gain.ewm(alpha=1 / r_short, adjust=False).mean()
  avg_loss_s = loss.ewm(alpha=1 / r_short, adjust=False).mean()
  df[f"RSI_{r_short}"] = 100 - (100 / (1 + (avg_gain_s / avg_loss_s)))

  avg_gain_l = gain.ewm(alpha=1 / r_long, adjust=False).mean()
  avg_loss_l = loss.ewm(alpha=1 / r_long, adjust=False).mean()
  df[f"RSI_{r_long}"] = 100 - (100 / (1 + (avg_gain_l / avg_loss_l)))

  low_min = df["Low"].rolling(window=kd_p).min()
  high_max = df["High"].rolling(window=kd_p).max()

  df["RSV"] = (df["Close"] - low_min) / (high_max - low_min) * 100
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


# 5. 掃描執行邏輯
is_full = "全上市市場" in scan_mode
all_stocks = get_twse_stocks(full_market=is_full)
st.sidebar.info(f"當前模式載入標的：**{len(all_stocks)}** 檔")

if st.button(
    f"🚀 開始掃描數據 ({len(all_stocks)} 檔標的)", use_container_width=True
):
  raw_results = []
  progress_bar = st.progress(0)
  status_text = st.empty()

  # 熱門模式單批 50 檔，全市場模式單批 20 檔（降低 Rate Limit）
  chunk_size = 20 if is_full else 50
  total_stocks = len(all_stocks)

  for i in range(0, total_stocks, chunk_size):
    chunk = all_stocks[i : i + chunk_size]
    tickers = [f"{s['Code']}.TW" for s in chunk]

    progress = min((i + chunk_size) / total_stocks, 1.0)
    progress_bar.progress(progress)
    status_text.text(
        f"⏳ 正在抓取市場行情：{min(i + chunk_size, total_stocks)} /"
        f" {total_stocks} 檔..."
    )

    try:
      # 使用抓取歷史行情
      data = yf.download(tickers, period="3m", progress=False)

      for s in chunk:
        try:
          code = s["Code"]
          name = s["Name"]
          ticker = f"{code}.TW"

          if isinstance(data.columns, pd.MultiIndex):
            if ticker in data.columns.get_level_values(0):
              df_stock = data[ticker].dropna(how="all")
            elif ticker in data.columns.get_level_values(1):
              df_stock = data.xs(ticker, axis=1, level=1).dropna(how="all")
            else:
              df_stock = pd.DataFrame()
          else:
            df_stock = data.dropna(how="all")

          if df_stock.empty or len(df_stock) < max(kd_period, rsi_long) + 5:
            continue

          if isinstance(df_stock.columns, pd.MultiIndex):
            df_stock.columns = df_stock.columns.get_level_values(0)

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
              "收盤價": round(float(latest["Close"]), 2),
              "K": round(float(latest["K"]), 2),
              "D": round(float(latest["D"]), 2),
              "RSI_S": round(float(latest[f"RSI_{rsi_short}"]), 2),
              "RSI_L": round(float(latest[f"RSI_{rsi_long}"]), 2),
          })
        except Exception:
          continue
    except Exception as e:
      st.warning(f"批次抓取提示: {e}")

    # 若是全市場模式，加強延遲防止被 Yahoo 封鎖
    if is_full:
      time.sleep(0.3)

  progress_bar.empty()

  if len(raw_results) > 0:
    status_text.success(
        f"🎉 數據更新完成！成功抓取 {len(raw_results)} 檔股票數據。"
    )
    st.session_state.scan_data = pd.DataFrame(raw_results)
  else:
    status_text.error(
        "⚠️ 抓取失敗（0 檔）。原因為短時間連線過於頻繁，觸發了 Yahoo"
        " 伺服器的冷卻保護。請等待 2~3 分鐘後再重新點擊！"
    )

# 6. 動態即時過濾區
if st.session_state.scan_data is not None and not st.session_state.scan_data.empty:
  df_all = st.session_state.scan_data.copy()

  matched_mask = (
      (df_all["K"] < k_threshold)
      & (df_all["RSI_S"] < rsi_s_threshold)
      & (df_all["RSI_L"] < rsi_l_threshold)
  )

  df_all["狀態"] = matched_mask.map(
      {True: "🎯 符合自訂條件", False: "觀察中"}
  )

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

  st.subheader(
      f"📊 即時篩選結果 (條件：K < {k_threshold}, RSI({rsi_short}) <"
      f" {rsi_s_threshold}, RSI({rsi_long}) < {rsi_l_threshold})"
  )
  st.dataframe(display_df, use_container_width=True)
else:
  st.info("💡 請點擊上方按鈕載入數據。如剛觸發連線過頻，請稍微等待 2 分鐘。")
