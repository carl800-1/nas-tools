import json
import os
import threading
import time

from cachetools import cached, TTLCache
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool

from app.db.models import BaseMedia, MEDIASYNCITEMS, MEDIASYNCSTATISTIC
from app.utils import ExceptionUtils
from config import Config

lock = threading.Lock()
_Engine = create_engine(
    f"sqlite:///{os.path.join(Config().get_config_path(), 'media.db')}?check_same_thread=False",
    echo=False,
    poolclass=QueuePool,
    pool_pre_ping=True,
    pool_size=100,
    pool_recycle=60 * 10,
    max_overflow=0
)
_Session = scoped_session(sessionmaker(bind=_Engine,
                                       autoflush=True,
                                       autocommit=False))


class MediaDb:

    @property
    def session(self):
        return _Session()

    @staticmethod
    def init_db():
        with lock:
            BaseMedia.metadata.create_all(_Engine)

    def insert(self, server_type, iteminfo, seasoninfo):
        if not server_type or not iteminfo:
            return False
        try:
            self.session.query(MEDIASYNCITEMS).filter(MEDIASYNCITEMS.SERVER == server_type,
                                                      MEDIASYNCITEMS.ITEM_ID == iteminfo.get("id")).delete()
            self.session.flush()
            self.session.add(MEDIASYNCITEMS(
                SERVER=server_type,
                LIBRARY=iteminfo.get("library"),
                ITEM_ID=iteminfo.get("id"),
                ITEM_TYPE=iteminfo.get("type"),
                TITLE=iteminfo.get("title"),
                ORGIN_TITLE=iteminfo.get("originalTitle"),
                YEAR=iteminfo.get("year"),
                TMDBID=iteminfo.get("tmdbid"),
                IMDBID=iteminfo.get("imdbid"),
                PATH=iteminfo.get("path"),
                JSON=json.dumps(seasoninfo)
            ))
            self.session.commit()
            self.__clear_query_cache()
            return True
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            self.session.rollback()
        return False

    @staticmethod
    def __clear_query_cache():
        """
        清理 __query_cached 的 TTL 缓存。
        它被 @cached 装饰，返回的是 ORM 实例；一旦 insert/empty 删掉了同一行，
        缓存里就会留下 detached 对象，下次命中会抛 DetachedInstanceError。
        """
        try:
            MediaDb._MediaDb__query_cached.cache_clear()
        except Exception:
            pass

    def empty(self, server_type=None, library=None):
        try:
            if server_type and library:
                self.session.query(MEDIASYNCITEMS).filter(MEDIASYNCITEMS.SERVER == server_type,
                                                          MEDIASYNCITEMS.LIBRARY == library).delete()
            elif server_type:
                self.session.query(MEDIASYNCITEMS).filter(MEDIASYNCITEMS.SERVER == server_type).delete()
            else:
                self.session.query(MEDIASYNCITEMS).delete()
            self.session.commit()
            self.__clear_query_cache()
            return True
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            self.session.rollback()
        return False

    def statistics(self, server_type, total_count, movie_count, tv_count):
        if not server_type:
            return False
        try:
            self.session.query(MEDIASYNCSTATISTIC).filter(MEDIASYNCSTATISTIC.SERVER == server_type).delete()
            self.session.flush()
            self.session.add(MEDIASYNCSTATISTIC(
                SERVER=server_type,
                TOTAL_COUNT=total_count,
                MOVIE_COUNT=movie_count,
                TV_COUNT=tv_count,
                UPDATE_TIME=time.strftime('%Y-%m-%d %H:%M:%S',
                                          time.localtime(time.time()))
            ))
            self.session.commit()
            return True
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            self.session.rollback()
        return False

    # 电影/剧集在库中的 ITEM_TYPE 写法因客户端而异，这里列出全部已知别名，
    # 供 query 按媒体类型收敛结果（同名电影与剧集会互相串台）。
    MOVIE_TYPES = ("电影", "Movie", "movie", "movies")
    TV_TYPES = ("电视剧", "Series", "series", "show", "tv", "tvshows")

    def query(self, server_type, title, year, tmdbid, mtype=None):
        """
        查询媒体库中是否已存在某项目
        :param mtype: 可选的媒体类型（MovieTypes 成员 / MediaType 枚举 / 字符串）。
                      传入时按类型收敛，避免同名电影与剧集互相命中。
        """
        # ⚠️ 归一必须发生在 @cached 装饰层【外面】：
        # cachetools 会先用实参算缓存键（hash），mtype 若是不可哈希的对象
        # （如自定义实例），哈希这一步就会先抛 TypeError，根本轮不到函数体。
        mtype_key = MediaDb.__normalize_mtype_key(mtype)
        return self.__query_cached(server_type=server_type,
                                   title=title,
                                   year=year,
                                   tmdbid=tmdbid,
                                   mtype_key=mtype_key)

    @cached(cache=TTLCache(maxsize=128, ttl=60))
    def __query_cached(self, server_type, title, year, tmdbid, mtype_key=None):
        """
        query 的实际实现（缓存键里的类型已是字符串，保证可哈希）
        """
        if not server_type or not title:
            return {}

        # 把 mtype_key 归一成「库中 ITEM_TYPE 的允许集合」，None 表示不限制
        allowed_types = self.__resolve_item_types(mtype_key)

        def _apply_type_filter(q):
            if allowed_types is None:
                return q
            return q.filter(MEDIASYNCITEMS.ITEM_TYPE.in_(allowed_types))

        if tmdbid:
            item = _apply_type_filter(
                self.session.query(MEDIASYNCITEMS).filter(
                    MEDIASYNCITEMS.SERVER == server_type,
                    MEDIASYNCITEMS.TMDBID == tmdbid)).first()
            if item:
                return item

        if year:
            item = _apply_type_filter(
                self.session.query(MEDIASYNCITEMS).filter(
                    MEDIASYNCITEMS.SERVER == server_type,
                    MEDIASYNCITEMS.TITLE == title,
                    MEDIASYNCITEMS.YEAR == year)).first()
        else:
            item = _apply_type_filter(
                self.session.query(MEDIASYNCITEMS).filter(
                    MEDIASYNCITEMS.SERVER == server_type,
                    MEDIASYNCITEMS.TITLE == title)).first()
        if item:
            if tmdbid and (not item.TMDBID or item.TMDBID != str(tmdbid)):
                return {}
        return item

    @staticmethod
    def __normalize_mtype_key(mtype):
        """
        把任意媒体类型对象归一成可哈希的字符串，供 @cached 当键使用
        """
        if mtype is None:
            return None
        try:
            # Enum 成员 / MediaType 之类的对象
            value = getattr(mtype, "value", mtype)
        except Exception:
            return None
        if isinstance(value, (str, int, float, bool)):
            return str(value).strip() or None
        # 其它不可哈希的类型，退化为取类型名（避免缓存键报错，同时仍能区分）
        try:
            hash(value)
            return str(value)
        except TypeError:
            return type(value).__name__

    @classmethod
    def __resolve_item_types(cls, mtype):
        """
        把媒体类型归一成 ITEM_TYPE 的候选值元组；无法判定时返回 None（不过滤）
        """
        if mtype is None:
            return None
        # MediaType.MOVIE / MediaType.TV 都是中文值（「电影」「电视剧」）
        type_str = getattr(mtype, "value", mtype)
        type_str = str(type_str or "").strip()
        if not type_str:
            return None
        if type_str in cls.MOVIE_TYPES:
            return cls.MOVIE_TYPES
        if type_str in cls.TV_TYPES:
            return cls.TV_TYPES
        return None

    def get_statistics(self, server_type):
        if not server_type:
            return None
        return self.session.query(MEDIASYNCSTATISTIC).filter(MEDIASYNCSTATISTIC.SERVER == server_type).first()
