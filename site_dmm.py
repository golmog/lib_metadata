# -*- coding: utf-8 -*-
import json
import re
import urllib.parse as py_urllib_parse
from lxml import html, etree # etree도 임포트 (v_new 참조)

# lib_metadata 패키지 내 다른 모듈 import
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

class SiteDmm:
    site_name = "dmm"
    site_base_url = "https://www.dmm.co.jp"
    # --- fanza_av_url 추가 (v_new 참조) ---
    fanza_av_url = "https://video.dmm.co.jp/av/"
    # --- age_check_confirm_url_template 추가 (v_new 참조) ---
    # age_check_confirm_url_template = "https://www.dmm.co.jp/age_check/set?r={redirect_url}" # v_new 방식
    module_char = "C"; site_char = "D"

    # --- DMM 전용 기본 헤더 (v_old 최신화 버전 사용) ---
    dmm_base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36", # v_old 참고
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"', # v_old 참고
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin", # v_old 에는 same-site 였으나 same-origin이 더 일반적
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        # --- v_new 에 있던 추가 헤더 포함 ---
        "Referer": site_base_url + "/",
        "DNT": "1",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
    }

    # --- 정규 표현식 (v_old + v_new 필요한 것 통합) ---
    PTN_SEARCH_CID = re.compile(r"\/cid=(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^(h_)?\d*(?P<real>[a-zA-Z]+)(?P<no>\d+)([a-zA-Z]+)?$")
    PTN_ID = re.compile(r"\d{2}id", re.I)
    # --- v_old 의 평점 패턴 사용 ---
    PTN_RATING = re.compile(r"(?P<rating>[\d|_]+)\.gif")

    # --- 상태 관리 변수 (v_new 방식 사용) ---
    age_verified = False
    last_proxy_used = None
    # --- _ps_url_cache 변경: value를 단순 URL이 아닌 dict로 저장 (v_new 방식) ---
    _ps_url_cache = {} # code: {'ps': ps_url, 'type': content_type} 딕셔너리

    @classmethod
    def _get_request_headers(cls, referer=None):
        """요청에 사용할 헤더를 생성합니다."""
        headers = cls.dmm_base_headers.copy()
        if referer:
            headers['Referer'] = referer
        # User-Agent는 dmm_base_headers 에서 설정됨
        return headers

    @classmethod
    def _ensure_age_verified(cls, proxy_url=None):
        """SiteUtil.session에 DMM 연령 확인 쿠키가 있는지 확인하고, 없으면 설정합니다. (v_new 개선 버전)"""
        if not cls.age_verified or cls.last_proxy_used != proxy_url:
            logger.debug("Checking/Performing DMM age verification...")
            cls.last_proxy_used = proxy_url
            session_cookies = SiteUtil.session.cookies
            # --- .dmm.com 도메인 쿠키 확인 추가 (v_new) ---
            domain_checks = ['.dmm.co.jp', '.dmm.com']
            if any('age_check_done' in session_cookies.get_dict(domain=d) and session_cookies.get_dict(domain=d)['age_check_done'] == '1' for d in domain_checks):
                logger.debug("Age verification cookie found in SiteUtil.session.")
                cls.age_verified = True; return True

            logger.debug("Attempting DMM age verification via confirmation GET...")
            try:
                # --- v_new의 rurl 및 경로 사용 ---
                target_rurl = cls.fanza_av_url # videoa 섹션 URL 사용
                confirm_path = f"/age_check/=/declared=yes/?rurl={py_urllib_parse.quote(target_rurl, safe='')}"
                age_check_confirm_url = py_urllib_parse.urljoin(cls.site_base_url, confirm_path)

                confirm_headers = cls._get_request_headers(referer=cls.site_base_url + "/") # 기본 리퍼러
                confirm_response = SiteUtil.get_response(
                    age_check_confirm_url, method='GET', proxy_url=proxy_url, headers=confirm_headers, allow_redirects=False
                )
                logger.debug(f"Confirmation GET status: {confirm_response.status_code}")
                # logger.debug(f"Session Cookies after confirm GET: {[(c.name, c.value, c.domain) for c in SiteUtil.session.cookies]}") # 상세 로깅 필요시

                if confirm_response.status_code == 302 and 'age_check_done=1' in confirm_response.headers.get('Set-Cookie', ''):
                    logger.debug("Age confirmation successful via Set-Cookie.")
                    # --- 쿠키 확인 및 수동 설정 로직 (v_new) ---
                    final_cookies = SiteUtil.session.cookies
                    if any('age_check_done' in final_cookies.get_dict(domain=d) and final_cookies.get_dict(domain=d)['age_check_done'] == '1' for d in domain_checks):
                        logger.debug("age_check_done=1 confirmed in session."); cls.age_verified = True; return True
                    else:
                        logger.warning("Set-Cookie received, but not updated in session. Trying manual set...")
                        # .com과 .co.jp 도메인 모두에 설정 시도
                        SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.co.jp", path="/"); SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.com", path="/")
                        logger.info("Manually set age_check_done cookie."); cls.age_verified = True; return True
                else: logger.warning(f"Age check failed (Status:{confirm_response.status_code} or cookie missing).")
            except Exception as e: logger.exception(f"Age verification exception: {e}")
            cls.age_verified = False; return False
        else:
            logger.debug("Age verification already done."); return True

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        """ (v_new의 검색 로직 사용 - 타입 판별 및 캐싱 기능 유지) """
        if not cls._ensure_age_verified(proxy_url=proxy_url): return []
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd": keyword = keyword[:-3]
        keyword = keyword.replace("-", " ")
        keyword_tmps = keyword.split(" ")
        if len(keyword_tmps) == 2: dmm_keyword = keyword_tmps[0] + keyword_tmps[1].zfill(5)
        else: dmm_keyword = keyword
        logger.debug("keyword [%s] -> [%s]", keyword, dmm_keyword)

        # --- v_new의 일반 검색 URL 사용 ---
        search_params = { 'redirect': '1', 'enc': 'UTF-8', 'category': '', 'searchstr': dmm_keyword }
        search_url = f"{cls.site_base_url}/search/?{py_urllib_parse.urlencode(search_params)}"
        logger.info(f"Using search URL: {search_url}")

        search_headers = cls._get_request_headers(referer=cls.fanza_av_url) # videoa 섹션 리퍼러
        tree = None
        try:
            tree = SiteUtil.get_tree(search_url, proxy_url=proxy_url, headers=search_headers, allow_redirects=True)
            if tree is None: logger.warning("Search tree is None."); return []
            # 연령 확인 페이지 체크 (v_new)
            title_tags_check = tree.xpath('//title/text()')
            if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]: logger.error("Age page received during search."); return []
        except Exception as e: logger.exception(f"Failed to get tree for search: {e}"); return []

        # --- v_new의 검색 결과 XPath 사용 ---
        list_xpath = '//div[contains(@class, "grid-cols-4")]//div[contains(@class, "border-r") and contains(@class, "border-b")]'
        lists = []; logger.debug(f"Attempting XPath (Desktop Grid): {list_xpath}")
        try: lists = tree.xpath(list_xpath)
        except Exception as e_xpath: logger.error(f"XPath error: {e_xpath}")
        logger.debug(f"Found {len(lists)} items using Desktop Grid XPath.")
        if not lists: logger.warning(f"No items found using Desktop Grid XPath."); return []

        ret = []; score = 60
        for node in lists[:10]: # 최대 10개 결과 처리
            try:
                item = EntityAVSearch(cls.site_name)
                href = None; original_ps_url = None; content_type = "unknown"

                # --- v_new의 결과 아이템 파싱 로직 ---
                link_tag_img = node.xpath('.//a[contains(@class, "flex justify-center")]');
                if not link_tag_img: continue
                img_link_href = link_tag_img[0].attrib.get("href", "").lower()
                img_tag = link_tag_img[0].xpath('./img/@src')
                if not img_tag: continue
                original_ps_url = img_tag[0]

                title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]')
                if not title_link_tag: continue
                title_link_with_p = node.xpath('.//a[contains(@href, "/detail/=/cid=") and ./p[contains(@class, "hover:text-linkHover")]]')
                title_link_tag = title_link_with_p[0] if title_link_with_p else title_link_tag[0]
                title_link_href = title_link_tag.attrib.get("href", "").lower()

                href = title_link_href if title_link_href else img_link_href
                if href: # URL 경로로 타입 추정 (v_new)
                    if "/digital/videoa/" in href: content_type = "videoa"
                    elif "/mono/dvd/" in href: content_type = "dvd"
                    # 다른 타입 필요시 추가 (예: /mono/anime/, /book/ ...)
                item.content_type = content_type # EntityAVSearch에 타입 저장

                title_p_tag = title_link_tag.xpath('./p[contains(@class, "hover:text-linkHover")]')
                if title_p_tag: item.title = title_p_tag[0].text_content().strip()

                if not original_ps_url: continue
                if original_ps_url.startswith("//"): original_ps_url = "https:" + original_ps_url
                item.image_url = original_ps_url
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try: item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"ImgProcErr:{e_img}")

                if not href: continue
                match_cid = cls.PTN_SEARCH_CID.search(href)
                if match_cid: item.code = cls.module_char + cls.site_char + match_cid.group("code")
                else: continue
                if any(i.get("code") == item.code for i in ret): continue # 중복 제거

                if not item.title or item.title == "Not Found": item.title = item.code # 제목 없으면 코드로 대체

                # --- 캐시에 ps_url과 **타입** 저장 (v_new 방식) ---
                if item.code and original_ps_url:
                    cls._ps_url_cache[item.code] = {'ps': original_ps_url, 'type': content_type}
                    logger.debug(f"Stored ps & type for {item.code} in cache: {content_type}")

                # 번역 처리 (v_new)
                if manual: item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                else: item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans) if do_trans and item.title else item.title

                # 점수 계산 (v_new 로직 사용)
                match_real_no = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                item_ui_code_base = match_real_no.group("real") + match_real_no.group("no") if match_real_no else item.code[2:]
                current_score = 0
                if len(keyword_tmps) == 2:
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif item_ui_code_base.replace("0", "") == dmm_keyword.replace("0", ""): current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    elif keyword_tmps[0] in item.code and keyword_tmps[1] in item.code: current_score = score
                    elif keyword_tmps[0] in item.code or keyword_tmps[1] in item.code: current_score = 60
                    else: current_score = 20
                else: # 키워드가 하나일 때 (품번 직접 검색 등)
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    else: current_score = 20
                item.score = current_score
                if current_score < 100 and score > 20: score -= 5 # 다음 항목 점수 감소

                # UI 코드 포맷팅 (v_new 로직 사용)
                if match_real_no:
                    real = match_real_no.group("real").upper(); no = match_real_no.group("no")
                    try: item.ui_code = f"{real}-{str(int(no)).zfill(3)}"
                    except ValueError: item.ui_code = f"{real}-{no}"
                else:
                    tmp = item.code[2:].upper();
                    if tmp.startswith("H_"): tmp = tmp[2:]
                    m = re.match(r"([a-zA-Z]+)(\d+.*)", tmp)
                    if m:
                        real = m.group(1); rest = m.group(2); num_m = re.match(r"(\d+)", rest)
                        item.ui_code = f"{real}-{str(int(num_m.group(1))).zfill(3)}" if num_m else f"{real}-{rest}"
                    else: item.ui_code = tmp # 매칭 실패 시 원래 코드 사용

                logger.debug(f"Item found ({content_type}) - Score: {item.score}, Code: {item.code}, UI Code: {item.ui_code}, Title: {item.title_ko}")
                ret.append(item.as_dict())
            except Exception as e_inner: logger.exception(f"ItemProcErr:{e_inner}")

        sorted_ret = sorted(ret, key=lambda k: k.get("score", 0), reverse=True)
        if not sorted_ret and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6)
            logger.debug(f"Retrying with {new_title}")
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)
        return sorted_ret

    @classmethod
    def search(cls, keyword, **kwargs):
        """ (v_new과 동일) """
        ret = {}
        try: data_list = cls.__search(keyword, **kwargs)
        except Exception as exception: logger.exception("SearchErr:"); ret["ret"] = "exception"; ret["data"] = str(exception)
        else: ret["ret"] = "success" if data_list else "no_match"; ret["data"] = data_list
        return ret

    @classmethod
    def __img_urls(cls, tree, content_type='unknown'):
        """순수 URL 추출 기능만 담당 (v_new 수정 + v_old DVD 로직 반영)"""
        logger.debug(f"Extracting raw image URLs for type: {content_type}")
        img_urls = {'ps': "", 'pl': "", 'arts': []} # 기본값 초기화

        try:
            if content_type == 'videoa':
                # videoa 타입 URL 추출 (v_new 로직 유지, XPath 검증 필요)
                logger.debug("Extracting videoa URLs...")
                # ps (작은 포스터): __info에서 캐시 우선 사용
                # pl (큰 포스터 링크 - videoa는 보통 없음, arts[0]를 pl로 사용)
                # arts (샘플 이미지)
                arts_xpath_main = '//div[@id="sample-image-block"]//a/@href'
                arts_xpath_alt = '//a[contains(@id, "sample-image")]/@href'

                arts_tags = tree.xpath(arts_xpath_main)
                if not arts_tags:
                    logger.debug(f"Trying alternative arts XPath for videoa: {arts_xpath_alt}")
                    arts_tags = tree.xpath(arts_xpath_alt)

                if arts_tags:
                    logger.debug(f"Found {len(arts_tags)} potential arts links for videoa.")
                    processed_arts = []
                    for href in arts_tags:
                        if href and href.strip():
                            full_href = py_urllib_parse.urljoin(cls.site_base_url, href)
                            processed_arts.append(full_href)
                    unique_arts = []; [unique_arts.append(x) for x in processed_arts if x not in unique_arts]
                    img_urls['arts'] = unique_arts
                    if img_urls['arts']:
                        img_urls['pl'] = img_urls['arts'][0] # videoa는 첫 art가 pl 역할
                        logger.debug(f"PL for videoa set from first art: {img_urls['pl']}")
                else:
                    logger.warning("Arts block not found for videoa using known XPaths.")


            elif content_type == 'dvd':
                # dvd 타입 URL 추출 (v_old 코드 기준 XPath 사용)
                logger.debug("Extracting dvd URLs using v_old logic...")

                # pl 추출 (v_old XPath 사용)
                pl_xpath = '//div[@id="fn-sampleImage-imagebox"]/img/@src' # v_old XPath
                pl_tags = tree.xpath(pl_xpath)
                raw_pl = pl_tags[0] if pl_tags else ""
                if raw_pl:
                    img_urls['pl'] = ("https:" + raw_pl) if not raw_pl.startswith("http") else raw_pl
                    logger.debug(f"Found dvd pl using v_old XPath: {img_urls['pl']}")
                else:
                    logger.warning("Could not find dvd pl using v_old XPath: %s", pl_xpath)

                # ps 추출 (v_old는 캐시에 의존했으므로 여기서는 추출 시도 안 함, __info에서 처리)
                img_urls['ps'] = "" # 명시적으로 비워둠

                # arts 추출 (v_old XPath 사용)
                arts_xpath = '//li[contains(@class, "fn-sampleImage__zoom") and not(@data-slick-index="0")]//img' # v_old XPath
                arts_tags = tree.xpath(arts_xpath)
                if arts_tags:
                    logger.debug(f"Found {len(arts_tags)} potential arts links for dvd using v_old XPath.")
                    processed_arts = []
                    for tag in arts_tags:
                        src = tag.attrib.get("src") or tag.attrib.get("data-lazy") # src 또는 data-lazy 사용 (v_old)
                        if src:
                            if not src.startswith("http"): src = "https:" + src
                            processed_arts.append(src)
                    unique_arts = []; [unique_arts.append(x) for x in processed_arts if x not in unique_arts]
                    img_urls['arts'] = unique_arts
                else:
                    logger.warning("Could not find dvd arts using v_old XPath: %s", arts_xpath)

            else:
                logger.error(f"Unknown content type '{content_type}' in __img_urls")

        except Exception as e:
            logger.exception(f"Error extracting image URLs: {e}")
            img_urls = {'ps': "", 'pl': "", 'arts': []} # 실패 시 기본값

        logger.debug(f"Extracted img_urls: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} arts={len(img_urls.get('arts',[]))}")
        return img_urls

    @classmethod
    def __info( cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, ps_to_poster=False, crop_mode=None):
        logger.info(f"Getting detail info for {code}")
        # --- 캐시된 정보 로드 (v_new 방식 - ps와 type) ---
        cached_data = cls._ps_url_cache.pop(code, {})
        ps_url_from_cache = cached_data.get('ps')
        content_type = cached_data.get('type', 'unknown')
        if ps_url_from_cache: logger.debug(f"Using cached ps_url for {code}: {ps_url_from_cache}")
        else: logger.warning(f"ps_url for {code} not found in cache.")
        logger.debug(f"Determined content type: {content_type}")

        if not cls._ensure_age_verified(proxy_url=proxy_url): raise Exception(f"Age verification failed for info ({code}).")

        # --- 상세 페이지 URL 결정 (v_new 방식) ---
        cid_part = code[2:]
        detail_url = None
        if content_type == 'videoa': detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
        elif content_type == 'dvd': detail_url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={cid_part}/"
        else:
            logger.warning(f"Unknown type '{content_type}'. Trying 'videoa' path for {code}.")
            # 타입을 알 수 없을 때 videoa/dvd 순서로 시도해볼 수도 있음 (복잡도 증가)
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/" # videoa 우선 시도
            content_type = 'videoa' # 임시 가정

        logger.info(f"Accessing DMM detail page ({content_type}): {detail_url}")
        # --- 리퍼러 설정 개선 (v_old 참고) ---
        referer_url = cls.fanza_av_url if content_type == 'videoa' else (cls.site_base_url + "/mono/dvd/")
        info_headers = cls._get_request_headers(referer=referer_url)
        tree = None
        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=info_headers)
            if tree is None: raise Exception(f"SiteUtil.get_tree returned None for {detail_url}.")
        except Exception as e: logger.exception(f"Failed get/process detail tree: {e}"); raise

        entity = EntityMovie(cls.site_name, code); entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"

        # --- 원시 이미지 URL 추출 (v_new 방식) ---
        img_urls = cls.__img_urls(tree, content_type=content_type)

        # --- 캐시된 ps_url 적용 (v_new 방식) ---
        if ps_url_from_cache:
            logger.debug(f"Applying ps url from cache: {ps_url_from_cache}")
            img_urls['ps'] = ps_url_from_cache
        elif not img_urls['ps'] and img_urls['pl']: # ps 없고 pl만 있을 경우 fallback (v_old 참고)
            logger.warning("ps URL missing, using pl as fallback for ps.")
            img_urls['ps'] = img_urls['pl']
        elif not img_urls['ps'] and not img_urls['pl']: # 둘 다 없으면 에러
            logger.error("Crucial ps and pl URLs are missing.")


        # --- SiteUtil을 이용한 이미지 처리 (resolve & process) (v_new 방식) ---
        logger.debug(f"Image URLs before resolve: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} arts={len(img_urls.get('arts',[]))}")
        try:
            SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)
            logger.debug(f"Image URLs after resolve: poster={bool(img_urls.get('poster'))} crop={img_urls.get('poster_crop')} landscape={bool(img_urls.get('landscape'))}")
            entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
            entity.fanart = []
            resolved_arts = img_urls.get("arts", [])
            logger.debug(f"Processing {len(resolved_arts)} arts for fanart (max: {max_arts})")
            processed_fanart_count = 0
            for href in resolved_arts:
                if processed_fanart_count >= max_arts: break
                # landscape/poster와 중복 체크는 선택사항
                try:
                    fanart_url = SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url)
                    if fanart_url: entity.fanart.append(fanart_url); processed_fanart_count += 1
                except Exception as e_fanart: logger.error(f"Error processing fanart image {href}: {e_fanart}")
            logger.debug(f"Final Thumb: {entity.thumb}, Fanart Count: {len(entity.fanart)}")
        except Exception as e_img_proc:
            logger.exception(f"Error during SiteUtil image processing: {e_img_proc}")
            entity.thumb = []; entity.fanart = [] # 실패 시 빈 리스트


        # --- 파싱 로직 분기 ---
        if content_type == 'videoa':
            # --- videoa 메타데이터 파싱 로직 (v_new 유지, XPath 검증 필요) ---
            logger.debug("Parsing 'videoa' metadata...")
            try:
                # 제목/Tagline (v_new)
                title_node = tree.xpath('//h1[@id="title"]')
                if title_node:
                    h1_text = title_node[0].text_content().strip()
                    prefix_tags = title_node[0].xpath('./span[@class="red"]/text()') # videoa 전용 태그?
                    title_cleaned = h1_text.replace(prefix_tags[0].strip(), "").strip() if prefix_tags else h1_text
                    entity.tagline = SiteUtil.trans(title_cleaned, do_trans=do_trans)
                else: logger.warning("Tagline (h1#title) not found for videoa.")

                # 정보 테이블 파싱 (v_new - XPath 검증 필요)
                info_table_xpath = '//table[contains(@class, "mg-b20")]//tr'
                tags = tree.xpath(info_table_xpath)
                premiered_shouhin = None; premiered_haishin = None
                for tag in tags:
                    key_node = tag.xpath('./td[@class="nw"]/text()')
                    value_node = tag.xpath('./td[not(@class="nw")]')
                    if not key_node or not value_node: continue
                    key = key_node[0].strip().replace("：", "")
                    value_td = value_node[0]; value_text_all = value_td.text_content().strip()
                    if value_text_all == "----" or not value_text_all: continue
                    # (videoa 파싱 로직 - 날짜, 시간, 배우, 감독, 시리즈, 제작사, 레이블, 장르, 품번, 평점 등)
                    # v_new 로직 유지 (필요시 v_old 로직 참고하여 보강)
                    if key == "配信開始日": premiered_haishin = value_text_all.replace("/", "-")
                    elif key == "商品発売日": premiered_shouhin = value_text_all.replace("/", "-")
                    elif key == "収録時間": m=re.search(r"(\d+)",value_text_all); entity.runtime = int(m.group(1)) if m else None
                    elif key == "出演者":
                        actors = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        if actors: entity.actor = [EntityActor(name) for name in actors]
                        elif value_text_all != '----': entity.actor = [EntityActor(n.strip()) for n in value_text_all.split('/') if n.strip()] # / 구분자 가능성
                        else: entity.actor = []
                    elif key == "監督":
                        directors = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        entity.director = directors[0] if directors else (value_text_all if value_text_all != '----' else None)
                    elif key == "シリーズ":
                        if entity.tag is None: entity.tag = []
                        series = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        s_name = series[0] if series else (value_text_all if value_text_all != '----' else None)
                        if s_name: entity.tag.append(SiteUtil.trans(s_name, do_trans=do_trans))
                    elif key == "メーカー":
                        if entity.studio is None:
                            makers = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                            m_name = makers[0] if makers else (value_text_all if value_text_all != '----' else None)
                            if m_name: entity.studio = SiteUtil.trans(m_name, do_trans=do_trans)
                    elif key == "レーベル":
                        labels = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        l_name = labels[0] if labels else (value_text_all if value_text_all != '----' else None)
                        if l_name:
                            if do_trans: entity.studio = SiteUtil.av_studio.get(l_name, SiteUtil.trans(l_name))
                            else: entity.studio = l_name
                    elif key == "ジャンル":
                        entity.genre = []
                        for genre_ja in value_td.xpath('.//a/text()'):
                            genre_ja = genre_ja.strip()
                            if not genre_ja or "％OFF" in genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                    elif key == "品番":
                        match_real = cls.PTN_SEARCH_REAL_NO.match(value_text_all)
                        if match_real:
                            label = match_real.group("real").upper()
                            if entity.tag is None: entity.tag = []
                            if label not in entity.tag: entity.tag.append(label)
                        # videoa 에서는 품번으로 title 설정 안 함 (보통 제목이 따로 있으므로)
                    elif key == "平均評価": # videoa 평점 (v_new 방식)
                        rating_img = value_td.xpath('.//img/@src')
                        if rating_img:
                            # videoa 평점은 /45.gif 형태가 아닐 수 있음 -> v_old PTN_RATING 사용 시 주의
                            # v_new 방식에서는 5점 만점 변환 로직이 있었음. 필요시 적용
                            # 여기서는 일단 v_old 방식대로 적용 (값 나누기 제외)
                            match_rate = cls.PTN_RATING.search(rating_img[0]) # v_old 패턴 사용
                            if match_rate:
                                rate_str = match_rate.group("rating").replace("_",".")
                                try:
                                    rate_val = float(rate_str)
                                    # videoa는 5점 만점 기준일 수 있음. 값 범위 확인 필요
                                    if 0 <= rate_val <= 50: # 0~50 범위 가정
                                        rate_val /= 10.0 # 5점 만점으로 변환
                                    if 0 <= rate_val <= 5:
                                        img_url = "https:" + rating_img[0] if rating_img[0].startswith("//") else rating_img[0]
                                        entity.ratings = [EntityRatings(rate_val, max=5, name="dmm", image_url=img_url)]
                                except ValueError: logger.warning(f"Rating conv err (videoa): {rate_str}")

                final_premiered = premiered_shouhin or premiered_haishin # videoa 날짜 우선순위?
                if final_premiered: entity.premiered = final_premiered; entity.year = int(final_premiered[:4]) if final_premiered else None
                else: logger.warning("Premiered date not found for videoa."); entity.premiered = None; entity.year = None

                # 줄거리 파싱 (v_new - XPath 검증 필요)
                plot_xpath = '//div[@class="mg-b20 lh4"]/text()'
                plot_nodes = tree.xpath(plot_xpath)
                if plot_nodes:
                    plot_text = "\n".join([p.strip() for p in plot_nodes if p.strip()]).split("※")[0].strip()
                    if plot_text: entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                else: logger.warning(f"Plot not found using XPath: {plot_xpath}")

                # 예고편 처리 (v_new AJAX 방식 유지, 검증 필요)
                entity.extras = []
                if use_extras:
                    # (v_new의 AJAX/Iframe 예고편 로직 삽입)
                    logger.debug(f"Attempting to extract trailer for videoa {code} via AJAX/Iframe")
                    try:
                        trailer_url = None
                        trailer_title = entity.title if entity.title and entity.title != code[2:].upper() else code

                        ajax_url = py_urllib_parse.urljoin(cls.site_base_url, f"/digital/videoa/-/detail/ajax-movie/=/cid={code[2:]}/")
                        logger.debug(f"Trailer Step 1: Requesting AJAX URL: {ajax_url}")
                        ajax_headers = cls._get_request_headers(referer=detail_url)
                        ajax_headers['Accept'] = 'text/html, */*; q=0.01'; ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                        ajax_response = SiteUtil.get_response(ajax_url, proxy_url=proxy_url, headers=ajax_headers)

                        if not (ajax_response and ajax_response.status_code == 200):
                            logger.warning(f"Trailer Step 1 Failed: AJAX request failed (Status: {ajax_response.status_code if ajax_response else 'No Resp'})")
                            raise Exception("AJAX request failed")
                        ajax_html_text = ajax_response.text; logger.debug("Trailer Step 1 Success: AJAX response received.")

                        iframe_tree = html.fromstring(ajax_html_text)
                        iframe_srcs = iframe_tree.xpath("//iframe/@src")
                        if not iframe_srcs: logger.warning("Trailer Step 2 Failed: No iframe found in AJAX response."); raise Exception("Iframe not found")
                        iframe_url = py_urllib_parse.urljoin(ajax_url, iframe_srcs[0]); logger.debug(f"Trailer Step 2 Success: Found iframe URL: {iframe_url}")

                        logger.debug(f"Trailer Step 3: Requesting Player Page URL: {iframe_url}")
                        player_headers = cls._get_request_headers(referer=ajax_url)
                        player_response_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=player_headers)
                        if not player_response_text: logger.warning(f"Trailer Step 3 Failed: Empty content from Player Page: {iframe_url}"); raise Exception("Failed to get player page content")
                        logger.debug(f"Trailer Step 3 Success: Player page content received.")

                        logger.debug("Trailer Step 4: Parsing 'const args' JSON...")
                        pos = player_response_text.find("const args = {")
                        if pos != -1:
                            json_start = player_response_text.find("{", pos); json_end = player_response_text.find("};", json_start)
                            if json_start != -1 and json_end != -1:
                                data_str = player_response_text[json_start : json_end+1]
                                try:
                                    data = json.loads(data_str)
                                    bitrates = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                    if bitrates:
                                        trailer_src = bitrates[0].get("src")
                                        if trailer_src:
                                            trailer_url = "https:" + trailer_src if trailer_src.startswith("//") else trailer_src
                                            if data.get("title"): trailer_title = data.get("title").strip()
                                            logger.info(f"Trailer URL found via JSON: {trailer_url}")
                                    else: logger.warning("'bitrates' array found in JSON, but it's empty.")
                                except json.JSONDecodeError as je: logger.warning(f"Failed to decode 'const args' JSON: {je}")
                            else: logger.warning("Could not find end '};' for 'const args' JSON.")
                        else: logger.warning("'const args' pattern not found in player page HTML.")

                        if trailer_url:
                            entity.extras.append(EntityExtra("trailer", SiteUtil.trans(trailer_title, do_trans=do_trans), "mp4", trailer_url))
                            logger.info(f"Trailer added successfully for {code}")
                        else: logger.error(f"Failed to extract trailer URL for {code}.")
                    except Exception as extra_e: logger.exception(f"Error processing trailer for videoa {code}: {extra_e}")

            except Exception as e_parse_videoa:
                logger.exception(f"Error parsing videoa metadata: {e_parse_videoa}")


        elif content_type == 'dvd':
            # --- dvd 메타데이터 파싱 로직 (v_old 코드 기반) ---
            logger.debug("Parsing 'dvd' metadata using v_old logic...")
            try:
                # --- 이미지 처리 로직은 이미 위에서 완료됨 ---

                # --- Tagline / Title 처리 (v_old) ---
                title_xpath = '//h1[@id="title"]' # v_old XPath
                title_tags = tree.xpath(title_xpath)
                title_text = None
                if title_tags:
                    h1_full_text = title_tags[0].text_content().strip()
                    # v_old 에는 span 제거 로직 있었음
                    span_text_nodes = title_tags[0].xpath('./span[contains(@class, "txt_before-sale")]/text()')
                    span_text = "".join(span_text_nodes).strip()
                    title_text = h1_full_text.replace(span_text, "").strip() if span_text else h1_full_text
                    if title_text:
                        entity.tagline = SiteUtil.trans(title_text, do_trans=do_trans).replace("[배달 전용]", "").replace("[특가]", "").strip()
                        logger.debug(f"Tagline set from h1 title (v_old): {entity.tagline}")
                    else: logger.warning("Could not extract text from h1#title (v_old).")
                else: logger.warning("h1#title tag not found (v_old).")
                # entity.title 은 아래 품번에서 설정됨

                # --- 정보 테이블 파싱 (v_old) ---
                info_table_xpath = '//div[@class="wrapper-product"]//table//tr' # v_old XPath
                tags = tree.xpath(info_table_xpath)
                premiered_shouhin = None; premiered_hatsubai = None; premiered_haishin = None

                for tag in tags:
                    td_tags = tag.xpath(".//td")
                    # 평점 행 처리 (v_old)
                    if td_tags and len(td_tags)==2 and "平均評価：" in td_tags[0].text_content():
                        rating_img_xpath = './/img/@src'
                        rating_img_tags = td_tags[1].xpath(rating_img_xpath)
                        if rating_img_tags:
                            match_rating = cls.PTN_RATING.search(rating_img_tags[0]) # v_old 패턴
                            if match_rating:
                                rating_value_str = match_rating.group("rating").replace("_", ".")
                                try:
                                    # --- 평점 값 10으로 나누기 (v_old) ---
                                    rating_value = float(rating_value_str) / 10.0
                                    rating_img_url = "https:" + rating_img_tags[0] if not rating_img_tags[0].startswith("http") else rating_img_tags[0]
                                    if 0 <= rating_value <= 5:
                                        # 이전에 리뷰 섹션에서 파싱했을 수 있으므로, 없으면 추가
                                        if not entity.ratings:
                                            entity.ratings = [EntityRatings(rating_value, max=5, name="dmm", image_url=rating_img_url)]
                                            logger.debug(f"Rating found from table (v_old - raw: {rating_value_str}, adjusted: {rating_value})")
                                        else: # 이미 있으면 값 업데이트 시도 (선택적)
                                            entity.ratings[0].value = rating_value
                                            entity.ratings[0].image_url = rating_img_url # 이미지 URL도 업데이트
                                            logger.debug(f"Rating updated from table (v_old - raw: {rating_value_str}, adjusted: {rating_value})")
                                    else: logger.warning(f"Parsed rating value {rating_value} is out of range (0-5) (v_old).")
                                except ValueError: logger.warning(f"Could not convert rating value to float: {rating_value_str} (v_old)")
                            else: logger.warning(f"Could not parse rating from image src: {rating_img_tags[0]} (v_old)")
                        continue # 다음 행으로

                    # 일반 정보 행 처리 (v_old)
                    if len(td_tags) != 2: continue
                    key = td_tags[0].text_content().strip()
                    value_node = td_tags[1]
                    value_text_all = value_node.text_content().strip()
                    if value_text_all == "----" or not value_text_all: continue

                    if key == "商品発売日：": premiered_shouhin = value_text_all.replace("/", "-"); logger.debug(f"Found 商品発売日 (v_old): {premiered_shouhin}")
                    elif key == "発売日：": premiered_hatsubai = value_text_all.replace("/", "-"); logger.debug(f"Found 発売日 (v_old): {premiered_hatsubai}")
                    elif key == "配信開始日：": premiered_haishin = value_text_all.replace("/", "-"); logger.debug(f"Found 配信開始日 (v_old): {premiered_haishin}")
                    elif key == "収録時間：":
                        match_runtime = re.search(r"(\d+)", value_text_all)
                        if match_runtime: entity.runtime = int(match_runtime.group(1)); logger.debug(f"Runtime (v_old): {entity.runtime}")
                    elif key == "出演者：":
                        entity.actor = []
                        a_tags = value_node.xpath(".//a")
                        for a_tag in a_tags:
                            actor_name = a_tag.text_content().strip()
                            if actor_name and actor_name != "▼すべて表示する": entity.actor.append(EntityActor(actor_name))
                        logger.debug(f"Actors (v_old): {[a.originalname for a in entity.actor]}")
                    elif key == "監督：":
                        a_tags = value_node.xpath(".//a"); entity.director = a_tags[0].text_content().strip() if a_tags else value_text_all; logger.debug(f"Director (v_old): {entity.director}")
                    elif key == "シリーズ：":
                        if entity.tag is None: entity.tag = []
                        a_tags = value_node.xpath(".//a"); series_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                        if series_name: entity.tag.append(SiteUtil.trans(series_name, do_trans=do_trans)); logger.debug(f"Series tags (v_old): {entity.tag}")
                    elif key == "メーカー：":
                        if entity.studio is None: # 레이블 정보가 우선
                            a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                            entity.studio = SiteUtil.trans(studio_name, do_trans=do_trans); logger.debug(f"Studio (from Maker) (v_old): {entity.studio}")
                    elif key == "レーベル：": # 레이블 정보가 더 정확한 스튜디오일 가능성 높음
                        a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                        if do_trans: entity.studio = SiteUtil.av_studio.get(studio_name, SiteUtil.trans(studio_name)) # Studio 매핑 시도
                        else: entity.studio = studio_name
                        logger.debug(f"Studio (from Label) (v_old): {entity.studio}")
                    elif key == "ジャンル：":
                        entity.genre = []
                        a_tags = value_node.xpath(".//a")
                        for tag_a in a_tags:
                            genre_ja = tag_a.text_content().strip()
                            if "％OFF" in genre_ja or not genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                        logger.debug(f"Genres (v_old): {entity.genre}")
                    elif key == "品番：": # 품번으로 title 설정 (v_old)
                        value = value_text_all
                        match_id = cls.PTN_ID.search(value); id_before = None
                        if match_id: id_before = match_id.group(0); value = value.lower().replace(id_before, "zzid")
                        match_real = cls.PTN_SEARCH_REAL_NO.match(value); formatted_title = value_text_all.upper()
                        if match_real:
                            label = match_real.group("real").upper()
                            if id_before is not None: label = label.replace("ZZID", id_before.upper())
                            formatted_title = label + "-" + str(int(match_real.group("no"))).zfill(3)
                            if entity.tag is None: entity.tag = []
                            if label not in entity.tag: entity.tag.append(label) # 레이블 태그 추가
                        entity.title = entity.originaltitle = entity.sorttitle = formatted_title
                        logger.debug(f"Title set from 品番 (v_old): {entity.title}")
                        # 태그라인 없으면 품번 제목으로 대체 (v_old + v_new)
                        if entity.tagline is None: entity.tagline = entity.title

                # 최종 날짜 설정 (v_old 우선순위)
                final_premiered = None
                if premiered_shouhin: final_premiered = premiered_shouhin; logger.debug("Using 商品発売日 for premiered date (v_old).")
                elif premiered_hatsubai: final_premiered = premiered_hatsubai; logger.debug("Using 発売日 for premiered date (v_old).")
                elif premiered_haishin: final_premiered = premiered_haishin; logger.debug("Using 配信開始日 for premiered date (v_old).")
                else: logger.warning("No premiered date found (v_old).")
                if final_premiered:
                    entity.premiered = final_premiered
                    try: entity.year = int(final_premiered[:4])
                    except ValueError: logger.warning(f"Could not parse year (v_old): {final_premiered}"); entity.year = None
                else: entity.premiered = None; entity.year = None

                # --- 줄거리 파싱 (v_old) ---
                plot_xpath = '//div[@class="mg-b20 lh4"]/p[@class="mg-b20"]/text()' # v_old XPath
                plot_tags = tree.xpath(plot_xpath)
                if plot_tags:
                    plot_text = "\n".join([p.strip() for p in plot_tags if p.strip()]).split("※")[0].strip()
                    entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                    logger.debug(f"Plot found (v_old): {entity.plot[:50]}...")
                else: logger.warning("Plot not found (v_old).")

                # --- 리뷰 섹션 평점 업데이트 (v_old) ---
                review_section_xpath = '//div[@id="review_anchor"]' # v_old XPath
                review_sections = tree.xpath(review_section_xpath)
                if review_sections:
                    review_section = review_sections[0]
                    try:
                        # 리뷰 평균 점수 (v_old)
                        avg_rating_xpath = './/div[@class="dcd-review__points"]/p[@class="dcd-review__average"]/strong/text()'
                        avg_rating_tags = review_section.xpath(avg_rating_xpath)
                        if avg_rating_tags:
                            avg_rating_str = avg_rating_tags[0].strip()
                            try:
                                avg_rating_value = float(avg_rating_str)
                                if 0 <= avg_rating_value <= 5: # 5점 만점 기준
                                    if entity.ratings: entity.ratings[0].value = avg_rating_value
                                    else: entity.ratings = [EntityRatings(avg_rating_value, max=5, name="dmm")] # 없으면 새로 생성
                                    logger.debug(f"Updated rating value from review (v_old): {avg_rating_value}")
                                else: logger.warning(f"Review rating value {avg_rating_value} out of range (0-5).")
                            except ValueError: logger.warning(f"Could not convert review rating to float: {avg_rating_str}")

                        # 리뷰 개수 (v_old)
                        votes_xpath = './/div[@class="dcd-review__points"]/p[@class="dcd-review__evaluates"]/strong/text()'
                        votes_tags = review_section.xpath(votes_xpath)
                        if votes_tags:
                            votes_str = votes_tags[0].strip(); match_votes = re.search(r"(\d+)", votes_str)
                            if match_votes:
                                votes_value = int(match_votes.group(1))
                                if entity.ratings: entity.ratings[0].votes = votes_value; logger.debug(f"Updated rating votes from review (v_old): {votes_value}")
                                elif avg_rating_tags: # 평점은 있는데 votes만 없을 경우 votes 추가
                                    entity.ratings[0].votes = votes_value; logger.debug(f"Added rating votes from review (v_old): {votes_value}")
                    except Exception as rating_update_e: logger.exception(f"Error updating rating details from review (v_old): {rating_update_e}")
                else: logger.warning("Review section not found (v_old).")

                # --- 예고편 처리 (v_old) ---
                entity.extras = []
                if use_extras:
                    try:
                        trailer_url = None; trailer_title_from_data = None
                        # AJAX 시도 (v_old)
                        ajax_url_xpath = '//a[@id="sample-video1"]/@data-video-url' # v_old XPath
                        ajax_url_tags = tree.xpath(ajax_url_xpath)
                        if ajax_url_tags:
                            ajax_relative_url = ajax_url_tags[0]
                            ajax_full_url = py_urllib_parse.urljoin(detail_url, ajax_relative_url) # detail_url 기준
                            logger.debug(f"Attempting trailer AJAX request (v_old): {ajax_full_url}")
                            try:
                                ajax_headers = cls._get_request_headers(referer=detail_url); ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                                ajax_response_text = SiteUtil.get_text(ajax_full_url, proxy_url=proxy_url, headers=ajax_headers)
                                # --- lxml 임포트 확인 (v_old 참조) ---
                                try: from lxml import html
                                except ImportError: logger.error("lxml library required for trailer parsing."); raise

                                ajax_tree = html.fromstring(ajax_response_text)
                                iframe_srcs = ajax_tree.xpath("//iframe/@src")
                                if iframe_srcs:
                                    iframe_url = py_urllib_parse.urljoin(ajax_full_url, iframe_srcs[0])
                                    iframe_headers = cls._get_request_headers(referer=ajax_full_url)
                                    iframe_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=iframe_headers)
                                    pos = iframe_text.find("const args = {")
                                    if pos != -1:
                                        json_start = iframe_text.find("{", pos); json_end = iframe_text.find("};", json_start)
                                        if json_start != -1 and json_end != -1:
                                            data_str = iframe_text[json_start : json_end+1]
                                            try:
                                                data = json.loads(data_str)
                                                data["bitrates"] = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                                if data.get("bitrates"):
                                                    trailer_src = data["bitrates"][0].get("src")
                                                    if trailer_src:
                                                        trailer_url = "https:" + trailer_src if not trailer_src.startswith("http") else trailer_src
                                                        trailer_title_from_data = data.get("title")
                                                        logger.debug(f"Trailer URL found from AJAX iframe (v_old): {trailer_url}")
                                                        if trailer_title_from_data: logger.debug(f"Trailer title found from DMM data (v_old): {trailer_title_from_data}")
                                            except json.JSONDecodeError as je: logger.warning(f"Failed to decode JSON from iframe (v_old): {data_str} - Error: {je}")
                                    else: logger.warning("Could not find 'const args = {' in iframe content (v_old).")
                                else: logger.warning("Could not find iframe src in AJAX response (v_old).")
                            except Exception as ajax_e: logger.exception(f"Error during trailer AJAX request (v_old): {ajax_e}")
                        else: logger.warning("data-video-url attribute not found for trailer AJAX (v_old).")

                        # onclick 파싱 시도 (v_old fallback)
                        if not trailer_url:
                            onclick_xpath = '//a[@id="sample-video1"]/@onclick' # v_old XPath
                            onclick_tags = tree.xpath(onclick_xpath)
                            if onclick_tags:
                                onclick_text = onclick_tags[0]
                                match_json = re.search(r"gaEventVideoStart\('(\{.*?\})','(\{.*?\})'\)", onclick_text)
                                if match_json:
                                    video_data_str = match_json.group(1)
                                    try:
                                        # v_old는 json.loads 바로 사용
                                        video_data = json.loads(video_data_str.replace('\\"', '"')) # 백슬래시 이스케이프 처리
                                        if video_data.get("video_url"):
                                            trailer_url = video_data["video_url"]
                                            logger.debug(f"Trailer URL found from onclick (v_old fallback): {trailer_url}")
                                    except Exception as json_e: logger.warning(f"Failed to parse JSON from onclick (v_old fallback): {json_e}")

                        # EntityExtra 추가 (v_old logic)
                        if trailer_url:
                            if trailer_title_from_data and trailer_title_from_data.strip(): trailer_title_to_use = trailer_title_from_data.strip()
                            elif entity.title: trailer_title_to_use = entity.title
                            else: trailer_title_to_use = "Trailer" # 기본 제목
                            entity.extras.append(EntityExtra("trailer", SiteUtil.trans(trailer_title_to_use, do_trans=do_trans), "mp4", trailer_url))
                            logger.debug(f"Added trailer (v_old) with title: '{trailer_title_to_use}' and URL: {trailer_url}")
                        else: logger.warning("No trailer URL found (v_old).")

                    except Exception as extra_e: logger.exception(f"미리보기 처리 중 예외 (v_old): {extra_e}")

            except Exception as e_parse_dvd:
                logger.exception(f"Error parsing dvd metadata (v_old logic block): {e_parse_dvd}")

        else: # 타입 불명
            logger.error(f"Cannot parse info: Unknown content type '{content_type}' for {code}")

        # --- 공통 후처리: 최종 제목 설정 (v_new 방식 사용) ---
        # 이미 품번 기반 제목 설정은 dvd 블록에서 처리됨
        # videoa는 제목이 보통 따로 있으므로 품번으로 덮어쓰지 않음
        # 최종 UI 코드 기반 제목 설정 필요 시 여기에 추가 가능
        # 예: if not entity.title: entity.title = final_ui_code
        if not entity.tagline and entity.title: entity.tagline = entity.title # 최종 태그라인 fallback

        logger.debug(f"Final Parsed Entity: Title='{entity.title}', Tagline='{entity.tagline}', Thumb={len(entity.thumb) if entity.thumb else 0}, Fanart={len(entity.fanart)}, Actors={len(entity.actor) if entity.actor else 0}")
        return entity

    @classmethod
    def info(cls, code, **kwargs):
        """ (v_new + v_old 통합) """
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            # __info에서 entity 반환 보장 (실패 시 빈 EntityMovie 또는 None)
            if entity: # entity 객체가 반환되었는지 확인
                ret["ret"] = "success"
                ret["data"] = entity.as_dict()
            else: # __info 내부에서 None 반환 등 실패 처리 시
                ret["ret"] = "error"
                ret["data"] = f"Failed to get info entity for {code}"
        except Exception as exception:
            # __info 내부에서 raise된 예외 처리
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        return ret
