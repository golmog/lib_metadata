# site_dmm.py (최신 검색 URL 및 파싱 로직 적용)

# -*- coding: utf-8 -*-
import json
import re
import requests # SiteUtil 내부 의존성
import urllib.parse as py_urllib_parse
from lxml import html # 파싱 위해 추가

# lib_metadata 패키지 내 다른 모듈 import
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings
from .plugin import P
from .site_util import SiteUtil
from lxml import html, etree # etree 추가 (HTML 출력용)

logger = P.logger

class SiteDmm:
    site_name = "dmm"
    site_base_url = "https://www.dmm.co.jp"
    module_char = "C"
    site_char = "D"

    # --- DMM 전용 기본 헤더 ---
    dmm_base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    # --- 정규 표현식 ---
    PTN_SEARCH_CID = re.compile(r"\/cid=(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^(h_)?\d*(?P<real>[a-zA-Z]+)(?P<no>\d+)([a-zA-Z]+)?$")
    PTN_ID = re.compile(r"\d{2}id", re.I)
    PTN_RATING = re.compile(r"(?P<rating>[\d|_]+)\.gif")

    # --- 상태 관리 변수 (원본 유지) ---
    age_verified = False
    last_proxy_used = None
    _ps_url_cache = {}

    # --- _get_request_headers: 원본 유지 ---
    @classmethod
    def _get_request_headers(cls, referer=None):
        headers = cls.dmm_base_headers.copy()
        if referer:
            headers['Referer'] = referer
        return headers

    # --- _ensure_age_verified: 원본 유지 ---
    @classmethod
    def _ensure_age_verified(cls, proxy_url=None):
        if not cls.age_verified or cls.last_proxy_used != proxy_url:
            logger.debug("Checking/Performing DMM age verification...")
            cls.last_proxy_used = proxy_url
            session_cookies = SiteUtil.session.cookies
            if 'age_check_done' in session_cookies and session_cookies.get('age_check_done') == '1':
                logger.debug("Age verification cookie already present in SiteUtil.session.")
                cls.age_verified = True; return True

            logger.debug("Attempting DMM age verification process by directly sending confirmation GET.")
            try:
                target_rurl = f"{cls.site_base_url}/digital/videoa/-/list/"
                confirm_path = f"/age_check/=/declared=yes/?rurl={py_urllib_parse.quote(target_rurl)}"
                age_check_confirm_url = py_urllib_parse.urljoin(cls.site_base_url, confirm_path)
                logger.debug(f"Constructed age confirmation URL: {age_check_confirm_url}")
                confirm_headers = cls._get_request_headers(referer=cls.site_base_url + "/")
                confirm_response = SiteUtil.get_response(
                    age_check_confirm_url, method='GET', proxy_url=proxy_url,
                    headers=confirm_headers, allow_redirects=False
                )
                logger.debug(f"Confirmation GET response status: {confirm_response.status_code}")
                logger.debug(f"Confirmation GET cookies received: {SiteUtil.session.cookies.items()}")

                if confirm_response.status_code == 302 and 'Location' in confirm_response.headers:
                    if 'age_check_done=1' in confirm_response.headers.get('Set-Cookie', ''):
                        logger.debug("Age confirmation successful via Set-Cookie.")
                        final_cookies = SiteUtil.session.cookies
                        if 'age_check_done' in final_cookies and final_cookies.get('age_check_done') == '1':
                            logger.debug("age_check_done=1 confirmed in session.")
                            cls.age_verified = True; return True
                        else: logger.warning("Set-Cookie received, but not updated in session."); cls.age_verified = False; return False
                    else: logger.warning("'age_check_done=1' not in Set-Cookie."); cls.age_verified = False; return False
                else: logger.warning(f"Expected 302 redirect not received. Status: {confirm_response.status_code}"); cls.age_verified = False; return False
            except Exception as e: logger.exception(f"Failed age verification: {e}"); cls.age_verified = False; return False
        else: return True

    # --- __search: 수정된 버전 (URL 및 파싱 로직 변경) ---
    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        # 연령 확인 선행
        if not cls._ensure_age_verified(proxy_url=proxy_url):
            logger.error("Age verification failed, cannot perform search.")
            return [] # 빈 리스트 반환

        # 키워드 처리
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd": keyword = keyword[:-3]
        keyword = keyword.replace("-", " ")
        keyword_tmps = keyword.split(" ")
        if len(keyword_tmps) == 2: dmm_keyword = keyword_tmps[0] + keyword_tmps[1].zfill(5)
        else: dmm_keyword = keyword
        logger.debug("keyword [%s] -> [%s]", keyword, dmm_keyword)

        # --- 검색 URL ---
        search_url = f"{cls.site_base_url}/digital/videoa/-/list/search/=/?searchstr={dmm_keyword}"
        # 필요시 파라미터 추가: "&sort=ranking" 등
        logger.info(f"Using NEW search URL: {search_url}")

        # SiteUtil.get_tree 사용
        search_headers = cls._get_request_headers(referer=cls.site_base_url + "/") # 적절한 Referer
        tree = None
        received_html_content = None # HTML 내용 저장 변수

        try:
            # SiteUtil.get_tree 사용
            tree = SiteUtil.get_tree(search_url, proxy_url=proxy_url, headers=search_headers)

            # --- 실제 받아온 HTML 내용 로깅/저장 ---
            if tree is not None:
                try:
                    # lxml 객체를 예쁘게 포맷된 문자열로 변환
                    received_html_content = etree.tostring(tree, pretty_print=True, encoding='unicode', method='html')

                    # 로그로 출력 (너무 길면 잘릴 수 있음)
                    logger.debug(">>>>>> Received HTML Start >>>>>>")
                    # 로그 길이를 고려하여 적절히 나누어 출력하거나 앞부분만 출력
                    log_chunk_size = 1500
                    for i in range(0, len(received_html_content), log_chunk_size):
                        logger.debug(received_html_content[i:i+log_chunk_size])
                    # logger.debug(f"Received HTML content:\n{received_html_content[:8000]}") # 앞 8000자 출력
                    logger.debug("<<<<<< Received HTML End <<<<<<")

                except Exception as e_log_html:
                    logger.error(f"Error converting or logging received HTML: {e_log_html}")
            else:
                logger.warning("SiteUtil.get_tree returned None, cannot log HTML.")
                return [] # tree가 None이면 더 이상 진행 불가

        except Exception as e:
            logger.exception(f"Failed to get tree for search URL: {search_url}")
            return []
        # tree가 None 이면 위에서 return 되었으므로 아래 코드는 실행 안 됨

        # --- XPath 수정: 데스크톱/모바일 구조 모두 시도 ---
        lists = []
        # 1. 데스크톱 grid 구조 시도
        list_xpath_desktop = '//div[contains(@class, "grid-cols-4")]//div[contains(@class, "border-r") and contains(@class, "border-b")]'
        try: lists = tree.xpath(list_xpath_desktop)
        except Exception as e_xpath: logger.error(f"XPath error (desktop): {e_xpath}")
        list_type = "desktop"

        # 2. 데스크톱 결과 없으면 모바일 flex 구조 시도
        if not lists:
            logger.debug("Desktop grid not found, trying mobile layout XPath...")
            list_xpath_mobile = '//div[contains(@class, "divide-y")]/div[contains(@class, "flex") and contains(@class, "py-1.5")]'
            try: lists = tree.xpath(list_xpath_mobile)
            except Exception as e_xpath: logger.error(f"XPath error (mobile): {e_xpath}")
            if lists: list_type = "mobile"
            else: list_type = None # 둘 다 실패

        logger.debug(f"Found {len(lists)} items using {list_type} layout XPath.")

        if not lists:
            logger.warning(f"No items found using either desktop or mobile XPath.")
            return []

        # --- 개별 결과 처리 루프 (구조별 XPath 적용) ---
        ret = []
        score = 60
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                href = None; item.image_url = None; item.title = item.title_ko = "Not Found"; original_ps_url = None

                # --- 구조(list_type)에 따라 다른 XPath 적용 ---
                if list_type == "desktop":
                    link_tag_img = node.xpath('.//a[contains(@class, "flex justify-center")]')
                    if not link_tag_img: continue
                    img_link_href = link_tag_img[0].attrib.get("href", "").lower()

                    img_tag = link_tag_img[0].xpath('./img/@src')
                    if not img_tag: continue
                    original_ps_url = img_tag[0]

                    title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]')
                    if not title_link_tag: continue
                    title_link_href = title_link_tag[0].attrib.get("href", "").lower()
                    href = title_link_href if title_link_href else img_link_href # 상세페이지 링크

                    title_p_tag = title_link_tag[0].xpath('./p[contains(@class, "hover:text-linkHover")]')
                    if title_p_tag: item.title = item.title_ko = title_p_tag[0].text_content().strip()

                elif list_type == "mobile":
                    # 모바일 구조에 맞는 XPath (위 HTML 분석 기반)
                    link_tag_img = node.xpath('.//a[div[contains(@class, "h-[180px]")]]') # 이미지를 포함하는 div를 가진 a
                    if not link_tag_img: continue
                    img_link_href = link_tag_img[0].attrib.get("href", "").lower()

                    img_tag = link_tag_img[0].xpath('.//img/@src')
                    if not img_tag: continue
                    original_ps_url = img_tag[0]

                    # 모바일 제목은 상세페이지 링크 안의 p 태그
                    title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]') # 데스크톱과 같을 수 있음
                    if not title_link_tag:
                        # 다른 구조의 제목 링크 찾기 시도 (필요 시)
                        title_link_tag = node.xpath('.//a[div/p[contains(@class, "line-clamp-2")]]') # 제목 p를 가진 div 안의 a
                        if not title_link_tag: continue # 그래도 없으면 스킵

                    title_link_href = title_link_tag[0].attrib.get("href", "").lower()
                    href = title_link_href if title_link_href else img_link_href # 상세페이지 링크

                    title_p_tag = title_link_tag[0].xpath('.//p[contains(@class, "line-clamp-2")]')
                    if title_p_tag: item.title = item.title_ko = title_p_tag[0].text_content().strip()

                else: # list_type이 None (알 수 없는 구조)
                    logger.error("Unknown list type, cannot parse item.")
                    continue

                # --- 이하 공통 처리 로직 ---
                if not original_ps_url: continue # 이미지 URL 없으면 스킵
                if original_ps_url.startswith("//"): original_ps_url = "https:" + original_ps_url

                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try: item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"Error processing image: {e_img}"); item.image_url = original_ps_url
                else:
                    item.image_url = original_ps_url

                if not href: continue # 상세 페이지 링크 없으면 스킵
                match_cid = cls.PTN_SEARCH_CID.search(href)
                if match_cid: item.code = cls.module_char + cls.site_char + match_cid.group("code")
                else: logger.warning(f"CID not found in href: {href}"); continue
                if any(exist_item.get("code") == item.code for exist_item in ret): continue
                if item.title == "Not Found": item.title = item.title_ko = item.code

                # ps_url 캐싱
                if item.code and original_ps_url:
                    cls._ps_url_cache[item.code] = original_ps_url
                    logger.debug(f"Stored ps_url for {item.code} in cache.")

                # 번역 처리
                if not manual:
                    if do_trans and item.title:
                        try: item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)
                        except Exception as e_trans: logger.error(f"Error translating title: {e_trans}"); item.title_ko = item.title
                    else: item.title_ko = item.title
                else:
                    item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title

                # 점수 계산
                match_real_no = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                if match_real_no: item_ui_code_base = match_real_no.group("real") + match_real_no.group("no")
                else: item_ui_code_base = item.code[2:]
                current_score = 0
                if len(keyword_tmps) == 2:
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif item_ui_code_base.replace("0", "") == dmm_keyword.replace("0", ""): current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    elif keyword_tmps[0] in item.code and keyword_tmps[1] in item.code: current_score = score
                    elif keyword_tmps[0] in item.code or keyword_tmps[1] in item.code: current_score = 60
                    else: current_score = 20
                else:
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    else: current_score = 20
                item.score = current_score
                if current_score < 100 and score > 20: score -= 5

                # UI 코드 형식화
                if match_real_no:
                    item.ui_code = match_real_no.group("real").upper() + "-" + str(int(match_real_no.group("no"))).zfill(5)
                else:
                    # Fallback 로직 개선 필요 가능성 있음
                    ui_code_temp = item_ui_code_base.upper()
                    if ui_code_temp.startswith("H_"): ui_code_temp = ui_code_temp[2:] # H_ 제거
                    # 하이픈 추가 로직 (더 견고하게 만들 필요 있음)
                    # 예: 문자와 숫자 경계 찾기
                    m = re.match(r"([a-zA-Z]+)(\d+.*)", ui_code_temp)
                    if m: item.ui_code = m.group(1) + "-" + m.group(2)
                    else: item.ui_code = ui_code_temp # 그대로 사용

                logger.debug(f"Item found - Score: {item.score}, Code: {item.code}, UI Code: {item.ui_code}, Title: {item.title}")
                ret.append(item.as_dict())

            except Exception as e_inner:
                logger.exception(f"Error processing individual search result item: {e_inner}")

        # 최종 정렬
        sorted_ret = sorted(ret, key=lambda k: k.get("score", 0), reverse=True)

        # 재검색 로직
        if not sorted_ret and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6)
            logger.debug(f"No results found for {dmm_keyword}, retrying with {new_title}")
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)

        return sorted_ret

    # --- search: 원본 유지 ---
    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            data_list = cls.__search(keyword, **kwargs) # 수정된 __search 호출
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data_list else "no_match"; ret["data"] = data_list
        return ret

    # --- __info 메소드: 최신 구조에 맞춰 XPath 수정 필요 ---
    @classmethod
    def __img_urls(cls, tree):
        logger.warning("__img_urls: XPath needs update for current detail page structure.")
        # 예시 XPath (수정 필요)
        ps = tree.xpath('//xpath/to/new/small_poster/@src')
        ps = ps[0] if ps else ""
        pl = tree.xpath('//xpath/to/new/large_poster/@href')
        pl = pl[0] if pl else ""
        arts = tree.xpath('//xpath/to/new/sample_images/@href')
        return {"ps": ps, "pl": pl, "arts": arts}

    @classmethod
    def __info( cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, ps_to_poster=False, crop_mode=None):
        logger.warning(f"Executing __info for {code}. DETAIL PAGE PARSING LOGIC NEEDS UPDATE.")
        ps_url = cls._ps_url_cache.pop(code, None)
        if ps_url: logger.debug(f"Retrieved ps_url for {code} from cache.")
        else: logger.warning(f"ps_url for {code} not found in cache.")

        if not cls._ensure_age_verified(proxy_url=proxy_url): raise Exception("DMM age verification failed.")

        # 상세 페이지 URL (최신 경로 사용 시도)
        url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={code[2:]}/"
        logger.debug(f"Using info URL (needs structure check): {url}")

        info_headers = cls._get_request_headers(referer=cls.site_base_url + "/digital/videoa/")
        tree = None
        try: tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=info_headers)
        except Exception as e: logger.exception(f"Failed to get tree for info URL: {url}"); raise
        if tree is None: logger.warning(f"Failed to get tree (None) for URL: {url}"); raise Exception("Failed to get tree.")

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"

        # --- 이미지 처리: __img_urls 및 이하 로직은 새 구조에 맞춰 수정 필요 ---
        img_urls = cls.__img_urls(tree) # 수정된 __img_urls 호출 필요
        img_urls['ps'] = ps_url if ps_url else img_urls.get('ps', "") # 캐시값 우선 사용
        # ... (SiteUtil 이미지 처리 호출 - 원본 로직 유지 또는 검토) ...
        try:
            SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)
            entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
            entity.fanart = []
            resolved_arts = img_urls.get("arts", [])
            for href in resolved_arts[:max_arts]:
                entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))
        except Exception as img_proc_e: logger.exception(f"Image processing error: {img_proc_e}")

        # --- 정보 테이블 파싱: 원본 로직 유지 (단, 새 구조에서는 작동 안 할 수 있음) ---
        # ... (원본 __info의 테이블 파싱 로직 복사/붙여넣기) ...
        # ... 이 부분 전체가 새로운 페이지 구조에 맞춰 재작성 필요 ...
        logger.warning(f"Parsing logic in __info for {code} is based on old structure and likely needs complete rewrite.")
        # 임시로 제목만 설정
        try: entity.title = tree.xpath('//h1[@id="title"]/text()')[0].strip() # 예전 XPath
        except: entity.title = code
        entity.originaltitle = entity.sorttitle = entity.title

        return entity # 실제로는 파싱된 entity 반환

    # --- info: 원본 유지 ---
    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            ret["ret"] = "success"; ret["data"] = entity.as_dict()
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        return ret
