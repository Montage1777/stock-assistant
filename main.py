import os
import time
import requests
import akshare as ak
import pandas as pd
from datetime import datetime


PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN")


def push_to_wechat(title, content):
    """
    推送到微信
    """
    if not PUSHPLUS_TOKEN:
        print("没有配置 PUSHPLUS_TOKEN")
        return

    url = "http://www.pushplus.plus/send"
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "html"
    }

    try:
        r = requests.post(url, json=data, timeout=20)
        print("推送结果：", r.text)
    except Exception as e:
        print("推送失败：", e)


def get_stock_pool():
    """
    获取股票池，带重试机制
    """
    last_error = None

    for i in range(5):
        try:
            print(f"第 {i + 1} 次尝试获取A股列表...")

            df = ak.stock_zh_a_spot_em()

            if df is None or df.empty:
                raise Exception("获取到的股票列表为空")

            print("成功获取A股列表")
            print("字段：", list(df.columns))

            # 排除 ST
            df = df[~df["名称"].str.contains("ST", na=False)]

            # 排除退市
            df = df[~df["名称"].str.contains("退", na=False)]

            # 排除北交所，常见 8、4 开头
            df = df[~df["代码"].astype(str).str.startswith("8")]
            df = df[~df["代码"].astype(str).str.startswith("4")]

            # 排除异常价格
            df = df[df["最新价"].notna()]
            df = df[df["最新价"] > 0]

            # 如果有成交额字段，就按成交额排序
            if "成交额" in df.columns:
                df = df.sort_values("成交额", ascending=False)

            # 先只扫前500只，更稳
            df = df.head(500)

            return df

        except Exception as e:
            last_error = e
            print(f"第 {i + 1} 次获取股票池失败：{e}")
            time.sleep(10)

    raise Exception(f"连续5次获取股票池失败，最后错误：{last_error}")



def get_hist(code):
    """
    获取单只股票历史日线
    """
    try:
        end_date = datetime.now().strftime("%Y%m%d")

        hist = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date="20240101",
            end_date=end_date,
            adjust="qfq"
        )

        if hist is None or hist.empty:
            return None

        return hist.tail(120).copy()

    except Exception as e:
        print(f"{code} 获取失败：{e}")
        return None


def add_indicators(df):
    """
    计算指标
    """
    df = df.copy()

    df["MA5"] = df["收盘"].rolling(5).mean()
    df["MA10"] = df["收盘"].rolling(10).mean()
    df["MA20"] = df["收盘"].rolling(20).mean()
    df["MA60"] = df["收盘"].rolling(60).mean()
    df["VOL5"] = df["成交量"].rolling(5).mean()

    return df


def check_strategy(hist):
    """
    策略判断
    """
    if hist is None or len(hist) < 60:
        return False, ""

    hist = add_indicators(hist)

    latest = hist.iloc[-1]

    close = latest["收盘"]
    ma5 = latest["MA5"]
    ma10 = latest["MA10"]
    ma20 = latest["MA20"]
    vol = latest["成交量"]
    vol5 = latest["VOL5"]

    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20) or pd.isna(vol5):
        return False, ""

    reasons = []

    # 条件1：收盘价站上20日线
    cond1 = close > ma20
    if cond1:
        reasons.append("收盘价站上MA20")

    # 条件2：均线多头排列
    cond2 = ma5 > ma10 > ma20
    if cond2:
        reasons.append("MA5>MA10>MA20")

    # 条件3：成交量放大
    cond3 = vol > vol5 * 1.5
    if cond3:
        reasons.append("成交量大于5日均量1.5倍")

    # 条件4：20日涨幅小于40%
    close_20_days_ago = hist.iloc[-20]["收盘"]
    rise_20 = (close - close_20_days_ago) / close_20_days_ago * 100

    cond4 = rise_20 < 40
    if cond4:
        reasons.append(f"20日涨幅{rise_20:.2f}%，未过热")

    passed = cond1 and cond2 and cond3 and cond4

    if passed:
        return True, "；".join(reasons)

    return False, ""


def make_report(results):
    """
    生成微信报告
    """
    today = datetime.now().strftime("%Y-%m-%d")

    if not results:
        return f"{today}<br><br>今天没有符合策略的股票。<br><br>提示：仅为策略筛选，不构成投资建议。"

    lines = []
    lines.append(f"{today}<br>")
    lines.append(f"今日共筛出 {len(results)} 只股票。<br><br>")

    for i, item in enumerate(results[:30], 1):
        lines.append(
            f"{i}. {item['代码']} {item['名称']}<br>"
            f"最新价：{item['最新价']}<br>"
            f"涨跌幅：{item['涨跌幅']}%<br>"
            f"成交额：{item['成交额']}<br>"
            f"理由：{item['理由']}<br><br>"
        )

    if len(results) > 30:
        lines.append(f"仅展示前30只，其余 {len(results) - 30} 只未展示。<br><br>")

    lines.append("提示：以上仅为策略筛选结果，不构成投资建议。")

    return "\n".join(lines)


def main():
    print("开始获取股票池...")
    stock_pool = get_stock_pool()
    print(f"股票池数量：{len(stock_pool)}")

    results = []

    for idx, row in stock_pool.iterrows():
        code = str(row["代码"]).zfill(6)
        name = row["名称"]

        print(f"扫描 {code} {name}")

        hist = get_hist(code)
        passed, reason = check_strategy(hist)

        if passed:
            results.append({
                "代码": code,
                "名称": name,
                "最新价": row.get("最新价", ""),
                "涨跌幅": row.get("涨跌幅", ""),
                "成交额": row.get("成交额", ""),
                "理由": reason
            })

        # 防止请求太快
        time.sleep(0.12)

    report = make_report(results)

    print(report)

    push_to_wechat("今日A股策略筛选结果", report)


if __name__ == "__main__":
    main()
