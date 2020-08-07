import tornado.ioloop
import tornado.web
from tornado.options import define, options, parse_command_line
from handler import handlers
from core import IbApi, Register
from config import tws_conf
import os

### asyncio incomp with windows/python 3.8
import sys
import asyncio
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # asyncio.set_event_loop(asyncio.new_event_loop())

options.log_file_prefix = os.path.join(os.path.dirname(__file__), "logs/ibApp.log")
options.logging = "warning"
options.log_rotate_mode = "time"
options.log_rotate_when = "H"
options.log_rotate_interval = 8
options.log_to_stderr = True
options.log_file_num_backups = 30

settings = {
    "static_path": os.path.join(os.path.dirname(__file__), "static"),
    "xsrf_cookies": False,
    "debug": True,
    "websocket_ping_interval": 5,
    "websocket_ping_timeout": 10
}

class Application(tornado.web.Application):
    def __init__(self):
        self.api = IbApi(tws_conf)
        tornado.web.Application.__init__(self, handlers, **settings)

if __name__ == "__main__":
    app = Application()
    app.listen(tws_conf["publish"])
    parse_command_line()
    tornado.ioloop.PeriodicCallback(app.api.checkTWSConn, 30000).start()
    tornado.ioloop.PeriodicCallback(app.api.client.run, 100).start()
    tornado.ioloop.IOLoop.current().start()
