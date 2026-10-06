#!/usr/bin/env python3
"""
Backtest poziomów pivot point (Floor Trader Pivots) dla NQ (Nasdaq 100
Futures) w sesji amerykańskiej.

Co robi:
1. Pobiera dzienne świece NQ=F (do liczenia pivotów) oraz świece 1h (do
   testowania odbić w sesji amerykańskiej) z Yahoo Finance (yfinance, za
   darmo, bez klucza API).
2. Dla każdego dnia liczy klasyczne pivoty (PP, R1-R3, S1-S3) na bazie
   High/Low/Close POPRZEDNIEGO dnia.
3. W oknie sesji amerykańskiej (domyślnie 9:30-16:00 czasu nowojorskiego,
   czyli klasyczna sesja kasowa NASDAQ — ustawienie SESSION_START/END
   poniżej) sprawdza dla każdego poziomu:
   - czy cena GO DOTKNĘŁA (świeca 1h przecięła poziom),
   - czy PO DOTKNIĘCIU nastąpiło odbicie o co najmniej
     REVERSAL_THRESHOLD_PCT (w % ceny) w ciągu najbliższych
     LOOKAHEAD_BARS świec 1h, zanim poziom został unieważniony
     przebiciem o więcej niż INVALIDATION_PCT.

Wynik: tabela per poziom z liczbą dotknięć, liczbą odbić i win-rate (%).
Tylko poziomy z win-rate >= 60% ORAZ minimalną liczbą dotknięć (próg
istotności, MIN_TOUCHES) są uznawane za "kwalifikujące się".

WAŻNE OGRANICZENIA — przeczytaj przed użyciem wyników do realnego handlu:
- To automatyczny backtest na podstawie prostych reguł geometrycznych
  (dotknięcie poziomu + ruch procentowy), NIE pełna analiza price action
  czy order blocków. Traktuj wynik jako wskazówkę statystyczną, a nie
  gotową, przetestowaną strategię.
- Dane 1h z Yahoo Finance dla NQ=F sięgają maks. ok. 730 dni wstecz — to
  naturalnie ogranicza wielkość próby (mniej danych niż przy pełnej
  analizie dziesięcioletniej).
- Konwencja "poprzedniego dnia" = pełna dzienna świeca futures (sesja
  elektroniczna), NIE tylko regularne godziny handlu (RTH). Różni
  brokerzy/platformy liczą to różnie — jeśli chcesz inną konwencję, daj
  znać, dostosuję.
- Wynik NIE uwzględnia poślizgu, spreadu ani prowizji.
- To nie jest porada inwestycyjna.
"""

import bisect
from datetime import time as dtime

import pandas as pd
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - fallback dla bardzo starego Pythona
    from backports.zoneinfo import ZoneInfo  # type: ignore

TICKER = "NQ=F"
SESSION_TZ = ZoneInfo("America/New_York")
SESSION_START = dtime(9, 30)
SESSION_END = dtime(16, 0)

INTRADAY_INTERVAL = "60m"
INTRADAY_PERIOD = "730d"  # maksimum dla interwału 60m na Yahoo Finance

REVERSAL_THRESHOLD_PCT = 0.003  # 0.3% ceny — minimalny ruch uznawany za "odbicie"
INVALIDATION_PCT = 0.0015       # 0.15% — przebicie unieważniające sygnał
LOOKAHEAD_BARS = 4              # ile świec 1h po dotknięciu sprawdzamy
MIN_TOUCHES = 15                # minimalna liczba dotknięć, żeby wynik liczyć za wiarygodny


def classic_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict:
    pp = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * pp - prev_low
    s1 = 2 * pp - prev_high
    r2 = pp + (prev_high - prev_low)
    s2 = pp - (prev_high - prev_low)
    r3 = prev_high + 2 * (pp - prev_low)
    s3 = prev_low - 2 * (prev_high - pp)
    return {"PP": pp, "R1": r1, "R2": r2, "R3": r3, "S1": s1, "S2": s2, "S3": s3}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df


def fetch_daily(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, period="3y", interval="1d", progress=False)
    df = _normalize_columns(df).dropna()
    return df


def fetch_intraday(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, period=INTRADAY_PERIOD, interval=INTRADAY_INTERVAL, progress=False)
    df = _normalize_columns(df).dropna()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(SESSION_TZ)
    return df


def in_session(ts: pd.Timestamp) -> bool:
    t = ts.time()
    return SESSION_START <= t <= SESSION_END


