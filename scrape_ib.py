# Gist example of IB wrapper from here: https://gist.github.com/robcarver17/f50aeebc2ecd084f818706d9f05c1eb4
#
# Download API from http://interactivebrokers.github.io/#
# (must be at least version 9.73)
#
# Install python API code /IBJts/source/pythonclient $ python3 setup.py install
#
# Note: The test cases, and the documentation refer to a python package called IBApi,
#    but the actual package is called ibapi. Go figure.
#
# Get the latest version of the gateway:
# https://www.interactivebrokers.com/en/?f=%2Fen%2Fcontrol%2Fsystemstandalone-ibGateway.php%3Fos%3Dunix
#    (for unix: windows and mac users please find your own version)
#
# Run the gateway
#
# user: edemo
# pwd: demo123
#

# duration units and bar sizes:
# https://interactivebrokers.github.io/tws-api/historical_bars.html#hd_duration
# limitations:
# https://interactivebrokers.github.io/tws-api/historical_limitations.html

import os
import time
import pprint
import queue
import datetime
import traceback
from pytz import timezone
from pathlib import Path

import pandas as pd
import numpy as np
from tqdm import tqdm
import seaborn as sns
import matplotlib.pyplot as plt

from ibapi.wrapper import EWrapper
from ibapi.client import EClient
from ibapi.contract import Contract as IBcontract
from threading import Thread

DEFAULT_HISTORIC_DATA_ID = 50
DEFAULT_GET_CONTRACT_ID = 43
DEFAULT_GET_NP_ID = 42
DEFAULT_GET_EARLIEST_ID = 1
DEFAULT_HISTORIC_NEWS_ID = 1001

## marker for when queue is finished
FINISHED = object()
STARTED = object()
TIME_OUT = object()

class finishableQueue(object):
    def __init__(self, queue_to_finish):

        self._queue = queue_to_finish
        self.status = STARTED


    def get(self, timeout):
        """
        Returns a list of queue elements once timeout is finished, or a FINISHED flag is received in the queue
        :param timeout: how long to wait before giving up
        :return: list of queue elements
        """
        contents_of_queue = []
        finished = False

        while not finished:
            try:
                current_element = self._queue.get(timeout=timeout)
                if current_element is FINISHED:
                    finished = True
                    self.status = FINISHED
                else:
                    contents_of_queue.append(current_element)
                    ## keep going and try and get more data

            except queue.Empty:
                ## If we hit a time out it's most probable we're not getting a finished element any time soon
                ## give up and return what we have
                finished = True
                self.status = TIME_OUT


        return contents_of_queue


    def timed_out(self):
        return self.status is TIME_OUT


class TestWrapper(EWrapper):
    """
    The wrapper deals with the action coming back from the IB gateway or TWS instance
    We override methods in EWrapper that will get called when this action happens, like currentTime
    Extra methods are added as we need to store the results in this object
    """
    def __init__(self):
        self._my_contract_details = {}
        self._my_historic_data_dict = {}
        self._my_earliest_timestamp_dict = {}
        self._my_np_dict = {}
        self._my_hn_dict = {}
        self._my_na_dict = {}
        self._my_errors = queue.Queue()


    ## error handling code
    def init_error(self):
        error_queue = queue.Queue()
        self._my_errors = error_queue


    def get_error(self, timeout=5):
        if self.is_error():
            try:
                return self._my_errors.get(timeout=timeout)
            except queue.Empty:
                return None

        return None


    def is_error(self):
        an_error_if=not self._my_errors.empty()
        return an_error_if


    def error(self, id, errorCode, errorString):
        ## Overriden method
        errormsg = "IB error id %d errorcode %d string %s" % (id, errorCode, errorString)
        self._my_errors.put(errormsg)


    ## get contract details code
    def init_contractdetails(self, reqId):
        self._my_contract_details[reqId] = queue.Queue()

        return self._my_contract_details[reqId]


    def contractDetails(self, reqId, contractDetails):
        ## overridden method

        if reqId not in self._my_contract_details.keys():
            self.init_contractdetails(reqId)

        self._my_contract_details[reqId].put(contractDetails)


    def contractDetailsEnd(self, reqId):
        ## overriden method
        if reqId not in self._my_contract_details.keys():
            self.init_contractdetails(reqId)

        self._my_contract_details[reqId].put(FINISHED)


    def init_historicprices(self, tickerid):
        self._my_historic_data_dict[tickerid] = queue.Queue()

        return self._my_historic_data_dict[tickerid]


    def init_earliest_timestamp(self, tickerid):
        self._my_earliest_timestamp_dict[tickerid] = queue.Queue()

        return self._my_earliest_timestamp_dict[tickerid]


    def init_np(self, tickerid):
        self._my_np_dict[tickerid] = queue.Queue()

        return self._my_np_dict[tickerid]


    def init_hn(self, requestId):
        self._my_hn_dict[requestId] = queue.Queue()

        return self._my_hn_dict[requestId]


    def init_na(self, requestId):
        self._my_na_dict[requestId] = queue.Queue()

        return self._my_na_dict[requestId]


    def historicalData(self, tickerid, bar):
        ## Overriden method
        ## Note I'm choosing to ignore barCount, WAP and hasGaps but you could use them if you like
        # pprint.pprint(bar.__dict__)
        bardata = (bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume)

        historic_data_dict = self._my_historic_data_dict

        ## Add on to the current data
        if tickerid not in historic_data_dict.keys():
            self.init_historicprices(tickerid)

        historic_data_dict[tickerid].put(bardata)


    def headTimestamp(self, tickerid, headTimestamp:str):
        ## overridden method
        if tickerid not in self._my_earliest_timestamp_dict.keys():
            self.init_earliest_timestamp(tickerid)

        self._my_earliest_timestamp_dict[tickerid].put(headTimestamp)

        self._my_earliest_timestamp_dict[tickerid].put(FINISHED)


    def newsProviders(self, newsProviders):
        ## overridden method
        tickerid = DEFAULT_GET_NP_ID
        if tickerid not in self._my_np_dict.keys():
            self.init_np(tickerid)

        self._my_np_dict[tickerid].put(newsProviders)
        self._my_np_dict[tickerid].put(FINISHED)


    def historicalDataEnd(self, tickerid, start:str, end:str):
        ## overriden method
        if tickerid not in self._my_historic_data_dict.keys():
            self.init_historicprices(tickerid)

        self._my_historic_data_dict[tickerid].put(FINISHED)


    def historicalNews(self, requestId, time, providerCode, articleId, headline):
        newsdata = (time, providerCode, articleId, headline)
        newsdict = self._my_hn_dict

        if requestId not in newsdict.keys():
            self.init_hn(requestId)

        newsdict[requestId].put(newsdata)


    def historicalNewsEnd(self, requestId, hasMore):
        if requestId not in self._my_hn_dict.keys():
            self.init_hn(requestId)

        if hasMore:
            print('more results available')

        self._my_hn_dict[requestId].put(FINISHED)


    def newsArticle(self, requestId, articleType, articleText):
        if requestId not in self._my_na_dict.keys():
            self.init_na(requestId)

        self._my_na_dict[requestId].put((articleType, articleText))
        self._my_na_dict[requestId].put(FINISHED)


