# EB Swing Desk — Algorithm Improvement Plan (R&D)

A research-backed roadmap to make the signal engine **more accurate** (fewer false
positives, edge that survives out-of-sample) and **more efficient** (faster scans,
cheaper compute). Every item is tied to the file that changes and to published
evidence.

> **Guiding principle (unchanged):** deterministic math stays the source of price
> truth. Every learning/statistical layer below is **additive** — it re-ranks or
> filters, never invents a price. Same contract the LLM layer already honours.

---

## 0. Honest starting point — measure before you optimise

The current backtest is *well-engineered for realism* (next-open fills, stop-checked-
first, the **same** `evaluate_strategies`/`build_levels`/`apply_costs` as live). But
its **numbers are not yet trustworthy**, for three structural reasons — so tuning to
them chases noise. Fix measurement first; everything else depends on it.

The three reasons, and the evidence:

1. **Survivorship bias.** `run_backtest()` tests **today's** NIFTY100 list
   ([universe.py](backend/app/services/universe.py)) over past years. We *just lived
   this* — TATAMOTORS and LTIM vanished. Backtesting only the survivors inflates
   returns, and it's **worst for momentum/relative-strength** strategies (which this
   app leans on), because momentum buys recent winners and bias removes the losers.
   Measured impact in the literature: a 26% CAGR momentum backtest fell to **12.2%**
   once delisted names were included — *almost all the "alpha" was bias.*
   ([QuantifiedStrategies](https://www.quantifiedstrategies.com/survivorship-bias-backtesting/),
   [Price Action Lab](https://www.priceactionlab.com/Blog/2019/11/survivorship-bias-in-backtests-of-momentum-rotational-trading-strategies/))

2. **In-sample tuning.** Settings (RSI bands, ADX mins, ATR mult, score weights) are
   hand-set in the UI and evaluated on the *same* history you'd report. Bailey &
   López de Prado proved high backtest Sharpes are trivially reachable after only a
   few configurations, and over-fit strategies systematically underperform live.
   ([Deflated Sharpe Ratio, SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551),
   [Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf))

3. **Serialized equity model.** [metrics.py](backend/app/backtest/metrics.py) does
   `equity += pnl` over trades sorted by exit time — one trade's P&L stacked after
   another. But the strategy holds many symbols concurrently. The real portfolio has
   simultaneous exposure, correlation, and capital contention that this curve can't
   see, so drawdown and return are both mis-stated.

**None of these are bugs — they're the standard traps.** They just mean the reported
PF 1.08 / +17%-over-2y is an *upper bound*, not an expectation.

---

## Phase 1 — Trustworthy measurement  *(highest impact, do first)*

| # | Change | File(s) | Effort |
|---|---|---|---|
| 1.1 | **Point-in-time universe + delisted names.** Backtest against the index membership *as it was* each year, and keep delisted stocks (with their final delist price) in the historical set. Ship a `data/nifty100_history.csv` (date-ranged membership); fall back to current list only if absent. | `universe.py`, `backtest/engine.py` | M |
| 1.2 | **Out-of-sample split + walk-forward.** Tune on a train window, report only on an untouched test window. Then roll it: re-fit on each rolling window, trade the next — the "gold standard." | `backtest/engine.py` (new `walk_forward()`) | M |
| 1.3 | **Real statistics.** Add annualised return (CAGR), **Sharpe & Sortino**, exposure-adjusted return, per-calendar-year table, and a **benchmark row** (NIFTY buy-and-hold over the same window). A strategy that trails the index net of costs has no edge. | `metrics.py` | S |
| 1.4 | **Deflated Sharpe + backtest counter.** Track how many configs you've tested and deflate the Sharpe for multiple-testing + non-normal returns. Surfaces "this looks good but you tried 40 things." | `metrics.py`, new `validation.py` | M |
| 1.5 | **Portfolio equity model.** Replace serialized `equity += pnl` with a daily mark-to-market of concurrent open positions against a shared capital pool + max-concurrent-positions cap. Real drawdown, real capital contention. | `backtest/engine.py`, `metrics.py` | M |

**Why first:** until 1.1–1.5 exist, you can't tell a real improvement from a lucky
one. This is the foundation every later phase is validated against.

---

## Phase 2 — Signal quality

| # | Change | File(s) | Evidence / rationale |
|---|---|---|---|
| 2.1 | **Cross-sectional ranking.** Today each stock gets an *absolute* 0–100 score ([scoring.py](backend/app/engine/scoring.py)); RS is only vs NIFTY. Rank candidates **against each other** each day (percentile of momentum, RS, quality) and take the top-N. Cross-sectional momentum is the form with the most robust published edge. | `scoring.py`, `scanner.py` | Cross-sectional momentum literature |
| 2.2 | **Parameter robustness, not point-optima.** Prefer parameters that work across a *plateau* (neighbouring values also profitable) over a single sharp peak — the peak is usually overfit. Report signal stability under ±1 param perturbation. | `backtest/engine.py` | PBO / López de Prado |
| 2.3 | **De-correlate features.** EMA/RSI/MACD/ADX are partly redundant (all trend/momentum). Check pairwise correlation of the sub-scores; drop or combine collinear ones so the composite isn't triple-counting trend. | `scoring.py`, `indicators.py` | Feature-engineering hygiene |
| 2.4 | **Richer regime.** Regime is currently binary (allows-longs). Add a **volatility regime** (e.g. NIFTY ATR%/VIX bucket) and size/relax thresholds by it — trend strategies behave very differently in calm vs stressed tape. | `regime.py`, `scanner.py` | Regime-conditioning |
| 2.5 | **Corporate-action-adjusted data.** Ensure splits/bonuses are back-adjusted (Angel/Yahoo mostly are, but verify) so indicators don't see a fake 50% "gap" on a split day. | `market_data.py`, adapters | Data hygiene |

---

## Phase 3 — Risk & position sizing

Current sizing is **fixed-fractional** (`risk_pct_per_trade`, [signals.py](backend/app/engine/signals.py)).
That's fine and conservative, but the research is blunt: *"a mediocre strategy with
excellent sizing can outperform a superior strategy with poor sizing."*
([QuantifiedStrategies](https://www.quantifiedstrategies.com/position-sizing-strategies/))

| # | Change | File(s) | Evidence |
|---|---|---|---|
| 3.1 | **Volatility-targeted sizing.** Scale each position inversely to its ATR so every trade contributes ~equal risk, and target a constant *portfolio* volatility. Smooths the equity curve vs fixed-% notional. | `signals.py` | Vol-targeting / risk parity |
| 3.2 | **Fractional Kelly cap.** Offer an optional ¼–½ Kelly ceiling from the *validated* (out-of-sample) win/payoff stats. Half-Kelly cuts volatility ~50% for only ~25% less growth — a strong trade-off. Never full Kelly. | `signals.py`, `metrics.py` | Kelly / fractional-Kelly |
| 3.3 | **Portfolio-level guards.** Max concurrent positions, max sector concentration, and a correlation cap so you don't hold 6 correlated bank stocks as "6 independent bets." | `scanner.py`, new `portfolio.py` | Diversification |
| 3.4 | **Cost realism sweep.** You already model brokerage + slippage; add a **sensitivity sweep** (0.05% → 0.20% slippage) so results aren't fragile to one optimistic cost assumption, plus a liquidity-scaled slippage for thinner names. | `signals.py`, `backtest/engine.py` | Implementation-shortfall |

---

## Phase 4 — Data-driven ranking layer *(optional, heavily guarded)*

This is the "learn from old data / patterns" you asked about. Done wrong it's pure
overfitting; done right it's a modest, *additive* re-ranker — exactly like the LLM
layer. It **never** sets prices or overrides the deterministic gates.

| # | Change | Approach | Guardrail |
|---|---|---|---|
| 4.1 | **Triple-barrier labels.** Label each historical setup by which barrier it hit first — target / stop / time — instead of raw next-day return. This is the correct label for a stop-and-target system. | López de Prado triple-barrier ([mlfinpy](https://mlfinpy.readthedocs.io/en/latest/Labelling.html)) | — |
| 4.2 | **Gradient-boosted cross-sectional ranker.** Train XGBoost/LightGBM on the engine's *existing* features to predict P(this setup outperforms) and re-order the top-N. Small, interpretable, feature-importance auditable. | Predict-and-rank constituents | Additive only; deterministic levels unchanged |
| 4.3 | **Purged / combinatorial-purged CV.** Standard k-fold **leaks** in finance (labels overlap in time). Use Purged K-Fold or **CPCV**, which the recent literature shows has the lowest probability of backtest overfitting. | [Purged CV](https://en.wikipedia.org/wiki/Purged_cross-validation), [CPCV](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110) | Mandatory — no plain CV |
| 4.4 | **Ship only if it beats the deterministic top-N out-of-sample** on a deflated Sharpe. If not, don't ship it. | — | The whole point of Phase 1 |

---

## Phase 5 — Efficiency

Accuracy work is worthless if a scan is too slow to iterate on. Angel throttles hard
(~130s/NIFTY100 scan; the retry/back-off is already in place).

| # | Change | File(s) | Win |
|---|---|---|---|
| 5.1 | **Incremental indicators.** `compute_indicators` recomputes the full history every scan. Cache indicator frames and update only the new tail. | `indicators.py`, `market_data.py` | Big CPU cut on re-scans |
| 5.2 | **Batch LTP, parallel history within rate limits.** LTP is already batched; pace historical fetches with a token-bucket to hit — not exceed — Angel's 3/s, cutting the 130s closer to the ~33s floor. | `angel_one.py`, `market_data.py` | ~2–3× faster cold scans |
| 5.3 | **Vectorise the backtest inner loop.** The per-bar Python loop is the slowest path; vectorise exit-checking with pandas/numpy where correctness allows. | `backtest/engine.py` | Faster parameter sweeps |
| 5.4 | **Persist a research dataset.** Store point-in-time features to Parquet so backtests/ML training don't refetch. | new `data/` pipeline | Fast, reproducible R&D |

---

## Suggested order (impact-first)

1. **Phase 1 (1.1, 1.3, 1.5)** — survivorship fix, real stats + benchmark, portfolio
   equity. *Without these you're flying blind.*
2. **Phase 1 (1.2, 1.4)** — walk-forward + deflated Sharpe. *Now you can trust a number.*
3. **Phase 2 (2.1, 2.4)** — cross-sectional ranking + volatility regime. *Most likely
   real accuracy gain.*
4. **Phase 3 (3.1, 3.3)** — vol-targeted sizing + portfolio guards. *Smoother curve.*
5. **Phase 5 (5.1, 5.2)** — speed, so iteration is cheap.
6. **Phase 4** — the ML ranker, only after 1–3 make "does it actually help?" answerable.
7. **Phase 2 (2.2, 2.3), 3 (3.2, 3.4), 5 (5.3, 5.4)** — hardening.

## The one rule that makes this work

Every change is judged on an **out-of-sample, benchmark-relative, deflated** number —
never on the in-sample backtest. Keep a running count of configurations tried so the
Sharpe can be honestly deflated. That discipline is worth more than any single feature.

---

### Sources
- Bailey & López de Prado — [The Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) · [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
- [Backtest overfitting in the ML era — CPCV comparison (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110)
- Survivorship bias — [QuantifiedStrategies](https://www.quantifiedstrategies.com/survivorship-bias-backtesting/) · [Price Action Lab (momentum)](https://www.priceactionlab.com/Blog/2019/11/survivorship-bias-in-backtests-of-momentum-rotational-trading-strategies/) · [India NIFTY Smallcap 250 study](https://arxiv.org/pdf/2603.19380)
- Position sizing — [QuantifiedStrategies: 18 methods](https://www.quantifiedstrategies.com/position-sizing-strategies/) · fractional Kelly
- ML — [Triple-barrier labelling (mlfinpy)](https://mlfinpy.readthedocs.io/en/latest/Labelling.html) · [Purged cross-validation](https://en.wikipedia.org/wiki/Purged_cross-validation)
