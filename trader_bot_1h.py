import requests
import json
import datetime
import time
import os
import threading
import xml.etree.ElementTree as ET
from flask import Flask

# ==========================================================
# НАСТРОЙКИ
# ==========================================================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

MODEL = "deepseek/deepseek-v4-pro"

# Главные монеты для Советника
MAIN_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

NEWS_CACHE = {"last_update": 0, "headlines": [], "sentiment": "Не определён"}

RSS_FEEDS = [
    "https://cointelegraph.com/feed",
    "https://www.coindesk.com/feed/",
    "https://cryptonews.com/news/feed/"
]

# ==========================================================
# ФУНКЦИИ ДЛЯ НОВОСТЕЙ
# ==========================================================
def fetch_rss_headlines():
    headlines = []
    for feed_url in RSS_FEEDS:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(feed_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                for item in root.findall('.//item'):
                    title_elem = item.find('title')
                    if title_elem is not None and title_elem.text:
                        headlines.append(title_elem.text.strip())
        except Exception as e:
            print(f"⚠️ Ошибка парсинга {feed_url}: {e}")
    unique_headlines = list(dict.fromkeys(headlines))[:3]
    return unique_headlines

def analyze_news_sentiment(news_text):
    if not news_text:
        return "Новостей нет"
    prompt = f"""
Проанализируй следующие новости криптовалютного рынка:
{news_text}
Дай краткую оценку: позитивный, нейтральный или негативный фон для рынка.
Ответь строго одним словом: ПОЗИТИВНЫЙ, НЕЙТРАЛЬНЫЙ или НЕГАТИВНЫЙ.
"""
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    data = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 100}
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=30)
        result = resp.json()
        if "error" in result:
            print(f"⚠️ Ошибка нейросети: {result['error']}")
            return "Не удалось оценить"
        raw = (result['choices'][0]['message']['content'] or "").strip().upper()
        if "ПОЗИТИВ" in raw:
            return "Позитивный"
        if "НЕГАТИВ" in raw:
            return "Негативный"
        return "Нейтральный"
    except Exception as e:
        print(f"❌ Ошибка вызова нейросети: {e}")
        return "Не удалось оценить"

def update_news_cache():
    global NEWS_CACHE
    now = time.time()
    if now - NEWS_CACHE["last_update"] < 1800:
        return NEWS_CACHE["headlines"], NEWS_CACHE["sentiment"]
    print("📰 Сканирую RSS-ленты для свежих новостей...")
    new_headlines = fetch_rss_headlines()
    if new_headlines:
        news_text = "\n".join(new_headlines)
        print("🧠 Запрашиваю оценку фона у нейросети...")
        sentiment = analyze_news_sentiment(news_text)
        NEWS_CACHE = {"last_update": now, "headlines": new_headlines, "sentiment": sentiment}
        print(f"📰 Новости обновлены: {new_headlines}")
        print(f"🧠 Оценка фона: {sentiment}")
    else:
        print("⚠️ Не удалось получить свежие новости.")
    return NEWS_CACHE["headlines"], NEWS_CACHE["sentiment"]

# ==========================================================
# БАЗОВЫЕ ФУНКЦИИ
# ==========================================================
app = Flask(__name__)

def is_working_hours():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    hour_ekb = (now_utc.hour + 5) % 24
    return (hour_ekb >= 14) or (hour_ekb < 3)

def get_30m_candles(symbol, limit=50):
    try:
        url = f"https://api.mexc.com/api/v3/klines?symbol={symbol}&interval=30m&limit={limit}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            candles = []
            for candle in data:
                candles.append({
                    'high': float(candle[2]),
                    'low': float(candle[3]),
                    'close': float(candle[4])
                })
            return candles
        return None
    except:
        return None

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text}, timeout=5)
    except:
        pass

