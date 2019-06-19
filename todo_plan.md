# Plan of attack

Start with one or a few tickers -- TSLA
Need to check that data is not missing
start with hard rules like in day trader book
https://www.investopedia.com/ask/answers/031115/what-common-strategy-traders-implement-when-using-volume-weighted-average-price-vwap.asp
  - e.g. if price crosses vwap, buy or sell if going up or down, and target upper bollinger band as exit; stop loss at vwap
Take 20 days of data, train on it, then predict on next day
Do this on a rolling basis and calculate metrics

Check - how often does first VWAP move carry through the day?
