# Initial Balance thirds — S&P 500 backtest

Setup: IB = high/low 9:30–10:30 ET. At 10:30, price in the top third → long with stop under the IB low; bottom third → short with stop over the IB high; middle → no trade.

Data: Oanda SPX500_USD 1-minute bars, 2005–2020 (3,064 full sessions), from [FutureSharks/financial-data](https://github.com/FutureSharks/financial-data). Costs: 0.5 index points per round trip. If stop and target fall in the same minute, the stop counts first.

Run: `python ib_backtest.py [path_to_SPX500_USD_folder]`. Full output in `results.txt`.

## Result

The statistic holds: the day's high or low is set in the IB on 69% of days; with price in the top third the IB low holds 55% of days (the IB high holds 14.7%), and in the bottom third the IB high holds 59% (the low holds 14.4%).

The trade does not: every target tested (0.5R–3R, IB extensions, exit at the close) and both stop placements lose between −0.05 and −0.12R per trade, in both halves of the sample and in 15 of 16 years (2008 is the exception). The stop is far from entry (≥ 2/3 of the IB), so it holds often, but the days it breaks cost a full R, and many days where it holds still close below entry.
