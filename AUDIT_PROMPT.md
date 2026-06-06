# Institutional-Grade Audit Prompt

Copy-paste this into a fresh Cowork chat (`Arbitrage / main / worktree`)
to get a deep, professional review of the trading bot.

---

אני בעלים של RateBridge — בוט delta-neutral funding-rate arbitrage
שרץ live עם כסף אמיתי על 5 בורסות (Binance, Bybit, KuCoin, Gate.io,
Bitget). הקוד ב-repo הזה, branch=main.

קרא קודם את HANDOFF.md כדי להבין את המבנה. ואז תבצע **audit מקצועי
ברמת בית מסחר** — לא ביקורת UI/UX, אלא ניתוח כמותי וחמור של:
ביצועים, latency, risk, edge, וקוד.

# 🎯 מטרת הסקירה
להעלות את המערכת מ"בוט שעובד" ל-"מערכת מסחר institutional-grade".
תתייחס אלי כמו שתתייחס ל-Jane Street או Jump Trading שמחפשים להריץ
את האסטרטגיה הזו ב-prod.

# 🔬 מה לסקור (בסדר עדיפויות)

## 1. אסטרטגיה (Strategy)
- האם NUTCRACKER vs CHERRY_PICK מסווגות נכון? קרא:
  `src/discovery/scanner.py`, `src/execution/_entry_mixin.py`
- מה ה-edge המתמטי בפועל? אחרי fees + slippage + funding cost של
  הרגל שמשלמת — האם ה-expected value חיובי בכל regime?
- האם logic של `exit_logic_mixin` תופס את כל ה-edge case:
  funding skipped, basis lock, perpetual mispricing?
- האם `profit_target_pct=0.7%` מיטבי? נתח על נתוני history אם יש
  (יש backtest engine ב-`src/backtest/`, השתמש בו).
- מה Sharpe התיאורטי? Max drawdown סימולטיבי?

## 2. Latency Budget (קריטי)
מדוד בקפדנות כל שלב:
- WS message → scanner detection (ms)
- Scanner → opportunity classification (ms)
- Classification → order placement (ms)
- Order send → fill confirmation (ms)
- Hedge gap (long fill → short fill)
- Total: opportunity_found → both_legs_filled

יעדים institutional:
- WS staleness: <100ms p99
- Hedge gap: <500ms p99 (אצלנו: `max_hedge_gap_ms=1000`)
- Order placement: <50ms p99

מצא איפה האלגוריתם מבזבז זמן. תן לי breakdown מספרי.
תסתכל ב-`_entry_mixin.py`, `_entry_orders_mixin.py`, `scanner.py`.

## 3. Risk Management
- האם delta-neutrality באמת נשמרת? בדוק `_close_finalize_mixin`,
  `risk/guard.py`.
- מה אם רגל אחת נסגרת באכזריות (חיסול, גלישה)? יש fail-safe?
- האם `max_margin_usage=0.70` בטוח? מה ה-max drawdown במצב stress?
- האם יש קסם של portfolio limits? exposure per exchange?
- `liquidation_safety_pct=20` — האם זה מספיק עם vol של 5x leverage?

## 4. Execution Quality
- VWAP מחושב נכון? slippage estimation realist?
- כאשר order book "מתאדה" בין decision ל-fill, מה קורה?
- האם `reduce_only` מוגדר נכון בכל close path?
- `_verify_exit_book_depth` — האם 30% degradation buffer הגיוני?
- maker/taker מנוהל? יש attempt ל-post-only?

## 5. Edge Cases ו-Race Conditions
חפש באגים בעדיפות גבוהה:
- מה אם funding payment קורה בדיוק בין tick של חישוב PnL?
- מה אם trade fully_open אבל הbroker מאחר לעדכן position?
- WebSocket reconnect מאבד הודעות? scanner ממשיך עם stale data?
- חישובי Decimal vs float — יש מקרה של `int(price)` שמעגל ב-execution?
- async race: שני tasks מעדכנים `_active_trades` במקביל?
- מה אם time sync של השרת מתחיל לסטות? יש detection?

## 6. State & Recovery
- אם הבוט קורס באמצע trade — איך הוא משחזר?
- positions reconciliation — האם בטוח מול state ב-Redis?
- מה אם Redis מאבד נתונים? יש fallback ל-exchange API?

## 7. Code Quality (מקצועי, לא קוסמטי)
- תפסת async patterns: `gather`, `Semaphore`, `timeout` — נכון?
- exception handling: יש `except Exception: pass` באף מקום?
- type safety: `Decimal` לכסף בכל ה-execution path?
- thread safety במידה ויש שיתוף state?
- logging level לעומת noise — נקי לoperator?

## 8. Observability
- היכן חסרים metrics ל-Prometheus?
- האם יש alerting על: `hedge_gap > threshold`, WS staleness,
  failed orders, delta drift, margin breach?
- TCA report — האם ניתן לחשב cost per trade בדיעבד?

## 9. Backtest Rigor
קרא את `src/backtest/`. בדוק:
- האם simulation realistic? slippage modeled?
- האם survivorship bias נוכח? (סימבולים שhe-delisted)
- האם funding rates historical נכונים מ-CCXT?
- האם ה-results ניתנים לשחזור (seed)?

## 10. Comparison ל-Institutional
תן לי טבלה:
"מה RateBridge עושה" vs "מה Jane Street/Jump היו עושים".
איפה הפערים? איך נסגור אותם?

# 📤 פורמט פלט

חזור לי עם:

## A. Executive Summary (3-5 שורות)
המצב הכללי + הבעיה הכי קריטית

## B. ממצאים לפי חומרה
- 🔴 CRITICAL  (יעצור מסחר / יסכן כסף) — מקסימום 5
- 🟠 HIGH      (פוגע משמעותית ב-edge) — עד 10
- 🟡 MEDIUM    (שיפור נאה)
- 🟢 LOW       (nice to have)

לכל ממצא:
- מיקום מדויק (`file:line`)
- מה הבעיה (טכני, ספציפי)
- מה הסיכון (כמותי אם אפשר: "מאבד X bps לעסקה")
- תיקון מוצע (קוד או pattern)

## C. Latency Breakdown
טבלה: שלב → median ms → p99 ms → bottleneck

## D. רשימת פערים מול institutional
מה הם עושים שאנחנו לא? בעדיפות.

## E. רוד-מאפ ל-3 חודשים
מה לעשות עכשיו, חודש, 3 חודשים — כדי להגיע לרמה institutional.

# ⚠️ כללי ברזל
- אל תהפנט אותי בכמה הקוד "נקי" או "יפה".
  חפש סדקים שמאבדים כסף.
- חישובים מעל הערכות. תן מספרים.
- אם משהו עובד אבל לא optimal — תגיד.
- אל תפחד להגיד "השיטה הזו לא תעבוד בקנה מידה X".
- אם אתה לא בטוח — תקרא יותר קוד, לא ננחש.

תתחיל. אני מצפה לדו"ח רציני שאפשר להראות לחבר ועד של hedge fund.
