# site_dmm.py (전체 코드, __info 에 HTML 로깅 추가)

# -*- coding: utf-8 -*-
import json
import re
import requests # SiteUtil 내부 의존성
import urllib.parse as py_urllib_parse
from lxml import html, etree # 파싱 및 HTML 출력용
import os # 파일 저장용 (선택적)

# lib_metadata 패키지 내 다른 모듈 import
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

class SiteDmm:
    site_name = "dmm"
    site_base_url = "https://www.dmm.co.jp"
    # 리다이렉션되는 최종 성인 콘텐츠 URL (클래스 변수로 선언)
    fanza_av_url = "https://video.dmm.co.jp/av/"
    # "예" 클릭 시 GET 요청 URL 템플릿 (클래스 변수로 선언, 실제 URL 확인 필요)
    age_check_confirm_url_template = "https://www.dmm.co.jp/age_check/set?r={redirect_url}" # 예시 값

    module_char = "C"
    site_char = "D"

    # --- DMM 전용 기본 헤더 ---
    dmm_base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36", # 예시 최신 UA
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="110", "Not A(Brand";v="24", "Google Chrome";v="110"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin", # 초기값, 요청 시 변경될 수 있음
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": site_base_url + "/", # 기본 Referer
    }

    # --- 정규 표현식 ---
    PTN_SEARCH_CID = re.compile(r"\/cid=(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^(h_)?\d*(?P<real>[a-zA-Z]+)(?P<no>\d+)([a-zA-Z]+)?$")
    PTN_ID = re.compile(r"\d{2}id", re.I)
    # 평점 이미지 URL에서 숫자 추출 (예: /digital/videoa/-/img/rank/45.gif -> 45)
    PTN_RATING = re.compile(r"/(?P<rating>\d{1,2})\.gif") # 경로 마지막 숫자 추출

    # --- 상태 관리 변수 (원본 유지) ---
    age_verified = False
    last_proxy_used = None
    _ps_url_cache = {} # 검색 시 ps_url 임시 저장용

    # --- _get_request_headers: 원본 유지 ---
    @classmethod
    def _get_request_headers(cls, referer=None):
        """요청에 사용할 헤더를 생성합니다."""
        headers = cls.dmm_base_headers.copy()
        if referer:
            headers['Referer'] = referer
        # 필요에 따라 다른 헤더 동적 설정 가능
        return headers

    # --- _ensure_age_verified: 원본 유지 ---
    @classmethod
    def _ensure_age_verified(cls, proxy_url=None):
        """SiteUtil.session에 DMM 연령 확인 쿠키가 있는지 확인하고, 없으면 설정 시도."""
        if not cls.age_verified or cls.last_proxy_used != proxy_url:
            logger.debug("Checking/Performing DMM age verification...")
            cls.last_proxy_used = proxy_url

            # SiteUtil.session은 lib_metadata의 공유 세션 객체로 가정
            session_cookies = SiteUtil.session.cookies
            # 쿠키 값 확인 시 문자열 '1'과 비교
            if 'age_check_done' in session_cookies and session_cookies.get('age_check_done', domain='.dmm.co.jp') == '1':
                logger.debug("Age verification cookie already present in SiteUtil.session.")
                cls.age_verified = True
                return True
            # .dmm.com 도메인도 체크 (필요시)
            if 'age_check_done' in session_cookies and session_cookies.get('age_check_done', domain='.dmm.com') == '1':
                logger.debug("Age verification cookie (dmm.com) already present in SiteUtil.session.")
                cls.age_verified = True
                return True

            logger.debug("Attempting DMM age verification process by directly sending confirmation GET.")
            try:
                # 리다이렉트 될 기본 URL 설정 (어디로 가든 크게 중요하지 않을 수 있음)
                target_rurl = cls.fanza_av_url
                # 연령 확인 설정 URL 생성
                confirm_path = f"/age_check/=/declared=yes/?rurl={py_urllib_parse.quote(target_rurl, safe='')}"
                age_check_confirm_url = py_urllib_parse.urljoin(cls.site_base_url, confirm_path)
                logger.debug(f"Constructed age confirmation URL: {age_check_confirm_url}")

                # 확인 요청 헤더 (Referer는 메인 페이지)
                confirm_headers = cls._get_request_headers(referer=cls.site_base_url + "/")

                # SiteUtil.get_response 사용하여 요청 (SiteUtil이 세션 쿠키 관리 가정)
                confirm_response = SiteUtil.get_response(
                    age_check_confirm_url,
                    method='GET',
                    proxy_url=proxy_url,
                    headers=confirm_headers,
                    allow_redirects=False # 리다이렉트 응답 자체를 확인하기 위해 False
                )
                logger.debug(f"Confirmation GET response status code: {confirm_response.status_code}")
                # 응답 후 세션 쿠키 로깅 (SiteUtil.session 사용)
                logger.debug(f"Cookies *after* confirmation GET in SiteUtil.session: {[(c.name, c.value, c.domain) for c in SiteUtil.session.cookies]}")

                # 302 리다이렉트 및 Set-Cookie 헤더 확인
                if confirm_response.status_code == 302 and 'Location' in confirm_response.headers:
                    set_cookie_header = confirm_response.headers.get('Set-Cookie', '')
                    # Set-Cookie 헤더에서 age_check_done=1 찾기 (도메인 등 상세 조건 무시하고 일단 찾기)
                    if 'age_check_done=1' in set_cookie_header:
                        logger.debug("Age confirmation successful. 'age_check_done=1' found in Set-Cookie header.")
                        # SiteUtil.session에 쿠키가 실제 반영되었는지 재확인
                        final_cookies = SiteUtil.session.cookies
                        if ('age_check_done' in final_cookies and final_cookies.get('age_check_done') == '1') or \
                           ('age_check_done' in final_cookies.get_dict(domain='.dmm.co.jp') and final_cookies.get_dict(domain='.dmm.co.jp')['age_check_done'] == '1') or \
                           ('age_check_done' in final_cookies.get_dict(domain='.dmm.com') and final_cookies.get_dict(domain='.dmm.com')['age_check_done'] == '1'):
                            logger.debug("age_check_done=1 cookie confirmed in SiteUtil.session.")
                            cls.age_verified = True
                            return True
                        else:
                            logger.warning("Set-Cookie received, but age_check_done cookie not updated correctly in SiteUtil.session. Trying manual set...")
                            # 수동 설정 시도 (최후의 수단)
                            try:
                                SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.co.jp", path="/")
                                SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.com", path="/")
                                logger.info("Manually set age_check_done cookie in SiteUtil.session.")
                                cls.age_verified = True; return True
                            except Exception as e_set:
                                logger.error(f"Failed to manually set cookie: {e_set}")
                                cls.age_verified = False; return False
                    else:
                        logger.warning("Age confirmation redirected, but 'age_check_done=1' not found in Set-Cookie header.")
                        cls.age_verified = False; return False
                else:
                    logger.warning(f"Age confirmation GET request did not return expected 302 redirect. Status: {confirm_response.status_code}")
                    cls.age_verified = False; return False
            except Exception as e:
                logger.exception(f"Failed during DMM age verification process: {e}")
                cls.age_verified = False; return False
        else:
            logger.debug("Age verification already done.")
            return True # 이미 확인됨

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        if not cls._ensure_age_verified(proxy_url=proxy_url):
            logger.error("Age verification failed, cannot perform search.")
            return []

        # 키워드 처리
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd": keyword = keyword[:-3]
        keyword = keyword.replace("-", " ")
        keyword_tmps = keyword.split(" ")
        if len(keyword_tmps) == 2: dmm_keyword = keyword_tmps[0] + keyword_tmps[1].zfill(5)
        else: dmm_keyword = keyword
        logger.debug("keyword [%s] -> [%s]", keyword, dmm_keyword)

        # 검색 URL (카테고리 미지정, 최신 /search 경로 시도)
        search_params = { 'redirect': '1', 'enc': 'UTF-8', 'category': '', 'searchstr': dmm_keyword }
        search_url = f"{cls.site_base_url}/search/?{py_urllib_parse.urlencode(search_params)}"
        # 또는 특정 카테고리 URL 사용
        # search_url = f"{cls.site_base_url}/digital/videoa/-/list/search/=/?searchstr={dmm_keyword}"
        logger.info(f"Using search URL: {search_url}")

        # 헤더 준비 (Referer는 연령 확인 페이지나 이전 페이지가 될 수 있음)
        search_headers = cls._get_request_headers(referer=cls.fanza_av_url) # FANZA AV 페이지를 Referer로
        tree = None
        try:
            tree = SiteUtil.get_tree(search_url, proxy_url=proxy_url, headers=search_headers)
        except Exception as e:
            logger.exception(f"Failed to get tree for search URL: {search_url}")
            return []
        if tree is None:
            logger.warning(f"Failed to get tree (returned None) for URL: {search_url}")
            return []

        # XPath 및 결과 처리: Tailwind 구조 기반
        list_xpath_desktop = '//div[contains(@class, "grid-cols-4")]//div[contains(@class, "border-r") and contains(@class, "border-b")]'
        list_xpath_mobile = '//div[contains(@class, "divide-y")]/div[contains(@class, "flex") and contains(@class, "py-1.5")]'
        lists = []
        list_type = None

        try: lists = tree.xpath(list_xpath_desktop)
        except Exception: pass
        if lists: list_type = "desktop"
        else:
            try: lists = tree.xpath(list_xpath_mobile)
            except Exception: pass
            if lists: list_type = "mobile"

        logger.debug(f"Found {len(lists)} items using {list_type} layout XPath.")

        if not lists:
            logger.warning(f"No items found using XPath for {search_url}.")
            return []

        # 개별 결과 처리 루프
        ret = []
        score = 60
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                href = None; item.image_url = None; item.title = item.title_ko = "Not Found"; original_ps_url = None

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
                    href = title_link_href if title_link_href else img_link_href
                    title_p_tag = title_link_tag[0].xpath('./p[contains(@class, "hover:text-linkHover")]')
                    if title_p_tag: item.title = item.title_ko = title_p_tag[0].text_content().strip()

                elif list_type == "mobile":
                    link_tag_img = node.xpath('.//a[div[contains(@class, "h-[180px]")]]')
                    if not link_tag_img: continue
                    img_link_href = link_tag_img[0].attrib.get("href", "").lower()
                    img_tag = link_tag_img[0].xpath('.//img/@src')
                    if not img_tag: continue
                    original_ps_url = img_tag[0]
                    title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]')
                    if not title_link_tag:
                        title_link_tag = node.xpath('.//a[div/p[contains(@class, "line-clamp-2")]]')
                        if not title_link_tag: continue
                    title_link_href = title_link_tag[0].attrib.get("href", "").lower()
                    href = title_link_href if title_link_href else img_link_href
                    title_p_tag = title_link_tag[0].xpath('.//p[contains(@class, "line-clamp-2")]')
                    if title_p_tag: item.title = item.title_ko = title_p_tag[0].text_content().strip()
                else: continue

                if not original_ps_url: continue
                if original_ps_url.startswith("//"): original_ps_url = "https:" + original_ps_url
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try: item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"Error processing image: {e_img}"); item.image_url = original_ps_url
                else: item.image_url = original_ps_url

                if not href: continue
                match_cid = cls.PTN_SEARCH_CID.search(href)
                if match_cid: item.code = cls.module_char + cls.site_char + match_cid.group("code")
                else: logger.warning(f"CID not found in href: {href}"); continue
                if any(exist_item.get("code") == item.code for exist_item in ret): continue
                if item.title == "Not Found": item.title = item.title_ko = item.code

                if item.code and original_ps_url:
                    cls._ps_url_cache[item.code] = original_ps_url
                    logger.debug(f"Stored ps_url for {item.code} in cache.")

                if not manual:
                    if do_trans and item.title:
                        try: item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)
                        except Exception as e_trans: logger.error(f"Error translating title: {e_trans}"); item.title_ko = item.title
                    else: item.title_ko = item.title
                else: item.title_ko = "(번역 안 함) " + item.title

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

                if match_real_no:
                    real_part = match_real_no.group("real").upper()
                    no_part_str = str(int(match_real_no.group("no")))
                    item.ui_code = f"{real_part}-{no_part_str}"
                else:
                    ui_code_temp = item.code[2:].upper()
                    if ui_code_temp.startswith("H_"): ui_code_temp = ui_code_temp[2:]
                    m = re.match(r"([a-zA-Z]+)(\d+.*)", ui_code_temp)
                    if m: item.ui_code = f"{m.group(1)}-{m.group(2)}"
                    else: item.ui_code = ui_code_temp

                logger.debug(f"Item found - Score: {item.score}, Code: {item.code}, UI Code: {item.ui_code}, Title: {item.title}")
                ret.append(item.as_dict())

            except Exception as e_inner:
                logger.exception(f"Error processing individual search result item: {e_inner}")

        sorted_ret = sorted(ret, key=lambda k: k.get("score", 0), reverse=True)

        if not sorted_ret and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6)
            logger.debug(f"No results found for {dmm_keyword}, retrying with {new_title}")
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)

        return sorted_ret

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            data_list = cls.__search(keyword, **kwargs)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data_list else "no_match"; ret["data"] = data_list
        return ret

    # --- __info 메소드: 상세 페이지 구조 분석 후 수정 필요 ---
    @classmethod
    def __img_urls(cls, tree):
        # 이 XPath들은 최신 상세 페이지 구조에 맞게 수정되어야 함
        logger.warning("__img_urls: XPath needs update for current detail page structure.")
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        # 예시: ps_url 은 검색 캐시 사용 또는 상세 페이지에서 찾기
        ps_tags = tree.xpath('//img[@id="package-src"]/@src') # 예시
        if ps_tags: img_urls['ps'] = ps_tags[0]

        pl_tags = tree.xpath('//a[@id="package-a"]/@href') # 예시
        if pl_tags: img_urls['pl'] = pl_tags[0]

        arts_tags = tree.xpath('//div[@id="sample-image-list"]//a/@href') # 예시
        if arts_tags: img_urls['arts'] = arts_tags

        # // 로 시작하는 URL 처리
        for key in ['ps', 'pl']:
            if img_urls.get(key) and img_urls[key].startswith("//"):
                img_urls[key] = "https:" + img_urls[key]
        img_urls['arts'] = ["https:" + url if url.startswith("//") else url for url in img_urls.get('arts', [])]

        return img_urls

    @classmethod
    def __info( cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, ps_to_poster=False, crop_mode=None):
        logger.info(f"Getting detail info for {code} (Requires XPath Update)")
        ps_url_from_cache = cls._ps_url_cache.pop(code, None)
        if ps_url_from_cache: logger.debug(f"Using cached ps_url for {code}.")
        else: logger.warning(f"ps_url for {code} not found in cache.")

        if not cls._ensure_age_verified(proxy_url=proxy_url):
            raise Exception(f"DMM age verification failed for info ({code}).")

        # 상세 페이지 URL (최신 경로 추정)
        detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={code[2:]}/"
        logger.info(f"Accessing DMM detail page (needs structure check): {detail_url}")
        info_headers = cls._get_request_headers(referer=cls.site_base_url + "/digital/videoa/")
        tree = None
        received_html_content = None

        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=info_headers)
            if tree is not None:
                try:
                    received_html_content = etree.tostring(tree, pretty_print=True, encoding='unicode', method='html')
                    logger.debug(f">>>>>> Received Detail HTML for {code} Start >>>>>>")
                    log_chunk_size = 1500
                    for i in range(0, len(received_html_content), log_chunk_size): logger.debug(received_html_content[i:i+log_chunk_size])
                    logger.debug(f"<<<<<< Received Detail HTML for {code} End <<<<<<")

                    title_tags_check = tree.xpath('//title/text()')
                    if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]:
                        logger.error(f"Age verification page received for detail page: {code}")
                        raise Exception("Received age verification page instead of detail.")
                except Exception as e_log_html: logger.error(f"Error logging detail HTML: {e_log_html}")
            else:
                logger.warning(f"SiteUtil.get_tree returned None for detail page: {code}")
                raise Exception("Failed to get detail page tree (None).")
        except Exception as e: logger.exception(f"Failed get/process detail tree: {e}"); raise

        # --- 이하 파싱 로직 전면 수정 필요 ---
        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        logger.warning(f"Parsing logic in __info for {code} needs COMPLETE REVISION.")

        # --- 예시: 제목/원본제목/정렬제목 설정 (이 부분은 유효) ---
        match_real_no = cls.PTN_SEARCH_REAL_NO.search(code[2:])
        if match_real_no:
            real_part = match_real_no.group("real").upper()
            no_part_str = str(int(match_real_no.group("no")))
            entity.title = entity.originaltitle = entity.sorttitle = f"{real_part}-{no_part_str}"
        else:
            ui_code_temp = code[2:].upper()
            if ui_code_temp.startswith("H_"): ui_code_temp = ui_code_temp[2:]
            m = re.match(r"([a-zA-Z]+)(\d+.*)", ui_code_temp)
            if m: entity.title = entity.originaltitle = entity.sorttitle = f"{m.group(1)}-{m.group(2)}"
            else: entity.title = entity.originaltitle = entity.sorttitle = ui_code_temp
        logger.debug(f"Set title/originaltitle/sorttitle from code: {entity.title}")

        # --- 이미지 파싱 (수정 필요) ---
        try:
            img_urls = cls.__img_urls(tree) # 수정된 XPath 필요
            img_urls['ps'] = ps_url_from_cache if ps_url_from_cache else img_urls.get('ps', "")
            logger.debug(f"Image URLs found: ps={img_urls.get('ps')}, pl={img_urls.get('pl')}, arts={len(img_urls.get('arts',[]))}")
            # SiteUtil 이미지 처리
            SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)
            entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
            entity.fanart = []
            resolved_arts = img_urls.get("arts", [])
            for href in resolved_arts[:max_arts]:
                entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))
        except Exception as e: logger.error(f"Error parsing/processing images: {e}")

        # --- 정보 테이블, 줄거리, 평점, 예고편 등 파싱 로직 추가 필요 ---
        # logger.warning("Parsing for tagline, actors, director, studio, genres, plot, rating, extras is needed.")
        # 예: entity.tagline = SiteUtil.trans(tree.xpath('//xpath/to/tagline')[0].text_content(), do_trans=do_trans)
        # 예: entity.actor = [EntityActor(a.strip()) for a in tree.xpath('//xpath/to/actors//a/text()')]
        # ... 등등 ...

        return entity # 현재는 부분적인 정보만 담긴 entity 반환

    @classmethod
    def info(cls, code, **kwargs):
        # 원본 유지
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            if entity: ret["ret"] = "success"; ret["data"] = entity.as_dict()
            else: ret["ret"] = "error"; ret["data"] = f"Failed to get info for {code}"
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        return ret