class TestClient(EClient):
    """
    The client method
    We don't override native methods, but instead call them from our own wrappers
    """
    def __init__(self, wrapper):
        ## Set up with a wrapper inside
        EClient.__init__(self, wrapper)


    def resolve_ib_contract(self, ibcontract, reqId=DEFAULT_GET_CONTRACT_ID):

        """
        From a partially formed contract, returns a fully fledged version
        :returns fully resolved IB contract
        """

        ## Make a place to store the data we're going to return
        contract_details_queue = finishableQueue(self.init_contractdetails(reqId))

        print("Getting full contract details from the server... ")

        self.reqContractDetails(reqId, ibcontract)

        ## Run until we get a valid contract(s) or get bored waiting
        MAX_WAIT_SECONDS = 3
        new_contract_details = contract_details_queue.get(timeout = MAX_WAIT_SECONDS)

        while self.wrapper.is_error():
            print(self.get_error())

        if contract_details_queue.timed_out():
            print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

        if len(new_contract_details)==0:
            print("Failed to get additional contract details: returning unresolved contract")
            return ibcontract, new_contract_details

        if len(new_contract_details)>1:
            print("got multiple contracts; using first one")

        new_contract_details = new_contract_details[0]

        resolved_ibcontract = new_contract_details.contract

        return resolved_ibcontract, new_contract_details


    def get_IB_historical_data(self,
                                ibcontract,
                                whatToShow="TRADES",
                                durationStr="1 Y",
                                barSizeSetting="1 day",
                                tickerid=DEFAULT_HISTORIC_DATA_ID,
                                latest_date=None):

        """
        Returns historical prices for a contract, up to latest_date
        if latest_date is none, uses todays date
        latest_date should be of form %Y%m%d %H:%M:%S %Z

        ibcontract is a Contract
        :returns list of prices in 4 tuples: Open high low close volume
        """
        # set latest_date to today and now if it is None
        if latest_date is None:
            latest_date = get_latest_date_local()

        ## Make a place to store the data we're going to return
        historic_data_queue = finishableQueue(self.init_historicprices(tickerid))

        # Request some historical data. Native method in EClient
        self.reqHistoricalData(
            tickerid,  # tickerId,
            ibcontract,  # contract,
            latest_date,  # endDateTime,
            durationStr,  # durationStr,
            barSizeSetting,  # barSizeSetting,
            whatToShow=whatToShow,
            useRTH=1,
            formatDate=1,
            keepUpToDate=False,  #  <<==== added for api 9.73.2
            chartOptions=[] ## chartOptions not used
        )



        ## Wait until we get a completed data, an error, or get bored waiting
        MAX_WAIT_SECONDS = 5
        print("Getting historical data from the server... could take %d seconds to complete " % MAX_WAIT_SECONDS)

        historic_data = historic_data_queue.get(timeout=MAX_WAIT_SECONDS)

        while self.wrapper.is_error():
            er = self.get_error()
            print(er)
            if "HMDS query returned no data" in er:
                print(historic_data)
                print(historic_data is None)

        if historic_data_queue.timed_out():
            print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

        # TODO: this is cancelling query early maybe?
        self.cancelHistoricalData(tickerid)

        # convert to pandas dataframe
        # date, open, high, low, close, vol
        # already adjusted for splits
        if len(historic_data) != 0:
            df = pd.DataFrame.from_records(data=historic_data, index='datetime', columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
            df.index = pd.to_datetime(df.index)
            if whatToShow != 'TRADES':
                # volume only available for trades
                df.drop('volume', axis=1, inplace=True)

            return df
        else:
            return historic_data


    def getEarliestTimestamp(self, contract, whatToShow='TRADES', useRTH=1, formatDate=1, tickerid=DEFAULT_GET_EARLIEST_ID):
        # parameters: https://interactivebrokers.github.io/tws-api/classIBApi_1_1EClient.html#a059b5072d1e8e8e96394e53366eb81f3

        ## Make a place to store the data we're going to return


        ## Wait until we get a completed data, an error, or get bored waiting
        MAX_WAIT_SECONDS = 2
        tries = 0
        while True:
            tries += 1
            earliest_timestamp_queue = finishableQueue(self.init_earliest_timestamp(tickerid))
            self.reqHeadTimeStamp(tickerid, contract, whatToShow, useRTH, formatDate)
            print("Getting earliest timestamp from the server... could take %d seconds to complete " % MAX_WAIT_SECONDS)

            earliest = earliest_timestamp_queue.get(timeout=MAX_WAIT_SECONDS)

            while self.wrapper.is_error():
                er = self.get_error()
                print(er)
                if 'No head time stamp' in er:
                    return None
                    break

            if earliest_timestamp_queue.timed_out():
                print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

            self.cancelHeadTimeStamp(tickerid)
            if len(earliest) != 0 or tries == 20:
                return None
                break

        return earliest[0]  # first element in list


    def getNewsProviders(self):
        """
        available news providers by default are
        [140007057343600: BRFG, Briefing.com General Market Columns,
         140007057342704: BRFUPDN, Briefing.com Analyst Actions,
         140007057343544: DJNL, Dow Jones Newsletters]
        """
        ## Make a place to store the data we're going to return
        tickerid = DEFAULT_GET_NP_ID
        np_queue = finishableQueue(self.init_np(tickerid))

        # Request news providers. Native method in EClient
        self.reqNewsProviders()

        ## Wait until we get a completed data, an error, or get bored waiting
        MAX_WAIT_SECONDS = 2
        print("Getting list of news providers from the server... could take %d seconds to complete " % MAX_WAIT_SECONDS)

        nps = np_queue.get(timeout=MAX_WAIT_SECONDS)

        while self.wrapper.is_error():
            print(self.get_error())

        if np_queue.timed_out():
            print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

        return nps[0]  # list within a list


    def getHistoricalNews(self, reqId, conId, providerCodes, startDateTime, endDateTime, totalResults):
        hn_queue = finishableQueue(self.init_hn(reqId))

        self.reqHistoricalNews(reqId, conId, providerCodes, startDateTime, endDateTime, totalResults, historicalNewsOptions=[])

        ## Wait until we get a completed data, an error, or get bored waiting
        MAX_WAIT_SECONDS = 15
        print("Getting historical news from the server... could take %d seconds to complete " % MAX_WAIT_SECONDS)

        hn = hn_queue.get(timeout=MAX_WAIT_SECONDS)

        while self.wrapper.is_error():
            print(self.get_error())

        if hn_queue.timed_out():
            print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

        return hn


    def getNewsArticle(self, reqId, providerCode, articleId):
        na_queue = finishableQueue(self.init_na(reqId))

        self.reqNewsArticle(reqId, providerCode, articleId, [])

        ## Wait until we get a completed data, an error, or get bored waiting
        MAX_WAIT_SECONDS = 5
        print("Getting historical news from the server... could take %d seconds to complete " % MAX_WAIT_SECONDS)

        na = na_queue.get(timeout=MAX_WAIT_SECONDS)

        while self.wrapper.is_error():
            print(self.get_error())

        if na_queue.timed_out():
            print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

        return na


class TestApp(TestWrapper, TestClient):
    def __init__(self, ipaddress, portid, clientid):
        TestWrapper.__init__(self)
        TestClient.__init__(self, wrapper=self)

        self.connect(ipaddress, portid, clientid)

        thread = Thread(target = self.run)
        thread.start()

        setattr(self, "_thread", thread)

        self.init_error()


    def get_hist_data_date_range(self,
                                ibcontract,
                                whatToShow='TRADES',
                                barSizeSetting='3 mins',
                                start_date=None,
                                end_date=None,
                                tickerid=DEFAULT_HISTORIC_DATA_ID):
        """
        gets historic data for date range
        if start_date is None, then first finds earliest date available,
        and gets all data to there

        if end_date is None, will get data to latest possible time

        start_date and end_date should be strings in format YYYYMMDD

        useful options for whatToShow for stocks can be:
            TRADES
            BID
            ASK
            OPTION_IMPLIED_VOLATILITY
            HISTORICAL_VOLATILITY

        """
        # convert start_date string to datetime date object for comparisons
        start_date_datetime_date = pd.to_datetime('1800-01-01').date()  # early date so it doesn't match df.index.date below (if not updating data)
        if start_date is not None:
            # go one day past start date just to make sure we have all data
            start_date_datetime_date = (pd.to_datetime(start_date) - pd.Timedelta('1D')).date()

        smallbars = ['1 secs', '5 secs', '10 secs', '15 secs', '30 secs', '1 min']
        max_step_sizes = {'1 secs': '1800 S',  # 30 mins
                            '5 secs': '3600 S',  # 1 hour
                            '10 secs': '14400 S',  # 4 hours
                            '15 secs': '14400 S',  # 4 hours
                            '30 secs': '28800 S',  # 8 hours
                            '1 min': '1 D',
                            '2 mins': '2 D',
                            '3 mins': '1 W',
                            '5 mins': '1 W',
                            '10 mins': '1 W',
                            '15 mins': '1 W',
                            '20 mins': '1 W',
                            '30 mins': '1 M',
                            '1 hour': '1 M',
                            '2 hours': '1 M',
                            '3 hours': '1 M',
                            '4 hours': '1 M',
                            '8 hours': '1 M',
                            '1 day': '1 Y',
                            '1 week': '1 Y',
                            '1 month': '1 Y'}

        # TODO: check if earliest timestamp is nothing or before/after end_date
        earliest_timestamp = self.getEarliestTimestamp(ibcontract, whatToShow=whatToShow, tickerid=tickerid)
        if earliest_timestamp is not None:
            earliest_datestamp = earliest_timestamp[:8]
        # if timeout, will return empty list
        df = []

        if end_date is None:
            latest_date = None
        else:
            # TODO: need to adopt this to other than mountain time
            latest_date = end_date + ' ' + get_close_hour_local() + ':00:00'

        # list is returned if there is an error or something?
        tries = 0
        while type(df) is list:
            tries += 1
            df = self.get_IB_historical_data(ibcontract,
                                            whatToShow=whatToShow,
                                            durationStr=max_step_sizes[barSizeSetting],
                                            barSizeSetting=barSizeSetting,
                                            tickerid=tickerid,
                                            latest_date=latest_date)
            if tries == 10:
                print('tried to get historic data 10x and failed, retutrning None')
                return None

        earliest_date = df.index[0]
        full_df = df
        self.df = full_df
        df_dates = df.index.date

        # keep going until the same result is returned twice...not perfectly efficient but oh well
        previous_earliest_date = None
        i = 0
        start_time = time.time()
        is_list = 0
        while previous_earliest_date != earliest_date:
            i += 1
            print(i)
            print(previous_earliest_date)
            print(earliest_date)
            # TODO: if "HMDS query returned no data" in error lots of times, maybe finish it
            df = self.get_IB_historical_data(ibcontract,
                                            whatToShow=whatToShow,
                                            durationStr=max_step_sizes[barSizeSetting],
                                            barSizeSetting=barSizeSetting,
                                            tickerid=tickerid,
                                            latest_date=earliest_date.strftime('%Y%m%d %H:%M:%S'))
            if type(df) is list:
                is_list += 1
                # we've probably hit the earliest time we can get
                if earliest_timestamp is not None:
                    if is_list >= 3 and earliest_date.date().strftime('%Y%m%d') == earliest_datestamp:
                        print("hit earliest timestamp")
                        break
                if is_list >= 10:
                    print('hit 10 lists in a row')
                    break

                df_dates = None

                continue
            else:
                is_list = 0
                previous_earliest_date = earliest_date
                earliest_date = df.index[0]
                full_df = pd.concat([df, full_df])
                self.df = full_df
                df_dates = df.index.date
                if df_dates.min() <= start_date_datetime_date:
                    print('start_date_datetime in dates, ending')
                    break

            # no more than 6 requests every 2s for bars under 30s
            # https://interactivebrokers.github.io/tws-api/historical_limitations.html
            # TODO: take care of 60 requests per 10 mins
            if barSizeSetting in smallbars and i >= 6:
                time_left = 2 - (time.time() - start_time())
                i = 0
                time.sleep(time_left)

        return full_df


    def get_stock_contract(self, ticker='SNAP', reqId=DEFAULT_HISTORIC_DATA_ID):
        """
        gets resolved IB contract for stocks

        assumes ISLAND exchange for now (NASDAQ and maybe others?)
        """
        # available sec types: https://interactivebrokers.github.io/tws-api/classIBApi_1_1Contract.html#a4f83111c0ea37a19fe1dae98e3b67456
        ibcontract = IBcontract()
        ibcontract.secType = 'STK'
        # get todays date, format as YYYYMMDD -- need to check this is correct
        # today = datetime.datetime.today().strftime('%Y%m%d')
        # ibcontract.lastTradeDateOrContractMonth = '20180711'#today
        ibcontract.symbol = ticker
        ibcontract.exchange = 'ISLAND'

        resolved_ibcontract, contract_details = self.resolve_ib_contract(ibcontract=ibcontract, reqId=reqId)

        return resolved_ibcontract, contract_details


    def download_all_history_stock(self, ticker='SNAP', barSizeSetting='3 mins', reqId=DEFAULT_HISTORIC_DATA_ID):
        """
        downloads all historical data for a stock including
            TRADES
            BID
            ASK
            OPTION_IMPLIED_VOLATILITY

        if data already exists, updates and appends to it
        """
        contract, contract_details = self.get_stock_contract(ticker=ticker, reqId=reqId)
        folder = 'data/'  # TODO: set this as a full path instead of relative
        trades_start_date = None
        bids_start_date = None
        asks_start_date = None
        opt_vol_start_date = None
        mode = 'w'
        bss = barSizeSetting.replace(' ', '_')
        trades_filename = folder + ticker + '_trades_' + bss + '.h5'
        bid_filename = folder + ticker + '_bid_' + bss + '.h5'
        ask_filename = folder + ticker + '_ask_' + bss + '.h5'
        opt_vol_filename = folder + ticker + '_opt_vol_' + bss + '.h5'

        # TODO: provide option for which files to download;
        # check each file individually and update individually
        if os.path.exists(trades_filename):
            print('trades file exists, going to append...')
            cur_trades = pd.read_hdf(trades_filename)
            cur_bids = pd.read_hdf(bid_filename)
            cur_asks = pd.read_hdf(ask_filename)
            cur_opt_vol = pd.read_hdf(opt_vol_filename)

            latest_trades_datetime = cur_trades.index[-1]
            trades_start_date = latest_trades_datetime.strftime('%Y%m%d')

            latest_bids_datetime = cur_bids.index[-1]
            bids_start_date = latest_bids_datetime.strftime('%Y%m%d')

            latest_asks_datetime = cur_asks.index[-1]
            asks_start_date = latest_asks_datetime.strftime('%Y%m%d')

            latest_opt_vol_datetime = cur_opt_vol.index[-1]
            opt_vol_start_date = latest_opt_vol_datetime.strftime('%Y%m%d')
            print('latest date is around', trades_start_date)
            mode = 'r+'  # append to existing files, should throw error if they don't exist

        end_date = None#'20170401'  # smaller amount of data for prototyping/testing
        print('\n\n\ngetting trades...\n\n\n')
        trades = self.get_hist_data_date_range(contract, barSizeSetting=barSizeSetting, end_date=end_date, start_date=trades_start_date, tickerid=reqId)
        print('\n\n\ngetting bids...\n\n\n')
        bid = self.get_hist_data_date_range(contract, barSizeSetting=barSizeSetting, whatToShow='BID', end_date=end_date, start_date=bids_start_date, tickerid=reqId)
        print('\n\n\ngetting asks...\n\n\n')
        ask = self.get_hist_data_date_range(contract, barSizeSetting=barSizeSetting, whatToShow='ASK', end_date=end_date, start_date=asks_start_date, tickerid=reqId)
        print('\n\n\ngetting opt_vol...\n\n\n')
        opt_vol = self.get_hist_data_date_range(contract, barSizeSetting=barSizeSetting, whatToShow='OPTION_IMPLIED_VOLATILITY', end_date=end_date, start_date=opt_vol_start_date, tickerid=reqId)

        # write or append data
        # TODO: function for cleaning up data and remove duplicates, sort data
        # TODO: only append things after the latest datetime, and do it for trades, bid, etc separately
        # if appending, get next index after latest existing datetime
        append = False  # need to set option in to_hdf
        if mode == 'r+':
            next_trades_idx = trades.loc[latest_trades_datetime:]
            if next_trades_idx.shape[0] <= 1 or cur_trades.iloc[-1].equals(trades.iloc[-1]):
                print('already have all the data I think, exiting')
                return

            next_trades_idx = next_trades_idx.index[1]
            trades = trades.loc[next_trades_idx:]

            next_bids_idx = bid.loc[latest_bids_datetime:]
            next_bids_idx = next_bids_idx.index[1]
            bid = bid.loc[next_bids_idx:]

            next_asks_idx = ask.loc[latest_asks_datetime:]
            next_asks_idx = next_asks_idx.index[1]
            ask = ask.loc[next_asks_idx:]

            next_opt_vol_idx = opt_vol.loc[latest_opt_vol_datetime:]
            next_opt_vol_idx = next_opt_vol_idx.index[1]
            opt_vol = opt_vol.loc[next_opt_vol_idx:]
            append = True

        trades.to_hdf(trades_filename, key='data', format='table', complevel=9, complib='blosc:lz4', mode=mode, append=append)
        bid.to_hdf(bid_filename, key='data', format='table', complevel=9, complib='blosc:lz4', mode=mode, append=append)
        ask.to_hdf(ask_filename, key='data', format='table', complevel=9, complib='blosc:lz4', mode=mode, append=append)
        opt_vol.to_hdf(opt_vol_filename, key='data', format='table', complevel=9, complib='blosc:lz4', mode=mode, append=append)


    def get_earliest_dates(self, ticker):
        contract, contract_details = self.get_stock_contract(ticker=ticker)
        for t in ['TRADES', 'BID', 'ASK', 'OPTION_IMPLIED_VOLATILITY']:
            earliest = self.getEarliestTimestamp(contract, tickerid=200)
            print(t)
            print(earliest)


def get_datetime_from_date(date='2018-06-30'):
    """
    not sure if I need this anymore...


    converts a date to a datetime (end-of-day) for historical data gathering

    date should be a string in format YYYYMMDD

    uses eastern timezone (EDT or EST) by default

    TODO: convert eastern to local timezone from machine
    """
    tz='US/Eastern'
    tz_obj = timezone(tz)
    date = datetime.datetime.strptime(date, '%Y-%m-%d')
    date = date.replace(hour = 16, minute = 0, second = 0)
    date = tz_obj.localize(date)

    return date.strftime('%Y%m%d %H:%M:%S %Z')


def get_latest_date_local():
    """
    gets the latest date with the machine's local timezone

    endDateTime and startDateTime "Uses TWS timezone specified at login."
    at least for tick-by-tick data
    """
    machines_tz = datetime.datetime.now(datetime.timezone.utc).astimezone().tzname()

    latest_date = datetime.datetime.today()
    # doesn't work with machines tz in there
    latest_date = latest_date.strftime('%Y%m%d %H:%M:%S')# + machines_tz

    return latest_date


def get_close_hour_local():
    """
    gets closing hour in local machine time (4 pm Eastern)
    """
    eastern_tz = timezone('US/Eastern')
    eastern_close = datetime.datetime(year=2018, month=6, day=29, hour=16)
    eastern_close = eastern_tz.localize(eastern_close)

    return str(eastern_close.astimezone().hour)


def get_home_dir(repo_name='scrape_ib'):
    cwd = str(Path(__file__).resolve())
    cwd_list = cwd.split('/')
    repo_position = [i for i, s in enumerate(cwd_list) if s == repo_name]
    if len(repo_position) > 1:
        print("error!  more than one intance of repo name in path")
        return None

    home_dir = '/'.join(cwd_list[:repo_position[0] + 1]) + '/'
    return home_dir


def load_data(ticker='SNAP', barSizeSetting='3 mins'):
    """
    loads historical tick data
    """
    folder = get_home_dir() + 'data/'
    bss = barSizeSetting.replace(' ', '_')

    trades = pd.read_hdf(folder + ticker + '_trades_' + bss + '.h5')
    # fill 0 volume with 1
    trades.at[trades['volume'] == 0, 'volume'] = 1
    bid = pd.read_hdf(folder + ticker + '_bid_' + bss + '.h5')
    ask = pd.read_hdf(folder + ticker + '_ask_' + bss + '.h5')
    opt_vol = pd.read_hdf(folder + ticker + '_opt_vol_' + bss + '.h5')

    # drop duplicates just in case...dupes throw off concat
    trades.drop_duplicates(inplace=True)
    bid.drop_duplicates(inplace=True)
    ask.drop_duplicates(inplace=True)
    opt_vol.drop_duplicates(inplace=True)

    # rename columns so can join to one big dataframe
    bid.columns = ['bid_' + c for c in bid.columns]
    ask.columns = ['ask_' + c for c in ask.columns]
    opt_vol.columns = ['opt_vol_' + c for c in opt_vol.columns]

    # inner join should drop na's but just to be safe
    # opt_vol has missing values at the end of each day for some reason...
    # so cant do inner join or dropna
    full_df = pd.concat([trades, bid, ask, opt_vol], axis=1)#, join='inner').dropna()
    full_df.index = full_df.index.tz_localize('America/New_York')

    return full_df


def make_feats_targs_individ_df(df, future_gap_idx_steps, future_span_idx_steps, feature_span, feature_span_idx_steps):
    """
    """
    targets = df['close'].pct_change(future_span_idx_steps).shift(-future_gap_idx_steps - future_span_idx_steps)

    pct_change_features = df.copy().pct_change(feature_span_idx_steps, axis=0)
    pct_change_features.columns = [c + '_' + str(feature_span) + '_min_pct_chg' for c in pct_change_features.columns]

    df['targets'] = targets

    # inner join should drop na's but just to be safe
    feats_targs = pd.concat([df, pct_change_features], axis=1, join='inner').dropna()

    feat_cols = [c for c in feats_targs.columns if c != 'targets']

    return feats_targs[feat_cols], feats_targs['targets']


def make_features_targets(full_df, future_gap=0, future_span=15, feature_span=15, intraday=True):
    """
    uses close price to make targets -- percent change over certain time in future

    features are percent change of other columns as well as raw values

    future_gap is number of minutes between current time and start of future pct_change
    future_span is number of minutes to calculate price percent change
    feature_span is number of minutes to calculate pct change of everything in df
    intraday is boolean; if True, will only get features/targs within each day
        and not extending over close/open times
    """
    # copy full_df so we don't modify it
    full_df_copy = full_df.copy()

    # get number of minutes between timesteps -- won't work if not integer minutes
    minute_gap = (full_df_copy.index[1] - full_df_copy.index[0]).seconds // 60
    future_gap_idx_steps = future_gap // minute_gap
    future_span_idx_steps = future_span // minute_gap
    feature_span_idx_steps = feature_span // minute_gap

    # TODO: use dask or multicore/multithread
    if intraday:
        # get dataframes for each day and make feats targs, then join
        days = [idx.date() for idx in full_df_copy.index]
        unique_days = np.unique(days)
        all_feats, all_targs = [], []
        for d in tqdm(unique_days):
            df = full_df_copy[full_df_copy.index.date == d].copy()
            d_feats, d_targs = make_feats_targs_individ_df(df,
                                    future_gap_idx_steps=future_gap_idx_steps,
                                    future_span_idx_steps=future_span_idx_steps,
                                    feature_span=feature_span,
                                    feature_span_idx_steps=feature_span_idx_steps)
            all_feats.append(d_feats)
            all_targs.append(d_targs)

        return pd.concat(all_feats), pd.concat(all_targs)

    else:
        # get feats and targets in bulk
        return make_feats_targs_individ_df(full_df,
                                future_gap_idx_steps=future_gap_idx_steps,
                                future_span_idx_steps=future_span_idx_steps,
                                feature_span=feature_span,
                                feature_span_idx_steps=feature_span_idx_steps)


def check_autocorrelations():
    """
    checks autocorrelations between previous price changes and future price changes
    over a range of timesteps
    """


if __name__ == '__main__':
    app = TestApp("127.0.0.1", 7496, 1)

    # test getting historical data range
    # contract, contract_details = app.get_stock_contract('SNAP')
    # df = app.get_hist_data_date_range(ibcontract=contract, start_date='2018-08-01')

    # TODO: update data on both sides -- new and old
    # tickers that have data back to earliest date
    # 'VUZI',    'TVIX', 'OSTK'
    # tickers = ['IQ']

    def download_lots_of_stocks(update=False):
        # TODO: skip stocks fully up-to-date
        # deal with missing tickers, e.g. DJIA
        # DJIA
        # Getting full contract details from the server...
        # IB error id 122 errorcode 366 string No historical data query found for ticker id:122
        # IB error id 123 errorcode 200 string No security definition has been found for the request

        import sys
        sys.path.append('../stocks_emotional_analysis/stocktwits')
        import get_st_data as  gs

        tickers = gs.get_stock_watchlist(update=update)

        exceptions = {}
        reqId = 100
        for t in tickers:
            # IB error id 107 errorcode 162 string Historical Market Data Service error message:HMDS query returned no data: AQ@ISLAND Option_Implied_Volatility
            # Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour
            # Getting historical data from the server... could take 5 seconds to complete
            # IB error id 107 errorcode 366 string No historical data query found for ticker id:107
            # IB error id 107 errorcode 162 string Historical Market Data Service error message:HMDS query returned no data: AQ@ISLAND Option_Implied_Volatility
            # Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour

            if t in ['BTC.X', 'ETH.X'] or '.X' in t:  # e.g. DASH.X ... I think all crypto ends in .X
                continue
            # TODO: catch connection lost errors
            print(t)
            reqId += 1
            try:
                app.download_all_history_stock(ticker=t, reqId=reqId)
            except Exception as e:
                print(e)
                exceptions[t] = e
                traceback.print_tb(e.__traceback__)
                continue

    # problem with VUZI:
    """
    getting opt_vol...



    Getting eariest timestamp from the server... could take 2 seconds to complete
    IB error id 50 errorcode 366 string No historical data query found for ticker id:50
    Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour
    ---------------------------------------------------------------------------
    IndexError                                Traceback (most recent call last)
    ~/github/scrape_ib/scrape_ib.py in <module>()
        888     for t in tickers:
        889         print(t)
    --> 890         app.download_all_history_stock(ticker=t)
        891
        892

    ~/github/scrape_ib/scrape_ib.py in download_all_history_stock(self, ticker, barSizeSetting)
        676         ask = self.get_hist_data_date_range(contract, barSizeSetting=barSizeSetting, whatToShow='ASK', end_date=end_date, start_date=start_date)
        677         print('\n\n\ngetting opt_vol...\n\n\n')
    --> 678         opt_vol = self.get_hist_data_date_range(contract, barSizeSetting=barSizeSetting, whatToShow='OPTION_IMPLIED_VOLATILITY', end_date=end_date, start_date=start_date)
        679
        680         # write or append data

    ~/github/scrape_ib/scrape_ib.py in get_hist_data_date_range(self, ibcontract, whatToShow, barSizeSetting, start_date, end_date, tickerid)
        542
        543         # TODO: check if earliest timestamp is nothing or before/after end_date
    --> 544         earliest_timestamp = self.getEarliestTimestamp(ibcontract, whatToShow=whatToShow)
        545         earliest_datestamp = earliest_timestamp[:8]
        546         # if timeout, will return empty list

    ~/github/scrape_ib/scrape_ib.py in getEarliestTimestamp(self, contract, whatToShow, useRTH, formatDate, tickerid)
        401         self.cancelHeadTimeStamp(tickerid)
        402
    --> 403         return earliest[0]  # first element in list
        404
        405

    IndexError: list index out of range
    """


    def test_news():
        # app.getNewsProviders()
        aapl, aapl_details = app.get_stock_contract(ticker='AAPL', reqId=304)
        # IBM conId = 8314
        hn1 = app.getHistoricalNews(reqId=DEFAULT_HISTORIC_NEWS_ID, conId=aapl.conId, providerCodes='BRFG', startDateTime="", endDateTime="", totalResults=1000)
        hn2 = app.getHistoricalNews(reqId=DEFAULT_HISTORIC_NEWS_ID, conId=aapl.conId, providerCodes='BRFUPDN', startDateTime="", endDateTime="", totalResults=1000)
        hn3 = app.getHistoricalNews(reqId=DEFAULT_HISTORIC_NEWS_ID, conId=aapl.conId, providerCodes='DJNL', startDateTime="", endDateTime="", totalResults=1000)

        na1 = app.getNewsArticle(1009, providerCode='BRFG', articleId=hn1[0][2])

        na2 = app.getNewsArticle(1009, providerCode='BRFUPDN', articleId=hn2[0][2])

    # aapl = app.get_stock_contract(ticker='AAPL')
    # app.getEarliestTimestamp(contract=aapl, whatToShow='OPTION_IMPLIED_VOLATILITY')
    # np.unique(df[df['opt_vol_high'] > 0.5].index.date)
    # plt.scatter(df['opt_vol_high'], df['close'])
    # # tickers = ['IQ', 'TSLA', 'AMD', 'AYX', 'LNG', 'MU', 'AAPL']
    # tickers = ['AAPL']
    # for t in tickers:
    #     app.download_all_history_stock(ticker=t)

    def snap_analysis():
        ticker = 'SNAP'
        full_df = load_data(ticker=ticker)
        feature_span = 30
        feats, targs = make_features_targets(full_df, future_span=30, feature_span=feature_span)
        feats_targs = feats.copy()
        # make bid-ask close differences
        # high and open seem to be most correlated
        feats_targs['ask_bid_close'] = feats_targs['ask_close'] - feats_targs['bid_close']
        feats_targs['ask_bid_open'] = feats_targs['ask_open'] - feats_targs['bid_open']
        feats_targs['ask_bid_high'] = feats_targs['ask_high'] - feats_targs['bid_high']
        feats_targs['ask_bid_low'] = feats_targs['ask_low'] - feats_targs['bid_low']

        feats_targs['targets'] = targs


        # with future_gap=0, future_span=15, feature_span=15, intraday=True
        #
        # with future_span=60, feature_span=60
        #
        # with future_span=30, feature_span=30
        #

        f = plt.figure(figsize=(12, 12))
        sns.heatmap(feats_targs.corr())
        plt.tight_layout()

        # features that are highly correlated:
        # OHLC with all bids and OHLC -- keep only close
        # all bids with all bids
        # same for OHLC and bid changes
        # all opt_vol with each other, except high with others is a bit less correlated
        # volume with itself and % change
        # bid/ask with itself and each other

        # gets more correlated with target as time shortens
        f = plt.figure(figsize=(12, 12))
        sns.heatmap(feats_targs.iloc[-10000:].corr())
        plt.tight_layout()

        plt.scatter(feats['close_15_min_pct_chg'], targs)
        plt.scatter(feats['close'], targs)
        # when opt_vol_high is very high, seems to be highly correlated with price change over 30 mins
        plt.scatter(feats['opt_vol_high'], targs)
        # all on one day -- 12/09/2017
        feats_targs[['opt_vol_high', 'targets']][feats['opt_vol_high'] > 5]
        # nothing for opt vol low for SNAP
        plt.scatter(feats['opt_vol_low'], targs)

        targs.plot.hist(bins=30)

        from sklearn.ensemble import RandomForestRegressor

        # trim features
        feats_trimmed = feats.copy()
        fs = str(feature_span)
        droplist = ['open',
                    'high',
                    'low',
                    'close',
                    'bid_open',
                    'bid_high',
                    'bid_low',
                    'bid_close',
                    'ask_open',
                    'ask_high',
                    'ask_low',
                    'ask_close',
                    'opt_vol_open',
                    'opt_vol_low',
                    'open_' + fs + '_min_pct_chg',
                    'high_' + fs + '_min_pct_chg',
                    'low_' + fs + '_min_pct_chg',
                    'bid_open_' + fs + '_min_pct_chg',
                    'bid_high_' + fs + '_min_pct_chg',
                    'bid_low_' + fs + '_min_pct_chg',
                    'bid_close_' + fs + '_min_pct_chg',
                    'ask_open_' + fs + '_min_pct_chg',
                    'ask_high_' + fs + '_min_pct_chg',
                    'ask_low_' + fs + '_min_pct_chg',
                    'ask_close_' + fs + '_min_pct_chg']
        feats_trimmed.drop(droplist, axis=1, inplace=True)

        # take last 25% of data -- about 2 months for SNAP currently (7-2018)
        trim_loc = int(0.75 * feats_trimmed.shape[0])
        feats_trimmed_small = feats_trimmed.iloc[trim_loc:]
        targs_trimmed_small = targs.iloc[trim_loc:]
        train_size = 0.85
        train_idx = int(train_size * feats_trimmed_small.shape[0])
        train_feats = feats_trimmed_small.iloc[:train_idx]
        train_targs = targs_trimmed_small.iloc[:train_idx]
        test_feats = feats_trimmed_small.iloc[train_idx:]
        test_targs = targs_trimmed_small.iloc[train_idx:]

        start = time.time()
        rfr = RandomForestRegressor(n_estimators=500, n_jobs=-1, min_samples_split=10, random_state=42)
        end = time.time()
        rfr.fit(train_feats, train_targs)
        print('training took:', int(start - end), 'seconds')
        print(rfr.score(train_feats, train_targs))
        print(rfr.score(test_feats, test_targs))
        plt.scatter(train_targs, rfr.predict(train_feats))
        plt.scatter(test_targs, rfr.predict(test_feats))

        feature_importances = rfr.feature_importances_
        fi_idx = np.argsort(feature_importances)[::-1]
        x = np.arange(len(feature_importances))
        f = plt.figure(figsize=(12, 12))
        plt.bar(x, feature_importances[fi_idx])
        plt.xticks(x, train_feats.columns[fi_idx], rotation=90)
        plt.tight_layout()

        plt.scatter(feats_trimmed_small['close'], targs_trimmed_small)

    #snap_contract, snap_details = app.get_stock_contract(ticker='SNAP')

    # seems to be a bit more data for 3m 1W compared with 1m 1D (650 vs 390)
    # historic_data = app.get_IB_historical_data(snap_contract, durationStr="1 D", barSizeSetting="1 min", latest_date='20170305 14:00:00')#'20180504 14:30:00')

    # seems to have weird issues with short bars, and seems to be a long-term indicator
    # snap_vol = app.get_hist_data_date_range(snap_contract, barSizeSetting='3 mins', whatToShow='HISTORICAL_VOLATILITY', end_date='20170425')

    # get earliest timestamp
    # earliest = app.getEarliestTimestamp(snap_contract, tickerid=200)

    # get list of news providers
    # nps = app.getNewsProviders()

    # look for period with highest autocorrelation and use that as prediction period

    # make a learning curve, look at scores with varying amount of data

    #app.disconnect()
