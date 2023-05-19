import time

from .plugin import P
from .site_util import SiteUtil

logger = P.logger


class SiteHentaku:
    site_char = "H"

    @staticmethod
    def __get_actor_info(originalname, proxy_url=None):
        url = "https://hentaku.co/starsearch.php"
        tree = SiteUtil.get_tree(url, post_data={"name": originalname}, proxy_url=proxy_url)

        hrefs = tree.xpath('//div[@class="avstar_photo"]/a/@href')
        if not hrefs:
            logger.debug("검색 결과 없음: originalname=%s", originalname)
            return None

        names = tree.xpath('//div[@class="avstar_info_b"]/text()')[0].split("/")
        if len(names) != 3:
            logger.debug("검색 결과에서 이름을 찾을 수 없음: len(%s) != 2", names)
            return None

        name_ko, name_en, name_ja = [x.strip() for x in names]
        if name_ja == originalname:
            doc = SiteUtil.get_tree(hrefs[0], proxy_url=proxy_url)
            thumb_url = doc.xpath('//div[@class="avstar_photo"]//img/@src')[0]
            return {
                "name": name_ko,
                "name2": name_en,
                "site": "hentaku",
                "thumb": SiteUtil.process_image_mode("3", thumb_url, proxy_url=proxy_url),
            }
        logger.debug("검색 결과 중 일치 항목 없음: %s != %s", name_ja, originalname)
        return None

    @staticmethod
    def get_actor_info(entity_actor, proxy_url=None, retry=True):
        try:
            info = SiteHentaku.__get_actor_info(entity_actor["originalname"], proxy_url=proxy_url)
        except Exception:
            # 2020-06-01
            # 단시간에 많은 요청시시 Error발생
            if retry:
                logger.debug("단시간 많은 요청으로 재시도")
                time.sleep(2)
                return SiteHentaku.get_actor_info(entity_actor, proxy_url=proxy_url, retry=False)
            logger.exception("배우 정보 업데이트 중 예외: originalname=%s", entity_actor["originalname"])
        else:
            if info is not None:
                entity_actor.update(info)
        return entity_actor
