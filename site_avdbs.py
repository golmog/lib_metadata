import time

from lxml import html

from .plugin import P
from .site_util import SiteUtil

logger = P.logger

# 사이트 차단


class SiteAvdbs:
    site_char = "A"

    @staticmethod
    def get_actor_info(entity_actor, proxy_url=None, retry=True):
        try:
            try:
                seq_url = "https://www.avdbs.com/w2017/api/iux_kwd_srch_log.php"
                seq_url += f"?op=srch&kwd={entity_actor['originalname']}"
                seq = SiteUtil.get_response(seq_url, proxy_url=proxy_url, timeout=5).json()["seq"]
                # logger.debug("seq: %s", seq)
                url = "https://www.avdbs.com/w2017/page/search/search_actor.php"
                url += f"?kwd={entity_actor['originalname']}&seq={seq}"
                # logger.debug(url)
                res = SiteUtil.get_response(url, proxy_url=proxy_url, timeout=5)
            except Exception:
                logger.exception("배우 정보를 가져오는 중 예외:")
            # logger.debug('avdbs status code : %s', res.status_code)
            # logger.debug(res.text)
            res.encoding = "utf-8"
            data = '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">' + res.text
            tree = html.fromstring(data)
            img_tag = tree.xpath("//img")

            if img_tag:
                nodes = tree.xpath('//div[@class="dscr"]/p')
                tmp = nodes[1].xpath("./a")[0].text_content().strip()
                # tmp = nodes[1].xpath('./a')[0].text_content().strip()
                if tmp.split("(")[1].split(")")[0] or tmp.split("（")[1].split("）")[0] == entity_actor["originalname"]:
                    entity_actor["name"] = nodes[0].xpath("./a")[0].text_content().strip()
                    entity_actor["name2"] = nodes[1].xpath("./a")[0].text_content().strip().split("(")[0]
                    entity_actor["site"] = "avdbs"
                    entity_actor["thumb"] = SiteUtil.process_image_mode("3", img_tag[0].attrib["src"].strip())
                else:
                    logger.debug("Avdbs miss match")
            else:
                logger.debug("Avdbs no match")
            return entity_actor
        except ValueError:
            # 2020-06-01
            # 단시간에 많은 요청시 Error발생
            if retry:
                logger.debug("단시간 많은 요청으로 재시도")
                time.sleep(2)
                return SiteAvdbs.get_actor_info(entity_actor, proxy_url=proxy_url, retry=False)
        except Exception:
            logger.exception("배우 정보 업데이트 중 예외:")
        return entity_actor
