import json
from collections import OrderedDict
from collections.abc import MutableMapping

from pathlib import Path

from framework import path_data  # pylint: disable=import-error

from .plugin import P

logger = P.logger

# requests_cache (expire_after=6h)
# all http requests including it with MetaServer

# sqlitedict (no expire) + memcache (during runtime)
# discord image proxy urls

# lru_cache (during runtime)
# translation


class MemCache(MutableMapping):
    def __init__(self, *args, **kwargs):
        self.__maxsize = kwargs.pop("maxsize", None)
        self.__d = OrderedDict(*args, **kwargs)

    @property
    def maxsize(self):
        return self.__maxsize

    def __getitem__(self, key):
        if key in self.__d:
            self.__d.move_to_end(key)
        return self.__d[key]

    def __setitem__(self, key, value):
        if key in self.__d:
            self.__d.move_to_end(key)
        elif len(self.__d) == self.maxsize:
            self.__d.popitem(last=False)
        self.__d[key] = value

    def __delitem__(self, key):
        del self.__d[key]

    def __iter__(self):
        return iter(self.__d)

    def __len__(self):
        return len(self.__d)

    def __repr__(self):
        return repr(self.__d)

    # 여기까지 필수

    def clear(self):
        return self.__d.clear()

    def keys(self):
        return self.__d.keys()

    def values(self):
        return self.__d.values()

    def items(self):
        return self.__d.items()

    def pop(self, *args):
        return self.__d.pop(*args)

    def __contains__(self, item):
        return item in self.__d


class CacheUtil:
    cache_dict = None
    cache_file = Path(path_data).joinpath("db/lib_metadata.db")

    @classmethod
    def get_cache(cls, maxsize=100) -> dict:
        if cls.cache_dict is not None:
            return cls.cache_dict
        try:
            from sqlitedict import SqliteDict

            cls.cache_dict = SqliteDict(
                cls.cache_file, tablename="lib_metadata_cache", encode=json.dumps, decode=json.loads, autocommit=True
            )
        except Exception as e:
            logger.warning("캐시 초기화 실패: %s", e)
            cls.cache_dict = MemCache(maxsize=maxsize)
        return cls.cache_dict
