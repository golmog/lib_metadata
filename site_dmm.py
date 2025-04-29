import json
import re
import requests
import urllib.parse as py_urllib_parse

from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings
from .plugin import P
from .site_util import SiteUtil

logger = P.logger


class SiteDmm:
    site_name = "dmm"
    site_base_url = "https://www.dmm.co.jp"
    module_char = "C"
    site_char = "D"

    # --- DMM 전용 헤더 정의 ---
    dmm_base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36", # 최신으로 업데이트 권장
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin", # 요청에 따라 'none', 'cross-site' 등으로 변경될 수 있음
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        # "Referer": site_base_url + "/", # Referer는 요청 시 동적으로 설정
        # "Cookie": # 쿠키는 SiteUtil.session에 의해 관리될 것으로 예상
    }

    PTN_SEARCH_CID = re.compile(r"\/cid=(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^(h_)?\d*(?P<real>[a-zA-Z]+)(?P<no>\d+)([a-zA-Z]+)?$")
    PTN_ID = re.compile(r"\d{2}id", re.I)
    PTN_RATING = re.compile(r"(?P<rating>[\d|_]+)\.gif")

    # --- 연령 확인 상태 관리 변수 ---
    age_verified = False
    last_proxy_used = None

    @classmethod
    def _get_request_headers(cls, referer=None):
        """요청에 사용할 헤더를 생성합니다."""
        headers = cls.dmm_base_headers.copy()
        if referer:
            headers['Referer'] = referer
        # 필요에 따라 다른 헤더 동적 추가 가능
        # 예: AJAX 요청 시 headers['X-Requested-With'] = 'XMLHttpRequest'
        return headers

    @classmethod
    def _ensure_age_verified(cls, proxy_url=None):
        """SiteUtil.session에 DMM 연령 확인 쿠키가 있는지 확인하고, 없으면 설정합니다."""
        # 프록시 변경 시 또는 아직 미확인 시 확인 절차 진행
        if not cls.age_verified or cls.last_proxy_used != proxy_url:
            logger.debug("Checking/Performing DMM age verification...")
            cls.last_proxy_used = proxy_url

            session_cookies = SiteUtil.session.cookies
            # 이미 유효한 쿠키가 있으면 바로 통과
            if 'age_check_done' in session_cookies and session_cookies.get('age_check_done') == '1':
                logger.debug("Age verification cookie already present in SiteUtil.session.")
                cls.age_verified = True
                return True

            logger.debug("Attempting DMM age verification process by directly sending confirmation GET.")

            # --- 바로 연령 확인 GET 요청 시도 ---
            try:
                # 원래 가려던 URL (rurl 값). 어디로든 상관없을 수 있으나, 기본 성인 페이지로 설정
                target_rurl = f"{cls.site_base_url}/digital/videoa/-/list/"

                # 확인 요청 URL 구성
                confirm_path = f"/age_check/=/declared=yes/?rurl={py_urllib_parse.quote(target_rurl)}"
                age_check_confirm_url = py_urllib_parse.urljoin(cls.site_base_url, confirm_path)
                logger.debug(f"Constructed age confirmation URL: {age_check_confirm_url}")

                # 확인 요청 헤더 (Referer는 이 경우 없거나 기본 URL)
                # Referer가 필수인지 확인 필요. 이전 요청 분석 시 Referer가 age_check 페이지였음.
                # age_check 페이지를 거치지 않으므로 Referer를 설정하지 않거나, cls.site_base_url + "/" 로 설정 시도.
                confirm_headers = cls._get_request_headers(referer=cls.site_base_url + "/") # 기본 Referer 시도

                confirm_response = SiteUtil.get_response(
                    age_check_confirm_url,
                    method='GET',
                    proxy_url=proxy_url,
                    headers=confirm_headers, # 명시적 헤더 전달
                    allow_redirects=False # 302 응답 직접 확인
                )
                logger.debug(f"Confirmation GET response status code: {confirm_response.status_code}")
                logger.debug(f"Confirmation GET response headers: {confirm_response.headers}")
                logger.debug(f"Cookies *after* confirmation GET in SiteUtil.session: {SiteUtil.session.cookies.items()}")

                # 302 응답 및 쿠키 확인
                if confirm_response.status_code == 302 and 'Location' in confirm_response.headers:
                    # Set-Cookie 헤더가 있는지 응답 헤더에서 직접 확인 (SiteUtil.session 업데이트 전)
                    set_cookie_header = confirm_response.headers.get('Set-Cookie', '')
                    if 'age_check_done=1' in set_cookie_header:
                        logger.debug("Age confirmation successful. 'age_check_done=1' found in Set-Cookie header.")
                        # SiteUtil.session 쿠키 업데이트 기다리거나, 바로 확인
                        # 잠시 대기 후 확인 (선택적)
                        # import time
                        # time.sleep(0.1)
                        final_cookies = SiteUtil.session.cookies
                        if 'age_check_done' in final_cookies and final_cookies.get('age_check_done') == '1':
                            logger.debug("age_check_done=1 cookie confirmed in SiteUtil.session.")
                            cls.age_verified = True
                            return True
                        else:
                            logger.warning("Set-Cookie header received, but age_check_done cookie not updated correctly in SiteUtil.session.")
                            cls.age_verified = False
                            return False
                    else:
                        logger.warning("Age confirmation redirected, but 'age_check_done=1' not found in Set-Cookie header.")
                        cls.age_verified = False
                        return False
                else:
                    logger.warning(f"Age confirmation GET request did not return expected 302 redirect. Status: {confirm_response.status_code}")
                    cls.age_verified = False
                    return False

            except Exception as e:
                logger.exception(f"Failed during DMM age verification process: {e}")
                cls.age_verified = False
                return False
        else:
            # 이미 확인됨
            return True


    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        if not cls._ensure_age_verified(proxy_url=proxy_url):
            logger.error("DMM age verification failed. Cannot proceed with search.")
            return []

        keyword = keyword.strip().lower()
        # 2020-06-24
        if keyword[-3:-1] == "cd":
            keyword = keyword[:-3]
        keyword = keyword.replace("-", " ")
        keyword_tmps = keyword.split(" ")
        if len(keyword_tmps) == 2:
            dmm_keyword = keyword_tmps[0] + keyword_tmps[1].zfill(5)
        else:
            dmm_keyword = keyword
        logger.debug("keyword [%s] -> [%s]", keyword, dmm_keyword)

        url = f"{cls.site_base_url}/mono/-/search/=/searchstr={dmm_keyword}/"
        # url = f"{cls.site_base_url}/digital/videoa/-/list/search/=/?searchstr={dmm_keyword}" # xpath 변경 많음
        # url = '%s/search/=/?searchstr=%s' % (cls.site_base_url, dmm_keyword)
        # https://www.dmm.co.jp/search/=/searchstr=tsms00060/

        logger.debug(f"Using search URL: {url}")

        # 검색 요청 헤더 (Referer는 보통 필요 없음, 필요시 이전 페이지 URL 등 설정)
        search_headers = cls._get_request_headers()
        try:
            # 명시적 헤더 전달
            tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=search_headers)
        except Exception as e:
            logger.exception(f"Failed to get tree for search URL: {url}")
            return []

        if tree is None:
            logger.warning(f"Failed to get tree for URL: {url}")
            return []

        lists = tree.xpath('//*[@id="list"]/li')
        logger.debug("dmm search len lists2 :%s", len(lists))

        score = 60  # default score
        ret = []
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                tag = node.xpath('.//div[@class="tmb"]/a')[0]
                href = tag.attrib["href"].lower()
                match = cls.PTN_SEARCH_CID.search(href)
                if match:
                    item.code = cls.module_char + cls.site_char + match.group("code")
                already_exist = False
                for exist_item in ret:
                    if exist_item["code"] == item.code:
                        already_exist = True
                        break
                if already_exist:
                    continue

                tag = node.xpath(".//span[1]/img")[0]
                item.title = item.title_ko = tag.attrib["alt"].strip()
                item.image_url = tag.attrib["src"]

                # tmp = SiteUtil.discord_proxy_get_target(item.image_url)
                # 2021-03-22 서치에는 discord 고정 url을 사용하지 않는다. 3번
                # manual == False  때는 아예 이미치 처리를 할 필요가 없다.
                # 일치항목 찾기 때는 화면에 보여줄 필요가 있는데 3번은 하면 하지 않는다.
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    item.image_url = SiteUtil.process_image_mode(_image_mode, item.image_url, proxy_url=proxy_url)
                    if do_trans:
                        item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                else:
                    item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)

                match = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                if match:
                    item.ui_code = match.group("real") + match.group("no")
                else:
                    item.ui_code = item.code[2:]

                if len(keyword_tmps) == 2:
                    # 2019-11-20 ntr mntr 둘다100
                    if item.ui_code == dmm_keyword:
                        item.score = 100
                    elif item.ui_code.replace("0", "") == dmm_keyword.replace("0", ""):
                        item.score = 100
                    elif dmm_keyword in item.ui_code:  # 전체포함 DAID => AID
                        item.score = score
                        score += -5
                    elif keyword_tmps[0] in item.code and keyword_tmps[1] in item.code:
                        item.score = score
                        score += -5
                    elif keyword_tmps[0] in item.code or keyword_tmps[1] in item.code:
                        item.score = 60
                    else:
                        item.score = 20
                else:
                    if item.code == keyword_tmps[0]:
                        item.score = 100
                    elif keyword_tmps[0] in item.code:
                        item.score = score
                        score += -5
                    else:
                        item.score = 20

                if match:
                    item.ui_code = match.group("real").upper() + "-" + str(int(match.group("no"))).zfill(3)
                else:
                    if "0000" in item.ui_code:
                        item.ui_code = item.ui_code.replace("0000", "-00").upper()
                    else:
                        item.ui_code = item.ui_code.replace("00", "-").upper()
                    if item.ui_code.endswith("-"):
                        item.ui_code = item.ui_code[:-1] + "00"

                logger.debug("score: %s %s ", item.score, item.ui_code)
                ret.append(item.as_dict())
            except Exception:
                logger.exception("개별 검색 결과 처리 중 예외:")
        if not ret and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6)
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode)
        return sorted(ret, key=lambda k: k["score"], reverse=True)

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            data = cls.__search(keyword, **kwargs)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data else "no_match"
            ret["data"] = data
        return ret

    @classmethod
    def __img_urls(cls, tree):
        """collect raw image urls from html page"""

        # poster small
        # 세로 이미지 / 저화질 썸네일
        # 없는 경우가 있나?
        ps = tree.xpath('//div[@id="sample-video"]//img/@src')
        ps = ps[0] if ps else ""
        if not ps:
            logger.warning("이미지 URL을 얻을 수 없음: poster small")

        # poster large
        # 보통 가로 이미지
        # 세로도 있음 zooo-067
        # 없는 경우도 있음 tsds-42464
        pl = tree.xpath('//div[@id="sample-video"]/a/@href')
        pl = pl[0] if pl else ""

        # fanart
        # 없는 경우도 있음 h_1237thtp00052
        # 첫번째 혹은 마지막에 고화질 포스터가 있을 수 있음
        arts = tree.xpath('//a[@name="sample-image"]/@href')

        return {"ps": ps, "pl": pl, "arts": arts}

    @classmethod
    def __info(
        cls,
        code,
        do_trans=True,
        proxy_url=None,
        image_mode="0",
        max_arts=10,
        use_extras=True,
        ps_to_poster=False,
        crop_mode=None,
    ):

        # 연령 확인 선행
        if not cls._ensure_age_verified(proxy_url=proxy_url):
            logger.error("DMM age verification failed. Cannot proceed with info.")
            raise Exception("DMM age verification failed.")

        url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={code[2:]}/" # 상세 페이지 URL 확인 필요! mono 쪽일 수도 있음
        logger.debug(f"Using info URL: {url}")

        # 상세 정보 요청 헤더 (Referer는 검색 결과 페이지 URL 등 설정 가능)
        info_headers = cls._get_request_headers() # 필요시 referer 추가
        try:
            # 명시적 헤더 전달
            tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=info_headers)
        except Exception as e:
            logger.exception(f"Failed to get tree for info URL: {url}")
            raise Exception(f"Failed to get tree for info URL: {url}")

        if tree is None:
            logger.warning(f"Failed to get tree for URL: {url}")
            raise Exception(f"Failed to get tree for URL: {url}")

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]
        entity.mpaa = "청소년 관람불가"

        #
        # 이미지 관련 시작
        #
        img_urls = cls.__img_urls(tree)
        SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)

        entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)

        entity.fanart = []
        for href in img_urls["arts"][:max_arts]:
            entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))
        #
        # 이미지 관련 끝
        #

        alt = tree.xpath('//div[@id="sample-video"]//img/@alt')[0].strip()
        entity.tagline = SiteUtil.trans(alt, do_trans=do_trans).replace("[배달 전용]", "").replace("[특가]", "").strip()

        basetag = '//*[@id="mu"]/div/table//tr/td[1]'

        tags = tree.xpath(f"{basetag}/table//tr")
        tmp_premiered = None
        for tag in tags:
            td_tag = tag.xpath(".//td")
            if len(td_tag) != 2:
                continue
            key = td_tag[0].text_content().strip()
            value = td_tag[1].text_content().strip()
            if value == "----":
                continue
            if key == "商品発売日：":
                entity.premiered = value.replace("/", "-")
                entity.year = int(value[:4])
            elif key == "配信開始日：":
                tmp_premiered = value.replace("/", "-")
            elif key == "収録時間：":
                entity.runtime = int(value.replace("分", ""))
            elif key == "出演者：":
                entity.actor = []
                a_tags = tag.xpath(".//a")
                for a_tag in a_tags:
                    tmp = a_tag.text_content().strip()
                    if tmp == "▼すべて表示する":
                        break
                    entity.actor.append(EntityActor(tmp))
                # for v in value.split(' '):
                #    entity.actor.append(EntityActor(v.strip()))
            elif key == "監督：":
                entity.director = value
            elif key == "シリーズ：":
                if entity.tag is None:
                    entity.tag = []
                entity.tag.append(SiteUtil.trans(value, do_trans=do_trans))
            elif key == "レーベル：":
                entity.studio = value
                if do_trans:
                    if value in SiteUtil.av_studio:
                        entity.studio = SiteUtil.av_studio[value]
                    else:
                        entity.studio = SiteUtil.change_html(SiteUtil.trans(value))
            elif key == "ジャンル：":
                a_tags = td_tag[1].xpath(".//a")
                entity.genre = []
                for tag in a_tags:
                    tmp = tag.text_content().strip()
                    if "％OFF" in tmp:
                        continue
                    if tmp in SiteUtil.av_genre:
                        entity.genre.append(SiteUtil.av_genre[tmp])
                    elif tmp in SiteUtil.av_genre_ignore_ja:
                        continue
                    else:
                        genre_tmp = SiteUtil.trans(tmp, do_trans=do_trans).replace(" ", "")
                        if genre_tmp not in SiteUtil.av_genre_ignore_ko:
                            entity.genre.append(genre_tmp)
            elif key == "品番：":
                # 24id
                match = cls.PTN_ID.search(value)
                id_before = None
                if match:
                    id_before = match.group(0)
                    value = value.lower().replace(id_before, "zzid")

                match = cls.PTN_SEARCH_REAL_NO.match(value)
                if match:
                    label = match.group("real").upper()
                    if id_before is not None:
                        label = label.replace("ZZID", id_before.upper())

                    value = label + "-" + str(int(match.group("no"))).zfill(3)
                    if entity.tag is None:
                        entity.tag = []
                    entity.tag.append(label)
                entity.title = entity.originaltitle = entity.sorttitle = value

        if entity.premiered is None and tmp_premiered is not None:
            entity.premiered = tmp_premiered
            entity.year = int(tmp_premiered[:4])

        try:
            tag = tree.xpath(f"{basetag}/table//tr[13]/td[2]/img")
            if tag:
                match = cls.PTN_RATING.search(tag[0].attrib["src"])
                if match:
                    tmp = match.group("rating").replace("_", ".")
                    entity.ratings = [EntityRatings(float(tmp), max=5, name="dmm", image_url=tag[0].attrib["src"])]
        except Exception:
            logger.exception("평점 정보 처리 중 예외:")

        tmp = tree.xpath(f"{basetag}/div[4]/text()")[0]
        tmp = tmp.split("※")[0].strip()
        entity.plot = SiteUtil.trans(tmp, do_trans=do_trans)

        try:
            tmp = tree.xpath('//div[@class="d-review__points"]/p/strong')
            if len(tmp) == 2 and entity.ratings:
                point = float(tmp[0].text_content().replace("点", "").strip())
                votes = int(tmp[1].text_content().strip())
                entity.ratings[0].value = point
                entity.ratings[0].votes = votes
        except Exception:
            logger.exception("평점 정보 업데이트 중 예외:")

        entity.extras = []
        if use_extras:
            try:
                for tmp in tree.xpath('//*[@id="detail-sample-movie"]/div/a/@onclick'):
                    url = cls.site_base_url + tmp.split("'")[1]
                    url = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.dmm_headers).xpath("//iframe/@src")[0]
                    text = SiteUtil.get_text(url, proxy_url=proxy_url, headers=cls.dmm_headers)
                    pos = text.find("const args = {")
                    data = json.loads(text[text.find("{", pos) : text.find(";", pos)])
                    # logger.debug(json.dumps(data, indent=4))
                    data["bitrates"] = sorted(data["bitrates"], key=lambda k: k["bitrate"], reverse=True)
                    entity.extras = [
                        EntityExtra(
                            "trailer",
                            SiteUtil.trans(data["title"], do_trans=do_trans),
                            "mp4",
                            "https:" + data["bitrates"][0]["src"],
                        )
                    ]
            except Exception:
                logger.exception("미리보기 처리 중 예외:")

        return entity

    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success"
            ret["data"] = entity.as_dict()
        return ret
