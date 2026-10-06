# Darmowy skaner GOLD / NQ co 5 minut (Telegram)

Automatyczny skrypt, który co ~5 minut sprawdza cenę i wysyła powiadomienie
na Telegram **tylko wtedy, gdy pojawi się sygnał** (wejście w strefę kupna/
sprzedaży + sygnał odrzucenia na świecy M15). Działa na darmowym GitHub
Actions — nie potrzebujesz własnego serwera.

To uproszczona, automatyczna heurystyka (długi knot + zamknięcie +
wolumen), nie pełna manualna analiza order blocków/FVG. Traktuj to jako
pierwszy filtr/alarm, decyzję o wejściu podejmuj sam na wykresie u
brokera. **To nie jest porada inwestycyjna.**

## Krok 1 — Konto GitHub i repozytorium

1. Załóż darmowe konto na [github.com](https://github.com) (jeśli nie masz).
2. Utwórz nowe repozytorium — **koniecznie publiczne** (Public). Przy
   publicznym repo GitHub Actions ma nielimitowane darmowe minuty. Przy
   prywatnym masz tylko ~2000 min/mies. za darmo, co przy skanowaniu co 5
   minut (czyli ok. 288 uruchomień dziennie) wyczerpie się w kilka dni.
3. Wgraj do repo wszystkie pliki z tej paczki, zachowując strukturę
   folderów (ważne, żeby `.github/workflows/scan.yml` został w ścieżce
   `.github/workflows/`).

## Krok 2 — Bot Telegram

1. W aplikacji Telegram znajdź `@BotFather` i wyślij `/newbot`.
2. Podaj nazwę bota, dostaniesz **token** (ciąg znaków typu
   `123456789:ABCdef...`) — zapisz go.
3. Napisz dowolną wiadomość (np. "cześć") do swojego nowego bota — musisz
   to zrobić, inaczej Telegram nie "zna" Twojego chat_id.
4. Wejdź w przeglądarce na:
   `https://api.telegram.org/bot<TWÓJ_TOKEN>/getUpdates`
   (podmień `<TWÓJ_TOKEN>` na swój token). W odpowiedzi znajdź
   `"chat":{"id": 123456789, ...}` — to jest Twój `chat_id`.

## Krok 3 — Sekrety w repozytorium

W repo na GitHub: **Settings → Secrets and variables → Actions → New
repository secret** i dodaj dwa sekrety:

- `TELEGRAM_BOT_TOKEN` — token z kroku 2.2
- `TELEGRAM_CHAT_ID` — chat_id z kroku 2.4

## Krok 4 — Uprawnienia workflow

**Settings → Actions → General → Workflow permissions** → zaznacz
**"Read and write permissions"** i zapisz. Bez tego skrypt nie będzie mógł
zapisywać pliku `state.json` (pamięć o już wysłanych sygnałach, żeby nie
spamować Cię co 5 minut tym samym alertem).

## Krok 5 — Gotowe

Workflow uruchomi się automatycznie co ~5 minut (zakładka **Actions** w
repo pokazuje historię uruchomień). Możesz też odpalić go ręcznie:
**Actions → "Skan rynku (Gold/NQ) co 5 minut" → Run workflow**.

## Dodanie NQ lub innego instrumentu

Otwórz `gold_scanner.py`, sekcję `SYMBOLS` — jest tam gotowy,
zakomentowany przykład dla NQ. Odkomentuj go i **wpisz własne strefy
kupna/sprzedaży i SL/TP** (nie zgaduj ich za Ciebie — zrób na podstawie
własnej analizy wykresu NQ). Tickery Yahoo Finance, które możesz użyć:

- `XAUUSD=X` — złoto spot (już skonfigurowane)
- `NQ=F` — Nasdaq 100 futures
- `ES=F` — S&P 500 futures
- `GC=F` — złoto (kontrakt futures, alternatywa dla XAUUSD=X)

## Ograniczenia, o których warto wiedzieć

- **Harmonogram "co 5 minut" nie jest gwarantowany co do sekundy** —
  GitHub może opóźnić uruchomienie przy dużym obciążeniu swoich
  serwerów (zwykle kilka minut, czasem więcej).
- Dane pochodzą z Yahoo Finance (biblioteka `yfinance`, bez klucza API,
  darmowa) — mogą mieć niewielkie opóźnienie względem feedu Twojego
  brokera i czasem brakować danych w weekendy/święta.
- Wykrywanie sygnału to uproszczona heurystyka świecowa, nie zastępuje
  Twojej własnej analizy order blocków i FVG — to tylko automatyczny
  "pierwszy filtr", żeby nie trzeba było patrzeć w wykres non-stop.
- Zaktualizuj strefy kupna/sprzedaży w `gold_scanner.py` w miarę jak
  zmienia się sytuacja rynkowa (te wpisane teraz pochodzą z analizy z
  końca września/początku października 2026 i mogą się zdezaktualizować).

## Rozwiązywanie problemów

- Jeśli w logach uruchomienia (zakładka Actions → wybrane uruchomienie)
  widzisz błędy połączenia z Yahoo Finance (np. `ConnectionError`,
  `403`) — to częsty, chwilowy problem przy pobieraniu danych z adresów
  IP serwerów w chmurze (dotyczy GitHub Actions, nie tylko tego
  skryptu). Skrypt ma wbudowane 3 próby z odczekaniem, ale czasem
  wszystkie 3 i tak się nie powiodą w danym uruchomieniu — kolejne
  uruchomienie za 5 minut zwykle już zadziała. Jeśli błędy powtarzają
  się non-stop, napisz, pomogę przestawić źródło danych na alternatywne
  darmowe API (np. Twelve Data, wymaga darmowego klucza API).
- Skryptu nie dało się przetestować z tego środowiska na żywych danych
  (środowisko, w którym go przygotowałem, ma zablokowany bezpośredni
  dostęp do Yahoo Finance) — kod jest oparty o standardową, szeroko
  używaną bibliotekę `yfinance` i powinien działać poprawnie na
  serwerach GitHub Actions, ale pierwsze uruchomienie warto sprawdzić
  ręcznie (Actions → Run workflow) i zajrzeć w logi.
