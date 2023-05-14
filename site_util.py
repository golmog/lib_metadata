import json
import os
import re
import sys
import time
import traceback
from datetime import timedelta
from io import BytesIO

import requests
from lxml import html
from PIL import Image

from framework import SystemModelSetting, path_data, py_urllib
from framework.util import Util
from system import SystemLogicTrans

from .plugin import P

logger = P.logger


class SiteUtil:
    try:
        from requests_cache import CachedSession

        session = CachedSession(
            "lib_metadata",
            use_temp=True,
            expire_after=timedelta(hours=6),
        )
    except Exception:
        session = requests.Session()

    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.82 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        # 'Cookie' : 'over18=1;age_check_done=1;',
    }

    @classmethod
    def get_tree(
        cls,
        url,
        proxy_url=None,
        headers=None,
        post_data=None,
        cookies=None,
        verify=None,
    ):
        text = cls.get_text(
            url,
            proxy_url=proxy_url,
            headers=headers,
            post_data=post_data,
            cookies=cookies,
            verify=verify,
        )
        # logger.debug(text)
        if text is None:
            return
        return html.fromstring(text)

    @classmethod
    def get_text(
        cls,
        url,
        proxy_url=None,
        headers=None,
        post_data=None,
        cookies=None,
        verify=None,
    ):
        res = cls.get_response(
            url,
            proxy_url=proxy_url,
            headers=headers,
            post_data=post_data,
            cookies=cookies,
            verify=verify,
        )
        # logger.debug('url: %s, %s', res.status_code, url)
        # if res.status_code != 200:
        #    return None
        return res.text

    @classmethod
    def get_response(
        cls,
        url,
        proxy_url=None,
        headers=None,
        post_data=None,
        cookies=None,
        verify=None,
    ):
        proxies = None
        if proxy_url is not None and proxy_url != "":
            proxies = {"http": proxy_url, "https": proxy_url}
        if headers is None:
            headers = cls.default_headers

        if post_data is None:
            if verify == None:
                res = cls.session.get(
                    url, headers=headers, proxies=proxies, cookies=cookies
                )
            else:
                res = cls.session.get(
                    url,
                    headers=headers,
                    proxies=proxies,
                    cookies=cookies,
                    verify=verify,
                )
        else:
            if verify == None:
                res = cls.session.post(
                    url,
                    headers=headers,
                    proxies=proxies,
                    data=post_data,
                    cookies=cookies,
                )
            else:
                res = cls.session.post(
                    url,
                    headers=headers,
                    proxies=proxies,
                    data=post_data,
                    cookies=cookies,
                    verify=verify,
                )

        # logger.debug(res.headers)
        # logger.debug(res.text)
        return res

    @classmethod
    def process_image_mode(cls, image_mode, image_url, proxy_url=None):
        # logger.debug('process_image_mode : %s %s', image_mode, image_url)
        if image_url is None:
            return
        ret = image_url
        if image_mode == "1":
            tmp = "{ddns}/metadata/api/image_proxy?url=" + py_urllib.quote_plus(
                image_url
            )
            if proxy_url is not None:
                tmp += "&proxy_url=" + py_urllib.quote_plus(proxy_url)
            ret = Util.make_apikey(tmp)
        elif image_mode == "2":
            tmp = "{ddns}/metadata/api/discord_proxy?url=" + py_urllib.quote_plus(
                image_url
            )
            ret = Util.make_apikey(tmp)
        elif image_mode == "3":  # 고정 디스코드 URL.
            ret = cls.discord_proxy_image(image_url)
        elif image_mode == "4":  # landscape to poster
            # logger.debug(image_url)
            ret = (
                "{ddns}/metadata/normal/image_process.jpg?mode=landscape_to_poster&url="
                + py_urllib.quote_plus(image_url)
            )
            ret = ret.format(ddns=SystemModelSetting.get("ddns"))
            # ret = Util.make_apikey(tmp)
        elif image_mode == "5":  # 로컬에 포스터를 만들고
            # image_url : 디스코드에 올라간 표지 url 임.
            im = Image.open(BytesIO(cls.session.get(image_url).content))
            width, height = im.size
            filename = "proxy_%s.jpg" % str(time.time())
            filepath = os.path.join(path_data, "tmp", filename)
            if width > height:
                left = width / 1.895734597
                top = 0
                right = width
                bottom = height
                poster = im.crop((left, top, right, bottom))
                poster.save(filepath)
            else:
                im.save(filepath)
            # poster_url = '{ddns}/file/data/tmp/%s' % filename
            # poster_url = Util.make_apikey(poster_url)
            # logger.debug('poster_url : %s', poster_url)
            ret = cls.discord_proxy_image_localfile(filepath)
        return ret

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
        "投稿": "투고",
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
    def trans(cls, text, do_trans=True, source="ja", target="ko"):
        if do_trans:
            return SystemLogicTrans.trans(text, source=source, target=target)
        return text

    """
    @classmethod
    def discord_proxy_get_target(cls, image_url):
        from tool_expand import ToolExpandDiscord
        return ToolExpandDiscord.discord_proxy_get_target(image_url)
    """

    @classmethod
    def discord_proxy_get_target_poster(cls, image_url):
        from tool_expand import ToolExpandDiscord

        return ToolExpandDiscord.discord_proxy_get_target(image_url + "av_poster")

    @classmethod
    def discord_proxy_set_target(cls, source, target):
        from tool_expand import ToolExpandDiscord

        return ToolExpandDiscord.discord_proxy_set_target(source, target)

    @classmethod
    def discord_proxy_set_target_poster(cls, source, target):
        from tool_expand import ToolExpandDiscord

        return ToolExpandDiscord.discord_proxy_set_target(source + "av_poster", target)

    @classmethod
    def discord_proxy_image(cls, image_url):
        from tool_expand import ToolExpandDiscord

        return ToolExpandDiscord.discord_proxy_image(image_url)

    @classmethod
    def discord_proxy_image_localfile(cls, filepath):
        from tool_expand import ToolExpandDiscord

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
            ret["image_url"] = cls.process_image_mode(
                image_mode, image_url, proxy_url=proxy_url
            )
            # else:
            #    ret['image_url'] = tmp

            if with_poster:
                logger.debug(ret["image_url"])
                # ret['poster_image_url'] = cls.discord_proxy_get_target_poster(image_url)
                # if ret['poster_image_url'] is None:
                if image_mode == "1":
                    im = Image.open(BytesIO(cls.session.get(image_url).content))
                    w, h = im.size
                    if w > h:
                        # landscape to poster
                        ret["poster_image_url"] = cls.process_image_mode(
                            "4", image_url, proxy_url=proxy_url
                        )
                    else:
                        # already poster
                        ret["poster_image_url"] = cls.process_image_mode(
                            "1", image_url, proxy_url=proxy_url
                        )
                else:
                    ret["poster_image_url"] = cls.process_image_mode(
                        "5", ret["image_url"]
                    )  # 포스터이미지 url 본인 sjva
                    # if image_mode == '3': # 디스코드 url 모드일때만 포스터도 디스코드로
                    # ret['poster_image_url'] = cls.process_image_mode('3', tmp) #디스코드 url / 본인 sjva가 소스이므로 공용으로 등록
                    # cls.discord_proxy_set_target_poster(image_url, ret['poster_image_url'])

        except Exception as exception:
            logger.error("Exception:%s", exception)
            logger.error(traceback.format_exc())
        # logger.debug('get_image_url')
        # logger.debug(ret)
        return ret

    @classmethod
    def is_hq_poster(cls, im_sm, im_lg):
        def imhist(im, pdf=True):
            arr = np.asarray(im.convert("RGB")).reshape(-1, 3)
            return np.apply_along_axis(
                lambda x: np.histogram(x, bins=255, range=(1, 256), density=pdf)[0],
                0,
                arr,
            )

        def imdist(_im1, _im2):
            """based on bhattacharyya distance"""
            h1, h2 = imhist(_im1), imhist(_im2)
            return (
                -np.log(np.sum(np.sqrt(h1 * h2), axis=0, keepdims=True).min()) * 255.0
            )

        def imopen(im):
            if not isinstance(im, Image.Image) and isinstance(im, str):
                return Image.open(BytesIO(cls.session.get(im).content))
            return im

        try:
            import numpy as np

            im_sm, im_lg = imopen(im_sm), imopen(im_lg)
            ws, hs = im_sm.size
            wl, hl = im_lg.size
            if ws > wl or hs > hl:
                # large image is not large enough
                return False
            if abs(ws / hs - wl / hl) > 0.1:
                # aspect ratio is quite different
                return False
            return imdist(im_sm, im_lg) < 5.0
        except Exception:
            return False

    @classmethod
    def is_same_image(cls, url1, url2, part1=False, part2=False):
        def imhist(im, pdf=True):
            arr = np.asarray(im.convert("RGB")).reshape(-1, 3)
            return np.apply_along_axis(
                lambda x: np.histogram(x, bins=255, range=(1, 256), density=pdf)[0],
                0,
                arr,
            )

        def imdist(_im1, _im2):
            """based on bhattacharyya distance"""
            h1, h2 = imhist(_im1), imhist(_im2)
            return (
                -np.log(np.sum(np.sqrt(h1 * h2), axis=0, keepdims=True).min()) * 255.0
            )

        try:
            import numpy as np

            im1 = Image.open(BytesIO(cls.session.get(url1).content))
            im2 = Image.open(BytesIO(cls.session.get(url2).content))
            w, h = im1.size
            if w > h and part1:
                im1 = im1.crop((w / 1.895734597, 0, w, h))
            w, h = im2.size
            if w > h and part2:
                im2 = im2.crop((w / 1.895734597, 0, w, h))
            return imdist(im1, im2) < 5.0
        except Exception:
            return False

    @classmethod
    def change_html(cls, text):
        if text is not None:
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
        return re.sub("[-=+,#/\?:^$.@*\"※~&%ㆍ!』\\‘|\(\)\[\]\<\>`'…》]", "", text)

    @classmethod
    def compare(cls, a, b):
        return (
            cls.remove_special_char(a).replace(" ", "").lower()
            == cls.remove_special_char(b).replace(" ", "").lower()
        )

    @classmethod
    def get_show_compare_text(cls, title):
        title = title.replace("일일연속극", "").strip()
        title = title.replace("특별기획드라마", "").strip()
        title = re.sub(r"\[.*?\]", "", title).strip()
        title = re.sub(r"\(.*?\)", "", title).strip()
        title = re.sub(r"^.{2,3}%s" % "드라마", "", title).strip()
        title = re.sub(r"^.{1,3}%s" % "특집", "", title).strip()
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
            if type(data["director"][0]) == type({}):
                tmp_list = []
                for tmp in data["director"]:
                    tmp_list.append(tmp["name"])
                data["info"]["director"] = ", ".join(tmp_list).strip()
            else:
                data["info"]["director"] = data["director"]
        if "credits" in data and len(data["credits"]) > 0:
            data["info"]["writer"] = []
            if type(data["credits"][0]) == type({}):
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
        pyVer3 = sys.version_info >= (3, 0)
        if pyVer3:  # for Ver 3 or later
            encText = text
        else:  # for Ver 2.x
            if type(text) is not unicode:
                encText = text.decode("utf-8")
            else:
                encText = text

        hanCount = len(re.findall("[\u3130-\u318F\uAC00-\uD7A3]+", encText))
        return hanCount > 0

    @classmethod
    def is_include_hangul(cls, text):
        try:
            pyVer3 = sys.version_info >= (3, 0)
            if pyVer3:  # for Ver 3 or later
                encText = text
            else:  # for Ver 2.x
                if type(text) is not unicode:
                    encText = text.decode("utf-8")
                else:
                    encText = text

            hanCount = len(re.findall("[\u3130-\u318F\uAC00-\uD7A3]+", encText))
            return hanCount > 0
        except:
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

        cookies = SystemLogicSite.get_daum_cookies()
        from framework import SystemModelSetting

        proxy_url = SystemModelSetting.get("site_daum_proxy")
        from .site_daum import SiteDaum

        headers = SiteDaum.default_headers
        text = cls.get_text(
            url,
            proxy_url=proxy_url,
            headers=headers,
            post_data=post_data,
            cookies=cookies,
        )
        if text is None:
            return
        return html.fromstring(text)

    @classmethod
    def get_text_daum(cls, url, post_data=None):
        from system.logic_site import SystemLogicSite

        cookies = SystemLogicSite.get_daum_cookies()
        from framework import SystemModelSetting

        proxy_url = SystemModelSetting.get("site_daum_proxy")
        from .site_daum import SiteDaum

        headers = SiteDaum.default_headers
        res = cls.get_response(
            url,
            proxy_url=proxy_url,
            headers=headers,
            post_data=post_data,
            cookies=cookies,
        )
        return res.text

    @classmethod
    def get_response_daum(cls, url, post_data=None):
        from system.logic_site import SystemLogicSite

        cookies = SystemLogicSite.get_daum_cookies()
        from framework import SystemModelSetting

        proxy_url = SystemModelSetting.get("site_daum_proxy")
        from .site_daum import SiteDaum

        headers = SiteDaum.default_headers

        res = cls.get_response(
            url,
            proxy_url=proxy_url,
            headers=headers,
            post_data=post_data,
            cookies=cookies,
        )
        return res

    @classmethod
    def process_image_book(cls, url):
        im = Image.open(BytesIO(cls.session.get(url).content))
        width, height = im.size
        filename = "proxy_%s.jpg" % str(time.time())
        filepath = os.path.join(path_data, "tmp", filename)
        left = 0
        top = 0
        right = width
        bottom = width
        poster = im.crop((left, top, right, bottom))
        try:
            poster.save(filepath)
        except:
            poster = poster.convert("RGB")
            poster.save(filepath)
        ret = cls.discord_proxy_image_localfile(filepath)
        return ret

    @classmethod
    def get_treefromcontent(
        cls, url, proxy_url=None, headers=None, post_data=None, cookies=None
    ):
        text = SiteUtil.get_response(
            url,
            proxy_url=proxy_url,
            headers=headers,
            post_data=post_data,
            cookies=cookies,
        ).content
        # logger.debug(text)
        if text is None:
            return
        return html.fromstring(text)

    @classmethod
    def get_translated_tag(cls, type, tag):
        tags_json = os.path.join(os.path.dirname(__file__), "tags.json")
        with open(tags_json, "r", encoding="utf8") as f:
            tags = json.load(f)

        if type in tags:
            if tag in tags[type]:
                res = tags[type][tag]

            else:
                trans_text = SystemLogicTrans.trans(
                    tag, source="ja", target="ko"
                ).strip()
                # logger.debug(f'태그 번역: {tag} - {trans_text}')
                if (
                    cls.is_include_hangul(trans_text)
                    or trans_text.replace(" ", "").isalnum()
                ):
                    tags[type][tag] = trans_text

                    with open(tags_json, "w", encoding="utf8") as f:
                        json.dump(tags, f, indent=4, ensure_ascii=False)

                    res = tags[type][tag]
                else:
                    res = tag

            return res

        else:
            return tag
