import json
import ssl
import time
import urllib.request
import pandas as pd
import streamlit as st
import yfinance as yf

# 1. 頁面基本設定
st.set_page_config(
    page_title="台股全市場 KD & RSI 彈性自訂篩選器",
    page_icon="📈",
    layout="wide",
)

st.title("🌐 台股上市全市場 KD & RSI 彈性自訂條件篩選器")
st.caption(
    "自動串接證交所 API 讀取全上市股票 ｜ 支援記憶庫快取，拉動滑桿可即時篩選"
)

# 初始化 Session State (記憶庫)
if "scan_data" not in st.session_state:
  st.session_state.scan_data = None


# 2. 側邊欄：自訂指標參數與門檻控制區
st.sidebar.header("⚙️ 1. 技術指標週期設定")

with st.sidebar.expander("📊 KD 指標週期設定", expanded=True):
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

with st.sidebar.expander("📈 RSI 指標週期設定", expanded=True):
  rsi_short = st.number_input(
      "短天期 RSI 週期", min_value=2, max_value=30, value=5, step=1
  )
  rsi_long = st.number_input(
      "長天期 RSI 週期", min_value=2, max_value=60, value=10, step=1
  )

st.sidebar.header("🎯 2. 篩選門檻條件設定")

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


# 3. 證交所 API 全上市股票清單擷取
@st.cache_data(ttl=14400)
def get_all_twse_stocks():
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
    st.error(f"自動抓取證交所股票清單失敗: {e}")
    return [
        {"Code": "2330", "Name": "台積電"},
        {"Code": "2317", "Name": "鴻海"},
        {"Code": "2454", "Name": "聯發科"},
    ]


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


# 5. 主程式處理
all_stocks = get_all_twse_stocks()
st.sidebar.info(f"目前證交所共載入：**{len(all_stocks)}** 檔上市股票")

# 重新掃描按鈕
if st.button(
    f"🚀 開始全市場掃描與更新數據 ({len(all_stocks)} 檔)",
    use_container_width=True,
):
  raw_results = []
  progress_bar = st.progress(0)
  status_text = st.empty()

  chunk_size = 30  # 縮小每批數量，避免被防爬蟲擋
  total_stocks = len(all_stocks)
  failed_count = 0

  for i in range(0, total_stocks, chunk_size):
    chunk = all_stocks[i : i + chunk_size]
    tickers = [f"{s['Code']}.TW" for s in chunk]

    progress = min((i + chunk_size) / total_stocks, 1.0)
    progress_bar.progress(progress)
    status_text.text(
        f"⏳ 正在抓取全台股數據：{min(i + chunk_size, total_stocks)} /"
        f" {total_stocks} 檔..."
    )

    try:
      # 多次輕量抓取
      data = yf.download(tickers, period="3m", progress=False)

      for s in chunk:
        try:
          code = s["Code"]
          name = s["Name"]
          ticker = f"{code}.TW"

          # 解析子表格
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
            failed_count += 1
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
          failed_count += 1
          continue
    except Exception:
      failed_count += chunk_size
      continue

    time.sleep(0.1)  # 稍微停頓，防止被 Yahoo Rate Limit 切斷

  progress_bar.empty()
  status_text.success(
      f"🎉 數據更新完成！成功抓取 {len(raw_results)} 檔股票數據。"
  )

  # 存入 Session State 記憶庫
  st.session_state.scan_data = pd.DataFrame(raw_results)

# 6. 動態即時篩選區（從記憶庫直接過濾，拉滑桿瞬間完成）
if st.session_state.scan_data is not None and not st.session_state.scan_data.empty:
  df_all = st.session_state.scan_data.copy()

  # 依當前滑桿即時過濾
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

  # 重新命名顯示欄位
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
  st.info("💡 請點擊上方『🚀 開始全市場掃描與更新數據』按鈕載入最新市場數據。")
