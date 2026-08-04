import json
import ssl
import urllib.request
import pandas as pd
import streamlit as st
import yfinance as yf

# 1. 頁面基本設定
st.set_page_config(
    page_title="台股全市場低檔超賣篩選器", page_icon="📈", layout="wide"
)

st.title("🌐 台股上市全市場 KD(9,3,3) & RSI(5,10) 自動掃描器")
st.caption(
    "自動串接證交所 API 讀取全上市股票 (~1,000+ 檔) ｜ 免輸入代號 ｜"
    " 適合手機/電腦外出即時查詢"
)


# 2. 自動取得台灣證交所 (TWSE) 全上市股票代碼清單 (已修正 SSL 憑證問題)
@st.cache_data(ttl=14400)  # 快取 4 小時
def get_all_twse_stocks():
  url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
  req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
  try:
    # 關鍵修正：建立繞過 SSL 憑證檢查的 context，解決 CERTIFICATE_VERIFY_FAILED 錯誤
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(req, context=context) as response:
      data = json.loads(response.read().decode())
      df = pd.DataFrame(data)
      # 篩選標準 4 位數股票代號（排除權證、公司債等）
      df = df[df["Code"].str.match(r"^\d{4}$")]
      return df[["Code", "Name"]].to_dict("records")
  except Exception as e:
    st.error(f"自動抓取證交所股票清單失敗: {e}")
    # 備用清單
    return [
        {"Code": "2330", "Name": "台積電"},
        {"Code": "2317", "Name": "鴻海"},
        {"Code": "2454", "Name": "聯發科"},
    ]


# 3. 技術指標計算邏輯
def calculate_kd_rsi(df, kd_period=9, rsi_short=5, rsi_long=10):
  delta = df["Close"].diff()
  gain = delta.where(delta > 0, 0)
  loss = -delta.where(delta < 0, 0)

  avg_gain5 = gain.ewm(alpha=1 / rsi_short, adjust=False).mean()
  avg_loss5 = loss.ewm(alpha=1 / rsi_short, adjust=False).mean()
  df["RSI_5"] = 100 - (100 / (1 + (avg_gain5 / avg_loss5)))

  avg_gain10 = gain.ewm(alpha=1 / rsi_long, adjust=False).mean()
  avg_loss10 = loss.ewm(alpha=1 / rsi_long, adjust=False).mean()
  df["RSI_10"] = 100 - (100 / (1 + (avg_gain10 / avg_loss10)))

  low_min = df["Low"].rolling(window=kd_period).min()
  high_max = df["High"].rolling(window=kd_period).max()

  df["RSV"] = (df["Close"] - low_min) / (high_max - low_min) * 100
  df["RSV"] = df["RSV"].fillna(50)

  k_list, d_list = [50.0], [50.0]
  for rsv in df["RSV"].iloc[1:]:
    k = (2 / 3) * k_list[-1] + (1 / 3) * rsv
    d = (2 / 3) * d_list[-1] + (1 / 3) * k
    k_list.append(k)
    d_list.append(d)

  df["K"] = k_list
  df["D"] = d_list
  return df


# 4. 側邊欄設定
st.sidebar.header("⚙️ 篩選模式設定")
only_oversold = st.sidebar.checkbox(
    "僅顯示符合低檔區股票 (K, RSI5, RSI10 < 25)", value=True
)

# 載入股票清單
all_stocks = get_all_twse_stocks()
st.sidebar.info(f"目前證交所共載入：**{len(all_stocks)}** 檔上市股票")

# 5. 主按鈕：啟動全市場掃描
if st.button(
    f"🚀 開始全市場掃描 ({len(all_stocks)} 檔上市股票)",
    use_container_width=True,
):
  results = []

  # 設定進度條與狀態提示
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
        f"⏳ 正在掃描全台股市場：{min(i + chunk_size, total_stocks)} /"
        f" {total_stocks} 檔個股..."
    )

    try:
      data = yf.download(
          tickers, period="3m", group_by="ticker", progress=False
      )

      for s in chunk:
        code = s["Code"]
        name = s["Name"]
        ticker = f"{code}.TW"

        if len(chunk) == 1:
          df_stock = data.copy()
        elif ticker in data.columns.levels[0]:
          df_stock = data[ticker].dropna(how="all")
        else:
          continue

        if df_stock.empty or len(df_stock) < 15:
          continue

        if isinstance(df_stock.columns, pd.MultiIndex):
          df_stock.columns = df_stock.columns.get_level_values(0)

        df_calc = calculate_kd_rsi(df_stock)
        latest = df_calc.iloc[-1]

        k = float(latest["K"])
        d = float(latest["D"])
        r5 = float(latest["RSI_5"])
        r10 = float(latest["RSI_10"])
        close_p = float(latest["Close"])

        is_oversold = k < 25 and r5 < 25 and r10 < 25

        if not only_oversold or is_oversold:
          results.append({
              "股票代號": code,
              "股票名稱": name,
              "收盤價": round(close_p, 2),
              "K(9,3,3)": round(k, 2),
              "D(9,3,3)": round(d, 2),
              "RSI(5)": round(r5, 2),
              "RSI(10)": round(r10, 2),
              "狀態": "🎯 低檔區" if is_oversold else "觀察中",
          })
    except Exception as e:
      continue

  progress_bar.empty()
  status_text.success("🎉 全市場掃描完成！")

  if results:
    res_df = pd.DataFrame(results)

    col1, col2 = st.columns(2)
    col1.metric("已掃描市場標的", f"{total_stocks} 檔")
    oversold_count = len(res_df[res_df["狀態"] == "🎯 低檔區"])
    col2.metric("符合低檔超賣 (K, RSI < 25)", f"{oversold_count} 檔")

    st.subheader("📊 全市場低檔超賣個股篩選結果")
    st.dataframe(res_df, use_container_width=True)
  else:
    st.info("目前全台灣上市股票中，沒有符合低檔區 (K, RSI5, RSI10 < 25) 的股票。")