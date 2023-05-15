import re

from lxml import html

from .plugin import P
from .entity_av import EntityAVSearch
from .entity_base import (
    EntityMovie,
    EntityThumb,
    EntityActor,
    EntityRatings,
    EntityExtra,
)
from .site_util import SiteUtil

logger = P.logger


class SiteJav321:
    site_name = "jav321"
    site_base_url = "https://www.jav321.com"
    module_char = "D"
    site_char = "T"

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        if keyword[-3:-1] == "cd":
            keyword = keyword[:-3]
        keyword = keyword.lower().replace(" ", "-")

        url = f"{cls.site_base_url}/search"
        res = SiteUtil.get_response(url, proxy_url=proxy_url, post_data={"sn": keyword.lower()})

        ret = []
        if res.history:
            item = EntityAVSearch(cls.site_name)
            item.code = cls.module_char + cls.site_char + res.url.split("/")[-1]
            item.score = 100
            item.ui_code = keyword.upper()
            try:
                base_xpath = "/html/body/div[2]/div[1]/div[1]"
                tree = html.fromstring(res.text)
                image_url = tree.xpath(f"{base_xpath}/div[2]/div[1]/div[1]/img/@src")[0]
                item.image_url = SiteUtil.process_image_mode(image_mode, image_url, proxy_url=proxy_url)
                if manual:
                    _image_mode = "0" if image_mode == "3" else image_mode
                    item.image_url = SiteUtil.process_image_mode(_image_mode, item.image_url, proxy_url=proxy_url)
                date = tree.xpath(
                    f'{base_xpath}/div[2]/div[1]/div[2]/b[contains(text(),"配信開始日")]/following-sibling::text()'
                )[0]
                item.desc = f"발매일{date}"
                item.year = int(date.lstrip(":").strip()[:4])
                item.title = item.title_ok = tree.xpath(f"{base_xpath}/div[1]/h3/text()")[0].strip()
                if do_trans:
                    item.title_ko = SiteUtil.trans(item.title)
            except Exception:
                logger.exception("개별 검색 결과 처리 중 예외:")
            ret.append(item.as_dict())
        return ret

    @classmethod
    def search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        ret = {}
        try:
            data = cls.__search(keyword, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data else "no_match"
            ret["data"] = data
        return ret

    @classmethod
    def __info(cls, code, do_trans=True, proxy_url=None, image_mode="0"):
        url = f"{cls.site_base_url}/video/{code[2:]}"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url)

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]
        entity.mpaa = "청소년 관람불가"
        entity.tag = []

        base_xpath = "/html/body/div[2]/div[1]/div[1]"
        nodes = tree.xpath(f"{base_xpath}/div[2]/div[1]/div[2]/b")
        for node in nodes:
            key = node.text_content().strip()
            value = node.xpath(".//following-sibling::text()")[0].replace(":", "").strip()
            if key == "女优":
                logger.debug(value)
                a_tags = node.xpath(".//following-sibling::a")
                if a_tags:
                    entity.actor = []
                    for a_tag in a_tags:
                        if "star" in a_tag.attrib["href"]:
                            entity.actor.append(EntityActor(a_tag.text_content().strip()))
                        else:
                            break
                if len(entity.actor) == 0:
                    try:
                        entity.actor = [EntityActor(value.split(" ")[0].split("/")[0].strip())]
                    except Exception:
                        pass
            elif key in ["标签", "ジャンル"]:
                entity.genre = []
                a_tags = node.xpath(".//following-sibling::a")
                for a_tag in a_tags:
                    tmp = a_tag.text_content().strip()
                    if tmp in SiteUtil.av_genre:
                        entity.genre.append(SiteUtil.av_genre[tmp])
                    elif tmp in SiteUtil.av_genre_ignore_ja:
                        continue
                    else:
                        genre_tmp = SiteUtil.trans(tmp, do_trans=do_trans).replace(" ", "")
                        if genre_tmp not in SiteUtil.av_genre_ignore_ko:
                            entity.genre.append(genre_tmp)
            elif key in ["番号", "品番"]:
                entity.title = entity.originaltitle = entity.sorttitle = value.upper()
                entity.tag = [entity.title.split("-")[0]]
            elif key in ["发行日期", "配信開始日"]:
                entity.premiered = value
                entity.year = int(value[:4])
            elif key in ["播放时长", "収録時間"]:
                try:
                    entity.runtime = int(re.compile(r"(?P<no>\d{2,3})").search(url).group("no"))
                except Exception:
                    pass
            elif key == "赞":
                if entity.ratings is None:
                    entity.ratings = [EntityRatings(0, votes=int(value), max=5, name="jav321")]
                else:
                    entity.ratings[0].votes = int(value)
            elif key in ["评分", "平均評価"]:
                try:
                    tmp = float(value)
                    if entity.ratings is None:
                        entity.ratings = [EntityRatings(tmp, max=5, name="jav321")]
                    else:
                        logger.debug(value)
                        entity.ratings[0].value = tmp
                except Exception:
                    pass
            elif key in ["片商", "メーカー"]:
                # entity.studio = value
                entity.studio = node.xpath(".//following-sibling::a")[0].text_content().strip()

        # low-res image poster - assuming always available
        image_url_thumb = tree.xpath(f"{base_xpath}/div[2]/div[1]/div[1]/img/@src")[0]

        image_url_landscape = ""
        for node in tree.xpath('//*[@id="vjs_sample_player"]'):
            image_url_landscape = node.attrib["poster"]
            entity.extras = [EntityExtra("trailer", entity.title, "mp4", node.xpath(".//source")[0].attrib["src"])]

        image_url_arts = []
        for img_src in tree.xpath("/html/body/div[2]/div[2]/div//img/@src"):
            if img_src == image_url_landscape:
                continue
            image_url_arts.append(img_src)

        # first art to landscape
        if image_url_arts and not image_url_landscape:
            image_url_landscape = image_url_arts.pop(0)

        # resolving image_url_poster
        image_url_poster, poster_from_landscape = "", False
        if image_url_arts:
            if SiteUtil.is_hq_poster(image_url_thumb, image_url_arts[0]):
                # first art to poster
                image_url_poster = image_url_arts[0]
            elif SiteUtil.is_hq_poster(image_url_thumb, image_url_arts[-1]):
                # last art to poster
                image_url_poster = image_url_arts[-1]
        if not image_url_poster and SiteUtil.has_hq_poster(image_url_thumb, image_url_landscape):
            image_url_poster = image_url_landscape
            poster_from_landscape = True
        if not image_url_poster:
            image_url_poster = image_url_thumb

        entity.thumb = []
        if poster_from_landscape:
            tmp = SiteUtil.get_image_url(image_url_poster, image_mode, proxy_url=proxy_url, with_poster=True)
            entity.thumb.append(EntityThumb(aspect="poster", value=tmp["poster_image_url"]))
        else:
            entity.thumb.append(
                EntityThumb(
                    aspect="poster",
                    value=SiteUtil.process_image_mode(image_mode, image_url_poster, proxy_url=proxy_url),
                )
            )
        if image_url_landscape:
            tmp = SiteUtil.get_image_url(image_url_landscape, image_mode, proxy_url=proxy_url)
            entity.thumb.append(EntityThumb(aspect="landscape", value=tmp["image_url"]))

        # entity.plot = SiteUtil.trans(tree.xpath(f'{base_xpath}/div[1]/h3/text()')[0], do_trans=do_trans)
        tmp = tree.xpath(f"{base_xpath}/div[2]/div[3]/div/text()")
        if len(tmp) > 0:
            entity.plot = SiteUtil.trans(tmp[0], do_trans=do_trans)

        tmp = tree.xpath(f"{base_xpath}/div[1]/h3/text()")[0].strip()
        # logger.debug(tmp)

        flag_is_plot = False
        if entity.actor is None or len(entity.actor) == 0:
            if len(tmp) < 10:
                entity.actor = [EntityActor(tmp)]
            else:
                flag_is_plot = True
        else:
            flag_is_plot = True
        if flag_is_plot:
            if entity.plot is None:
                entity.plot = SiteUtil.trans(tmp, do_trans=do_trans)
            else:
                entity.plot += SiteUtil.trans(tmp, do_trans=do_trans)
        # logger.debug(entity.plot)

        entity.fanart = []
        for idx, img_url in enumerate(image_url_arts):
            if idx > 9:
                break
            value = SiteUtil.process_image_mode(image_mode, img_url, proxy_url=proxy_url)
            entity.fanart.append(value)

        entity.tagline = entity.plot

        return entity

    @classmethod
    def info(cls, code, do_trans=True, proxy_url=None, image_mode="0"):
        ret = {}
        try:
            entity = cls.__info(code, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode)
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success"
            ret["data"] = entity.as_dict()
        return ret
