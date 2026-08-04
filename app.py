import json
import ssl
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
    "自動串接證交所 API 讀取全上市股票 ｜ 支援側邊欄自由調整 KD/RSI 參數與門檻"
)

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


# 4. 萬能股票 K 線欄位擷取函數（解決 MultiIndex 錯位問題）
def extract_stock_df(df_data, ticker_symbol):
  if not isinstance(df_data.columns, pd.MultiIndex):
    return df_data.copy()

  # 第一層是股票代號 (Level 0)
  if ticker_symbol in df_data.columns.get_level_values(0):
    return df_data[ticker_symbol].copy()
  # 第一層是價格種類，第二層是股票代號 (Level 1)
  elif ticker_symbol in df_data.columns.get_level_values(1):
    return df_data.xs(ticker_symbol, axis=1, level=1).copy()
  else:
    return pd.DataFrame()


# 5. 動態技術指標計算邏輯
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


# 6. 主程式掃描邏輯
all_stocks = get_all_twse_stocks()
st.sidebar.info(f"目前證交所共載入：**{len(all_stocks)}** 檔上市股票")

if st.button(
    f"🚀 開始全市場自訂條件掃描 ({len(all_stocks)} 檔上市股票)",
    use_container_width=True,
):
  results = []
  progress_bar = st.progress(0)
  status_text = st.empty()

  chunk_size = 50
  total_stocks = len(all_stocks)

  for i in range(0, total_stocks, chunk_size):
    chunk = all_stocks[i : i + chunk_size]
    tickers = [f"{s['Code']}.TW" for s in chunk]

    progress = min((i + chunk_size) / total_stocks, 1.0)
    progress_bar.progress(progress)
    status_text.text(
        f"⏳ 正在依自訂條件掃描：{min(i + chunk_size, total_stocks)} /"
        f" {total_stocks} 檔個股..."
    )

    try:
      # 批次抓取市場資料
      data = yf.download(tickers, period="3m", progress=False)

      for s in chunk:
        try:
          code = s["Code"]
          name = s["Name"]
          ticker = f"{code}.TW"

          # 提取單檔股票 DataFrame
          df_stock = extract_stock_df(data, ticker).dropna(how="all")

          if df_stock.empty or len(df_stock) < max(kd_period, rsi_long) + 5:
            continue

          # 確保欄位名稱正確
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

          k = float(latest["K"])
          d = float(latest["D"])
          r_s = float(latest[f"RSI_{rsi_short}"])
          r_l = float(latest[f"RSI_{rsi_long}"])
          close_p = float(latest["Close"])

          # 判斷門檻
          is_matched = (
              k < k_threshold
              and r_s < rsi_s_threshold
              and r_l < rsi_l_threshold
          )

          if not only_matched or is_matched:
            results.append({
                "股票代號": code,
                "股票名稱": name,
                "收盤價": round(close_p, 2),
                f"K({kd_period})": round(k, 2),
                f"D({kd_period})": round(d, 2),
                f"RSI({rsi_short})": round(r_s, 2),
                f"RSI({rsi_long})": round(r_l, 2),
                "狀態": "🎯 符合自訂條件" if is_matched else "觀察中",
            })
        except Exception:
          continue
    except Exception:
      continue

  progress_bar.empty()
  status_text.success("🎉 全市場自訂條件掃描完成！")

  if results:
    res_df = pd.DataFrame(results)

    col1, col2 = st.columns(2)
    col1.metric("已成功掃描標的", f"{len(res_df)} 檔")
    matched_count = len(res_df[res_df["狀態"] == "🎯 符合自訂條件"])
    col2.metric("符合條件個股", f"{matched_count} 檔")

    st.subheader(
        f"📊 篩選結果 (條件：K < {k_threshold}, RSI({rsi_short}) <"
        f" {rsi_s_threshold}, RSI({rsi_long}) < {rsi_l_threshold})"
    )
    st.dataframe(res_df, use_container_width=True)
  else:
    st.info("目前全台灣上市股票中，沒有符合您設定門檻的股票。")