# Расчет RSI (14)
def calculate_rsi(candles, period=14):
    if len(candles) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(candles)):
        change = candles[i]['close'] - candles[i-1]['close']
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# Расчет MACD (12, 26, 9)
def calculate_macd(candles, fast=12, slow=26, signal=9):
    if len(candles) < slow + signal:
        return None, None
    closes = [c['close'] for c in candles]
    # EMA расчет
    def ema(values, period):
        k = 2 / (period + 1)
        ema = values[0]
        for i in range(1, len(values)):
            ema = (values[i] - ema) * k + ema
        return ema
    ema_fast = [ema(closes[:i+1], fast) for i in range(len(closes))]
    ema_slow = [ema(closes[:i+1], slow) for i in range(len(closes))]
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    # Сигнальная линия
    signal_line = ema(macd_line, signal)
    # Отношение текущей MACD к сигнальной
    current_macd = macd_line[-1]
    current_signal = signal_line
    return current_macd, current_signal

# Расчет ADX, DI+, DI-
def calculate_adx_di(candles, period=14):
    if len(candles) < period + 1:
        return None, None, None
    tr_list = []
    plus_dm_list = []
    minus_dm_list = []
    for i in range(1, len(candles)):
        high = candles[i]['high']
        low = candles[i]['low']
        prev_high = candles[i-1]['high']
        prev_low = candles[i-1]['low']
        prev_close = candles[i-1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        plus_dm = high - prev_high if (high - prev_high) > (prev_low - low) else 0
        minus_dm = prev_low - low if (prev_low - low) > (high - prev_high) else 0
        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
    tr_smooth = sum(tr_list[:period])
    plus_dm_smooth = sum(plus_dm_list[:period])
    minus_dm_smooth = sum(minus_dm_list[:period])
    di_plus_values = []
    di_minus_values = []
    dx_values = []
    di_plus = 100 * (plus_dm_smooth / tr_smooth) if tr_smooth > 0 else 0
    di_minus = 100 * (minus_dm_smooth / tr_smooth) if tr_smooth > 0 else 0
    di_plus_values.append(di_plus)
    di_minus_values.append(di_minus)
    if (di_plus + di_minus) > 0:
        dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus)
    else:
        dx = 0
    dx_values.append(dx)
    for i in range(period, len(tr_list)):
        tr_smooth = tr_smooth - (tr_smooth / period) + tr_list[i]
        plus_dm_smooth = plus_dm_smooth - (plus_dm_smooth / period) + plus_dm_list[i]
        minus_dm_smooth = minus_dm_smooth - (minus_dm_smooth / period) + minus_dm_list[i]
        di_plus = 100 * (plus_dm_smooth / tr_smooth) if tr_smooth > 0 else 0
        di_minus = 100 * (minus_dm_smooth / tr_smooth) if tr_smooth > 0 else 0
        di_plus_values.append(di_plus)
        di_minus_values.append(di_minus)
        if (di_plus + di_minus) > 0:
            dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus)
        else:
            dx = 0
        dx_values.append(dx)
    adx = sum(dx_values[:period]) / period
    adx_values = [adx]
    for i in range(period, len(dx_values)):
        adx = ((adx * (period - 1)) + dx_values[i]) / period
        adx_values.append(adx)
    return adx_values[-1], di_plus_values[-1], di_minus_values[-1]

# ==========================================================
# ТРЕНДОВЫЙ СОВЕТНИК (30 мин, 3 значения, мягкие фразы)
# ==========================================================
def trend_adviser():
    if not is_working_hours():
        return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ekb = now_utc + datetime.timedelta(hours=5)
    lines = []
    for sym in MAIN_SYMBOLS:
        candles = get_30m_candles(sym, limit=5)
        if not candles or len(candles) < 4:
            lines.append(f"{sym}: Нет данных")
            continue
        change_1 = (candles[-3]['close'] - candles[-4]['close']) / candles[-4]['close'] * 100
        change_2 = (candles[-2]['close'] - candles[-3]['close']) / candles[-3]['close'] * 100
        change_3 = (candles[-1]['close'] - candles[-2]['close']) / candles[-2]['close'] * 100
        if abs(change_3) < 0.05:
            label = "Боковик"
        elif change_3 > 0:
            if change_2 < -0.05:
                label = "Возможен разворот вверх"
            elif change_3 >= change_2:
                label = "Импульс вверх усиливается"
            else:
                label = "Восходящий импульс ослабевает"
        else:
            if change_2 > 0.05:
                label = "Возможен разворот вниз"
            elif change_3 <= change_2:
                label = "Импульс вниз усиливается"
            else:
                label = "Нисходящий импульс ослабевает"
        lines.append(f"{sym}: {change_1:+.2f}% | {change_2:+.2f}% | {change_3:+.2f}% | {label}")
    msg = f"📊 ТРЕНД 30М ({now_ekb.strftime('%H:%M')}):\n" + "\n".join(lines)
    send_telegram(msg)
    print("📊 Советник отправлен!")

