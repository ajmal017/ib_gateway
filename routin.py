from datetime import datetime, timedelta
from tornado.concurrent import run_on_executor
from concurrent.futures import ThreadPoolExecutor
from config import tws_conf
import psutil
import os
import sys

class TimeFormatter:
    def weekday(self):
        return datetime.utcnow().isoweekday()
    def timestamp(self):
        return int(datetime.utcnow().timestamp() * 1000)
    def today(self):
        return datetime.utcnow().strftime("%Y%m%d")
    def ts2dtstr(self, timestamp):
        return datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

class Maintainer:
    executor = ThreadPoolExecutor(10)

    def __init__(self):
        self.timer = TimeFormatter()
        self.today = self.timer.today()
        self.pid2kill = 0
        self.pids = []
        self.system = sys.platform

    @run_on_executor
    def runTWS(self):
        if self.system == "win32":
            p = os.system(f"{os.getcwd()}/control-win/StartTWS.bat {tws_conf['username']} {tws_conf['password']}")
        elif self.system == "linux":
            p = os.system(f"bash {os.getcwd()}/control-unix/gatewaystart.sh -inline {tws_conf['username']} {tws_conf['password']}")
        return True

    @run_on_executor
    def getPid(self):
        pids = {proc.pid:proc.name() for proc in psutil.process_iter() if proc.name()=="java"}
        self.pids = list(pids.keys())
        self.pid2kill = -1 if len(self.pids) > 1 else len(self.pids)
        return self.pid2kill
    
    def closeTWS(self):
        for pid in self.pids:
            if self.system == "win32":
                os.system(f"taskkill /pid {pid}")
            elif self.system == "linux":
                os.system(f"kill -9 {pid}")