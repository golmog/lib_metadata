import time

import requests
from lxml import html

from .plugin import P
from .site_util import SiteUtil

logger = P.logger

# 사이트 차단


class SiteAvdbs:
    site_char = "A"
    site_name = "avdbs"

    @staticmethod
    def __get_actor_info(originalname, proxy_url=None, image_mode="0") -> dict:
        with requests.Session() as s:
            s.headers.update(SiteUtil.default_headers)
            if proxy_url:
                s.proxies.update({"http": proxy_url, "https": proxy_url})
            base_url = "https://www.avdbs.com"
            s.get(base_url)  # 한번 접속해서 쿠키를 받아와야 함
            url = base_url + "/w2017/api/iux_kwd_srch_log.php"
            params = {"op": "srch", "kwd": originalname}
            seq = s.get(url, params=params).json()["seq"]
            url = base_url + "/w2017/page/search/search_actor.php"
            params = {"kwd": originalname, "seq": seq}
            tree = html.fromstring(s.get(url, params=params).text)

        img_src = tree.xpath(".//img/@src")
        if not img_src:
            logger.debug("검색 결과 없음: originalname=%s", originalname)
            return None

        names = tree.xpath('//p[starts-with(@class, "e_name")]/a')[0].text_content()
        names = names.strip(")").split("(")
        if len(names) != 2:
            logger.debug("검색 결과에서 이름을 찾을 수 없음: len(%s) != 2", names)
            return None

        name_en, name_ja = [x.strip() for x in names]
        if name_ja == originalname:
            name_ko = tree.xpath('//p[starts-with(@class, "k_name")]/a')[0].text_content().strip()
            return {
                "name": name_ko,
                "name2": name_en,
                "site": "avdbs",
                "thumb": SiteUtil.process_image_mode(image_mode, img_src[0], proxy_url=proxy_url),
            }
        logger.debug("검색 결과 중 일치 항목 없음: %s != %s", name_ja, originalname)
        return None

    @staticmethod
    def get_actor_info(entity_actor, **kwargs):
        retry = kwargs.pop("retry", True)
        try:
            info = SiteAvdbs.__get_actor_info(entity_actor["originalname"], **kwargs)
        except Exception:
            # 2020-06-01
            # 단시간에 많은 요청시 Error발생
            if retry:
                logger.debug("단시간 많은 요청으로 재시도")
                time.sleep(2)
                return SiteAvdbs.get_actor_info(entity_actor, retry=False, **kwargs)
            logger.exception("배우 정보 업데이트 중 예외: originalname=%s", entity_actor["originalname"])
        else:
            if info is not None:
                entity_actor.update(info)
        return entity_actor
