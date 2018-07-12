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

import pprint
import queue
import datetime

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
        if tickerid not in earliest_timestamp_dict.keys():
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
        MAX_WAIT_SECONDS = 10
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
                                durationStr="1 Y",
                                barSizeSetting="1 day",
                                tickerid=DEFAULT_HISTORIC_DATA_ID,
                                latest_date=datetime.datetime.today().strftime("%Y%m%d %H:%M:%S %Z")):

        """
        Returns historical prices for a contract, up to today
        ibcontract is a Contract
        :returns list of prices in 4 tuples: Open high low close volume
        """


        ## Make a place to store the data we're going to return
        historic_data_queue = finishableQueue(self.init_historicprices(tickerid))

        # Request some historical data. Native method in EClient
        self.reqHistoricalData(
            tickerid,  # tickerId,
            ibcontract,  # contract,
            latest_date,  # endDateTime,
            durationStr,  # durationStr,
            barSizeSetting,  # barSizeSetting,
            "TRADES",  # whatToShow,
            1,  # useRTH,
            1,  # formatDate
            False,  # KeepUpToDate <<==== added for api 9.73.2
            [] ## chartoptions not used
        )



        ## Wait until we get a completed data, an error, or get bored waiting
        MAX_WAIT_SECONDS = 10
        print("Getting historical data from the server... could take %d seconds to complete " % MAX_WAIT_SECONDS)

        historic_data = historic_data_queue.get(timeout=MAX_WAIT_SECONDS)

        while self.wrapper.is_error():
            print(self.get_error())

        if historic_data_queue.timed_out():
            print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

        self.cancelHistoricalData(tickerid)


        return historic_data


    def getEarliestTimestamp(self, contract, whatToShow='TRADES', useRTH=1, formatDate=1, tickerid=DEFAULT_GET_EARLIEST_ID):
        # parameters: https://interactivebrokers.github.io/tws-api/classIBApi_1_1EClient.html#a059b5072d1e8e8e96394e53366eb81f3

        ## Make a place to store the data we're going to return
        earliest_timestamp_queue = finishableQueue(self.init_earliest_timestamp(tickerid))

        self.reqHeadTimeStamp(tickerid, contract, whatToShow, useRTH, formatDate)

        ## Wait until we get a completed data, an error, or get bored waiting
        MAX_WAIT_SECONDS = 10
        print("Getting eariest timestamp from the server... could take %d seconds to complete " % MAX_WAIT_SECONDS)

        earliest = earliest_timestamp_queue.get(timeout=MAX_WAIT_SECONDS)

        while self.wrapper.is_error():
            print(self.get_error())

        if earliest_timestamp_queue.timed_out():
            print("Exceeded maximum wait for wrapper to confirm finished - seems to be normal behaviour")

        self.cancelHeadTimeStamp(tickerid)

        return earliest


    def getNewsProviders(self):
        ## Make a place to store the data we're going to return
        tickerid = DEFAULT_GET_NP_ID
        np_queue = finishableQueue(self.init_np(tickerid))

        # Request news providers. Native method in EClient
        self.reqNewsProviders()

        ## Wait until we get a completed data, an error, or get bored waiting
        MAX_WAIT_SECONDS = 10
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
    # historic_data = app.get_IB_historical_data(resolved_ibcontract, durationStr="1 W", barSizeSetting="3 mins", latest_date='20180504 14:30:00')

    # get earliest timestamp
    # earliest = app.getEarliestTimestamp(resolved_ibcontract, tickerid=200)

    # get list of news providers
    nps = app.getNewsProviders()

    # convert to pandas dataframe
    # date, open, high, low, close, vol
    # need to adjust for splits/etc



    #print(historic_data)

    #app.disconnect()
