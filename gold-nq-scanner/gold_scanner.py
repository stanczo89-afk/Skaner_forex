#!/usr/bin/env python3
"""
Darmowy skaner GOLD / NQ / innych instrumentów.

Co robi:
- pobiera świece M15 z Yahoo Finance (yfinance, bez klucza API, za darmo)
- sprawdza czy cena jest w zdefiniowanej strefie kupna/sprzedaży
- sprawdza czy ostatnia ZAMKNIĘTA świeca M15 daje sygnał odrzucenia
  (długi dolny/górny knot + zamknięcie wyraźnie po "dobrej" stronie + wolumen)
- jeśli tak -> wysyła powiadomienie na Telegram (i tylko wtedy)
- stan (żeby nie spamować tym samym sygnałem co 5 minut) trzymany w state.json

To jest uproszczona, automatyczna heurystyka — NIE jest to pełna manualna
analiza order blocków / FVG. Traktuj to jako pierwszy filtr / alarm,
a decyzję o wejściu podejmuj sam, patrząc na wykres u brokera.

To NIE jest porada inwestycyjna.
"""

import os
import json
import time
import pandas as pd
import requests
import yfinance as yf

STATE_FILE = "state.json"

# ---------------------------------------------------------------------------
# KONFIGURACJA INSTRUMENTÓW
# Dopisz kolejne instrumenty analogicznie do GOLD. Tickery Yahoo Finance:
#   złoto spot:        "XAUUSD=X"
#   złoto (futures):   "GC=F"
#   Nasdaq 100 futures:"NQ=F"
#   S&P 500 futures:   "ES=F"
# Strefy (buy_zone / sell_zone) i SL/TP wpisz sam na podstawie własnej
# analizy — poniższe dla GOLD są zgodne z ustaleniami z 29.09.2026 i
# wymagają okresowej aktualizacji w miarę ruchu rynku.
# ---------------------------------------------------------------------------
SYMBOLS = {
    "XAUUSD=X": {
        "label": "GOLD (XAU/USD)",
        "buy_zone": (4090, 4150),
        "sell_zone": (4200, 4230),
        "sl_buy": 4055,
        "tp1_buy": 4200,
        "tp2_buy": 4250,
        "sl_sell": 4260,
        "tp1_sell": 4100,
        "tp2_sell": 4050,
    },
    # Przykład dla NQ — UZUPEŁNIJ własnymi strefami przed włączeniem:
    # "NQ=F": {
    #     "label": "NASDAQ 100 Futures (NQ)",
    #     "buy_zone": (21000, 21300),
    #     "sell_zone": (21800, 22000),
    #     "sl_buy": 20900,
    #     "tp1_buy": 21800,
    #     "tp2_buy": 22200,
    #     "sl_sell": 22100,
    #     "tp1_sell": 21300,
    #     "tp2_sell": 21000,
    # },
}

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_telegram(text: str) -> None:
    """Wysyła wiadomość na Telegram. Jeśli brak sekretów, tylko loguje."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[UWAGA] Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — "
              "wiadomość NIE została wysłana, tylko wypisana niżej:")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[BŁĄD] Wysyłka Telegram nie powiodła się: {e}")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _normalize_columns(data: pd.DataFrame) -> pd.DataFrame:
    """yfinance czasem zwraca MultiIndex kolumn nawet dla jednego tickera."""
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [c[0] for c in data.columns]
    return data


def detect_bullish_rejection(df: pd.DataFrame) -> bool:
    """Długi dolny knot + zamknięcie wyraźnie powyżej otwarcia + wolumen."""
    if len(df) < 10:
        return False
    candle = df.iloc[-2]  # ostatnia ZAMKNIĘTA świeca (-1 może być w trakcie)
    o, h, l, c = candle["Open"], candle["High"], candle["Low"], candle["Close"]
    full_range = h - l
    if full_range <= 0:
        return False
    body = abs(c - o)
    lower_wick = min(o, c) - l
    is_bullish = c > o
    long_lower_wick = lower_wick > max(2 * body, 0.3 * full_range)
    close_near_high = (c - l) / full_range > 0.6
    volume_ok = True
    if "Volume" in df.columns and df["Volume"].iloc[-10:-2].mean() > 0:
        volume_ok = candle["Volume"] >= df["Volume"].iloc[-10:-2].mean()
    return bool(is_bullish and long_lower_wick and close_near_high and volume_ok)


def detect_bearish_rejection(df: pd.DataFrame) -> bool:
    """Długi górny knot + zamknięcie wyraźnie poniżej otwarcia + wolumen."""
    if len(df) < 10:
        return False
    candle = df.iloc[-2]
    o, h, l, c = candle["Open"], candle["High"], candle["Low"], candle["Close"]
    full_range = h - l
    if full_range <= 0:
        return False
    body = abs(c - o)
    upper_wick = h - max(o, c)
    is_bearish = c < o
    long_upper_wick = upper_wick > max(2 * body, 0.3 * full_range)
    close_near_low = (h - c) / full_range > 0.6
    volume_ok = True
    if "Volume" in df.columns and df["Volume"].iloc[-10:-2].mean() > 0:
        volume_ok = candle["Volume"] >= df["Volume"].iloc[-10:-2].mean()
    return bool(is_bearish and long_upper_wick and close_near_low and volume_ok)


def download_with_retry(ticker: str, attempts: int = 3, delay_s: float = 5.0) -> pd.DataFrame:
    """Yahoo Finance czasem zwraca chwilowy błąd połączenia (403/ConnectionError)
    — zwłaszcza z adresów IP serwerów w chmurze. Kilka prób z odczekaniem
    rozwiązuje większość takich przypadków."""
    last_err = None
    for i in range(attempts):
        try:
            data = yf.download(ticker, period="5d", interval="15m", progress=False)
            if data is not None and not data.empty:
                return data
        except Exception as e:  # noqa: BLE001
            last_err = e
        if i < attempts - 1:
            time.sleep(delay_s)
    if last_err:
        print(f"[INFO] {ticker}: ostatni błąd pobierania: {last_err}")
    return pd.DataFrame()


def check_symbol(ticker: str, cfg: dict, state: dict) -> None:
    data = download_with_retry(ticker)
    if data is None or data.empty:
        print(f"[INFO] Brak danych dla {ticker} (rynek zamknięty / chwilowy błąd API Yahoo Finance).")
        return

    data = _normalize_columns(data).reset_index()
    last_price = float(data["Close"].iloc[-1])
    label = cfg["label"]
    buy_lo, buy_hi = cfg["buy_zone"]
    sell_lo, sell_hi = cfg["sell_zone"]

    key_buy = f"{ticker}_buy"
    key_sell = f"{ticker}_sell"

    in_buy_zone = buy_lo <= last_price <= buy_hi
    in_sell_zone = sell_lo <= last_price <= sell_hi

    if in_buy_zone and detect_bullish_rejection(data):
        if state.get(key_buy) != "sent":
            msg = (
                f"🟢 SYGNAŁ KUPNA — {label}\n"
                f"Cena: {last_price:.2f}\n"
                f"SL: {cfg['sl_buy']}\n"
                f"TP1: {cfg['tp1_buy']}  TP2: {cfg['tp2_buy']}\n"
                f"Długi dolny knot + zamknięcie nad otwarciem na M15, "
                f"w strefie kupna {buy_lo}-{buy_hi}.\n"
                f"To nie jest porada inwestycyjna — zweryfikuj cenę/świecę u brokera."
            )
            send_telegram(msg)
            state[key_buy] = "sent"
    else:
        state[key_buy] = "idle"

    if in_sell_zone and detect_bearish_rejection(data):
        if state.get(key_sell) != "sent":
            msg = (
                f"🔴 SYGNAŁ SPRZEDAŻY — {label}\n"
                f"Cena: {last_price:.2f}\n"
                f"SL: {cfg['sl_sell']}\n"
                f"TP1: {cfg['tp1_sell']}  TP2: {cfg['tp2_sell']}\n"
                f"Długi górny knot + zamknięcie pod otwarciem na M15, "
                f"w strefie sprzedaży {sell_lo}-{sell_hi}.\n"
                f"To nie jest porada inwestycyjna — zweryfikuj cenę/świecę u brokera."
            )
            send_telegram(msg)
            state[key_sell] = "sent"
    else:
        state[key_sell] = "idle"

    print(f"[OK] {label}: cena={last_price:.2f} "
          f"w_strefie_kupna={in_buy_zone} w_strefie_sprzedazy={in_sell_zone}")


def main() -> None:
    state = load_state()
    for ticker, cfg in SYMBOLS.items():
        try:
            check_symbol(ticker, cfg, state)
        except Exception as e:  # noqa: BLE001
            print(f"[BŁĄD] {ticker}: {e}")
    save_state(state)


if __name__ == "__main__":
    main()
