import json
import os
import random
import re
import time
from datetime import timedelta
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import requests
from discord_webhook import DiscordEmbed, DiscordWebhook
from lxml import html
from PIL import Image

from framework import SystemModelSetting, path_data, py_urllib
from framework.util import Util
from system import SystemLogicTrans
from tool_expand import ToolExpandDiscord

from .entity_base import EntityThumb, EntityActor
from .plugin import P
from .cache_util import CacheUtil

logger = P.logger

try:
    webhook_file = Path(path_data).joinpath("db/lib_metadata.webhook")
    with open(webhook_file, encoding="utf-8") as fp:
        my_webhooks = list(filter(str, fp.read().splitlines()))
except Exception as e:
    logger.warning("나만의 웹훅 사용 안함: %s", e)
    my_webhooks = []


class SiteUtil:
    try:
        from requests_cache import CachedSession

        session = CachedSession(
            "lib_metadata",
            use_temp=True,
            expire_after=timedelta(hours=6),
        )
    except Exception as e:
        logger.warning("requests cache 사용 안함: %s", e)
        session = requests.Session()

    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.82 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        # 'Cookie' : 'over18=1;age_check_done=1;',
    }

    @classmethod
    def get_tree(cls, url, **kwargs):
        text = cls.get_text(url, **kwargs)
        # logger.debug(text)
        if text is None:
            return text
        return html.fromstring(text)

    @classmethod
    def get_text(cls, url, **kwargs):
        res = cls.get_response(url, **kwargs)
        # logger.debug('url: %s, %s', res.status_code, url)
        # if res.status_code != 200:
        #    return None
        return res.text

    @classmethod
    def get_response(cls, url, **kwargs):
        proxy_url = kwargs.pop("proxy_url", None)
        if proxy_url:
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

        kwargs.setdefault("headers", cls.default_headers)

        method = kwargs.pop("method", "GET")
        post_data = kwargs.pop("post_data", None)
        if post_data:
            method = "POST"
            kwargs["data"] = post_data

        res = cls.session.request(method, url, **kwargs)
        # logger.debug(res.headers)
        # logger.debug(res.text)
        return res

    @classmethod
    def imopen(cls, img_src, proxy_url=None):
        if isinstance(img_src, Image.Image):
            return img_src
        try:
            # local file
            return Image.open(img_src)
        except (FileNotFoundError, OSError):
            # remote url
            try:
                res = cls.get_response(img_src, proxy_url=proxy_url)
                return Image.open(BytesIO(res.content))
            except Exception:
                logger.exception("이미지 여는 중 예외:")
                return None

    @classmethod
    def imcrop(cls, im, position=None, box_only=False):
        """원본 이미지에서 잘라내 세로로 긴 포스터를 만드는 함수"""

        if not isinstance(im, Image.Image):
            return im
        width, height = im.size
        new_w = height / 1.4225
        if position == "l":
            left = 0
        elif position == "c":
            left = (width - new_w) / 2
        else:
            # default: from right
            left = width - new_w
        box = (left, 0, left + new_w, height)
        if box_only:
            return box
        return im.crop(box)

    @classmethod
    def resolve_jav_imgs(cls, img_urls: dict, ps_to_poster: bool = False, proxy_url: str = None, crop_mode: str = None):
        ps = img_urls["ps"]  # poster small
        pl = img_urls["pl"]  # poster large
        arts = img_urls["arts"]  # arts

        # poster 기본값
        poster = ps if ps_to_poster else ""
        poster_crop = None

        if not poster and arts:
            if cls.is_hq_poster(ps, arts[0], proxy_url=proxy_url):
                # first art to poster
                poster = arts[0]
            elif len(arts) > 1 and cls.is_hq_poster(ps, arts[-1], proxy_url=proxy_url):
                # last art to poster
                poster = arts[-1]
        if not poster and pl:
            if cls.is_hq_poster(ps, pl, proxy_url=proxy_url):
                # pl이 세로로 큰 이미지
                poster = pl
            elif crop_mode is not None:
                # 사용자 설정에 따름
                poster, poster_crop = pl, crop_mode
            else:
                loc = cls.has_hq_poster(ps, pl, proxy_url=proxy_url)
                if loc:
                    # pl의 일부를 crop해서 포스터로...
                    poster, poster_crop = pl, loc
        if not poster:
            # 그래도 없으면...
            poster = ps

        # # first art to landscape
        # if arts and not pl:
        #     pl = arts.pop(0)

        img_urls.update(
            {
                "poster": poster,
                "poster_crop": poster_crop,
                "landscape": pl,
            }
        )

    @classmethod
    def process_jav_imgs(cls, image_mode: str, img_urls: dict, proxy_url: str = None):
        thumbs = []

        landscape = img_urls["landscape"]
        if landscape:
            _url = cls.process_image_mode(image_mode, landscape, proxy_url=proxy_url)
            thumbs.append(EntityThumb(aspect="landscape", value=_url))

        poster, poster_crop = img_urls["poster"], img_urls["poster_crop"]
        if poster:
            _url = cls.process_image_mode(image_mode, poster, proxy_url=proxy_url, crop_mode=poster_crop)
            thumbs.append(EntityThumb(aspect="poster", value=_url))

        return thumbs

    @classmethod
    def process_image_mode(cls, image_mode, image_url, proxy_url=None, crop_mode=None):
        # logger.debug('process_image_mode : %s %s', image_mode, image_url)
        if image_url is None:
            return
        ret = image_url
        if image_mode == "1":
            tmp = "{ddns}/metadata/api/image_proxy?url=" + py_urllib.quote_plus(image_url)
            if proxy_url is not None:
                tmp += "&proxy_url=" + py_urllib.quote_plus(proxy_url)
            if crop_mode is not None:
                tmp += "&crop_mode=" + py_urllib.quote_plus(crop_mode)
            ret = Util.make_apikey(tmp)
        elif image_mode == "2":
            tmp = "{ddns}/metadata/api/discord_proxy?url=" + py_urllib.quote_plus(image_url)
            if proxy_url is not None:
                tmp += "&proxy_url=" + py_urllib.quote_plus(proxy_url)
            if crop_mode is not None:
                tmp += "&crop_mode=" + py_urllib.quote_plus(crop_mode)
            ret = Util.make_apikey(tmp)
        elif image_mode == "3":  # 고정 디스코드 URL.
            ret = cls.discord_proxy_image(image_url, proxy_url=proxy_url, crop_mode=crop_mode)
        elif image_mode == "4":  # landscape to poster
            # logger.debug(image_url)
            ret = "{ddns}/metadata/normal/image_process.jpg?mode=landscape_to_poster&url=" + py_urllib.quote_plus(
                image_url
            )
            ret = ret.format(ddns=SystemModelSetting.get("ddns"))
            # ret = Util.make_apikey(tmp)
        elif image_mode == "5":  # 로컬에 포스터를 만들고
            # image_url : 디스코드에 올라간 표지 url 임.
            im = cls.imopen(image_url, proxy_url=proxy_url)
            width, height = im.size
            filename = f"proxy_{time.time()}.jpg"
            filepath = os.path.join(path_data, "tmp", filename)
            if width > height:
                im = cls.imcrop(im)
            im.save(filepath, quality=95)
            # poster_url = '{ddns}/file/data/tmp/%s' % filename
            # poster_url = Util.make_apikey(poster_url)
            # logger.debug('poster_url : %s', poster_url)
            ret = cls.discord_proxy_image_localfile(filepath)
        return ret

    @classmethod
    def __shiroutoname_info(cls, keyword):
        url = "https://shiroutoname.com/"
        tree = cls.get_tree(url, params={"s": keyword}, timeout=30)

        results = []
        for article in tree.xpath("//section//article"):
            title = article.xpath("./h2")[0].text_content()
            title = title[title.find("【") + 1 : title.rfind("】")]

            link = article.xpath(".//a/@href")[0]
            thumb_url = article.xpath(".//a/img/@data-src")[0]
            title_alt = article.xpath(".//a/img/@alt")[0]
            assert title == title_alt  # 다르면?

            result = {"title": title, "link": link, "thumb_url": thumb_url}

            for div in article.xpath("./div/div"):
                kv = div.xpath("./div")
                if len(kv) != 2:
                    continue
                key, value = [x.text_content().strip() for x in kv]
                if not key.endswith("："):
                    continue

                if key.startswith("品番"):
                    result["code"] = value
                    another_link = kv[1].xpath("./a/@href")[0]
                    assert link == another_link  # 다르면?
                elif key.startswith("素人名"):
                    result["name"] = value
                elif key.startswith("配信日"):
                    result["premiered"] = value
                    # format - YYYY/MM/DD
                elif key.startswith("シリーズ"):
                    result["series"] = value
                else:
                    logger.warning("UNKNOWN: %s=%s", key, value)

            a_class = "mlink" if "mgstage.com" in link else "flink"
            actors = []
            for a_tag in article.xpath(f'./div/div/a[@class="{a_class}"]'):
                actors.append(
                    {
                        "name": a_tag.text_content().strip(),
                        "href": a_tag.xpath("./@href")[0],
                    }
                )
            result["actors"] = actors
            results.append(result)
        return results

    @classmethod
    def shiroutoname_info(cls, entity):
        """upgrade entity(meta info) by shiroutoname"""
        data = None
        for d in cls.__shiroutoname_info(entity.originaltitle):
            if entity.originaltitle.lower() in d["code"].lower():
                data = d
                break
        if data is None:
            return entity
        if data.get("premiered", None):
            value = data["premiered"].replace("/", "-")
            entity.premiered = value
            entity.year = int(value[:4])
        if data.get("actors", []):
            entity.actor = [EntityActor(a["name"]) for a in data["actors"]]
        return entity

    av_genre = {
        "巨尻": "큰엉덩이",
        "ギャル": "갸루",
        "着エロ": "착에로",
        "競泳・スクール水着": "학교수영복",
        "日焼け": "태닝",
        "指マン": "핑거링",
        "潮吹き": "시오후키",
        "ごっくん": "곳쿤",
        "パイズリ": "파이즈리",
        "手コキ": "수음",
        "淫語": "음란한말",
        "姉・妹": "남매",
        "お姉さん": "누님",
        "インストラクター": "트레이너",
        "ぶっかけ": "붓카케",
        "シックスナイン": "69",
        "ボディコン": "타이트원피스",
        "電マ": "전동마사지",
        "イタズラ": "짖궂음",
        "足コキ": "풋잡",
        "原作コラボ": "원작각색",
        "看護婦・ナース": "간호사",
        "コンパニオン": "접객업",
        "家庭教師": "과외",
        "キス・接吻": "딥키스",
        "局部アップ": "음부확대",
        "ポルチオ": "자궁성감자극",
        "セーラー服": "교복",
        "イラマチオ": "격한페라·딥스로트",
        "投稿": "투고",
        "キャンギャル": "도우미걸",
        "女優ベスト・総集編": "베스트총집편",
        "クンニ": "커닐링구스",
        "アナル": "항문노출",
        "超乳": "폭유",
        "復刻": "리마스터",
        "義母": "새어머니",
        "おもちゃ": "노리개",
        "くノ一": "여자닌자",
        "羞恥": "수치심",
        "ドラッグ": "최음제",
        "パンチラ": "판치라",
        "巨乳フェチ": "큰가슴",
        "巨乳": "큰가슴",
        "レズキス": "레즈비언",
        "レズ": "레즈비언",
        "スパンキング": "엉덩이때리기",
        "放尿・お漏らし": "방뇨·오모라시",
        "アクメ・オーガズム": "절정·오르가즘",
        "ニューハーフ": "쉬메일",
        "鬼畜": "색마·양아치",
        "辱め": "능욕",
        "フェラ": "펠라치오",
    }

    av_genre_ignore_ja = ["DMM獨家"]

    av_genre_ignore_ko = [
        "고화질",
        "독점전달",
        "세트상품",
        "단체작품",
        "기간한정세일",
        "기리모자",
        "데지모",
        "슬림",
        "미소녀",
        "미유",
        "망상족",
        "거유",
        "에로스",
        "작은",
        "섹시",
    ]

    av_studio = {
        "乱丸": "란마루",
        "大洋図書": "대양도서",
        "ミル": "미루",
        "無垢": "무쿠",
        "サムシング": "Something",
        "本中": "혼나카",
        "ナンパJAPAN": "난파 재팬",
        "溜池ゴロー": "다메이케고로",
        "プラム": "프라무",
        "アップス": "Apps",
        "えむっ娘ラボ": "엠코 라보",
        "クンカ": "킁카",
        "映天": "에이텐",
        "ジャムズ": "JAMS",
        "牛感": "규칸",
    }

    @classmethod
    @lru_cache(maxsize=100)
    def __trans(cls, text):
        return SystemLogicTrans.trans(text, source="ja", target="ko")

    @classmethod
    def trans(cls, text, do_trans=True, source="ja", target="ko"):
        text = text.strip()
        if do_trans and text:
            if source == "ja" and target == "ko":
                text = cls.__trans(text)
            else:
                text = SystemLogicTrans.trans(text, source=source, target=target)
        return text.strip()

    @classmethod
    def __discord_proxy_image(cls, image_url, webhook_url, proxy_url=None, crop_mode=None):
        if not image_url:
            return image_url

        cache = CacheUtil.get_cache()
        cached = cache.get(image_url, {})

        mode = f"crop{crop_mode}" if crop_mode is not None else "original"
        if mode in cached:
            return cached[mode]

        im = cls.imopen(image_url, proxy_url=proxy_url)
        if im is None:
            return image_url

        if crop_mode is not None:
            imformat = im.format  # retain original image's format like "JPEG", "PNG"
            im = cls.imcrop(im, position=crop_mode)
            im.format = imformat

        webhook = DiscordWebhook(url=webhook_url, rate_limit_retry=True)

        # 파일 이름이 대충 이상한 값이면 첨부가 안될 수 있음
        filename = f"{mode}.{im.format.lower().replace('jpeg', 'jpg')}"
        with BytesIO() as buf:
            im.save(buf, format=im.format, quality=95)
            webhook.add_file(buf.getvalue(), filename=filename)
        embed = DiscordEmbed(title=image_url, color=16164096)
        embed.set_footer(text="lib_metadata")
        embed.set_timestamp()
        embed.set_image(url=f"attachment://{filename}")
        embed.add_embed_field(name="mode", value=mode)
        webhook.add_embed(embed)

        res = webhook.execute()
        try:
            cached[mode] = res.json()["embeds"][0]["image"]["url"]
        except AttributeError:
            cached[mode] = res[0].json()["embeds"][0]["image"]["url"]

        cache[image_url] = cached
        return cached[mode]

    @classmethod
    def discord_proxy_image(cls, image_url, **kwargs):
        if my_webhooks:
            kwargs.setdefault("proxy_url", None)
            kwargs.setdefault("crop_mode", None)
            try:
                return cls.__discord_proxy_image(image_url, random.choice(my_webhooks), **kwargs)
            except Exception:
                logger.exception("이미지 프록시 중 예외:")
                return image_url
        return ToolExpandDiscord.discord_proxy_image(image_url)

    @classmethod
    def discord_proxy_image_localfile(cls, filepath):
        if my_webhooks:
            try:
                return cls.__discord_proxy_image(filepath, random.choice(my_webhooks))
            except Exception:
                logger.exception("이미지 프록시 중 예외:")
                return filepath
        return ToolExpandDiscord.discord_proxy_image_localfile(filepath)

    @classmethod
    def get_image_url(cls, image_url, image_mode, proxy_url=None, with_poster=False):
        try:
            # logger.debug('get_image_url')
            # logger.debug(image_url)
            # logger.debug(image_mode)
            ret = {}
            # tmp = cls.discord_proxy_get_target(image_url)

            # logger.debug('tmp : %s', tmp)
            # if tmp is None:
            ret["image_url"] = cls.process_image_mode(image_mode, image_url, proxy_url=proxy_url)
            # else:
            #    ret['image_url'] = tmp

            if with_poster:
                logger.debug(ret["image_url"])
                # ret['poster_image_url'] = cls.discord_proxy_get_target_poster(image_url)
                # if ret['poster_image_url'] is None:
                ret["poster_image_url"] = cls.process_image_mode("5", ret["image_url"])  # 포스터이미지 url 본인 sjva
                # if image_mode == '3': # 디스코드 url 모드일때만 포스터도 디스코드로
                # ret['poster_image_url'] = cls.process_image_mode('3', tmp) #디스코드 url / 본인 sjva가 소스이므로 공용으로 등록
                # cls.discord_proxy_set_target_poster(image_url, ret['poster_image_url'])

        except Exception:
            logger.exception("Image URL 생성 중 예외:")
        # logger.debug('get_image_url')
        # logger.debug(ret)
        return ret

    @classmethod
    def is_hq_poster(cls, im_sm, im_lg, proxy_url=None):
        try:
            from imagehash import dhash as hfun  # threshold = [11, 18]

            im_sm = cls.imopen(im_sm, proxy_url=proxy_url)
            im_lg = cls.imopen(im_lg, proxy_url=proxy_url)
            ws, hs = im_sm.size
            wl, hl = im_lg.size
            if ws > wl or hs > hl:
                # large image is not large enough
                return False
            if abs(ws / hs - wl / hl) > 0.1:
                # aspect ratio is quite different
                return False
            hdis = hfun(im_sm) - hfun(im_lg)
            if hdis >= 14:
                return False
            if hdis <= 6:
                return True
            from imagehash import phash as hfun

            hdis += hfun(im_sm) - hfun(im_lg)
            return hdis < 20  # threshold = [15, 25]
        except ImportError:
            return False
        except Exception:
            logger.exception("고화질 포스터 확인 중 예외:")
            return False

    @classmethod
    def has_hq_poster(cls, im_sm, im_lg, proxy_url=None):
        try:
            from imagehash import average_hash as hfun  # crop한 이미지의 align이 확실하지 않아서 average_hash가 더 적합함

            im_sm = cls.imopen(im_sm, proxy_url=proxy_url)
            im_lg = cls.imopen(im_lg, proxy_url=proxy_url)
            ws, hs = im_sm.size
            wl, hl = im_lg.size
            if ws > wl or hs > hl:
                # large image is not large enough
                return None

            for pos in ["r", "l", "c"]:
                val = hfun(im_sm) - hfun(cls.imcrop(im_lg, position=pos))
                if val <= 10:
                    return pos
        except ImportError:
            pass
        except Exception:
            logger.exception("고화질 포스터 확인 중 예외:")
        return None

    @classmethod
    def change_html(cls, text):
        if not text:
            return text
        return (
            text.replace("&nbsp;", " ")
            .replace("&nbsp", " ")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&amp;", "&")
            .replace("&quot;", '"')
            .replace("&#35;", "#")
            .replace("&#39;", "‘")
        )

    @classmethod
    def remove_special_char(cls, text):
        return re.sub(r"[-=+,#/\?:^$.@*\"※~&%ㆍ!』\\‘|\(\)\[\]\<\>`'…》]", "", text)

    @classmethod
    def compare(cls, a, b):
        return (
            cls.remove_special_char(a).replace(" ", "").lower() == cls.remove_special_char(b).replace(" ", "").lower()
        )

    @classmethod
    def get_show_compare_text(cls, title):
        title = title.replace("일일연속극", "").strip()
        title = title.replace("특별기획드라마", "").strip()
        title = re.sub(r"\[.*?\]", "", title).strip()
        title = re.sub(r"\(.*?\)", "", title).strip()
        title = re.sub(r"^.{2,3}드라마", "", title).strip()
        title = re.sub(r"^.{1,3}특집", "", title).strip()
        return title

    @classmethod
    def compare_show_title(cls, title1, title2):
        title1 = cls.get_show_compare_text(title1)
        title2 = cls.get_show_compare_text(title2)
        return cls.compare(title1, title2)

    @classmethod
    def info_to_kodi(cls, data):
        data["info"] = {}
        data["info"]["title"] = data["title"]
        data["info"]["studio"] = data["studio"]
        data["info"]["premiered"] = data["premiered"]
        # if data['info']['premiered'] == '':
        #    data['info']['premiered'] = data['year'] + '-01-01'
        data["info"]["year"] = data["year"]
        data["info"]["genre"] = data["genre"]
        data["info"]["plot"] = data["plot"]
        data["info"]["tagline"] = data["tagline"]
        data["info"]["mpaa"] = data["mpaa"]
        if "director" in data and len(data["director"]) > 0:
            if isinstance(data["director"][0], dict):
                tmp_list = []
                for tmp in data["director"]:
                    tmp_list.append(tmp["name"])
                data["info"]["director"] = ", ".join(tmp_list).strip()
            else:
                data["info"]["director"] = data["director"]
        if "credits" in data and len(data["credits"]) > 0:
            data["info"]["writer"] = []
            if isinstance(data["credits"][0], dict):
                for tmp in data["credits"]:
                    data["info"]["writer"].append(tmp["name"])
            else:
                data["info"]["writer"] = data["credits"]

        if "extras" in data and data["extras"] is not None and len(data["extras"]) > 0:
            if data["extras"][0]["mode"] in ["naver", "youtube"]:
                url = "{ddns}/metadata/api/video?site={site}&param={param}&apikey={apikey}".format(
                    ddns=SystemModelSetting.get("ddns"),
                    site=data["extras"][0]["mode"],
                    param=data["extras"][0]["content_url"],
                    apikey=SystemModelSetting.get("auth_apikey"),
                )
                data["info"]["trailer"] = url
            elif data["extras"][0]["mode"] == "mp4":
                data["info"]["trailer"] = data["extras"][0]["content_url"]

        data["cast"] = []

        if "actor" in data and data["actor"] is not None:
            for item in data["actor"]:
                entity = {}
                entity["type"] = "actor"
                entity["role"] = item["role"]
                entity["name"] = item["name"]
                entity["thumbnail"] = item["thumb"]
                data["cast"].append(entity)

        if "art" in data and data["art"] is not None:
            for item in data["art"]:
                if item["aspect"] == "landscape":
                    item["aspect"] = "fanart"
        elif "thumb" in data and data["thumb"] is not None:
            for item in data["thumb"]:
                if item["aspect"] == "landscape":
                    item["aspect"] = "fanart"
            data["art"] = data["thumb"]
        if "art" in data:
            data["art"] = sorted(data["art"], key=lambda k: k["score"], reverse=True)
        return data

    @classmethod
    def is_hangul(cls, text):
        hanCount = len(re.findall("[\u3130-\u318F\uAC00-\uD7A3]+", text))
        return hanCount > 0

    @classmethod
    def is_include_hangul(cls, text):
        try:
            hanCount = len(re.findall("[\u3130-\u318F\uAC00-\uD7A3]+", text))
            return hanCount > 0
        except Exception:
            return False

    country_code_translate = {
        "GH": "가나",
        "GA": "가봉",
        "GY": "가이아나",
        "GM": "감비아",
        "GP": "프랑스",
        "GT": "과테말라",
        "GU": "미국",
        "GD": "그레나다",
        "GE": "그루지야",
        "GR": "그리스",
        "GL": "덴마크",
        "GW": "기니비소",
        "GN": "기니",
        "NA": "나미비아",
        "NG": "나이지리아",
        "ZA": "남아프리카공화국",
        "NL": "네덜란드",
        "AN": "네덜란드",
        "NP": "네팔",
        "NO": "노르웨이",
        "NF": "오스트레일리아",
        "NZ": "뉴질랜드",
        "NC": "프랑스",
        "NE": "니제르",
        "NI": "니카라과",
        "TW": "타이완",
        "DK": "덴마크",
        "DM": "도미니카연방",
        "DO": "도미니카공화국",
        "DE": "독일",
        "LA": "라오스",
        "LV": "라트비아",
        "RU": "러시아",
        "LB": "레바논",
        "LS": "레소토",
        "RO": "루마니아",
        "RW": "르완다",
        "LU": "룩셈부르크",
        "LR": "라이베리아",
        "LY": "리비아",
        "RE": "프랑스",
        "LT": "리투아니아",
        "LI": "리첸쉬테인",
        "MG": "마다가스카르",
        "MH": "미국",
        "FM": "미크로네시아",
        "MK": "마케도니아",
        "MW": "말라위",
        "MY": "말레이지아",
        "ML": "말리",
        "MT": "몰타",
        "MQ": "프랑스",
        "MX": "멕시코",
        "MC": "모나코",
        "MA": "모로코",
        "MU": "모리셔스",
        "MR": "모리타니",
        "MZ": "모잠비크",
        "MS": "영국",
        "MD": "몰도바",
        "MV": "몰디브",
        "MN": "몽고",
        "US": "미국",
        "VI": "미국",
        "AS": "미국",
        "MM": "미얀마",
        "VU": "바누아투",
        "BH": "바레인",
        "BB": "바베이도스",
        "BS": "바하마",
        "BD": "방글라데시",
        "BY": "벨라루스",
        "BM": "영국",
        "VE": "베네수엘라",
        "BJ": "베넹",
        "VN": "베트남",
        "BE": "벨기에",
        "BZ": "벨리세",
        "BA": "보스니아헤르체코비나",
        "BW": "보츠와나",
        "BO": "볼리비아",
        "BF": "부르키나파소",
        "BT": "부탄",
        "MP": "미국",
        "BG": "불가리아",
        "BR": "브라질",
        "BN": "브루네이",
        "BI": "브룬디",
        "WS": "미국(사모아,",
        "SA": "사우디아라비아",
        "CY": "사이프러스",
        "SM": "산마리노",
        "SN": "세네갈",
        "SC": "세이셸",
        "LC": "세인트루시아",
        "VC": "세인트빈센트그레나딘",
        "KN": "세인트키츠네비스",
        "SB": "솔로몬아일란드",
        "SR": "수리남",
        "LK": "스리랑카",
        "SZ": "스와질랜드",
        "SE": "스웨덴",
        "CH": "스위스",
        "ES": "스페인",
        "SK": "슬로바키아",
        "SI": "슬로베니아",
        "SL": "시에라리온",
        "SG": "싱가포르",
        "AE": "아랍에미레이트연합국",
        "AW": "네덜란드",
        "AM": "아르메니아",
        "AR": "아르헨티나",
        "IS": "아이슬란드",
        "HT": "아이티",
        "IE": "아일란드",
        "AZ": "아제르바이잔",
        "AF": "아프가니스탄",
        "AI": "영국",
        "AD": "안도라",
        "AG": "앤티과바부다",
        "AL": "알바니아",
        "DZ": "알제리",
        "AO": "앙골라",
        "ER": "에리트리아",
        "EE": "에스토니아",
        "EC": "에콰도르",
        "SV": "엘살바도르",
        "GB": "영국",
        "VG": "영국",
        "YE": "예멘",
        "OM": "오만",
        "AU": "오스트레일리아",
        "AT": "오스트리아",
        "HN": "온두라스",
        "JO": "요르단",
        "UG": "우간다",
        "UY": "우루과이",
        "UZ": "우즈베크",
        "UA": "우크라이나",
        "ET": "이디오피아",
        "IQ": "이라크",
        "IR": "이란",
        "IL": "이스라엘",
        "EG": "이집트",
        "IT": "이탈리아",
        "IN": "인도",
        "ID": "인도네시아",
        "JP": "일본",
        "JM": "자메이카",
        "ZM": "잠비아",
        "CN": "중국",
        "MO": "중국",
        "HK": "중국",
        "CF": "중앙아프리카",
        "DJ": "지부티",
        "GI": "영국",
        "ZW": "짐바브웨",
        "TD": "차드",
        "CZ": "체코",
        "CS": "체코슬로바키아",
        "CL": "칠레",
        "CA": "캐나다",
        "CM": "카메룬",
        "CV": "카보베르데",
        "KY": "영국",
        "KZ": "카자흐",
        "QA": "카타르",
        "KH": "캄보디아",
        "KE": "케냐",
        "CR": "코스타리카",
        "CI": "코트디봐르",
        "CO": "콜롬비아",
        "CG": "콩고",
        "CU": "쿠바",
        "KW": "쿠웨이트",
        "HR": "크로아티아",
        "KG": "키르키즈스탄",
        "KI": "키리바티",
        "TJ": "타지키스탄",
        "TZ": "탄자니아",
        "TH": "타이",
        "TC": "영국",
        "TR": "터키",
        "TG": "토고",
        "TO": "통가",
        "TV": "투발루",
        "TN": "튀니지",
        "TT": "트리니다드토바고",
        "PA": "파나마",
        "PY": "파라과이",
        "PK": "파키스탄",
        "PG": "파푸아뉴기니",
        "PW": "미국",
        "FO": "덴마크",
        "PE": "페루",
        "PT": "포르투갈",
        "PL": "폴란드",
        "PR": "미국",
        "FR": "프랑스",
        "GF": "프랑스",
        "PF": "프랑스",
        "FJ": "피지",
        "FI": "필란드",
        "PH": "필리핀",
        "HU": "헝가리",
        "KR": "한국",
        "EU": "유럽",
        "SY": "시리아",
        "A1": "Anonymous Proxy",
        "A2": "인공위성IP",
        "PS": "팔레스타인",
        "RS": "세르비아",
        "JE": "저지",
    }

    genre_map = {
        "Action": "액션",
        "Adventure": "어드벤처",
        "Drama": "드라마",
        "Mystery": "미스터리",
        "Mini-Series": "미니시리즈",
        "Science-Fiction": "SF",
        "Thriller": "스릴러",
        "Crime": "범죄",
        "Documentary": "다큐멘터리",
        "Sci-Fi & Fantasy": "SF & 판타지",
        "Animation": "애니메이션",
        "Comedy": "코미디",
        "Romance": "로맨스",
        "Fantasy": "판타지",
        "Sport": "스포츠",
        "Soap": "연속극",
        "Suspense": "서스펜스",
        "Action & Adventure": "액션 & 어드벤처",
        "History": "역사",
        "Science Fiction": "SF",
        "War & Politics": "전쟁 & 정치",
        "Reality": "리얼리티",
    }

    # 의미상으로 여기 있으면 안되나 예전 코드에서 많이 사용하기 때문에 잠깐만 나둔다.
    @classmethod
    def get_tree_daum(cls, url, post_data=None):
        from system.logic_site import SystemLogicSite

        from .site_daum import SiteDaum

        return cls.get_tree(
            url,
            proxy_url=SystemModelSetting.get("site_daum_proxy"),
            headers=SiteDaum.default_headers,
            post_data=post_data,
            cookies=SystemLogicSite.get_daum_cookies(),
        )

    @classmethod
    def get_text_daum(cls, url, post_data=None):
        from system.logic_site import SystemLogicSite

        from .site_daum import SiteDaum

        return cls.get_text(
            url,
            proxy_url=SystemModelSetting.get("site_daum_proxy"),
            headers=SiteDaum.default_headers,
            post_data=post_data,
            cookies=SystemLogicSite.get_daum_cookies(),
        )

    @classmethod
    def get_response_daum(cls, url, post_data=None):
        from system.logic_site import SystemLogicSite

        from .site_daum import SiteDaum

        return cls.get_response(
            url,
            proxy_url=SystemModelSetting.get("site_daum_proxy"),
            headers=SiteDaum.default_headers,
            post_data=post_data,
            cookies=SystemLogicSite.get_daum_cookies(),
        )

    @classmethod
    def process_image_book(cls, url):
        im = cls.imopen(url)
        width, _ = im.size
        filename = f"proxy_{time.time()}.jpg"
        filepath = os.path.join(path_data, "tmp", filename)
        left = 0
        top = 0
        right = width
        bottom = width
        poster = im.crop((left, top, right, bottom))
        try:
            poster.save(filepath, quality=95)
        except Exception:
            poster = poster.convert("RGB")
            poster.save(filepath, quality=95)
        ret = cls.discord_proxy_image_localfile(filepath)
        return ret

    @classmethod
    def get_treefromcontent(cls, url, **kwargs):
        text = cls.get_response(url, **kwargs).content
        # logger.debug(text)
        if text is None:
            return
        return html.fromstring(text)

    @classmethod
    def get_translated_tag(cls, tag_type, tag):
        tags_json = os.path.join(os.path.dirname(__file__), "tags.json")
        with open(tags_json, "r", encoding="utf8") as f:
            tags = json.load(f)

        if tag_type in tags:
            if tag in tags[tag_type]:
                return tags[tag_type][tag]

            trans_text = cls.trans(tag, source="ja", target="ko").strip()
            # logger.debug(f'태그 번역: {tag} - {trans_text}')
            if cls.is_include_hangul(trans_text) or trans_text.replace(" ", "").isalnum():
                tags[tag_type][tag] = trans_text

                with open(tags_json, "w", encoding="utf8") as f:
                    json.dump(tags, f, indent=4, ensure_ascii=False)

                res = tags[tag_type][tag]
            else:
                res = tag

            return res
        return tag