# ==========================================================
# МАРШАЛ (BTC, 30 мин, ADX, DI+/DI-, RSI, MACD)
# ==========================================================
def marshal_btc():
    if not is_working_hours():
        return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ekb = now_utc + datetime.timedelta(hours=5)
    candles = get_30m_candles("BTCUSDT", limit=50)
    if not candles or len(candles) < 30:
        return
    adx_current, di_plus, di_minus = calculate_adx_di(candles[:-1], period=14)
    adx_prev, _, _ = calculate_adx_di(candles[:-2], period=14)
    if adx_current is None or di_plus is None or di_minus is None:
        return
    if di_plus > di_minus:
        direction = f"ВВЕРХ (DI+ {di_plus:.0f} / DI- {di_minus:.0f})"
    else:
        direction = f"ВНИЗ (DI+ {di_plus:.0f} / DI- {di_minus:.0f})"
    if adx_prev is not None:
        strength = f"ADX {adx_current:.0f} (было {adx_prev:.0f})"
    else:
        strength = f"ADX {adx_current:.0f}"
    if adx_current > 40:
        duration = "Экстремальный тренд, может продержаться еще 1-2 часа."
    elif adx_current > 25:
        duration = "Сильный тренд, ожидается продолжение еще 2-3 часа."
    elif adx_current > 20:
        duration = "Умеренный тренд, может продлиться около 1 часа."
    else:
        duration = "Слабый тренд или боковик."

    # RSI
    rsi = calculate_rsi(candles[:-1], period=14)
    if rsi is not None:
        if rsi > 70:
            rsi_text = f"RSI {rsi:.0f} (Перекупленность)"
        elif rsi < 30:
            rsi_text = f"RSI {rsi:.0f} (Перепроданность)"
        else:
            rsi_text = f"RSI {rsi:.0f}"
    else:
        rsi_text = "Нет данных"

    # MACD
    current_macd, signal_line = calculate_macd(candles[:-1])
    if current_macd is not None and signal_line is not None:
        if current_macd > signal_line:
            macd_text = "MACD: выше сигнальной (Импульс вверх)"
        else:
            macd_text = "MACD: ниже сигнальной (Импульс вниз)"
    else:
        macd_text = "MACD: Нет данных"

    msg = f"📊 ТРЕНД BTC (Маршал) {now_ekb.strftime('%H:%M')}:\n"
    msg += f"🧭 Направление: {direction}\n"
    msg += f"💪 Сила тренда: {strength}\n"
    msg += f"⏳ Прогноз длительности: {duration}\n"
    msg += f"📉 RSI: {rsi_text}\n"
    msg += f"📈 MACD: {macd_text}"

    send_telegram(msg)
    print("🧠 Маршал (BTC) отправлен!")

# ==========================================================
# ФОНОВЫЙ ПОТОК (Каждые 30 минут, ровно в 00 и 30, без дублей)
# ==========================================================
def bg_alarm():
    print("🚀 Фоновый поток Советника и Маршала запущен!", flush=True)
    last_sent_minute = ""
    while True:
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            now_ekb = now_utc + datetime.timedelta(hours=5)
            if now_ekb.minute in [0, 30] and now_ekb.second < 30:
                current_minute = now_ekb.strftime("%H:%M")
                if last_sent_minute != current_minute:
                    trend_adviser()
                    marshal_btc()
                    last_sent_minute = current_minute
            time.sleep(5)
        except Exception as e:
            print(f"⚠️ Ошибка: {e}")
            time.sleep(300)

@app.route('/')
def handler():
    return "OK", 200

if __name__ == "__main__":
    alarm_thread = threading.Thread(target=bg_alarm)
    alarm_thread.daemon = True
    alarm_thread.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
