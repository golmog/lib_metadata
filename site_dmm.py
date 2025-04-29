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

        search_headers = cls._get_request_headers()
        try:
            tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=search_headers)
        except Exception as e:
            logger.exception(f"Failed to get tree for search URL: {url}")
            return []

        if tree is None:
            logger.warning(f"Failed to get tree for URL: {url}")
            return []

        lists = tree.xpath('//ul[@id="list"]/li')
        logger.debug("dmm search len lists2 :%s", len(lists))

        score = 60
        ret = []
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)

                link_tag_xpath = './/p[@class="tmb"]/a'
                link_tags = node.xpath(link_tag_xpath)
                if not link_tags:
                    logger.warning(f"Could not find link tag with XPath: {link_tag_xpath}")
                    continue # 링크 없으면 다음 아이템
                link_tag = link_tags[0]
                href = link_tag.attrib["href"].lower()

                # CID 추출
                match = cls.PTN_SEARCH_CID.search(href)
                if match:
                    item.code = cls.module_char + cls.site_char + match.group("code")
                else:
                    logger.warning(f"Could not extract CID from href: {href}")
                    continue # CID 없으면 다음 아이템으로

                # 중복 제거
                already_exist = False
                for exist_item in ret:
                    if exist_item["code"] == item.code:
                        already_exist = True
                        break
                if already_exist:
                    continue

                # --- 이미지, 제목 XPath ---
                img_tag_xpath = './/p[@class="tmb"]/a/span[@class="img"]/img'
                img_tags = node.xpath(img_tag_xpath)
                if img_tags:
                    img_tag = img_tags[0]
                    item.title = item.title_ko = img_tag.attrib.get("alt", "").strip()
                    item.image_url = img_tag.attrib.get("src")
                    if item.image_url and not item.image_url.startswith("http"):
                        item.image_url = "https:" + item.image_url
                else:
                    # 기본값 설정
                    item.title = item.title_ko = "제목 정보 없음"
                    item.image_url = None

                # 이미지 처리
                if manual and item.image_url: # 이미지가 있을 때만 처리
                    _image_mode = "1" if image_mode != "0" else image_mode
                    item.image_url = SiteUtil.process_image_mode(_image_mode, item.image_url, proxy_url=proxy_url)

                # 제목 번역
                if do_trans:
                    if manual:
                        item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                    elif item.title:
                        item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)
                else:
                    item.title_ko = item.title

                # UI 코드 추출 및 점수 계산
                match_real_no = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                if match_real_no:
                    item.ui_code = match_real_no.group("real") + match_real_no.group("no")
                else:
                    item.ui_code = item.code[2:]

                # 점수 계산 로직
                if len(keyword_tmps) == 2:
                    if item.ui_code == dmm_keyword: item.score = 100
                    elif item.ui_code.replace("0", "") == dmm_keyword.replace("0", ""): item.score = 100
                    elif dmm_keyword in item.ui_code: item.score = score; score -= 5
                    elif keyword_tmps[0] in item.code and keyword_tmps[1] in item.code: item.score = score; score -= 5
                    elif keyword_tmps[0] in item.code or keyword_tmps[1] in item.code: item.score = 60
                    else: item.score = 20
                else:
                    # keyword_tmps[0]이 dmm_keyword 와 동일할 것임
                    if item.ui_code == dmm_keyword: item.score = 100 # 품번 완전 일치 시 100점
                    elif dmm_keyword in item.ui_code: item.score = score; score -= 5
                    else: item.score = 20

                # UI 코드 포맷팅
                if match_real_no:
                    item.ui_code = match_real_no.group("real").upper() + "-" + str(int(match_real_no.group("no"))).zfill(3)
                else:
                    if "0000" in item.ui_code: item.ui_code = item.ui_code.replace("0000", "-00").upper()
                    else: item.ui_code = item.ui_code.replace("00", "-").upper()
                    if item.ui_code.endswith("-"): item.ui_code = item.ui_code[:-1] + "00"

                logger.debug(f"Item found - Score: {item.score}, Code: {item.code}, UI Code: {item.ui_code}, Title: {item.title}, ps_url: {item.image_url}")
                ret.append(item) # 딕셔너리 대신 객체 추가

            except Exception as e:
                logger.exception(f"Error processing individual search result item: {e}")

        # 재시도 로직
        if not ret and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6)
            logger.debug(f"No results found for {dmm_keyword}, retrying with {new_title}")
            # manual 인자 전달 확인
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)

        # --- 점수 순 정렬 (객체 속성 접근 방식으로 수정) ---
        return sorted(ret, key=lambda k: k.score, reverse=True)

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        data_list = [] # 최종 반환될 딕셔너리 리스트
        try:
            # __search는 이제 EntityAVSearch 객체 리스트 반환
            search_results_obj = cls.__search(keyword, **kwargs)
            # 객체를 딕셔너리로 변환
            for item_obj in search_results_obj:
                data_list.append(item_obj.as_dict())
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data_list else "no_match"
            ret["data"] = data_list # 딕셔너리 리스트 반환
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
        ps_url=None,
        do_trans=True,
        proxy_url=None,
        image_mode="0",
        max_arts=10,
        use_extras=True,
        ps_to_poster=False, # 이 옵션은 새 구조에서 의미가 달라질 수 있음
        crop_mode=None,
    ):
        url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={code[2:]}/"
        logger.debug(f"Using info URL: {url}")

        info_headers = cls._get_request_headers()
        try:
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

        # 이미지 관련 시작
        img_urls = {}
        # pl 추출 (상세 페이지)
        pl_xpath = '//div[@id="fn-sampleImage-imagebox"]/img/@src'
        pl_tags = tree.xpath(pl_xpath)
        img_urls['pl'] = ("https:" + pl_tags[0]) if pl_tags and not pl_tags[0].startswith("http") else (pl_tags[0] if pl_tags else "")
        if not img_urls['pl']: logger.warning("고화질 메인 이미지(pl) URL을 얻을 수 없음.")

        # ps 추출 (search 결과에서 전달받음)
        img_urls['ps'] = ps_url if ps_url else "" # 전달받은 ps_url 사용
        if not img_urls['ps']:
            logger.warning("저화질 썸네일 이미지(ps) URL을 전달받지 못했거나 유효하지 않음.")
            # ps가 없으면 pl을 fallback으로 사용 (오류 방지)
            if img_urls.get('pl'):
                img_urls['ps'] = img_urls['pl']
            else:
                logger.error("Both pl and ps URLs are missing.")

        # 팬아트 (첫 번째 샘플 제외)
        arts_xpath = '//li[contains(@class, "fn-sampleImage__zoom") and not(@data-slick-index="0")]//img'
        arts_tags = tree.xpath(arts_xpath)
        img_urls['arts'] = []
        for tag in arts_tags:
            src = tag.attrib.get("src") or tag.attrib.get("data-lazy")
            if src:
                if not src.startswith("http"): src = "https:" + src
                img_urls['arts'].append(src)
        logger.debug(f"Found {len(img_urls['arts'])} arts images.")

        # --- SiteUtil.resolve_jav_imgs 호출 전 ps 유효성 검사 ---
        if not img_urls.get('ps'):
            logger.warning("ps URL is empty or None before calling resolve_jav_imgs. Using pl as fallback for ps if available.")
            # ps가 없으면 이미지 비교 로직이 제대로 동작하지 않을 수 있음.
            # pl 이라도 ps 값으로 넣어주거나, resolve_jav_imgs 호출 로직을 조정해야 할 수 있음.
            # 가장 간단한 방법은 ps가 없으면 pl을 ps로도 사용하도록 하는 것:
            if img_urls.get('pl'):
                img_urls['ps'] = img_urls['pl']
            else:
                # pl, ps 둘 다 없으면 이미지 처리 어려움
                logger.error("Both pl and ps URLs are missing. Image processing might fail.")
                # 이후 로직에서 오류 발생 가능성 높음

        # SiteUtil.resolve_jav_imgs 호출 (ps_to_poster=False 유지)
        SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)

        # resolve_jav_imgs 결과 사용
        entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)

        entity.fanart = []
        # resolve_jav_imgs에서 landscape로 분류되지 않은 arts 이미지를 팬아트로 사용
        resolved_arts = img_urls.get("arts", [])
        for href in resolved_arts[:max_arts]:
            # landscape로 사용된 이미지는 팬아트에서 제외 (선택적)
            if href != img_urls.get("landscape"):
                entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))

        # --- Tagline / Title 처리 ---
        title_xpath = '//h1[@id="title"]'
        title_tags = tree.xpath(title_xpath)
        if title_tags:
            # h1 태그 내부의 모든 텍스트 노드를 합침 (span 등 내부 태그 제외 가능성 고려)
            # 먼저 span.txt_before-sale 제외 시도
            title_text_nodes = title_tags[0].xpath('./text()')
            title_text = "".join(title_text_nodes).strip()

            # 만약 위 방식이 잘 안되면, h1 전체 텍스트에서 span 내용 제거
            if not title_text:
                h1_full_text = title_tags[0].text_content().strip()
                span_text_nodes = title_tags[0].xpath('./span[contains(@class, "txt_before-sale")]/text()')
                span_text = "".join(span_text_nodes).strip()
                if span_text:
                    title_text = h1_full_text.replace(span_text, "").strip()
                else:
                    title_text = h1_full_text

            if title_text:
                # Tagline 설정 (번역 및 불필요 문자 제거)
                entity.tagline = SiteUtil.trans(title_text, do_trans=do_trans).replace("[배달 전용]", "").replace("[특가]", "").strip()
                logger.debug(f"Tagline set from h1 title: {entity.tagline}")
            else:
                logger.warning("Could not extract text from h1#title.")
                entity.tagline = None # 추출 실패 시 None
        else:
            logger.warning("h1#title tag not found.")
            entity.tagline = None # 태그 없음


        # 정보 테이블 파싱
        info_table_xpath = '//div[@class="wrapper-product"]//table//tr'
        tags = tree.xpath(info_table_xpath)

        # 날짜 정보를 임시 저장할 변수 초기화
        premiered_shouhin = None # 商品発売日 (우선순위 1)
        premiered_hatsubai = None # 発売日 (우선순위 2)
        premiered_haishin = None # 配信開始日 (우선순위 3)

        for tag in tags:
            td_tags = tag.xpath(".//td")
            if len(td_tags) != 2:
                # 평점, 관련 태그 등의 다른 구조 처리 가능성
                # 예: 평점 처리
                if td_tags and "平均評価：" in td_tags[0].text_content():
                    rating_img_xpath = './/img/@src'
                    rating_img_tags = td_tags[1].xpath(rating_img_xpath)
                    if rating_img_tags:
                        match_rating = cls.PTN_RATING.search(rating_img_tags[0])
                        if match_rating:
                            rating_value_str = match_rating.group("rating").replace("_", ".")
                            try:
                                rating_value = float(rating_value_str)
                                entity.ratings = [EntityRatings(rating_value, max=5, name="dmm", image_url="https:" + rating_img_tags[0])]
                                logger.debug(f"Rating found: {rating_value}")
                            except ValueError:
                                logger.warning(f"Could not convert rating value to float: {rating_value_str}")
                        else:
                            logger.warning(f"Could not parse rating from image src: {rating_img_tags[0]}")
                continue # key-value 쌍이 아니면 건너뜀

            key = td_tags[0].text_content().strip()
            value_node = td_tags[1] # 값은 text() 또는 하위 태그 포함 가능

            # 빈 값 처리 (예: "----")
            value_text_all = value_node.text_content().strip()
            if value_text_all == "----" or not value_text_all:
                continue

            # 임시 변수에 날짜 정보 저장
            if key == "商品発売日：":
                premiered_shouhin = value_text_all.replace("/", "-")
                logger.debug(f"Found 商品発売日: {premiered_shouhin}")
            elif key == "発売日：":
                premiered_hatsubai = value_text_all.replace("/", "-")
                logger.debug(f"Found 発売日: {premiered_hatsubai}")
            elif key == "配信開始日：":
                premiered_haishin = value_text_all.replace("/", "-")
                logger.debug(f"Found 配信開始日: {premiered_haishin}")

            elif key == "収録時間：":
                match_runtime = re.search(r"(\d+)", value_text_all)
                if match_runtime:
                    entity.runtime = int(match_runtime.group(1))
                    logger.debug(f"Runtime: {entity.runtime}")
            elif key == "出演者：":
                entity.actor = []
                a_tags = value_node.xpath(".//a")
                for a_tag in a_tags:
                    actor_name = a_tag.text_content().strip()
                    if actor_name and actor_name != "▼すべて表示する": # 확인 필요
                        entity.actor.append(EntityActor(actor_name))
                logger.debug(f"Actors: {[a.originalname for a in entity.actor]}")
            elif key == "監督：":
                # 감독은 링크가 있을 수도, 없을 수도 있음
                a_tags = value_node.xpath(".//a")
                if a_tags:
                    entity.director = a_tags[0].text_content().strip()
                else:
                    entity.director = value_text_all
                logger.debug(f"Director: {entity.director}")
            elif key == "シリーズ：":
                if entity.tag is None: entity.tag = []
                # 시리즈는 링크가 있을 수도, 없을 수도 있음
                a_tags = value_node.xpath(".//a")
                series_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                if series_name:
                    entity.tag.append(SiteUtil.trans(series_name, do_trans=do_trans))
                logger.debug(f"Series tags: {entity.tag}")
            elif key == "メーカー：": # Studio 후보 1
                if entity.studio is None: # Label이 없으면 Maker를 사용
                    a_tags = value_node.xpath(".//a")
                    studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                    entity.studio = SiteUtil.trans(studio_name, do_trans=do_trans) # 번역 적용 고려
                    logger.debug(f"Studio (from Maker): {entity.studio}")
            elif key == "レーベル：": # Studio 후보 2 (우선)
                a_tags = value_node.xpath(".//a")
                studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                # 기존 번역 로직 적용
                if do_trans:
                    if studio_name in SiteUtil.av_studio:
                        entity.studio = SiteUtil.av_studio[studio_name]
                    else:
                        # SiteUtil.change_html 은 필요 없어 보임
                        entity.studio = SiteUtil.trans(studio_name)
                else:
                    entity.studio = studio_name
                logger.debug(f"Studio (from Label): {entity.studio}")

            elif key == "ジャンル：":
                entity.genre = []
                a_tags = value_node.xpath(".//a")
                for tag_a in a_tags:
                    genre_ja = tag_a.text_content().strip()
                    if "％OFF" in genre_ja or not genre_ja: # 할인 태그 등 제외
                        continue
                    # 기존 장르 처리 로직 적용
                    if genre_ja in SiteUtil.av_genre:
                        entity.genre.append(SiteUtil.av_genre[genre_ja])
                    elif genre_ja in SiteUtil.av_genre_ignore_ja:
                        continue
                    else:
                        genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                        if genre_ko not in SiteUtil.av_genre_ignore_ko:
                            entity.genre.append(genre_ko)
                logger.debug(f"Genres: {entity.genre}")
            elif key == "品番：":
                # 기존 품번 처리 로직 적용
                value = value_text_all # td의 전체 텍스트 사용
                match_id = cls.PTN_ID.search(value)
                id_before = None
                if match_id:
                    id_before = match_id.group(0)
                    value = value.lower().replace(id_before, "zzid")

                match_real = cls.PTN_SEARCH_REAL_NO.match(value)
                if match_real:
                    label = match_real.group("real").upper()
                    if id_before is not None:
                        label = label.replace("ZZID", id_before.upper())
                    formatted_title = label + "-" + str(int(match_real.group("no"))).zfill(3)
                    if entity.tag is None: entity.tag = []
                    entity.tag.append(label) # 품번 앞부분을 태그로 추가
                else:
                    # 매칭 실패 시 원본 값 사용 또는 다른 처리
                    formatted_title = value_text_all.upper()

                entity.title = entity.originaltitle = entity.sorttitle = formatted_title
                logger.debug(f"Title (from 品番): {entity.title}")
                # Tagline이 비어있으면 제목으로 채우기 (선택적)
                if entity.tagline is None:
                    entity.tagline = entity.title

        # --- 루프 종료 후 우선순위에 따라 최종 날짜 설정 ---
        final_premiered = None
        if premiered_shouhin: # 1순위: 상품 발매일
            final_premiered = premiered_shouhin
            logger.debug("Using 商品発売日 for premiered date.")
        elif premiered_hatsubai: # 2순위: 발매일
            final_premiered = premiered_hatsubai
            logger.debug("Using 発売日 for premiered date.")
        elif premiered_haishin: # 3순위: 전송 시작일
            final_premiered = premiered_haishin
            logger.debug("Using 配信開始日 for premiered date.")
        else:
            logger.warning("No premiered date found (商品発売日, 発売日, 配信開始日).")

        if final_premiered:
            entity.premiered = final_premiered
            try:
                entity.year = int(final_premiered[:4])
            except ValueError:
                logger.warning(f"Could not parse year from final premiered date: {final_premiered}")
                entity.year = None # 파싱 실패 시 None 설정
        else:
            # 날짜 정보가 전혀 없으면 None 유지 (EntityMovie 기본값)
            entity.premiered = None
            entity.year = None

        # 줄거리 파싱
        plot_xpath = '//div[@class="mg-b20 lh4"]/p[@class="mg-b20"]/text()'
        plot_tags = tree.xpath(plot_xpath)
        if plot_tags:
            # 여러 줄일 수 있으므로 join 후 처리
            plot_text = "\n".join([p.strip() for p in plot_tags if p.strip()])
            # ※ 이후 내용 제거 등 기존 처리 유지 가능
            plot_text = plot_text.split("※")[0].strip()
            entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
            logger.debug(f"Plot found: {entity.plot[:50]}...") # 일부만 로그 출력
        else:
            logger.warning("Plot not found.")


        # --- 리뷰 섹션에서 평점 상세 정보 업데이트 ---
        review_section_xpath = '//div[@id="review_anchor"]'
        review_sections = tree.xpath(review_section_xpath)
        if review_sections:
            review_section = review_sections[0]
            try:
                # 평균 평점 값
                avg_rating_xpath = './/div[@class="dcd-review__points"]/p[@class="dcd-review__average"]/strong/text()'
                avg_rating_tags = review_section.xpath(avg_rating_xpath)
                if avg_rating_tags:
                    avg_rating_str = avg_rating_tags[0].strip()
                    try:
                        avg_rating_value = float(avg_rating_str)
                        # 기존 entity.ratings가 있으면 업데이트, 없으면 새로 생성
                        if entity.ratings:
                            entity.ratings[0].value = avg_rating_value
                        else:
                            # 평점 이미지를 못 찾았을 경우 대비
                            entity.ratings = [EntityRatings(avg_rating_value, max=5, name="dmm")]
                        logger.debug(f"Updated rating value from review section: {avg_rating_value}")
                    except ValueError:
                        logger.warning(f"Could not convert average rating to float: {avg_rating_str}")

                # 총 평가 수 (Votes)
                votes_xpath = './/div[@class="dcd-review__points"]/p[@class="dcd-review__evaluates"]/strong/text()'
                votes_tags = review_section.xpath(votes_xpath)
                if votes_tags:
                    votes_str = votes_tags[0].strip()
                    match_votes = re.search(r"(\d+)", votes_str) # 숫자만 추출
                    if match_votes:
                        try:
                            votes_value = int(match_votes.group(1))
                            if entity.ratings:
                                entity.ratings[0].votes = votes_value
                                logger.debug(f"Updated rating votes: {votes_value}")
                            # else: # ratings 객체가 없으면 votes만 설정할 수 없음
                        except ValueError:
                            logger.warning(f"Could not convert votes to int: {votes_str}")

            except Exception as rating_update_e:
                logger.exception(f"Error updating rating details from review section: {rating_update_e}")
        else:
            logger.warning("Review section not found, cannot update rating details.")


        # 예고편 (Extras) 처리
        entity.extras = []
        if use_extras:
            try:
                # 방법 1: onclick 속성 파싱 (JSON 유사 구조 분석)
                onclick_xpath = '//a[@id="sample-video1"]/@onclick'
                onclick_tags = tree.xpath(onclick_xpath)
                if onclick_tags:
                    onclick_text = onclick_tags[0]
                    # gaEventVideoStart('{"video_url":"..."}','{...}') 형태 분석
                    match_json = re.search(r"gaEventVideoStart\('(\{.*?\})','(\{.*?\})'\)", onclick_text)
                    if match_json:
                        video_data_str = match_json.group(1)
                        # JSON 디코딩 시 이스케이프 문자 처리 주의
                        try:
                            video_data = json.loads(video_data_str.replace('\\"', '"')) # \" 를 " 로 치환
                            if video_data.get("video_url"):
                                trailer_url = video_data["video_url"]
                                # 트레일러 제목은 별도로 없으므로 기본값 사용
                                trailer_title = f"{entity.title} Trailer" if entity.title else "Trailer"
                                entity.extras.append(EntityExtra("trailer", SiteUtil.trans(trailer_title, do_trans=do_trans), "mp4", trailer_url))
                                logger.debug(f"Trailer found from onclick: {trailer_url}")
                        except json.JSONDecodeError as je:
                            logger.warning(f"Failed to decode JSON from onclick: {video_data_str} - Error: {je}")
                        except KeyError as ke:
                            logger.warning(f"Key 'video_url' not found in onclick JSON: {video_data_str} - Error: {ke}")

                # 방법 2: data-video-url AJAX 요청 (기존 코드 방식 변형) - 방법 1 실패 시 또는 병행
                # 이 방식은 AJAX 응답 구조를 알아야 함
                if not entity.extras: # 방법 1 실패 시 시도
                    ajax_url_xpath = '//a[@id="sample-video1"]/@data-video-url'
                    ajax_url_tags = tree.xpath(ajax_url_xpath)
                    if ajax_url_tags:
                        ajax_relative_url = ajax_url_tags[0]
                        ajax_full_url = py_urllib_parse.urljoin(url, ajax_relative_url) # 절대 경로로
                        logger.debug(f"Attempting trailer AJAX request: {ajax_full_url}")
                        try:
                            # AJAX 요청 헤더 설정 (X-Requested-With 등 필요할 수 있음)
                            ajax_headers = cls._get_request_headers(referer=url)
                            ajax_headers['X-Requested-With'] = 'XMLHttpRequest' # AJAX 요청 표시

                            # SiteUtil.get_text 또는 get_response 사용
                            ajax_response_text = SiteUtil.get_text(ajax_full_url, proxy_url=proxy_url, headers=ajax_headers)
                            # ajax_response_text = SiteUtil.get_response(ajax_full_url, proxy_url=proxy_url, headers=ajax_headers).text

                            # AJAX 응답 파싱 (iframe URL 추출 등)
                            # 예시: iframe src 추출 (응답이 HTML iframe 태그일 경우)
                            ajax_tree = html.fromstring(ajax_response_text)
                            iframe_src_xpath = "//iframe/@src"
                            iframe_srcs = ajax_tree.xpath(iframe_src_xpath)
                            if iframe_srcs:
                                iframe_url = iframe_srcs[0]
                                # iframe 내용 가져오기
                                iframe_headers = cls._get_request_headers(referer=ajax_full_url)
                                iframe_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=iframe_headers)
                                # iframe 내용에서 const args = {...} 파싱 (기존 로직 활용)
                                pos = iframe_text.find("const args = {")
                                if pos != -1:
                                    json_start = iframe_text.find("{", pos)
                                    json_end = iframe_text.find("};", json_start) # }; 로 끝나는지 확인
                                    if json_start != -1 and json_end != -1:
                                        data_str = iframe_text[json_start : json_end+1]
                                        try:
                                            data = json.loads(data_str)
                                            data["bitrates"] = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                            if data.get("bitrates"):
                                                trailer_src = data["bitrates"][0].get("src")
                                                if trailer_src:
                                                    trailer_url = "https:" + trailer_src if not trailer_src.startswith("http") else trailer_src
                                                    trailer_title = data.get("title", f"{entity.title} Trailer" if entity.title else "Trailer")
                                                    entity.extras.append(EntityExtra("trailer", SiteUtil.trans(trailer_title, do_trans=do_trans), "mp4", trailer_url))
                                                    logger.debug(f"Trailer found from AJAX iframe: {trailer_url}")
                                        except json.JSONDecodeError as je:
                                            logger.warning(f"Failed to decode JSON from iframe: {data_str} - Error: {je}")
                                else:
                                    logger.warning("Could not find 'const args = {' in iframe content.")
                            else:
                                logger.warning("Could not find iframe src in AJAX response.")
                        except Exception as ajax_e:
                            logger.exception(f"Error during trailer AJAX request: {ajax_e}")

            except Exception as extra_e:
                logger.exception(f"미리보기 처리 중 예외: {extra_e}")

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
