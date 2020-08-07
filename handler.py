import tornado
import tornado.websocket
import json

class BaseHttpHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "x-requested-with, Authentication")
        self.set_header('Access-Control-Allow-Methods', "POST, GET, OPTIONS, PUT, DELETE")

    def _request_summary(self):
        """rewrite log format"""
        return f"{self.request.path}, {self.request.method}, {self.request.remote_ip}, {self.request.headers.get('X-Forwarded-For', '')}, {self.request.arguments}"

    @property
    def api(self):
        return self.application.api

    def options(self):
        # no body
        self.set_status(204)
        self.finish()

class BaseWsHandler(tornado.websocket.WebSocketHandler):
    # for CORS debug
    def check_origin(self, origin):
        return True 

    def open(self):
        self.api.logger(f"new connection, {self.request.uri}, {self.request.remote_ip}, {self.request.headers._dict}")

    def on_close(self):
        self.final_callback = None
        self.api.logger(f"connection closed, {self.request.uri}, {self.request.remote_ip}")

    def on_error(self, error: str):
        self.api.logger(f"WS on_error, {error}")

    @property
    def api(self):
        return self.application.api

class Contract(BaseHttpHandler):
    async def get(self):
        res = {"result": True, "data": self.api.ib_contract}
        self.finish(res)

class Position(BaseHttpHandler):
    async def get(self):
        res = {"result": True, "data": self.api.ib_pos}
        self.finish(res)

class Account(BaseHttpHandler):
    async def get(self):
        res = {"result": True, "data": self.api.ib_account}
        self.finish(res)

class QueryOrder(BaseHttpHandler):
    async def get(self):
        order_id = self.get_argument("order_id", "")
        order = self.api.ib_orders.get(order_id, {})
        res = {"result": False, "data": order}
        if order:
            res["result"] = True
        self.finish(res)

class MakeOrder(BaseHttpHandler):
    async def post(self):
        direction = self.get_argument("direction", "").upper()
        orderType = self.get_argument("orderType", "").upper()
        price = self.get_argument('price', "0")
        volume = self.get_argument('volume', "0")
        
        res = {"result": False, "order_id": -1, "err_msg": ""}
        if not direction in ["BUY", "SELL"]:
            res["err_msg"] = "invalid direction"
        
        if not res["err_msg"]:
            if orderType not in ["LMT", "MKT"]:
                # "STP":stop,"MIT":market if touched, "MOC": market on close,"PEG MKT":peg
                res["err_msg"] = "invalid orderType"
        
        if not res["err_msg"]:
            vol = volume.replace(".", "")
            if not vol.isnumeric():
                res["err_msg"] = "invalid volume"
        
        if not res["err_msg"]:
            px = price.replace(".", "")
            if orderType == "LMT" and not px.isnumeric():
                res["err_msg"] = "invalid price"

        if not res["err_msg"]:
            order_id = self.api.make_order(direction, orderType, price, volume)
            res["result"] = True
            res["order_id"] = order_id
        self.finish(res)

class CancelOrder(BaseHttpHandler):
    async def post(self):
        order_id = self.get_argument("order_id", "")
        res = {"result": False, "order_id": order_id, "err_msg":""}
        if not order_id:
            res["err_msg"] = "invalid order_id"
        if not res["err_msg"]:
            order = self.api.ib_orders.get(order_id, {})
            if not order:
                res["err_msg"] = "order in exist"

        if not res["err_msg"]:
            res["result"] = True
            self.api.cancel_order(orderid=order_id)
        self.finish(res)

class OpenOrder(BaseHttpHandler):
    async def get(self):
        orders = list(self.api.ib_orders.values())
        open_orders = list(filter(lambda x: x["status"] in ["Submitted"], orders))
        res = {"result": True, "data": open_orders}
        self.finish(res)

class Trade(BaseWsHandler):
    def open(self):
        self.api.trade_register.login(self.callback)
        self.api.logger(f"Trade on open {self.request.remote_ip}")
        self.write_message(json.dumps({"result":True,"message":"Trade kaigao"}))
        pass

    def on_message(self, message):
        self.api.logger(message)
        pass
        
    def on_close(self):
        self.api.trade_register.logout(self.callback)
        self.api.logger(f"Tick on close {self.request.remote_ip}")
        pass

    def callback(self, message):
        try:
            self.write_message(json.dumps(message))
        except Exception as e:
            self.api.logger(str(e))

class Depth(BaseWsHandler):
    def open(self):
        self.api.depth_register.login(self.callback)
        self.api.logger(f"Depth on open {self.request.remote_ip}")
        self.write_message(json.dumps({"result":True,"message":"Depth kaigao"}))
        pass

    def on_message(self, message):
        self.api.logger(message)
        pass
        
    def on_close(self):
        self.api.depth_register.logout(self.callback)
        self.api.logger(f"Depth on close {self.request.remote_ip}")
        pass

    def callback(self, message):
        try:
            self.write_message(json.dumps(message))
        except Exception as e:
            self.api.logger(str(e))

class Candle(BaseWsHandler):
    def open(self):
        self.api.candle_register.login(self.callback)
        self.api.logger(f"Candle on open {self.request.remote_ip}")
        self.write_message(json.dumps({"result":True,"message":"Candle kaigao"}))
        pass

    def on_message(self, message):
        self.api.logger(message)
        pass
        
    def on_close(self):
        self.api.candle_register.logout(self.callback)
        self.api.logger(f"Candle on close {self.request.remote_ip}")
        pass

    def callback(self, message):
        try:
            self.write_message(json.dumps(message))
        except Exception as e:
            self.api.logger(str(e))

class Order(BaseWsHandler):
    def open(self):
        self.api.order_register.login(self.callback)
        self.write_message(json.dumps({"result":True,"message":"Order kaigao"}))
        self.api.logger(f"Order on open {self.request.remote_ip}")
        pass

    def on_message(self, message):
        self.api.logger(message)
        pass
        
    def on_close(self):
        self.api.order_register.logout(self.callback)
        self.api.logger(f"Order on close {self.request.remote_ip}")
        pass

    def callback(self, message):
        try:
            self.write_message(json.dumps(message))
        except Exception as e:
            self.api.logger(str(e))

handlers = [
    (r"/contract", Contract),
    (r"/position", Position),
    (r"/make_order", MakeOrder),
    (r"/open_order", OpenOrder),
    (r"/cancel_order", CancelOrder),
    (r"/account", Account),
    (r"/query_order", QueryOrder),
    (r"/trade", Trade),
    (r"/depth", Depth),
    (r"/candle_stick", Candle),
    (r"/order", Order),
]