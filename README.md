# scrape_ib
Scrapes interactive brokers for stock data

# install ibapi

Download API from http://interactivebrokers.github.io/#

```bash
cd ~/Downloads
sudo unzip twsapi_macunix.973.05.zip -d ~/  # need at least version 9.73
cd ~/IBJts/source/pythonclient
sudo python3 setup.py install
```

# data subscriptions
You will need `NASDAQ (Network C/UTP)` to get most US stocks (NASDAQ, NYSE, ...)
Right now it's $1.50/month.


# back-normalization
Should use this methodology to back-normalize:
https://www.quandl.com/data/EOD-End-of-Day-US-Stock-Prices/documentation/methodology
