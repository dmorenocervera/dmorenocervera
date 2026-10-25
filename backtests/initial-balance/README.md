# Initial Balance thirds — S&P 500 backtest

Setup: IB = high/low 9:30–10:30 ET. At 10:30, price in the top third → long with stop under the IB low; bottom third → short with stop over the IB high; middle → no trade.

Data: Oanda SPX500_USD 1-minute bars, 2005–2020 (3,064 full sessions), from [FutureSharks/financial-data](https://github.com/FutureSharks/financial-data). Costs: 0.5 index points per round trip. If stop and target fall in the same minute, the stop counts first.

Run: `python ib_backtest.py [path_to_SPX500_USD_folder]`. Full output in `results.txt`.

## Result

The statistic holds: the day's high or low is set in the IB on 69% of days; with price in the top third the IB low holds 55% of days (the IB high holds 14.7%), and in the bottom third the IB high holds 59% (the low holds 14.4%).

The trade does not: every target tested (0.5R–3R, IB extensions, exit at the close) and both stop placements lose between −0.05 and −0.12R per trade, in both halves of the sample and in 15 of 16 years (2008 is the exception). The stop is far from entry (≥ 2/3 of the IB), so it holds often, but the days it breaks cost a full R, and many days where it holds still close below entry.

## Pullback variant (`ib_pullback.py`)

Breakout close outside the IB after 10:30 → retest of the IB edge → entry on the first candle that closes back outside. Stop at the IB midpoint (`mid`) or one third + 10% buffer inside the IB (`third`). Optional filter: only trade in the direction the 10:30 third points to. 96 configs, chosen on 2005–2012 and checked on 2013–2020. Output in `results_pullback.txt`, `pullback_grid.csv` and `results_costs.txt`.

Best config: 15-min candles, stop at the IB midpoint, 10:30 filter on, exit at the close. 945 trades, +0.157R per trade before costs (t ≈ 3.5, out-of-sample +0.096R). The edge is real before costs, but the stop is only ~5–6 points in 2005–2020, so execution cost decides the result: +0.06R at 0.5 pt per round trip, negative at 1 pt. The 10:30 filter and exiting at the close (not a fixed target) are what make it work.

## Break-even and trailing stop (`ib_trailing.py`)

Tested on the best pullback configs: BE at 0.5–2R, and trailing stops 0.5–2R behind the best price, switched on from the start or after 0.5–1.5R. Output in `results_trailing.txt`.

Neither raises the expectancy. Tight rules (BE at 0.5R, trail ≤ 1R) make it worse: price often goes in favour, comes back to entry and then runs. Wide rules (BE ≥ 1.5R, trail 1.5R) leave it the same. The useful one is risk control: trail 1.5R after +1R keeps almost the same avgR (0.105 vs 0.108 at 0.25 pt cost) with a ~30% smaller max drawdown.

## Extra filters (`ib_filters.py`, `ib_combos.py`)

24 filters on the 2,103 signal days (price in the top/bottom third at 10:30), each aligned with the signal direction. Two things measured: how often the opposite IB extreme holds ("hit", base 56.8%), and the avg R of the pullback strategy (15-min, stop IB mid, exit at the close, 0.25 pt cost, base +0.109R). Output in `results_filters.txt` / `filters.csv` and `results_combos.txt`.

- Raise the hit rate in both halves: wide IB (>1.3x the 20-day average, 66.9%), open drive (open at the opposite end of the IB, 64.2%), 10:30 price beyond the overnight high/low (63.6%). Narrow IB lowers it to 46%.
- Raise the strategy's R in both halves: trend with the 20-day SMA (+0.184R) and open drive (+0.190R). The trend filter doesn't change the hit rate; it makes the winners run further.
- No help: IB volume (tick volume from a CFD, so a weak proxy), FVG, IFVG, prior week high/low, prior day color.
- Best combination: trend + open drive, +0.327R per trade (2005–2012 +0.322R, 2013–2020 +0.336R), 56% wins, PF 1.84, ~12 trades a year. The combos were chosen after looking at the whole 2005–2020 sample, so they still need a test on data they have not seen (2021 onwards).

## Other markets (`ib_multi.py`)

Same pullback setup and filters on NAS100 and US2000 (cost 1.25 bps of price). Output in `results_multi.txt`.

It does not carry over: Nasdaq is negative (−0.03R base, −0.08R with the trend filter) and Russell is weak (+0.05 to +0.12R, t < 2). Same-day results correlate 0.6–0.9 with the S&P, so adding these markets brings more trades but little diversification. That the edge only shows on the S&P is a warning sign that part of it may be specific to that sample.
