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

import time
import pprint
import queue
import datetime
from pytz import timezone

import pandas as pd

from ibapi.wrapper import EWrapper
from ibapi.client import EClient
from ibapi.contract import Contract as IBcontract
from threading import Thread

DEFAULT_HISTORIC_DATA_ID = 50
DEFAULT_GET_CONTRACT_ID = 43
DEFAULT_GET_NP_ID = 42
DEFAULT_GET_EARLIEST_ID = 1

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


    def historicalData(self, tickerid , bar):
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
            return ibcontract

        if len(new_contract_details)>1:
            print("got multiple contracts using first one")

        new_contract_details = new_contract_details[0]

        resolved_ibcontract = new_contract_details.contract

        return resolved_ibcontract


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
            print(self.get_error())

        if historic_data_queue.timed_out():
            print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

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
        earliest_timestamp_queue = finishableQueue(self.init_earliest_timestamp(tickerid))

        self.reqHeadTimeStamp(tickerid, contract, whatToShow, useRTH, formatDate)

        ## Wait until we get a completed data, an error, or get bored waiting
        MAX_WAIT_SECONDS = 2
        print("Getting eariest timestamp from the server... could take %d seconds to complete " % MAX_WAIT_SECONDS)

        earliest = earliest_timestamp_queue.get(timeout=MAX_WAIT_SECONDS)

        while self.wrapper.is_error():
            print(self.get_error())

        if earliest_timestamp_queue.timed_out():
            print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

        self.cancelHeadTimeStamp(tickerid)

        return earliest[0]  # first element in list


    def getNewsProviders(self):
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
        earliest_timestamp = self.getEarliestTimestamp(ibcontract, whatToShow=whatToShow)
        earliest_datestamp = earliest_timestamp[:8]
        # if timeout, will return empty list
        df = []

        if end_date is None:
            latest_date = None
        else:
            # TODO: need to adopt this to other than mountain time
            latest_date = end_date + ' ' + get_close_hour_local() + ':00:00'

        while type(df) is list:
            df = self.get_IB_historical_data(ibcontract,
                                            whatToShow=whatToShow,
                                            durationStr=max_step_sizes[barSizeSetting],
                                            barSizeSetting=barSizeSetting,
                                            tickerid=tickerid,
                                            latest_date=latest_date)

        earliest_date = df.index[0]
        full_df = df
        self.df = full_df

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
            df = self.get_IB_historical_data(ibcontract,
                                            whatToShow=whatToShow,
                                            durationStr=max_step_sizes[barSizeSetting],
                                            barSizeSetting=barSizeSetting,
                                            tickerid=tickerid,
                                            latest_date=earliest_date.strftime('%Y%m%d %H:%M:%S'))
            if type(df) is list:
                is_list += 1
                # we've probably hit the earliest time we can get
                if is_list >= 3 and earliest_date.date().strftime('%Y%m%d') == earliest_datestamp:
                    break

                continue

            previous_earliest_date = earliest_date
            earliest_date = df.index[0]
            full_df = pd.concat([df, full_df])
            self.df = full_df
            is_list = 0


            # no more than 6 requests every 2s for bars under 30s
            # https://interactivebrokers.github.io/tws-api/historical_limitations.html
            # TODO: take care of 60 requests per 10 mins
            if barSizeSetting in smallbars and i >= 6:
                time_left = 2 - (time.time() - start_time())
                i = 0
                time.sleep(time_left)


        return full_df


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


if __name__ == '__main__':
    app = TestApp("127.0.0.1", 7496, 1)

    # available sec types: https://interactivebrokers.github.io/tws-api/classIBApi_1_1Contract.html#a4f83111c0ea37a19fe1dae98e3b67456
    ibcontract = IBcontract()
    ibcontract.secType = "STK"
    # get todays date, format as YYYYMMDD -- need to check this is correct
    # today = datetime.datetime.today().strftime('%Y%m%d')
    # ibcontract.lastTradeDateOrContractMonth = '20180711'#today
    ibcontract.symbol = "SNAP"
    ibcontract.exchange = "ISLAND"

    resolved_ibcontract = app.resolve_ib_contract(ibcontract)

    # duration units and bar sizes:
    # https://interactivebrokers.github.io/tws-api/historical_bars.html#hd_duration
    # limitations:
    # https://interactivebrokers.github.io/tws-api/historical_limitations.html
    # seems to be a bit more data for 3m 1W compared with 1m 1D (650 vs 390)
    # historic_data = app.get_IB_historical_data(resolved_ibcontract, durationStr="1 D", barSizeSetting="1 min", latest_date='20170305 14:00:00')#'20180504 14:30:00')


    end_date = None#'20170401'  # smaller amount of data for prototyping/testing
    snap_trades = app.get_hist_data_date_range(resolved_ibcontract, barSizeSetting='3 mins', end_date=end_date)
    snap_bid = app.get_hist_data_date_range(resolved_ibcontract, barSizeSetting='3 mins', whatToShow='BID', end_date=end_date)
    snap_ask = app.get_hist_data_date_range(resolved_ibcontract, barSizeSetting='3 mins', whatToShow='ASK', end_date=end_date)
    snap_opt_vol = app.get_hist_data_date_range(resolved_ibcontract, barSizeSetting='3 mins', whatToShow='OPTION_IMPLIED_VOLATILITY', end_date=end_date)

    snap_trades.to_hdf('snap_trades.h5', key='data', format='table', complevel=9, complib='blosc:lz4')
    snap_bid.to_hdf('snap_bid.h5', key='data', format='table', complevel=9, complib='blosc:lz4')
    snap_ask.to_hdf('snap_ask.h5', key='data', format='table', complevel=9, complib='blosc:lz4')
    snap_opt_vol.to_hdf('snap_opt_vol.h5', key='data', format='table', complevel=9, complib='blosc:lz4')

    # seems to have weird issues with short bars, and seems to be a long-term indicator
    # snap_vol = app.get_hist_data_date_range(resolved_ibcontract, barSizeSetting='3 mins', whatToShow='HISTORICAL_VOLATILITY', end_date='20170425')



    # get earliest timestamp
    # earliest = app.getEarliestTimestamp(resolved_ibcontract, tickerid=200)

    # get list of news providers
    # nps = app.getNewsProviders()

    # look for period with highest autocorrelation and use that as prediction period

    #app.disconnect()
