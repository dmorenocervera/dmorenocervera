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
