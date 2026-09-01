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

# Главные монеты для анализа
MAIN_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

NEWS_CACHE = {"last_update": 0, "headlines": [], "sentiment": "Не определён"}

RSS_FEEDS = [
    "https://cointelegraph.com/feed",
    "https://www.coindesk.com/feed/",
    "https://cryptonews.com/news/feed/"
]

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

app = Flask(__name__)

def is_working_hours():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    hour_ekb = (now_utc.hour + 5) % 24
    return (hour_ekb >= 14) or (hour_ekb < 3)

def get_30m_candles(symbol):
    try:
        url = f"https://api.mexc.com/api/v3/klines?symbol={symbol}&interval=30m&limit=5"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            candles = []
            for candle in data:
                candles.append(float(candle[4]))
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

# ==========================================================
# ТРЕНДОВЫЙ СОВЕТНИК (30 мин, 3 значения, точное время)
# ==========================================================
def trend_adviser():
    if not is_working_hours():
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ekb = now_utc + datetime.timedelta(hours=5)

    # Отправляем только если минута = 00 или 30
    if now_ekb.minute not in [0, 30]:
        return

    print("📊 Советник: отправляю сводку по 5 главным монетам (30 мин)...")

    lines = []

    for sym in MAIN_SYMBOLS:
        candles = get_30m_candles(sym)
        if not candles or len(candles) < 4:
            lines.append(f"{sym}: Нет данных")
            continue

        change_1 = (candles[-3] - candles[-4]) / candles[-4] * 100
        change_2 = (candles[-2] - candles[-3]) / candles[-3] * 100
        change_3 = (candles[-1] - candles[-2]) / candles[-2] * 100

        if abs(change_3) < 0.05:
            label = "Боковик"
        elif change_3 > 0:
            if change_2 < -0.05:
                label = "Разворот вверх!"
            elif change_3 >= change_2:
                label = "Ускорение вверх"
            else:
                label = "Замедление роста"
        else:
            if change_2 > 0.05:
                label = "Разворот вниз!"
            elif change_3 <= change_2:
                label = "Ускорение вниз"
            else:
                label = "Замедление падения"

        lines.append(f"{sym}: {change_1:+.2f}% | {change_2:+.2f}% | {change_3:+.2f}% | {label}")

    msg = f"📊 ТРЕНД 30М ({now_ekb.strftime('%H:%M')}):\n" + "\n".join(lines)

    send_telegram(msg)
    print("📊 Сводка отправлена!")

# ==========================================================
# ФОНОВЫЙ ПОТОК (проверяем время каждые 30 секунд)
# ==========================================================
def bg_alarm():
    print("🚀 Фоновый поток Советника запущен!", flush=True)
    while True:
        try:
            trend_adviser()
            time.sleep(30)  # Проверяем каждые 30 секунд, отправка только в 00 и 30 минут
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
