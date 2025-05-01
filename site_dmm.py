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
                href = None; item.image_url = None; item.title = "Not Found"; original_ps_url = None

                if list_type == "desktop":
                    link_tag_img = node.xpath('.//a[contains(@class, "flex justify-center")]')
                    if not link_tag_img: continue
                    img_link_href = link_tag_img[0].attrib.get("href", "").lower()
                    img_tag = link_tag_img[0].xpath('./img/@src')
                    if not img_tag: continue
                    original_ps_url = img_tag[0]
                    title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]')
                    if title_link_tag:
                        title_link_tag = title_link_tag[0]
                        title_link_href = title_link_tag.attrib.get("href", "").lower()
                        href = title_link_href if title_link_href else img_link_href
                        title_p_tag = title_link_tag.xpath('./p[contains(@class, "hover:text-linkHover")]')
                        if title_p_tag: item.title = title_p_tag[0].text_content().strip()

                elif list_type == "mobile":
                    link_tag_img = node.xpath('.//a[div[contains(@class, "h-[180px]")]]')
                    if not link_tag_img: continue
                    img_link_href = link_tag_img[0].attrib.get("href", "").lower()
                    img_tag = link_tag_img[0].xpath('.//img/@src')
                    if not img_tag: continue
                    original_ps_url = img_tag[0]
                    title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]')
                    if not title_link_tag: title_link_tag = node.xpath('.//a[div/p[contains(@class, "line-clamp-2")]]')
                    if title_link_tag:
                        title_link_tag = title_link_tag[0]
                        title_link_href = title_link_tag.attrib.get("href", "").lower()
                        href = title_link_href if title_link_href else img_link_href
                        title_p_tag = title_link_tag[0].xpath('.//p[contains(@class, "line-clamp-2")]')
                        if title_p_tag: item.title = title_p_tag[0].text_content().strip()
                else: continue

                # --- 공통 처리 로직 ---
                if not original_ps_url: continue
                if original_ps_url.startswith("//"): original_ps_url = "https:" + original_ps_url

                # 이미지 URL 설정 (manual 여부 따라)
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try: item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"Error processing image: {e_img}"); item.image_url = original_ps_url
                else:
                    item.image_url = original_ps_url

                if not href: continue
                match_cid = cls.PTN_SEARCH_CID.search(href)
                if match_cid: item.code = cls.module_char + cls.site_char + match_cid.group("code")
                else: logger.warning(f"CID not found in href: {href}"); continue
                if any(exist_item.get("code") == item.code for exist_item in ret): continue

                # --- 제목 Fallback 및 title_ko 설정 수정 ---
                # XPath에서 제목을 못 찾았거나 비어있으면 코드로 대체
                if not item.title or item.title == "Not Found":
                    logger.warning(f"Title not found via XPath for {item.code}, using code as title.")
                    item.title = item.code # 원본 제목을 코드로 설정

                # title_ko 설정
                if manual:
                    # manual=True 이면 항상 지정된 형식 사용 (item.title 사용 확인)
                    item.title_ko = f"(현재 인터페이스에서는 번역을 제공하지 않습니다) {item.title}"
                else:
                    # manual=False 일 때만 번역 시도
                    if do_trans and item.title:
                        try: item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)
                        except Exception as e_trans: logger.error(f"Error translating title: {e_trans}"); item.title_ko = item.title
                    else: item.title_ko = item.title

                # --- 점수 계산 ---
                match_real_no_local = cls.PTN_SEARCH_REAL_NO.search(item.code[2:]) # 여기서 match 결과 저장
                if match_real_no_local: item_ui_code_base = match_real_no_local.group("real") + match_real_no_local.group("no")
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

                # --- UI 코드 형식화 수정 (zfill(3) 적용) ---
                if match_real_no_local:
                    real_part = match_real_no_local.group("real").upper()
                    try:
                        no_part_str = str(int(match_real_no_local.group("no"))).zfill(3) # 3자리 패딩
                        item.ui_code = f"{real_part}-{no_part_str}"
                    except ValueError: # 숫자 변환 실패 시
                        item.ui_code = f"{real_part}-{match_real_no_local.group('no')}" # 그룹 그대로 사용
                else:
                    ui_code_temp = item.code[2:].upper()
                    if ui_code_temp.startswith("H_"): ui_code_temp = ui_code_temp[2:]
                    m = re.match(r"([a-zA-Z]+)(\d+.*)", ui_code_temp)
                    if m:
                        real_part = m.group(1)
                        num_part_match = re.match(r"(\d+)", m.group(2))
                        if num_part_match:
                            no_part_str = str(int(num_part_match.group(1))).zfill(3) # 3자리 패딩
                            item.ui_code = f"{real_part}-{no_part_str}"
                        else: item.ui_code = f"{real_part}-{m.group(2)}"
                    else: item.ui_code = ui_code_temp # 최종 fallback

                logger.debug(f"Item found - Score: {item.score}, Code: {item.code}, UI Code: {item.ui_code}, Title: {item.title_ko}")
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
        """상세 페이지 HTML(tree)에서 이미지 URL들을 추출합니다."""
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        try:
            # 메인 이미지 (pl) 추출
            pl_xpath = '//div[@id="sample-video"]//img/@src' # 메인 비디오/이미지 영역의 img src
            pl_tags = tree.xpath(pl_xpath)
            if pl_tags:
                img_urls['pl'] = pl_tags[0]
                # // 로 시작하면 https: 추가
                if img_urls['pl'].startswith("//"): img_urls['pl'] = "https:" + img_urls['pl']
                # ps가 없다면 pl을 ps로 우선 사용 (썸네일 캐시 없을 경우 대비)
                if not img_urls['ps']: img_urls['ps'] = img_urls['pl']
            else:
                logger.warning("__img_urls: Main image (pl) not found.")

            # 샘플 이미지 (arts) 추출 (고화질 링크 우선)
            arts_xpath = '//div[@id="sample-image-block"]//a/@href' # 샘플 이미지 링크(href)
            arts_tags = tree.xpath(arts_xpath)
            if arts_tags:
                # 첫번째 링크는 pl과 동일할 수 있으므로 제외하거나 확인 후 추가
                # 여기서는 일단 모두 추가하되, 중복 제거 및 pl과 다른 것만 선택
                all_arts = []
                for href in arts_tags:
                    if href and href.strip(): # 비어있지 않은 링크만
                        full_href = href if href.startswith("http") else py_urllib_parse.urljoin(cls.site_base_url, href)
                        # pl 과 다른 이미지만 추가 (선택적)
                        if full_href != img_urls.get('pl'):
                            all_arts.append(full_href)
                # 중복 제거 (순서 유지)
                img_urls['arts'] = sorted(list(set(all_arts)), key=all_arts.index)
            else:
                logger.warning("__img_urls: Sample images (arts) not found.")

        except Exception as e:
            logger.exception(f"Error extracting image URLs in __img_urls: {e}")

        logger.debug(f"Extracted image URLs: ps={bool(img_urls.get('ps'))}, pl={bool(img_urls.get('pl'))}, arts={len(img_urls.get('arts',[]))}")
        return img_urls

    @classmethod
    def __info( cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, ps_to_poster=False, crop_mode=None):
        logger.info(f"Getting detail info for {code} using new parsing logic.")
        ps_url_from_cache = cls._ps_url_cache.pop(code, None)
        if ps_url_from_cache: logger.debug(f"Using cached ps_url for {code}.")
        else: logger.warning(f"ps_url for {code} not found in cache. Fallback needed.")

        if not cls._ensure_age_verified(proxy_url=proxy_url):
            raise Exception(f"DMM age verification failed for info ({code}).")

        # 상세 페이지 URL (경로 확인 필요 - videoa 가 맞는지 mono/dvd 인지)
        # 일단 videoa 로 시도
        detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={code[2:]}/"
        logger.info(f"Accessing DMM detail page: {detail_url}")
        info_headers = cls._get_request_headers(referer=cls.site_base_url + "/digital/videoa/")
        tree = None
        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=info_headers)
            if tree is None: raise Exception("SiteUtil.get_tree returned None.")
            # 연령 확인 페이지 체크
            title_tags_check = tree.xpath('//title/text()')
            if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]:
                raise Exception("Received age verification page instead of detail.")
        except Exception as e:
            logger.exception(f"Failed to get or verify detail page tree for {code}: {e}")
            raise # 에러 다시 발생

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"

        # --- 제목 / Tagline ---
        try:
            title_node = tree.xpath('//h1[@id="title"]')
            if title_node:
                h1_text = title_node[0].text_content().strip()
                # 앞부분 태그 제거 (예: 【最新作】)
                prefix_tags = title_node[0].xpath('./span[@class="red"]/text()')
                title_cleaned = h1_text
                if prefix_tags:
                    title_cleaned = h1_text.replace(prefix_tags[0].strip(), "").strip()
                entity.tagline = SiteUtil.trans(title_cleaned, do_trans=do_trans)
                logger.debug(f"Tagline parsed: {entity.tagline[:50]}...")
            else: logger.warning("Tagline (h1#title) not found.")
        except Exception as e: logger.error(f"Error parsing tagline: {e}")

        # --- 이미지 URL 추출 및 처리 (SiteUtil 호출 강화) ---
        try:
            img_urls = cls.__img_urls(tree) # ['ps':?, 'pl':?, 'arts':[]] 반환 예상
            img_urls['ps'] = ps_url_from_cache if ps_url_from_cache else img_urls.get('ps', "")
            if not img_urls['ps']: logger.error("Failed to obtain ps_url.")

            logger.debug(f"Calling SiteUtil.resolve_jav_imgs with: {img_urls}")
            # resolve_jav_imgs 가 내부적으로 비율 체크 및 비교 로직 수행 가정
            # 반환되는 img_urls 에는 결정된 poster, landscape, crop_mode 등이 포함될 것임
            SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)
            logger.debug(f"Result from SiteUtil.resolve_jav_imgs: {img_urls}") # 결과 확인

            # process_jav_imgs 는 resolve 결과를 바탕으로 최종 썸네일 URL 생성
            entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
            logger.debug(f"Final entity.thumb: {entity.thumb}")

            # 팬아트 처리 (landscape 는 제외)
            entity.fanart = []
            resolved_arts = img_urls.get("arts", [])
            landscape_url = img_urls.get("landscape") # resolve 에서 결정된 landscape URL
            logger.debug(f"Resolved arts count: {len(resolved_arts)}, Landscape URL: {landscape_url}")
            for href in resolved_arts[:max_arts]:
                # 팬아트 목록에서 최종 landscape URL과 동일한 것은 제외
                if href != landscape_url:
                    entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))
            logger.debug(f"Final fanart count: {len(entity.fanart)}")

        except Exception as e: logger.exception(f"Error processing images: {e}")

        # --- 정보 테이블 파싱 ---
        info_table_xpath = '//table[contains(@class, "mg-b20")]//tr' # mg-b20 클래스를 가진 테이블 내 tr
        tags = tree.xpath(info_table_xpath)
        logger.debug(f"Found {len(tags)} rows in info table.")
        premiered_shouhin = None; premiered_haishin = None;

        for tag in tags:
            try:
                # 키(th)와 값(td) 추출 시도 (td가 2개인 구조 예상)
                key_node = tag.xpath('./td[@class="nw"]/text()') # nw 클래스 td의 텍스트
                value_node = tag.xpath('./td[not(@class="nw")]') # nw 클래스 없는 td

                if not key_node or not value_node: continue # 키 또는 값이 없으면 스킵
                key = key_node[0].strip().replace("：", "") # 콜론 제거
                value_td = value_node[0] # 값 td 요소
                value_text_all = value_td.text_content().strip()
                if value_text_all == "----" or not value_text_all: continue

                logger.debug(f"Processing table row: Key='{key}', Value='{value_text_all[:50]}...'")

                if key == "配信開始日": premiered_haishin = value_text_all.replace("/", "-")
                elif key == "商品発売日": premiered_shouhin = value_text_all.replace("/", "-")
                elif key == "収録時間":
                    m = re.search(r"(\d+)", value_text_all)
                    if m: entity.runtime = int(m.group(1))
                elif key == "出演者":
                    # 링크가 있으면 링크 텍스트, 없으면 전체 텍스트 (하지만 이 경우 '----'일 가능성 높음)
                    actors_found = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                    if actors_found:
                        entity.actor = [EntityActor(name) for name in actors_found]
                    else:
                        # '----'가 아닐 경우 텍스트를 그대로 사용 (쉼표 등으로 구분된 경우?)
                        if value_text_all != '----':
                            # 공백 등으로 분리 시도 (불확실)
                            possible_actors = [name.strip() for name in value_text_all.split()]
                            entity.actor = [EntityActor(name) for name in possible_actors if name]
                        else: entity.actor = [] # 명시적으로 빈 리스트
                elif key == "監督":
                    directors = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                    entity.director = directors[0] if directors else (value_text_all if value_text_all != '----' else None)
                elif key == "シリーズ":
                    if entity.tag is None: entity.tag = []
                    series_list = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                    series_name = series_list[0] if series_list else (value_text_all if value_text_all != '----' else None)
                    if series_name: entity.tag.append(SiteUtil.trans(series_name, do_trans=do_trans))
                elif key == "メーカー":
                    if entity.studio is None: # 레이블 정보가 없을 때만 사용
                        makers = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        maker_name = makers[0] if makers else (value_text_all if value_text_all != '----' else None)
                        if maker_name: entity.studio = SiteUtil.trans(maker_name, do_trans=do_trans)
                elif key == "レーベル": # 레이블 정보를 우선 사용
                    labels = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                    label_name = labels[0] if labels else (value_text_all if value_text_all != '----' else None)
                    if label_name:
                        if do_trans: entity.studio = SiteUtil.av_studio.get(label_name, SiteUtil.trans(label_name))
                        else: entity.studio = label_name
                elif key == "ジャンル":
                    entity.genre = []
                    genre_links = value_td.xpath('.//a/text()')
                    for genre_ja in genre_links:
                        genre_ja = genre_ja.strip()
                        if not genre_ja or "％OFF" in genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                        if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                        else:
                            genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                            if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                elif key == "品番":
                    # 검색 결과에서 이미 설정했으므로 여기서 title 설정은 불필요
                    # 레이블 정보 추출은 여기서 다시 시도 가능 (선택적)
                    match_real = cls.PTN_SEARCH_REAL_NO.match(value_text_all)
                    if match_real:
                        label = match_real.group("real").upper()
                        if entity.tag is None: entity.tag = []
                        if label not in entity.tag: entity.tag.append(label) # 태그에 레이블 추가
                elif key == "平均評価":
                    # 평점 이미지 추출
                    rating_img_tags = value_td.xpath('.//img/@src')
                    if rating_img_tags:
                        match_rating = cls.PTN_RATING.search(rating_img_tags[0])
                        if match_rating:
                            rating_value_str = match_rating.group("rating")
                            try:
                                # 값 보정: DMM 평점은 50점 만점 기준일 수 있음 (예: 45 -> 4.5)
                                # 또는 5점 만점 기준이면 그대로 사용
                                # 여기서는 5점 만점으로 가정하고 변환
                                rating_value = float(rating_value_str) # 0~50 예상
                                if rating_value > 5: rating_value = rating_value / 10.0 # 5 초과 시 10으로 나눔
                                if 0 <= rating_value <= 5:
                                    rating_img_url = "https:" + rating_img_tags[0] if rating_img_tags[0].startswith("//") else rating_img_tags[0]
                                    entity.ratings = [EntityRatings(rating_value, max=5, name="dmm", image_url=rating_img_url)]
                                    logger.debug(f"Rating parsed from table: {rating_value}")
                            except ValueError: logger.warning(f"Cannot convert rating value: {rating_value_str}")
            except Exception as e_row: logger.exception(f"Error parsing info table row: {e_row}")


        # 최종 날짜 설정 (상품 출시일 우선)
        final_premiered = None
        if premiered_shouhin: final_premiered = premiered_shouhin; logger.debug("Using 商品発売日 for premiered date.")
        elif premiered_haishin: final_premiered = premiered_haishin; logger.debug("Using 配信開始日 for premiered date.")
        else: logger.warning("No premiered date found in table.")
        if final_premiered:
            entity.premiered = final_premiered
            try: entity.year = int(final_premiered[:4])
            except ValueError: logger.warning(f"Could not parse year: {final_premiered}"); entity.year = None
        else: entity.premiered = None; entity.year = None

        # --- 줄거리 파싱 ---
        try:
            plot_xpath = '//div[@class="mg-b20 lh4"]/text()' # 이 클래스를 가진 div 아래의 텍스트 노드들
            plot_nodes = tree.xpath(plot_xpath)
            if plot_nodes:
                plot_text = "\n".join([p.strip() for p in plot_nodes if p.strip()]).split("※")[0].strip()
                entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                logger.debug(f"Plot parsed: {entity.plot[:50]}...")
            else: logger.warning("Plot node (div.mg-b20.lh4 > text()) not found.")
        except Exception as e: logger.exception(f"Error parsing plot: {e}")

        # --- 예고편 처리 (수정 필요) ---
        entity.extras = []
        if use_extras:
            logger.warning("Trailer parsing logic needs implementation based on new structure.")
            # 예시: 새로운 구조에서 예고편 URL 찾는 로직 추가
            # try:
            #      trailer_script = tree.xpath('//script[contains(text(),"sample")]/text()')
            #      # ... JSON 파싱 또는 URL 추출 ...
            #      if trailer_url: entity.extras.append(...)
            # except Exception as e: logger.error(f"Error parsing extras: {e}")

        # --- 원본 제목/정렬 제목 설정 (UI 코드 형식 수정) ---
        # title, originaltitle, sorttitle 을 최종적으로 UI 코드 형식으로 설정
        final_ui_code = code[2:].upper()
        match_real_no = cls.PTN_SEARCH_REAL_NO.search(code[2:])
        if match_real_no:
            real_part = match_real_no.group("real").upper()
            # 숫자 부분을 정수로 변환 후, zfill(3)을 사용하여 최소 3자리로 패딩
            try:
                no_part_str = str(int(match_real_no.group("no"))).zfill(3) # 3자리 패딩 적용
                final_ui_code = f"{real_part}-{no_part_str}" # 예: HAME-041
            except ValueError:
                logger.warning(f"Could not parse number part for UI code: {match_real_no.group('no')}")
                # 숫자 변환 실패 시, 정규식 그룹 그대로 사용 시도 (다른 문자가 섞인 경우?)
                final_ui_code = f"{real_part}-{match_real_no.group('no')}"
        else:
            # 정규식 매칭 실패 시 (예: h_ 접두사 등)
            ui_code_temp = code[2:].upper()
            if ui_code_temp.startswith("H_"): ui_code_temp = ui_code_temp[2:]
            # 문자-숫자 분리 후 숫자 부분 3자리 패딩 시도
            m = re.match(r"([a-zA-Z]+)(\d+.*)", ui_code_temp)
            if m:
                real_part = m.group(1)
                num_part_match = re.match(r"(\d+)", m.group(2))
                if num_part_match:
                    no_part_str = str(int(num_part_match.group(1))).zfill(3) # 3자리 패딩
                    final_ui_code = f"{real_part}-{no_part_str}"
                else: # 숫자 부분 못찾으면 그대로 붙임
                    final_ui_code = f"{real_part}-{m.group(2)}"
            else: # 분리 실패 시 그냥 사용
                final_ui_code = ui_code_temp

        entity.title = entity.originaltitle = entity.sorttitle = final_ui_code
        logger.debug(f"Final title/originaltitle/sorttitle set to formatted UI code: {entity.title}")

        return entity # 최종 entity 반환

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
