from ibapi import comm
from ibapi.client import EClient
from ibapi.common import MAX_MSG_LEN, NO_VALID_ID, OrderId, TickAttrib, TickerId
from ibapi.contract import Contract, ContractDetails
from ibapi.execution import Execution
from ibapi.order import Order
from ibapi.order_state import OrderState
from ibapi.ticktype import TickType, TickTypeEnum
from ibapi.wrapper import EWrapper
from ibapi.errors import BAD_LENGTH
from ibapi.common import BarData as IbBarData

from queue import Empty
from threading import Thread, Condition
from notification import Dingding
from routin import Maintainer
import tornado
import logging

depthSide = {0:"ask",1:"bid"}
tickerSide = {0:"bid",1:"bid",2:"ask",3:"ask",4:"last",5:"last",6:"highest",7:"lowest",8:"volume",9:"pre-close"}

def contract_maker(instrument: str):
    market_map = {"FUTURE": "FUT", "SPOT": "CMDTY"}
    xchg, symbol, market = instrument.upper().split(".")
    sym = symbol.split("_")
    ib_contract = Contract()
    ib_contract.symbol = f"{sym[0]}USD"
    ib_contract.currency = "USD"
    ib_contract.secType = market_map.get(market, "ERR")
    ib_contract.exchange = xchg
    return ib_contract

class Register:
    def __init__(self):
        self.callbacks = []

    def login(self, callback):
        self.callbacks.append(callback)

    def logout(self, callback):
        self.callbacks.remove(callback)

    def trigger(self, message):
        self.notify_callbacks(message)

    def notify_callbacks(self, message):
        if len(self.callbacks):
            for callback in self.callbacks:
                callback(message)

class IbClient(EClient):
    def run(self):
        if not self.done and self.isConnected():
            try:
                text = self.msg_queue.get(block=True, timeout=0.2)

                if len(text) > MAX_MSG_LEN:
                    errorMsg = "%s:%d:%s" % (BAD_LENGTH.msg(), len(text), text)
                    self.wrapper.error(
                        NO_VALID_ID, BAD_LENGTH.code(), errorMsg
                    )
                    self.disconnect()
                    # break

                fields = comm.read_fields(text)
                self.decoder.interpret(fields)
            except Empty:
                pass

