import logging
import os
import re
import threading
import time
from collections import deque
from html import escape
from logging.handlers import RotatingFileHandler

from config import Config

logging.getLogger('werkzeug').setLevel(logging.ERROR)
lock = threading.Lock()

LOG_QUEUE = deque(maxlen=200)
LOG_INDEX = 0
# 单调递增的日志序号：每条日志带一个只增不减的 seq，
# 供「实时日志」的每个连接各自定位游标（见 get_logs_since），
# 避免共用一份全局游标时多个连接互相「抢走」对方的日志
LOG_SEQ = 0


class Logger:
    logger = None
    __instance = {}
    __config = None

    __loglevels = {
        "info": logging.INFO,
        "debug": logging.DEBUG,
        "error": logging.ERROR
    }

    def __init__(self, module):
        self.logger = logging.getLogger(module)
        self.__config = Config()
        logtype = self.__config.get_config('app').get('logtype') or "console"
        loglevel = self.__config.get_config('app').get('loglevel') or "info"
        self.logger.setLevel(level=self.__loglevels.get(loglevel))
        if logtype == "server":
            logserver = self.__config.get_config('app').get('logserver', '').split(':')
            if logserver:
                logip = logserver[0]
                if len(logserver) > 1:
                    logport = int(logserver[1] or '514')
                else:
                    logport = 514
                log_server_handler = logging.handlers.SysLogHandler((logip, logport),
                                                                    logging.handlers.SysLogHandler.LOG_USER)
                log_server_handler.setFormatter(logging.Formatter('%(filename)s: %(message)s'))
                self.logger.addHandler(log_server_handler)

        # 记录日志到文件
        logpath = os.environ.get('NASTOOL_LOG') or Config().get_config_path() + "/logs"
        if logpath:
            if not os.path.exists(logpath):
                os.makedirs(logpath, exist_ok=True)
            log_file_handler = RotatingFileHandler(filename=os.path.join(logpath, module + "_log.txt"),
                                                   maxBytes=5 * 1024 * 1024,
                                                   backupCount=3,
                                                   encoding='utf-8')
            log_file_handler.setFormatter(logging.Formatter('%(asctime)s\t%(levelname)s: %(message)s'))
            self.logger.addHandler(log_file_handler)
        # 记录日志到终端
        log_console_handler = logging.StreamHandler()
        log_console_handler.setFormatter(logging.Formatter('%(asctime)s\t%(levelname)s: %(message)s'))
        self.logger.addHandler(log_console_handler)

    def refresh_loglevel(self):
        loglevel = self.__config.get_config('app').get('loglevel') or "info"
        self.logger.setLevel(level=self.__loglevels.get(loglevel))

    @staticmethod
    def get_instance(module):
        if not module:
            module = "run"
        if Logger.__instance.get(module):
            return Logger.__instance.get(module)
        with lock:
            Logger.__instance[module] = Logger(module)
        return Logger.__instance.get(module)


def __append_log_queue(level, text):
    global LOG_INDEX, LOG_QUEUE, LOG_SEQ
    with lock:
        text = escape(text)
        if text.startswith("【"):
            source = re.findall(r"(?<=【).*?(?=】)", text)[0]
            text = text.replace(f"【{source}】", "")
        else:
            source = "System"
        LOG_SEQ += 1
        LOG_QUEUE.append({
            "seq": LOG_SEQ,
            "time": time.strftime('%H:%M:%S', time.localtime(time.time())),
            "level": level,
            "source": source,
            "text": text})
        LOG_INDEX += 1


def get_logs_since(since=0, source=None):
    """
    取序号大于 since 的日志（供「实时日志」按各自游标拉取）。

    每个连接传入自己上次拿到的 cursor，互不干扰：
    旧连接未及时关闭、多标签页、反向代理保持的长连接，都不会再「吃掉」新连接的日志。
    :param since: 上次返回的 cursor（首次传 0）
    :param source: 可选，按来源过滤
    :return: (logs, cursor) —— cursor 为当前最大序号，下次原样传回
    """
    with lock:
        if not LOG_QUEUE:
            return [], LOG_SEQ
        # since 落后于队列现存最早一条时（首次连接、或落后太多被挤出队列），
        # 从现存最早一条开始给；首次连接因此能看到最近的历史日志
        earliest = LOG_QUEUE[0].get("seq", 0)
        if since < earliest - 1:
            since = earliest - 1
        if since > LOG_SEQ:
            since = LOG_SEQ
        logs = [lg for lg in LOG_QUEUE if lg.get("seq", 0) > since]
        if source:
            logs = [lg for lg in logs if lg.get("source") == source]
        return logs, LOG_SEQ


def debug(text, module=None):
    return Logger.get_instance(module).logger.debug(text)


def info(text, module=None):
    __append_log_queue("INFO", text)
    return Logger.get_instance(module).logger.info(text)


def error(text, module=None):
    __append_log_queue("ERROR", text)
    return Logger.get_instance(module).logger.error(text)


def warn(text, module=None):
    __append_log_queue("WARN", text)
    return Logger.get_instance(module).logger.warning(text)


def refresh_loglevel(module=None):
    Logger.get_instance(module).refresh_loglevel()


def console(text):
    __append_log_queue("INFO", text)
    print(text)
