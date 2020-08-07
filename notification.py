import requests

import time
import json
import hmac
import hashlib
import base64

from config import ding
from tornado.concurrent import run_on_executor
from concurrent.futures import ThreadPoolExecutor

class Dingding:
    executor = ThreadPoolExecutor(20)
    def __init__(self):
        self.token = ding["token"]
        self.private_key = ding["private_key"]
        self.headers = {"Content-Type":"application/json;charset=utf-8"}
        self.url = "https://oapi.dingtalk.com/robot/send"

    def sign(self):
        timestamp = int(time.time() * 1000)
        hmac_code = hmac.new(
            bytes(self.private_key, encoding = 'utf-8'), 
            bytes(f'{timestamp}\n{self.private_key}', encoding = 'utf-8'), 
            digestmod = hashlib.sha256
            ).digest()
        return {
            "access_token": self.token,
            "timestamp":timestamp,
            "sign":base64.b64encode(hmac_code)
            }
    @run_on_executor
    def send_request(self, msg):
        res = None
        err_msg = ""
        try:
            res = requests.post(
                self.url, 
                data = json.dumps(msg), 
                headers = self.headers, 
                params = self.sign(), 
                timeout = 6)
        except Exception as e:
            err_msg = f"EtherCONN Error:{e}"
        return res, err_msg

    def process_err(self, res, err_msg):
        if not err_msg:
            if res.status_code == 200:
                try:
                    res = res.json()
                    err_msg = res.get("errmsg", "wrong JSON")
                except:
                    err_msg = "Ding Api Return Error"
            else:
                err_msg = "requests failed"
        return err_msg

    async def send_msg(self, title, context, at_person=[], at_all=False):
        msg = {
            "msgtype" : "markdown", 
            "markdown": {
                    "title": title,
                    "text": context,
            },
            "at":{
                "atMobiles": at_person, 
                "isAtAll": at_all
            }
        }
    
        res, err = await self.send_request(msg)
        err_msg = self.process_err(res, err)

        return err_msg