class IbApi(EWrapper):
    def __init__(self, conf):
        super().__init__()
        self.client = IbClient(self)
        self.messenger = Dingding()
        self.maintainer = Maintainer()
        self.tws_date = self.maintainer.timer.today()
        self.connection_ts = self.maintainer.timer.timestamp()
        self.checkTWSConn()

        self.depth_register = Register()
        self.ib_depth = {"asks":[[],[],[],[],[]],"bids":[[],[],[],[],[]]}
        self.ib_depth_ready = False
        self.ib_depth_ts = 0

        self.trade_register = Register()
        self.ib_trade = {}
        self.ib_trade_ready = False
        self.ib_trade_ts = 0

        self.candle_register = Register()
        self.ib_candle = {}

        self.order_register = Register()

        self.ib_contract = {}
        self.ib_account = {}
        self.ib_pos = {}
        self.ib_orders = {}

        self.reqid = 0
        self.orderid = 0
        self.clientid = conf["clientid"]
        self.accountid = conf["accountid"]
        self.contractid = contract_maker(conf["symbol"])
        self.host = conf["host"]
        self.port = conf["port"]
    
    def logger(self, log_str):
        return logging.warning(log_str)

    @tornado.gen.coroutine
    def checkTWSConn(self):
        pid_status = yield self.maintainer.getPid()
        today = self.maintainer.timer.today()

        if pid_status < 0:
            # market close or multiple tws running
            yield self.messenger.send_msg("ib msg", f"Control: pid_status -1, Close ib TWS")
            self.close()
            self.maintainer.closeTWS()

        elif pid_status == 0:
            # no tws running
            if self.maintainer.timer.weekday() < 6:
                yield self.messenger.send_msg("ib msg", f"Control: Lost ib TWS")
                self.close()
                yield self.maintainer.runTWS()

        else:
            # one tws running
            if self.maintainer.timer.weekday() < 6:
                self.connect()
            else:
                yield self.messenger.send_msg("ib msg", f"Control: Close ib TWS on Weekend")
                self.close()
                self.maintainer.closeTWS()

        if not self.tws_date == today:
            # daily relaunch
            if self.maintainer.timer.weekday() < 6:
                yield self.messenger.send_msg("ib msg", f"Control: ib TWS daily relaunch")
                self.close()
                self.maintainer.closeTWS()
                self.tws_date = today

        if self.client.isConnected():
            ts_diff = self.maintainer.timer.timestamp() - self.connection_ts
            if ts_diff > 30000:
                yield self.messenger.send_msg("ib msg", f"Control: no data received in {ts_diff / 1000} secs")
                self.subscribe()
            
    @tornado.gen.coroutine
    def error(self, reqId: TickerId, errorCode: int, errorString: str):
        """Callback of error caused by specific request."""
        super().error(reqId, errorCode, errorString)
        yield self.messenger.send_msg("ib msg", f"TWS: {errorString}")
        self.connection_ts = self.maintainer.timer.timestamp()
        order = self.ib_orders.get(str(reqId), {})
        if order:
            order["status"] = "Cancelled" if "Order Canceled - reason" in errorString else "Rejected"
            order["time"] = self.connection_ts
            self.ib_orders[str(reqId)] = order
        self.logger(f"errorCode: {errorCode}, errorString: {errorString}")
    
    @tornado.gen.coroutine
    def connect(self):
        """Connect to TWS."""
        if not self.client.isConnected():
            yield self.messenger.send_msg("ib msg", f"Control: ib TWS running, connnecting")
            self.client.connect(self.host, self.port, self.clientid)
            self.client.reqCurrentTime()
            self.subscribe()
            
    def subscribe(self):
        if self.client.isConnected():
            self.streamTick(self.contractid)
            self.streamDepth(self.contractid)
            self.streamCandleStick(self.contractid)
            self.query_contract(self.contractid)
            self.query_account_list()
            self.query_account()
            self.query_position()

    def close(self):
        """Disconnect from TWS."""
        self.client.disconnect()

    def connectAck(self):
        """Callback when connection is established."""
        self.logger("IB TWS Connected")

    def connectionClosed(self):
        """Callback when connection is closed."""
        self.logger("IB TWS DisConnected")

    def currentTime(self, time: int):
        """Callback of current server time of IB."""
        super().currentTime(time)
        time_string = self.maintainer.timer.ts2dtstr(time)
        self.logger(f"Server Time: {time_string}")

    def query_history(self, ib_contract, start, end):
        """"""
        self.reqid += 1
        end_str = ""
        end_str = end.strftime("%Y%m%d %H:%M:%S")
        days = min((end - start).days, 180)     # IB only provides 6-month data
        duration = f"{days} D"
        bar_size = "1 min" # 1 secs, 5 secs, 10 secs, 15 secs, 30 secs, 1 min, 2 mins, 3 mins, 5 mins, 10 mins, 15 mins, 20 mins, 30 mins, 1 hour, 2 hours, 3 hours, 4 hours, 8 hours, 1 day, 1W, 1M
        bar_type = "MIDPOINT" if ib_contract.exchange=="IDEALPRO" else "TRADES"
        self.client.reqHistoricalData(self.reqid,ib_contract,end_str,duration,bar_size,bar_type,1,1,False,[])

    def historicalData(self, reqId: int, ib_bar: IbBarData):
        """Callback of history data update."""
        """{'date': '20200729  13:48:00', 'open': 1.172705, 'high': 1.17271, 'low': 1.1727, 'close': 1.17271, 'volume': -1, 'barCount': -1, 'average': -1.0}"""
        self.logger(f"\nhistoricalData, {reqId}, {ib_bar.__dict__}")
    
    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Callback of history data finished."""
        self.logger(f"historicalDataEnd {reqId}, {start}, {end}")

    def streamCandleStick(self, ib_contract):
        """"""
        self.reqid += 1
        self.client.reqRealTimeBars(self.reqid, ib_contract, 5, "MIDPOINT", True, [])

    def realtimeBar(self, reqId: TickerId, time:int, open_: float, high: float, low: float, close: float, volume: int, wap: float, count: int):
        """Callback of 5 Second Real Time Bars."""
        """return: {'reqId': 1, 'time': 1596173490, 'open_': 1969.75, 'high': 1969.75, 'low': 1969.6, 'close': 1969.65, 'volume': -1, 'wap': -1.0, 'count': -1}"""
        super().realtimeBar(reqId, time, open_, high, low, close, volume, wap, count)
        self.ib_candle["open"] = open_
        self.ib_candle["high"] = high
        self.ib_candle["low"] = low
        self.ib_candle["close"] = close
        self.ib_candle["volume"] = volume
        self.ib_candle["wap"] = wap
        self.ib_candle["ts"] = time
        self.candle_register.trigger(self.ib_candle)

    def streamTick(self, ib_contract):
        """"""
        self.reqid += 1
        self.client.reqMktData(self.reqid, ib_contract, "", False, False, [])

    def tickPrice(self, reqId: TickerId, tickType: TickType, price: float, attrib: TickAttrib):
        """Callback of tick price update."""
        """return: tickPrice  1 1 0.90778 CanAutoExecute: 1, PastLimit: 0, PreOpen: 0"""
        super().tickPrice(reqId, tickType, price, attrib)
        self.make_ticker(tickType, price=price)

    def tickSize(self, reqId: TickerId, tickType: TickType, size: int):
        """Callback of tick volume update."""
        """return: tickSize  1 3 7000000"""
        super().tickSize(reqId, tickType, size)
        self.make_ticker(tickType, size=size)

    def make_ticker(self, ticker_type, price=0, size=0):
        if ticker_type < 4:
            self.ib_trade["side"] = tickerSide[ticker_type]
        else:
            return

        if price:
            self.ib_trade["price"] = price
        if size:
            self.ib_trade["size"] = size

        if not self.ib_trade_ready:
            if all(self.ib_trade.values()):
                self.ib_trade_ready = True
        else:
            ts = self.maintainer.timer.timestamp()
            self.ib_trade["ts"] = ts
            check_ts = ts // 100
            if not check_ts == self.ib_trade_ts:
                self.ib_trade_ts = check_ts
                self.trade_register.trigger(self.ib_trade)
        self.connection_ts = self.maintainer.timer.timestamp()

    def tickString(self, reqId: TickerId, tickType: TickType, value: str):
        """Callback of tick string update."""
        super().tickString(reqId, tickType, value)
        self.logger(f"tickString , {reqId}, {tickType}, {value}")

    def streamDepth(self, ib_contract):
        """"""
        self.reqid += 1
        self.client.reqMktDepth(self.reqid, ib_contract, 5, False, [])

    def updateMktDepth(self, reqId: TickerId, position: int, operation: int, side: int, price: float, size: int):
        """Callback of depth update."""
        """ ReqId: 1 Position: 5 Operation: 1 Side: 1 Price: 1.17712 Size: 9000000
            ReqId: 1 Position: 2 Operation: 1 Side: 1 Price: 1.17713 Size: 7950000
            ReqId: 1 Position: 0 Operation: 1 Side: 0 Price: 1.17717 Size: 7000000
            
            operation - Identifies the how this order should be applied to the market depth. Valid values are: 
            0 to insert this new order into the row identified by position,
            1 to update the existing order in the row identified by position, or 
            2 to delete the existing order at the row identified by position.
            side - Identifies the side of the book that this order belongs to. Valid values are 0 for ask and 1 for bid.
            """
        
        super().updateMktDepth(reqId, position, operation, side, price, size)
        self.ib_depth[f"{depthSide[side]}s"][position] = [price, size]
        if not self.ib_depth_ready:
            if all(self.ib_depth["asks"]) and all(self.ib_depth["bids"]):
                self.ib_depth_ready = True
        else:
            ts = self.maintainer.timer.timestamp()
            self.ib_depth["ts"] = ts
            check_ts = ts // 100
            if not check_ts == self.ib_depth_ts:
                self.ib_depth_ts = check_ts
                self.depth_register.trigger(self.ib_depth)
        self.connection_ts = self.maintainer.timer.timestamp()

    def updateMktDepthL2(self, reqId: TickerId, position: int, marketMaker: str, operation: int, side: int, price: float, size: int, isSmartDepth: bool):
        """Callback of depth L2 update."""
        super().updateMktDepthL2(reqId, position, marketMaker, operation, side, price, size, isSmartDepth)
        self.logger(f"UpdateMarketDepthL2. ReqId: {reqId}, Position:{position}, MarketMaker:{marketMaker}, Operation:{operation}, Side:{side}, Price:{price}, Size:{size}, isSmartDepth:{isSmartDepth}")


    ##### contract #####
    def query_contract(self, ib_contract):
        self.reqid += 1
        self.client.reqContractDetails(self.reqid, ib_contract)

    def contractDetails(self, reqId: int, contractDetails: ContractDetails):
        """Callback of contract data update."""
        super().contractDetails(reqId, contractDetails)
        self.ib_contract["symbol"]= contractDetails.marketName
        self.ib_contract["minTick"]= contractDetails.minTick
        self.ib_contract["xchg"]= contractDetails.validExchanges
        self.ib_contract["longName"]= contractDetails.longName
        self.ib_contract["mdSizeMultiplier"]= contractDetails.mdSizeMultiplier

    def contractDetailsEnd(self, reqId: int):
        super().contractDetailsEnd(reqId)
        self.logger(f"ContractDetailsEnd. ReqId, {reqId} ")

    ##### Account #####
    def query_account_list(self):
        self.client.reqManagedAccts()

    def managedAccounts(self, accountsList: str):
        """Callback of all sub accountid."""
        super().managedAccounts(accountsList)
        self.logger(f"managedAccounts ,{accountsList}")

    def query_account(self):
        self.client.reqAccountUpdates(True, self.accountid)

    def updateAccountValue(self, key: str, val: str, currency: str, accountName: str):
        """ Callback of account update."""
        """AvailableFunds 1037870.12 USD"""
        super().updateAccountValue(key, val, currency, accountName)
        if accountName == self.accountid:
            # self.logger(f"updateAccountValue ,{key}, {val}, {currency}, {accountName}")
            self.ib_account[key] = val

    def updateAccountTime(self, timeStamp: str):
        """Callback of account update time."""
        super().updateAccountTime(timeStamp)
        self.ib_account["time"] = timeStamp
        self.logger(f"updateAccountTime, {timeStamp}")

    def updatePortfolio(self,contract: Contract,position: float,marketPrice: float,marketValue: float,
        averageCost: float,unrealizedPNL: float,realizedPNL: float,accountName: str):
        """Callback of position update."""
        """{'conId': 12087792, 'symbol': 'EUR', 'secType': 'CASH', 'lastTradeDateOrContractMonth': '', 'strike': 0.0, 'right': '0', 'multiplier': '', 
        'exchange': '', 'primaryExchange': 'IDEALPRO', 'currency': 'USD', 'localSymbol': 'EUR.USD', 'tradingClass': 'EUR.USD', 
        'includeExpired': False, 'secIdType': '', 'secId': '', 'comboLegsDescrip': '', 'comboLegs': None, 'deltaNeutralContract': None}
        {position:-10.0, marketPrice:1.173885, marketValue:-11.74,averageCost:0.9238,unrealizedPNL:-2.5,realizedPNL:-4.99,accountName:DU228384 }"""
        super().updatePortfolio(contract,position,marketPrice,marketValue,averageCost,unrealizedPNL,realizedPNL,accountName)
        self.logger(f"updatePortfolio\n{position},{marketPrice},{marketValue},{averageCost},{unrealizedPNL},{realizedPNL},{accountName}\n\n{contract.__dict__}")

    ##### Position #####
    def query_position(self):
        self.client.reqPositions()

    def position(self, account: str, contract: Contract, position: float, avgCost: float):
        """Position. Account: DU229352 Symbol: XAUUSD Currency: USD Position: -10.0 Avg cost: 1952.25"""
        super().position(account, contract, position, avgCost)
        if account == self.accountid:
            self.ib_pos = {"account": account, "symbol":contract.symbol, "currency": contract.currency, "position": position, "avg_cost": avgCost}
        # self.logger("Position.", "Account:", account, "Symbol:", contract.symbol, "Currency:", contract.currency,"Position:", position, "Avg cost:", avgCost)

    def positionEnd(self):
        """PositionEnd"""
        super().positionEnd()
        if not self.ib_pos:
            self.ib_pos = {"account": self.accountid, "symbol":self.contractid.symbol, "currency": self.contractid.currency, "position": 0, "avg_cost": 0}
        self.logger("PositionEnd")

    ##### Order #####
    def nextValidId(self, orderId: int):
        """Callback of next valid orderid."""
        super().nextValidId(orderId)
        self.logger(f"nextValidId {orderId}")
        self.reqid = orderId

    def orderStatus(self,orderId: OrderId,status: str,filled: float,remaining: float,avgFillPrice: float,
        permId: int,parentId: int,lastFillPrice: float,clientId: int,whyHeld: str,mktCapPrice: float):
        """Callback of order status update."""
        """PreSubmitted, Submitted, Filled, Cancelled"""
        super().orderStatus(orderId,status,filled,remaining,avgFillPrice,permId,parentId,lastFillPrice,clientId,whyHeld,mktCapPrice)
        self.logger(f"orderStatus\nid:{orderId}, {status}, f:{filled}, r:{remaining}, avg:{avgFillPrice}, {permId},{parentId},{lastFillPrice},{clientId},{whyHeld},{mktCapPrice}")
        order = self.ib_orders.get(str(orderId), {})
        if order:
            order = order.copy()
            order["filledQuantity"] = filled
            order["avgFillPrice"] = avgFillPrice
            order["status"] = status
            order["time"] = self.maintainer.timer.timestamp()
            self.order_register.trigger(order)
            self.ib_orders[str(orderId)] = order
        
    def openOrder(self,orderId: OrderId,ib_contract: Contract,ib_order: Order,orderState: OrderState,):
        """Callback when opening new order."""
        """orderState: {'status': 'PreSubmitted', 'initMarginBefore': '1.7976931348623157E308', 'maintMarginBefore': '1.7976931348623157E308', 'equityWithLoanBefore': '1.7976931348623157E308', 'initMarginChange': '1.7976931348623157E308', 'maintMarginChange': '1.7976931348623157E308', 'equityWithLoanChange': '1.7976931348623157E308', 'initMarginAfter': '1.7976931348623157E308', 'maintMarginAfter': '1.7976931348623157E308', 'equityWithLoanAfter': '1.7976931348623157E308', 'commission': 1.7976931348623157e+308, 'minCommission': 1.7976931348623157e+308, 'maxCommission': 1.7976931348623157e+308, 'commissionCurrency': '', 'warningText': '', 'completedTime': '', 'completedStatus': ''}"""

        """ib_order: {'softDollarTier': 1543640930240: Name: , Value: , DisplayName: , 'orderId': 5, 'clientId': 15178, 'permId': 1538198311, 'action': 'BUY', 'totalQuantity': 10.0, 'orderType': 'LMT', 'lmtPrice': 1.15, 'auxPrice': 0.0, 'tif': 'DAY', 'activeStartTime': '', 'activeStopTime': '', 'ocaGroup': '', 'ocaType': 3, 'orderRef': '', 'transmit': True, 'parentId': 0, 'blockOrder': False, 'sweepToFill': False, 'displaySize': 0, 'triggerMethod': 0, 'outsideRth': False, 'hidden': False, 'goodAfterTime': '', 'goodTillDate': '', 'rule80A': '', 'allOrNone': False, 'minQty': 2147483647, 'percentOffset': 1.7976931348623157e+308, 'overridePercentageConstraints': False, 'trailStopPrice': 2.15, 'trailingPercent': 1.7976931348623157e+308, 'faGroup': '', 'faProfile': '', 'faMethod': '', 'faPercentage': '', 'designatedLocation': '', 'openClose': '', 'origin': 0, 'shortSaleSlot': 0, 'exemptCode': -1, 'discretionaryAmt': 0.0, 'eTradeOnly': False, 'firmQuoteOnly': False, 'nbboPriceCap': 1.7976931348623157e+308, 'optOutSmartRouting': False, 'auctionStrategy': 0, 'startingPrice': 1.7976931348623157e+308, 'stockRefPrice': 1.7976931348623157e+308, 'delta': 1.7976931348623157e+308, 'stockRangeLower': 1.7976931348623157e+308, 'stockRangeUpper': 1.7976931348623157e+308, 'randomizePrice': False, 'randomizeSize': False, 'volatility': 1.7976931348623157e+308, 'volatilityType': 0, 'deltaNeutralOrderType': 'None', 'deltaNeutralAuxPrice': 1.7976931348623157e+308, 'deltaNeutralConId': 0, 'deltaNeutralSettlingFirm': '', 'deltaNeutralClearingAccount': '', 'deltaNeutralClearingIntent': '', 'deltaNeutralOpenClose': '?', 'deltaNeutralShortSale': False, 'deltaNeutralShortSaleSlot': 0, 'deltaNeutralDesignatedLocation': '', 'continuousUpdate': False, 'referencePriceType': 0, 'basisPoints': 1.7976931348623157e+308, 'basisPointsType': 2147483647, 'scaleInitLevelSize': 2147483647, 'scaleSubsLevelSize': 2147483647, 'scalePriceIncrement': 1.7976931348623157e+308, 'scalePriceAdjustValue': 1.7976931348623157e+308, 'scalePriceAdjustInterval': 2147483647, 'scaleProfitOffset': 1.7976931348623157e+308, 'scaleAutoReset': False, 'scaleInitPosition': 2147483647, 'scaleInitFillQty': 2147483647, 'scaleRandomPercent': False, 'scaleTable': '', 'hedgeType': '', 'hedgeParam': '', 'account': 'DU228384', 'settlingFirm': '', 'clearingAccount': '', 'clearingIntent': 'IB', 'algoStrategy': '', 'algoParams': None, 'smartComboRoutingParams': None, 'algoId': '', 'whatIf': False, 'notHeld': False, 'solicited': False, 'modelCode': '', 'orderComboLegs': None, 'orderMiscOptions': None, 'referenceContractId': 0, 'peggedChangeAmount': 0.0, 'isPeggedChangeAmountDecrease': False, 'referenceChangeAmount': 0.0, 'referenceExchangeId': '', 'adjustedOrderType': 'None', 'triggerPrice': 1.7976931348623157e+308, 'adjustedStopPrice': 1.7976931348623157e+308, 'adjustedStopLimitPrice': 1.7976931348623157e+308, 'adjustedTrailingAmount': 1.7976931348623157e+308, 'adjustableTrailingUnit': 0, 'lmtPriceOffset': 1.7976931348623157e+308, 'conditions': [], 'conditionsCancelOrder': False, 'conditionsIgnoreRth': False, 'extOperator': '', 'cashQty': 0.0, 'mifid2DecisionMaker': '', 'mifid2DecisionAlgo': '', 'mifid2ExecutionTrader': '', 'mifid2ExecutionAlgo': '', 'dontUseAutoPriceForHedge': True, 'isOmsContainer': False, 'discretionaryUpToLimitPrice': False, 'autoCancelDate': '', 'filledQuantity': 1.7976931348623157e+308, 'refFuturesConId': 0, 'autoCancelParent': False, 'shareholder': '', 'imbalanceOnly': False, 'routeMarketableToBbo': False, 'parentPermId': 0, 'usePriceMgmtAlgo': False}"""
        """ib_contract: {'conId': 12087792, 'symbol': 'EUR', 'secType': 'CASH', 'lastTradeDateOrContractMonth': '', 'strike': 0.0, 'right': '?', 'multiplier': '', 'exchange': 'IDEALPRO', 'primaryExchange': '', 'currency': 'USD', 'localSymbol': 'EUR.USD', 'tradingClass': 'EUR.USD', 'includeExpired': False, 'secIdType': '', 'secId': '', 'comboLegsDescrip': '', 'comboLegs': None, 'deltaNeutralContract': None}"""

        super().openOrder(orderId, ib_contract, ib_order, orderState)
        self.logger(f"openOrder\n, {orderId}, {orderState.__dict__}, {ib_order.__dict__}")
        order = {
            "orderId": ib_order.orderId,
            "orderType": ib_order.orderType,
            "permId": ib_order.permId,
            "action": ib_order.action,
            "totalQuantity": ib_order.totalQuantity,
            "lmtPrice": ib_order.lmtPrice,
            "auxPrice": ib_order.auxPrice,
            "tif": ib_order.tif,
            "account": ib_order.account,
            "whatIf": ib_order.whatIf,
            "autoCancelDate": ib_order.autoCancelDate,
            "symbol": ib_contract.symbol,
            "exchange": ib_contract.exchange,
            "currency": ib_contract.currency,
            "secType": ib_contract.secType,
            "status": orderState.status,
            "commission": orderState.commission,
            "time": self.maintainer.timer.timestamp(),
            "completedStatus": orderState.completedStatus,
            "filledQuantity": ib_order.filledQuantity,
            "avgFillPrice": ib_order.startingPrice,
        }
        self.order_register.trigger(order)
        self.ib_orders[str(orderId)] = order

    def execDetails(self, reqId: int, contract: Contract, execution: Execution):
        """Callback of trade data update."""
        """execDetails  -1 {'conId': 12087792, 'symbol': 'EUR', 'secType': 'CASH', 'lastTradeDateOrContractMonth': '', 'strike': 0.0, 'right': '', 'multiplier': '', 'exchange': 'IDEALPRO', 'primaryExchange': '', 'currency': 'USD', 'localSymbol': 'EUR.USD', 'tradingClass': 'EUR.USD', 'includeExpired': False, 'secIdType': '', 'secId': '', 'comboLegsDescrip': '', 'comboLegs': None, 'deltaNeutralContract': None} {'execId': '000132b0.5f209c35.01.01', 'time': '20200729  14:15:29', 'acctNumber': 'DU228384', 'exchange': 'IDEALPRO', 'side': 'SLD', 'shares': 10.0, 'price': 1.174, 'permId': 1538198312, 'clientId': 15178, 'orderId': 7, 'liquidation': 0, 'cumQty': 10.0, 'avgPrice': 1.174, 'orderRef': '', 'evRule': '', 'evMultiplier': 0.0, 'modelCode': '', 'lastLiquidity': 2}"""
        super().execDetails(reqId, contract, execution)
        self.logger(f"execDetails \n {reqId}, {execution.__dict__}, {contract.__dict__}")
        # order = self.ib_orders.get(orderId, {}).copy()
        # order["completedTime"] = time
        # self.order_register.trigger(order)
        # self.ib_orders[orderId] = order

    def make_order(self, direction, orderType, price, volume):
        """New order."""
        self.reqid += 1
        ib_order = Order()
        ib_order.orderId = self.reqid
        ib_order.clientId = self.clientid
        ib_order.action = direction
        ib_order.orderType = orderType
        if orderType == "LMT":
            ib_order.lmtPrice = float(price)
        ib_order.totalQuantity = float(volume)
        ib_order.account = self.accountid
        self.client.placeOrder(self.reqid, self.contractid, ib_order)
        order = {
            "orderId": ib_order.orderId,
            "orderType": ib_order.orderType,
            "permId": 0,
            "action": ib_order.action,
            "totalQuantity": ib_order.totalQuantity,
            "lmtPrice": ib_order.lmtPrice,
            "auxPrice": ib_order.auxPrice,
            "tif": ib_order.tif,
            "account": ib_order.account,
            "whatIf": ib_order.whatIf,
            "autoCancelDate": ib_order.autoCancelDate,
            "symbol": self.contractid.symbol,
            "exchange": self.contractid.exchange,
            "currency": self.contractid.currency,
            "secType": self.contractid.secType,
            "status": "PendingNew",
            "commission": 0,
            "time": self.maintainer.timer.timestamp(),
            "completedStatus": "",
            "filledQuantity": 0.0,
            "avgFillPrice": 0.0,
        }
        if not self.client.isConnected():
            order["status"] = "Rejected"

        self.ib_orders[str(ib_order.orderId)] = order
        self.client.reqIds(1)
        self.logger(f"make order, {ib_order.orderId}, {order['status']}")
        return self.reqid

    def cancel_order(self, orderid):
        """Cancel an existing order."""
        self.client.cancelOrder(int(orderid))


# 获取历史K线
# api.query_history(ib_contract, start=datetime.now()-timedelta(days=1), end=datetime.now())