def run_backtest() -> None:
    print(f"Pobieram dane dla {TICKER} (dzienne do liczenia pivotów, {INTRADAY_INTERVAL} "
          f"do testowania odbić w sesji {SESSION_START}-{SESSION_END} ET)...")

    daily = fetch_daily(TICKER)
    intraday = fetch_intraday(TICKER)

    if daily.empty or intraday.empty:
        print("[BŁĄD] Brak danych — sprawdź połączenie z Yahoo Finance / ticker.")
        return

    daily_dates = [d.date() for d in daily.index]  # posortowane rosnąco
    results = {lvl: {"touches": 0, "reversals": 0} for lvl in
               ["PP", "R1", "R2", "R3", "S1", "S2", "S3"]}

    intraday_days = sorted(set(intraday.index.normalize()))
    print(f"Dni z danymi intraday w próbie: {len(intraday_days)}")

    for day in intraday_days:
        day_date = day.date()
        idx = bisect.bisect_left(daily_dates, day_date) - 1
        if idx < 0:
            continue
        prev = daily.iloc[idx]
        pivots = classic_pivots(float(prev["High"]), float(prev["Low"]), float(prev["Close"]))

        day_bars = intraday[intraday.index.normalize() == day]
        day_bars = day_bars[[in_session(ts) for ts in day_bars.index]]
        if day_bars.empty:
            continue

        bars_list = list(day_bars.itertuples())

        for level_name, level_price in pivots.items():
            touched_idx = None
            for i, bar in enumerate(bars_list):
                if bar.Low <= level_price <= bar.High:
                    touched_idx = i
                    break
            if touched_idx is None:
                continue

            results[level_name]["touches"] += 1

            lookahead = bars_list[touched_idx + 1: touched_idx + 1 + LOOKAHEAD_BARS]
            if not lookahead:
                continue

            touch_price = level_price
            reversal_up_target = touch_price * (1 + REVERSAL_THRESHOLD_PCT)
            reversal_down_target = touch_price * (1 - REVERSAL_THRESHOLD_PCT)
            invalidate_up = touch_price * (1 + INVALIDATION_PCT)
            invalidate_down = touch_price * (1 - INVALIDATION_PCT)

            reversed_ok = False
            for bar in lookahead:
                if level_name.startswith("R"):
                    # opór -> oczekujemy odbicia w dół
                    if bar.High > invalidate_up:
                        break
                    if bar.Low <= reversal_down_target:
                        reversed_ok = True
                        break
                elif level_name.startswith("S"):
                    # wsparcie -> oczekujemy odbicia w górę
                    if bar.Low < invalidate_down:
                        break
                    if bar.High >= reversal_up_target:
                        reversed_ok = True
                        break
                else:  # PP — poziom dwukierunkowy, liczymy silniejszy z ruchów
                    if bar.High >= reversal_up_target or bar.Low <= reversal_down_target:
                        reversed_ok = True
                        break

            if reversed_ok:
                results[level_name]["reversals"] += 1

    print(f"\n=== Backtest pivot points — {TICKER}, sesja {SESSION_START}-{SESSION_END} ET ===")
    print(f"Okres danych {INTRADAY_INTERVAL}: {INTRADAY_PERIOD} | "
          f"próg odbicia: {REVERSAL_THRESHOLD_PCT * 100:.2f}% | "
          f"próg unieważnienia: {INVALIDATION_PCT * 100:.2f}% | "
          f"lookahead: {LOOKAHEAD_BARS} świec\n")

    header = f"{'Poziom':8}{'Dotknięcia':12}{'Odbicia':10}{'Win-rate':10}Kwalifikuje się (>=60%, min {MIN_TOUCHES} dotknięć)"
    print(header)
    print("-" * len(header))

    qualifying = []
    for lvl, d in results.items():
        touches = d["touches"]
        reversals = d["reversals"]
        wr = (reversals / touches * 100) if touches else 0.0
        qualifies = touches >= MIN_TOUCHES and wr >= 60.0
        if qualifies:
            qualifying.append((lvl, wr, touches))
        print(f"{lvl:8}{touches:<12}{reversals:<10}{wr:<10.1f}{'TAK' if qualifies else 'nie'}")

    print("\nPoziomy kwalifikujące się do użycia w skanerze "
          f"(win-rate >= 60%, min. {MIN_TOUCHES} dotknięć w próbie):")
    if qualifying:
        for lvl, wr, touches in sorted(qualifying, key=lambda x: -x[1]):
            print(f"  - {lvl}: win-rate {wr:.1f}% (na podstawie {touches} dotknięć)")
    else:
        print("  Brak — żaden poziom nie spełnił progu 60% przy obecnych parametrach.")

    print("\nSkopiuj całą powyższą tabelę i wklej z powrotem w rozmowie — na tej "
          "podstawie dopiszę regułę sygnału do skanera.")


if __name__ == "__main__":
    run_backtest()
