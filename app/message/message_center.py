import threading
import time
from collections import deque

from app.utils.commons import singleton

# 消息序号计数器，单调递增，唯一标识一条消息。
# 不能改用时间戳做游标：同秒插入的多条消息共享同一时间戳，
# 前端回传「最新一条的时间」后，同时间的其余消息会被判定为已读而永久丢失
# （典型场景：一次转移 N 部剧，循环体内每条插一条消息）。
#
# 为什么是模块级变量，而不是原来的类属性 MessageCenter._seq：
#   `@singleton` 会把类名 MessageCenter 整个替换成包装函数 _singleton，
#   类体方法里再引用 MessageCenter，拿到的是那个函数，于是
#   `MessageCenter._seq += 1` 必然抛
#   AttributeError: 'function' object has no attribute '_seq'。
#   后果是所有 insert_system_message 全部失败 —— 下载失败、订阅成功等通知
#   既进不了消息中心，也推不到消息客户端；Web 端点「下载」还会直接 500。
_seq_lock = threading.Lock()
_seq_counter = 0


def _next_seq():
    """
    取下一个消息序号
    """
    global _seq_counter
    with _seq_lock:
        _seq_counter += 1
        return _seq_counter


@singleton
class MessageCenter:
    _message_queue = deque(maxlen=50)

    def __init__(self):
        pass

    def insert_system_message(self, title, content=None):
        """
        新增系统消息
        :param title: 标题
        :param content: 内容
        """
        title = title.replace("\n", "<br>").strip() if title else ""
        content = content.replace("\n", "<br>").strip() if content else ""
        self.__append_message_queue(title, content)

    def __append_message_queue(self, title, content):
        """
        将消息增加到队列
        """
        seq = _next_seq()
        self._message_queue.appendleft({
            "title": title,
            "content": content,
            "seq": seq,
            "time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))
        })

    def get_system_messages(self, num=20, lst_seq=None):
        """
        查询系统消息
        :param num: 条数，仅 lst_seq 为空（首屏全量拉取）时生效
        :param lst_seq: 客户端已收到的最大序号，只返回比它更新的消息
        """
        # 游标可能来自前端 JSON（数字、字符串）或空值，统一归一化。
        # 不能只靠 or 0 兜底：字符串 "0" 是 truthy，"0" > 0 会抛 TypeError
        try:
            lst_seq = int(lst_seq or 0)
        except (TypeError, ValueError):
            lst_seq = 0
        if not lst_seq:
            return list(self._message_queue)[-num:]
        ret_messages = []
        # 队列按 seq 递减排列（最新在最前），遇到不比自己新的即可停止
        for message in list(self._message_queue):
            if message.get("seq", 0) > lst_seq:
                ret_messages.append(message)
            else:
                break
        return ret_messages
