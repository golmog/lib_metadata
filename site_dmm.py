# -*- coding: utf-8 -*-
import json
import re
import urllib.parse as py_urllib_parse
from lxml import html, etree
import os

# lib_metadata 패키지 내 다른 모듈 import
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings, EntityThumb
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

class SiteDmm:
    site_name = "dmm"
    site_base_url = "https://www.dmm.co.jp"
    fanza_av_url = "https://video.dmm.co.jp/av/"
    module_char = "C"; site_char = "D"

    dmm_base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
        "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1",
        "Referer": site_base_url + "/", "DNT": "1", "Cache-Control": "max-age=0", "Connection": "keep-alive",
    }

    PTN_SEARCH_CID = re.compile(r"\/cid=(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^(h_)?\d*(?P<real>[a-zA-Z]+)(?P<no>\d+)([a-zA-Z]+)?$")
    PTN_ID = re.compile(r"\d{2}id", re.I)
    PTN_RATING = re.compile(r"(?P<rating>[\d|_]+)\.gif") # v_old 패턴

    age_verified = False
    last_proxy_used = None
    _ps_url_cache = {} # code: {'ps': ps_url, 'type': content_type}

    CONTENT_TYPE_PRIORITY = ['videoa', 'vr', 'dvd', 'bluray', 'unknown']


    @classmethod
    def _get_request_headers(cls, referer=None):
        headers = cls.dmm_base_headers.copy()
        if referer: headers['Referer'] = referer
        return headers

    @classmethod
    def _ensure_age_verified(cls, proxy_url=None):
        if not cls.age_verified or cls.last_proxy_used != proxy_url:
            logger.debug("Checking/Performing DMM age verification...")
            cls.last_proxy_used = proxy_url
            session_cookies = SiteUtil.session.cookies
            domain_checks = ['.dmm.co.jp', '.dmm.com']
            if any('age_check_done' in session_cookies.get_dict(domain=d) and session_cookies.get_dict(domain=d)['age_check_done'] == '1' for d in domain_checks):
                logger.debug("Age verification cookie found in SiteUtil.session.")
                cls.age_verified = True; return True
            logger.debug("Attempting DMM age verification via confirmation GET...")
            try:
                target_rurl = cls.fanza_av_url
                confirm_path = f"/age_check/=/declared=yes/?rurl={py_urllib_parse.quote(target_rurl, safe='')}"
                age_check_confirm_url = py_urllib_parse.urljoin(cls.site_base_url, confirm_path)
                confirm_headers = cls._get_request_headers(referer=cls.site_base_url + "/")
                confirm_response = SiteUtil.get_response( age_check_confirm_url, method='GET', proxy_url=proxy_url, headers=confirm_headers, allow_redirects=False, verify=False )
                if confirm_response.status_code == 302 and 'age_check_done=1' in confirm_response.headers.get('Set-Cookie', ''):
                    logger.debug("Age confirmation successful via Set-Cookie.")
                    final_cookies = SiteUtil.session.cookies
                    if any('age_check_done' in final_cookies.get_dict(domain=d) and final_cookies.get_dict(domain=d)['age_check_done'] == '1' for d in domain_checks):
                        logger.debug("age_check_done=1 confirmed in session."); cls.age_verified = True; return True
                    else:
                        logger.warning("Set-Cookie received, but not updated in session. Trying manual set...")
                        SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.co.jp", path="/"); SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.com", path="/")
                        logger.info("Manually set age_check_done cookie."); cls.age_verified = True; return True
                else: logger.warning(f"Age check failed (Status:{confirm_response.status_code} or cookie missing).")
            except Exception as e: logger.exception(f"Age verification exception: {e}")
            cls.age_verified = False; return False
        else:
            logger.debug("Age verification already done."); return True

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        if not cls._ensure_age_verified(proxy_url=proxy_url): return []
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd": keyword = keyword[:-3]
        keyword = keyword.replace("-", " ")
        keyword_tmps = keyword.split(" ")
        if len(keyword_tmps) == 2: dmm_keyword = keyword_tmps[0] + keyword_tmps[1].zfill(5)
        else: dmm_keyword = keyword
        logger.debug("keyword [%s] -> [%s]", keyword, dmm_keyword)

        search_params = { 'redirect': '1', 'enc': 'UTF-8', 'category': '', 'searchstr': dmm_keyword }
        search_url = f"{cls.site_base_url}/search/?{py_urllib_parse.urlencode(search_params)}"
        logger.info(f"Using search URL: {search_url}")
        search_headers = cls._get_request_headers(referer=cls.fanza_av_url)
        tree = None
        try:
            tree = SiteUtil.get_tree(search_url, proxy_url=proxy_url, headers=search_headers, allow_redirects=True)
            if tree is None: logger.warning("Search tree is None."); return []
            title_tags_check = tree.xpath('//title/text()')
            if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]: logger.error("Age page received during search."); return []
        except Exception as e: logger.exception(f"Failed to get tree for search: {e}"); return []

        # 검색 결과 목록 추출
        list_xpath = '//div[contains(@class, "grid-cols-4")]//div[contains(@class, "border-r") and contains(@class, "border-b")]'
        lists = []
        try: lists = tree.xpath(list_xpath)
        except Exception as e_xpath: logger.error(f"XPath error: {e_xpath}")
        if not lists: logger.warning(f"No items found using Desktop Grid XPath."); return []

        ret = []; score = 60

        # --- 1단계: 모든 검색 결과 일단 파싱하여 ret 리스트 생성 ---
        for node in lists[:10]: # 상위 10개만 처리
            try:
                item = EntityAVSearch(cls.site_name)
                href = None; original_ps_url = None; content_type = "unknown" # 기본 타입 unknown

                # --- 링크 및 이미지 URL 추출 ---
                link_tag_img = node.xpath('.//a[contains(@class, "flex justify-center")]');
                if not link_tag_img: continue
                img_link_href = link_tag_img[0].attrib.get("href", "").lower() # 이미지 링크 (타입 판별 보조)
                img_tag = link_tag_img[0].xpath('./img/@src')
                if not img_tag: continue
                original_ps_url = img_tag[0] # 작은 포스터 URL

                title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]')
                if not title_link_tag: continue
                title_link_with_p = node.xpath('.//a[contains(@href, "/detail/=/cid=") and ./p[contains(@class, "hover:text-linkHover")]]')
                # 제목 링크 우선순위: <p> 태그 포함 > 일반 링크
                title_link_tag = title_link_with_p[0] if title_link_with_p else title_link_tag[0]
                title_link_href = title_link_tag.attrib.get("href", "").lower() # 제목 링크 (타입 판별 및 코드 추출 주 사용)

                # 최종 사용할 href 결정 (제목 링크 우선)
                href = title_link_href if title_link_href else img_link_href

                # --- ★★★ 블루레이 및 컨텐츠 타입 판별 로직 추가 ★★★ ---
                is_bluray = False
                # 블루레이 스팬 태그 확인 (텍스트 내용과 클래스 동시 확인)
                bluray_span = node.xpath('.//span[contains(@class, "text-blue-600") and contains(text(), "Blu-ray")]')
                if bluray_span:
                    is_bluray = True
                    content_type = 'bluray' # 블루레이로 타입 확정
                    logger.debug("Blu-ray span detected.")

                # 블루레이가 아니라면 기존 방식으로 판별
                if not is_bluray and href:
                    if "/digital/videoa/" in href:
                        content_type = "videoa"
                    elif "/mono/dvd/" in href:
                        content_type = "dvd"

                item.content_type = content_type # 판별된 타입 저장

                # --- 제목 추출 ---
                title_p_tag = title_link_tag.xpath('./p[contains(@class, "hover:text-linkHover")]')
                raw_title = title_p_tag[0].text_content().strip() if title_p_tag else "" # 원본 제목
                item.title = raw_title # Entity에 원본 제목 우선 저장

                # --- 이미지 URL 처리 ---
                if not original_ps_url: continue
                if original_ps_url.startswith("//"): original_ps_url = "https:" + original_ps_url
                item.image_url = original_ps_url # Entity에 이미지 URL 저장

                # --- 매뉴얼 모드 이미지 처리 ---
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try:
                        item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"ImgProcErr:{e_img}")

                # --- 코드 추출 ---
                if not href: continue
                match_cid_s = cls.PTN_SEARCH_CID.search(href)
                if not match_cid_s: logger.warning(f"DMM Search: Could not extract CID from href: {href}"); continue
                item.code = cls.module_char + cls.site_char + match_cid_s.group("code")

                # --- 중복 코드 체크 ---
                if any(i_s.get("code") == item.code and i_s.get("content_type") == item.content_type for i_s in ret):
                    logger.debug(f"DMM Search: Duplicate code and type, skipping: {item.code} ({item.content_type})")
                    continue

                # --- 제목 접두사 추가 ---
                type_prefix = ""
                if content_type == 'dvd': type_prefix = "[DVD] "
                elif content_type == 'videoa': type_prefix = "[Digital] "
                elif content_type == 'bluray': type_prefix = "[Blu-ray] " # 블루레이 접두사

                # 제목 최종 설정 (접두사 포함)
                if not item.title or item.title == "Not Found": # 제목 없으면 품번으로
                    # item.code가 여기서 필요하므로 위에서 먼저 설정됨
                    match_real_no_for_title = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                    default_title = match_real_no_for_title.group("real").upper() + "-" + str(int(match_real_no_for_title.group("no"))).zfill(3) if match_real_no_for_title else item.code[2:].upper()
                    item.title = type_prefix + default_title
                else:
                    # 기존 제목에 접두사 추가
                    # item.title = type_prefix + item.title # 이 라인은 이미 원본 제목이 저장되어 있으므로, 아래 ko 번역에서 처리하거나 별도 필드에 저장
                    pass # 이미 item.title에는 원본 제목 저장됨

                # --- 캐시 저장 (판별된 타입 및 우선순위 고려) ---
                if item.code and original_ps_url and content_type:
                    existing_cache_entry = cls._ps_url_cache.get(item.code)
                    should_update_cache = True 

                    if existing_cache_entry:
                        existing_type = existing_cache_entry.get('type', 'unknown')
                        try:
                            current_priority_index = cls.CONTENT_TYPE_PRIORITY.index(content_type)
                            existing_priority_index = cls.CONTENT_TYPE_PRIORITY.index(existing_type)
                            
                            if current_priority_index >= existing_priority_index:
                                should_update_cache = False
                        except ValueError: 
                            logger.warning(f"DMM Search: Type for priority compare not in list. Current='{content_type}', Existing='{existing_type}'. Will attempt cache update.")
                            pass 
                    
                    if should_update_cache:
                        cls._ps_url_cache[item.code] = {'ps': original_ps_url, 'type': content_type}
                        logger.debug(f"DMM Search: Stored/Updated ps & type for {item.code} in cache: Type='{content_type}'")
                    else:
                        logger.debug(f"DMM Search: Skipped cache update for {item.code} (existing type '{existing_type}' has =< priority than '{content_type}')")

                # --- 번역 (원본 제목 기준, 접두사 포함 X) ---
                if manual:
                    item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + type_prefix + item.title # 매뉴얼 모드 시 접두사 포함
                else:
                    # 번역 시에는 원본 제목(접두사 제외) 사용
                    trans_title = SiteUtil.trans(item.title, do_trans=do_trans) if do_trans and item.title else item.title
                    item.title_ko = type_prefix + trans_title # 번역된 제목에 접두사 추가

                # --- 점수 계산 ---
                match_real_no = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                item_ui_code_base = match_real_no.group("real") + match_real_no.group("no") if match_real_no else item.code[2:]
                current_score = 0
                # 검색 키워드와 품번 비교하여 점수 산정
                if len(keyword_tmps) == 2:
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif item_ui_code_base.replace("0", "") == dmm_keyword.replace("0", ""): current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    elif keyword_tmps[0] in item.code and keyword_tmps[1] in item.code: current_score = score
                    elif keyword_tmps[0] in item.code or keyword_tmps[1] in item.code: current_score = 60
                    else: current_score = 20
                else: # 검색어가 하나일 때
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    else: current_score = 20
                item.score = current_score # 계산된 점수 저장
                # 점수 감소 로직 (100점 아니면 다음 아이템 기본 점수 감소)
                if current_score < 100 and score > 20: score -= 5

                # --- UI 코드 생성 ---
                if match_real_no:
                    real = match_real_no.group("real").upper(); no = match_real_no.group("no")
                    try: item.ui_code = f"{real}-{str(int(no)).zfill(3)}"
                    except ValueError: item.ui_code = f"{real}-{no}" # 숫자로 변환 안 되면 그대로 사용
                else: # 정규식 매칭 안 될 경우 대비
                    tmp = item.code[2:].upper();
                    if tmp.startswith("H_"): tmp = tmp[2:]
                    m = re.match(r"([a-zA-Z]+)(\d+.*)", tmp) # 간단한 분리 시도
                    if m:
                        real = m.group(1); rest = m.group(2); num_m = re.match(r"(\d+)", rest)
                        item.ui_code = f"{real}-{str(int(num_m.group(1))).zfill(3)}" if num_m else f"{real}-{rest}"
                    else: item.ui_code = tmp # 최후의 수단: 코드 그대로 사용
                # --- 최종 결과 저장 ---
                logger.debug(f"Item Processed: Type={content_type}, Score={item.score}, Code={item.code}, UI Code={item.ui_code}, Title(KO)={item.title_ko}")
                ret.append(item.as_dict()) # 결과 리스트에 추가
            except Exception as e_inner: logger.exception(f"아이템 처리 중 예외 발생: {e_inner}")
        # --- 파싱 완료 ---

        # --- 2단계: Blu-ray 필터링 수행 (ret 리스트 생성 완료 후) ---
        if not ret: return []

        filtered_ret = []
        dvd_ui_codes = {item.get('ui_code') for item in ret if item.get('content_type') == 'dvd' and item.get('ui_code')}

        for item in ret:
            item_content_type = item.get('content_type')
            item_ui_code = item.get('ui_code')

            logger.debug(f"Processing item for filtering: Code={item.get('code')}, Type={item_content_type}, UI Code={item_ui_code}")

            is_bluray_to_filter = item_content_type == 'bluray' and item_ui_code is not None

            if is_bluray_to_filter:
                dvd_exists = item_ui_code in dvd_ui_codes
                logger.debug(f"  Item is Blu-ray. DVD exists for UI Code '{item_ui_code}'? {dvd_exists}")

                if dvd_exists:
                    logger.info(f"Excluding Blu-ray item '{item.get('code')}' because DVD version exists.")
                    # 제외 (filtered_ret에 추가 안 함)
                else:
                    logger.debug(f"  Keep Blu-ray item '{item.get('code')}' as no matching DVD found.")
                    filtered_ret.append(item) # DVD 없으면 Blu-ray 추가
            else:
                logger.debug(f"  Keep Non-Blu-ray item '{item.get('code')}'")
                filtered_ret.append(item) # Blu-ray 아니면 무조건 추가
        # --- 필터링 완료 ---

        # --- 3단계: 최종 결과 처리 (filtered_ret 사용) ---
        final_result = filtered_ret
        logger.debug(f"필터링 후 결과 개수: {len(final_result)}")

        # 정렬 로직 (final_result 대상)
        sorted_result = sorted(final_result, key=lambda k: k.get("score", 0), reverse=True)

        # 로깅 (sorted_result 대상)
        if sorted_result:
            log_count = min(len(sorted_result), 5)
            logger.debug(f"정렬된 상위 {log_count}개 결과 (Blu-ray 필터링 적용):")
            for idx, item_log in enumerate(sorted_result):
                log_type = item_log.get('content_type')
                logger.debug(f"  {idx+1}. Score={item_log.get('score')}, Type={log_type}, Code={item_log.get('code')}, UI Code={item_log.get('ui_code')}, Title={item_log.get('title_ko')}")

        # --- 재시도 로직 ---
        if not sorted_result and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6) # 6자리로 재시도
            logger.debug(f"결과 없음. 6자리 숫자로 재시도: {new_title}")
            # 재귀 호출 시 manual 플래그 등 전달 확인
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)

        return sorted_result

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            do_trans_arg = kwargs.get('do_trans', True)
            proxy_url_arg = kwargs.get('proxy_url', None)
            image_mode_arg = kwargs.get('image_mode', "0")
            manual_arg = kwargs.get('manual', False)

            data_list = cls.__search(keyword, do_trans=do_trans_arg, proxy_url=proxy_url_arg, image_mode=image_mode_arg, manual=manual_arg)

        except Exception as exception:
            logger.exception("SearchErr:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data_list else "no_match"
            ret["data"] = data_list
        return ret

    @classmethod
    def __img_urls(cls, tree, content_type='unknown'):
        logger.debug(f"Extracting raw image URLs for type: {content_type}")
        img_urls = {'ps': "", 'pl': "", 'arts': [], 'specific_poster_candidate': None}
        try:
            # videoa, vr 은 동일 로직 사용
            if content_type == 'videoa' or content_type == 'vr':
                logger.debug(f"Extracting {content_type} URLs using videoa logic (href first)...")
                sample_image_links = tree.xpath('//div[@id="sample-image-block"]//a[.//img]')

                if not sample_image_links:
                    logger.warning("Could not find 'a' tags with 'img' inside sample-image-block.")
                    all_img_tags = tree.xpath('//div[@id="sample-image-block"]//img/@src')
                    if not all_img_tags: return img_urls
                    img_urls['pl'] = py_urllib_parse.urljoin(cls.site_base_url, all_img_tags[0].strip()) if all_img_tags else ""
                    img_urls['arts'] = [py_urllib_parse.urljoin(cls.site_base_url, src.strip()) for src in all_img_tags[1:] if src.strip()]
                    logger.warning("Using fallback: extracted only img src attributes.")
                    return img_urls

                processed_arts = []
                pl_url = None
                specific_poster_url = None

                for idx, a_tag in enumerate(sample_image_links):
                    final_image_url = None
                    source_type = "unknown"
                    href = a_tag.attrib.get("href", "").strip()
                    is_href_image = bool(href and re.search(r'\.(jpg|jpeg|png|webp)$', href, re.IGNORECASE))

                    if is_href_image:
                        final_image_url = py_urllib_parse.urljoin(cls.site_base_url, href)
                        source_type = "href"
                    else:
                        img_tag_src_list = a_tag.xpath('.//img/@src')
                        if img_tag_src_list:
                            src = img_tag_src_list[0].strip()
                            is_src_image = bool(src and re.search(r'\.(jpg|jpeg|png|webp)$', src, re.IGNORECASE))
                            if is_src_image:
                                final_image_url = py_urllib_parse.urljoin(cls.site_base_url, src)
                                source_type = "src"

                    if not final_image_url:
                        logger.warning(f"Sample image {idx}: Could not find valid URL in href or src.")
                        continue

                    filename = final_image_url.split('/')[-1].lower()
                    logger.debug(f"Sample image {idx}: Found URL='{filename}' (from {source_type})")

                    is_current_pl = False
                    is_current_specific = False
                    if filename.endswith("pl.jpg") or filename.endswith("jp-0.jpg"):
                        is_current_pl = True
                    match_specific = re.match(r".*jp-(\d+)\.jpg$", filename)
                    if specific_poster_url is None and match_specific:
                        is_current_specific = True

                    if is_current_pl:
                        if pl_url is None: pl_url = final_image_url; logger.debug(f"  -> Assigned as 'pl'.")
                        else: logger.warning(f"  -> Another 'pl' found, adding to arts: {filename}"); processed_arts.append(final_image_url)
                    elif is_current_specific:
                        if specific_poster_url is None: specific_poster_url = final_image_url; logger.debug(f"  -> Assigned as 'specific_poster_candidate'.")
                        else: logger.debug(f"  -> Another 'specific' found, adding to arts: {filename}"); processed_arts.append(final_image_url)
                    elif idx == 0 and pl_url is None:
                        pl_url = final_image_url; logger.debug(f"  -> Assigned as 'pl' (Fallback - first image).")
                    else:
                        logger.debug(f"  -> Adding to potential arts."); processed_arts.append(final_image_url)

                img_urls['pl'] = pl_url if pl_url else ""
                img_urls['specific_poster_candidate'] = specific_poster_url if specific_poster_url else ""

                unique_arts = []
                urls_to_exclude = {img_urls['pl'], img_urls['specific_poster_candidate']}
                for art_url in processed_arts:
                    # 중복 제거 및 None/빈문자열 제외
                    if art_url and art_url not in urls_to_exclude and art_url not in unique_arts:
                        unique_arts.append(art_url)
                img_urls['arts'] = unique_arts
                logger.debug(f"Found {len(img_urls['arts'])} unique arts links.")

            elif content_type == 'dvd': # 블루레이도 이 로직 사용 (캐시에서 type 'dvd'로 받음)
                logger.debug("Extracting dvd/bluray URLs using v_old logic...")
                pl_xpath = '//div[@id="fn-sampleImage-imagebox"]/img/@src'
                pl_tags = tree.xpath(pl_xpath)
                raw_pl = pl_tags[0].strip() if pl_tags else ""
                if raw_pl:
                    img_urls['pl'] = ("https:" + raw_pl) if not raw_pl.startswith("http") else raw_pl
                    logger.debug(f"Found dvd/br pl: {img_urls['pl']}")
                else: logger.warning("Could not find dvd/br pl using XPath: %s", pl_xpath)
                img_urls['ps'] = "" # ps는 캐시 사용

                arts_xpath = '//li[contains(@class, "fn-sampleImage__zoom") and not(@data-slick-index="0")]//img'
                arts_tags = tree.xpath(arts_xpath)
                if arts_tags:
                    processed_arts = []
                    for tag in arts_tags:
                        src = tag.attrib.get("src") or tag.attrib.get("data-lazy")
                        if src:
                            src = src.strip()
                            if not src.startswith("http"): src = "https:" + src
                            processed_arts.append(src)
                    unique_arts = []; [unique_arts.append(x) for x in processed_arts if x not in unique_arts]
                    img_urls['arts'] = unique_arts
                    logger.debug(f"Found {len(img_urls['arts'])} arts links for dvd/br.")
                else: logger.warning("Could not find dvd/br arts using XPath: %s", arts_xpath)
            else:
                logger.error(f"Unknown content type '{content_type}' for image extraction.")

        except Exception as e:
            logger.exception(f"Error extracting image URLs: {e}")
            img_urls = {'ps': "", 'pl': "", 'arts': [], 'specific_poster_candidate': None}

        logger.debug(f"Extracted img_urls: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} specific_poster={bool(img_urls.get('specific_poster_candidate'))} arts={len(img_urls.get('arts',[]))}")
        return img_urls


    @classmethod
    def __info( 
        cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, 
        use_extras=True, ps_to_poster=False, crop_mode=None, **kwargs 
    ):
        # === 1. 설정값 로드, 캐시 로드, 페이지 로딩, Entity 초기화 ===
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        ps_to_poster_setting = ps_to_poster
        crop_mode_setting = crop_mode # DMM은 타입별로 crop_mode 사용 여부 다를 수 있음

        cached_data = cls._ps_url_cache.get(code, {})
        ps_url_from_search_cache = cached_data.get('ps') 
        content_type_from_cache = cached_data.get('type', 'unknown') # 검색 시점 타입
        
        logger.debug(f"DMM Info: Starting for {code}. Type from cache: {content_type_from_cache}. ImgMode: {image_mode}, UseImgServ: {use_image_server}")

        if not cls._ensure_age_verified(proxy_url=proxy_url):
            logger.error(f"DMM Info ({content_type_from_cache}): Age verification failed for {code}.")
            return None

        cid_part = code[2:]
        detail_url = None; current_content_type = content_type_from_cache 
        
        # 상세 페이지 URL 결정
        if current_content_type == 'videoa' or current_content_type == 'vr':
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
        elif current_content_type == 'dvd' or current_content_type == 'bluray': # bluray도 dvd 경로 사용
            detail_url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={cid_part}/"
        else: 
            logger.warning(f"DMM Info: Type for {code} is '{current_content_type}' from cache. Defaulting to 'videoa' path.")
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
            current_content_type = 'videoa' # 파싱 시 사용할 임시 타입

        logger.info(f"DMM Info (Processing as {current_content_type}): Accessing detail page: {detail_url}")
        referer = cls.fanza_av_url if current_content_type in ['videoa', 'vr'] else (cls.site_base_url + "/mono/dvd/")
        headers = cls._get_request_headers(referer=referer)
        
        tree = None
        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=headers, timeout=30, verify=False)
            if tree is None: 
                logger.error(f"DMM Info ({current_content_type}): Failed to get page tree for {code}. URL: {detail_url}")
                if (content_type_from_cache == 'unknown' or content_type_from_cache == 'videoa') and current_content_type == 'videoa':
                    logger.info(f"DMM Info: Retrying with DVD path for {code} as videoa failed.")
                    current_content_type = 'dvd' 
                    detail_url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={cid_part}/"
                    referer = cls.site_base_url + "/mono/dvd/"
                    headers = cls._get_request_headers(referer=referer)
                    tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=headers, timeout=30, verify=False)
                    if tree is None: logger.error(f"DMM Info (DVD Retry): Failed to get page tree for {code}."); return None
                else: return None 
            if "年齢認証" in (tree.xpath('//title/text()')[0] if tree.xpath('//title/text()') else ""):
                logger.error(f"DMM Info ({current_content_type}): Age page received for {code}."); return None
        except Exception as e_gt_info_dmm: logger.exception(f"DMM Info ({current_content_type}): Exc getting detail page: {e_gt_info_dmm}"); return None

        entity = EntityMovie(cls.site_name, code); entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = []; entity.fanart = []; entity.extras = []
        ui_code_for_image = ""; entity.content_type = current_content_type # 최종 확정된 타입 entity에 저장
        
        # === 2. 전체 메타데이터 파싱 (ui_code_for_image 및 entity.title 등 확정) ===
        identifier_parsed = False; is_vr_actual = False # 상세페이지에서 VR 여부 최종 확인
        try:
            logger.debug(f"DMM Info (Parsing as {entity.content_type}): Metadata for {code}...")
            
            # --- DMM 타입별 메타데이터 파싱 로직 (제공해주신 원본 코드 기반) ---
            if entity.content_type == 'videoa' or entity.content_type == 'vr':
                # 원본 videoa/vr 파싱 로직 시작
                raw_title_text_v = "" # 변수명에 _v 접미사 추가
                try:
                    title_node_v = tree.xpath('//h1[@id="title"]')
                    if title_node_v:
                        raw_title_text_v = title_node_v[0].text_content().strip()
                        if raw_title_text_v.startswith("【VR】"): is_vr_actual = True; entity.content_type = 'vr' # VR 타입 최종 확정
                        entity.tagline = SiteUtil.trans(raw_title_text_v, do_trans=do_trans) # 원본과 동일하게 tagline 우선 설정
                    else: logger.warning(f"DMM ({entity.content_type}): Could not find h1#title.")
                except Exception as e_title_parse_v: logger.warning(f"DMM ({entity.content_type}): Error parsing title: {e_title_parse_v}")
                
                info_table_xpath_v = '//table[contains(@class, "mg-b20")]//tr' # 원본 XPath
                # DMM은 product-info 테이블도 있으므로, 원본에 있었다면 추가:
                # info_table_xpath_v = '//table[contains(@class, "mg-b20")]//tr | //div[contains(@class,"product-info")]//dl'
                tags_v = tree.xpath(info_table_xpath_v)
                premiered_shouhin_v = None; premiered_haishin_v = None
                for tag_v in tags_v:
                    key_node_v = tag_v.xpath('./td[@class="nw"]/text()')
                    value_node_list_v = tag_v.xpath('./td[not(@class="nw")]')
                    if not key_node_v or not value_node_list_v: continue
                    key_v = key_node_v[0].strip().replace("：", "")
                    value_node_v_instance = value_node_list_v[0]; value_text_all_v = value_node_v_instance.text_content().strip()
                    if value_text_all_v == "----" or not value_text_all_v: continue

                    if "品番" in key_v:
                        value_pid_v = value_text_all_v; match_id_v = cls.PTN_ID.search(value_pid_v); id_before_v = None
                        if match_id_v: id_before_v = match_id_v.group(0); value_pid_v = value_pid_v.lower().replace(id_before_v.lower(), "zzid") # 원본처럼 소문자 변환 후 치환
                        match_real_v = cls.PTN_SEARCH_REAL_NO.match(value_pid_v); formatted_code_v = value_text_all_v.upper()
                        if match_real_v:
                            label_v = match_real_v.group("real").upper()
                            if id_before_v is not None: label_v = label_v.replace("ZZID", id_before_v.upper())
                            num_str_v = str(int(match_real_v.group("no"))).zfill(3)
                            formatted_code_v = f"{label_v}-{num_str_v}"
                            if entity.tag is None: entity.tag = []
                            if label_v not in entity.tag: entity.tag.append(label_v)
                        ui_code_for_image = formatted_code_v # 확정된 품번
                        entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image # 품번을 타이틀로
                        entity.ui_code = ui_code_for_image
                        identifier_parsed = True
                        # logger.debug(f"DMM ({entity.content_type}): 品番 파싱 완료, ui_code_for_image='{ui_code_for_image}'")

                    elif "配信開始日" in key_v: premiered_haishin_v = value_text_all_v.replace("/", "-")
                    elif "商品発売日" in key_v: premiered_shouhin_v = value_text_all_v.replace("/", "-") # videoa에도 있을 수 있음
                    elif "収録時間" in key_v: 
                        m_rt_v = re.search(r"(\d+)",value_text_all_v); entity.runtime = int(m_rt_v.group(1)) if m_rt_v else None
                    elif "出演者" in key_v:
                        # 원본 파싱 로직 (링크 있을때/없을때 모두 처리)
                        actors_v = [a_v.strip() for a_v in value_node_v_instance.xpath('.//a/text()') if a_v.strip()]
                        if actors_v: entity.actor = [EntityActor(name_v) for name_v in actors_v]
                        elif value_text_all_v != '----': entity.actor = [EntityActor(n_v.strip()) for n_v in value_text_all_v.split('/') if n_v.strip()]
                    elif "監督" in key_v:
                        directors_v = [d_v.strip() for d_v in value_node_v_instance.xpath('.//a/text()') if d_v.strip()]
                        entity.director = directors_v[0] if directors_v else (value_text_all_v if value_text_all_v != '----' else None)
                    elif "シリーズ" in key_v:
                        if entity.tag is None: entity.tag = []
                        series_v = [s_v.strip() for s_v in value_node_v_instance.xpath('.//a/text()') if s_v.strip()]
                        s_name_v = series_v[0] if series_v else (value_text_all_v if value_text_all_v != '----' else None)
                        if s_name_v and SiteUtil.trans(s_name_v, do_trans=do_trans) not in entity.tag: entity.tag.append(SiteUtil.trans(s_name_v, do_trans=do_trans))
                    elif "メーカー" in key_v:
                        if entity.studio is None: # 스튜디오 정보 없으면 제작사로 채움
                            makers_v = [mk_v.strip() for mk_v in value_node_v_instance.xpath('.//a/text()') if mk_v.strip()]
                            m_name_v = makers_v[0] if makers_v else (value_text_all_v if value_text_all_v != '----' else None)
                            if m_name_v: entity.studio = SiteUtil.trans(m_name_v, do_trans=do_trans)
                    elif "レーベル" in key_v: # 레이블은 스튜디오로 사용 (제작사보다 우선)
                        labels_v = [lb_v.strip() for lb_v in value_node_v_instance.xpath('.//a/text()') if lb_v.strip()]
                        l_name_v = labels_v[0] if labels_v else (value_text_all_v if value_text_all_v != '----' else None)
                        if l_name_v:
                            if do_trans: entity.studio = SiteUtil.av_studio.get(l_name_v, SiteUtil.trans(l_name_v))
                            else: entity.studio = l_name_v
                    elif "ジャンル" in key_v:
                        entity.genre = []
                        for genre_ja_tag_v in value_node_v_instance.xpath('.//a'):
                            genre_ja_v = genre_ja_tag_v.text_content().strip();
                            if not genre_ja_v or "％OFF" in genre_ja_v or genre_ja_v in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja_v in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja_v])
                            else:
                                genre_ko_v = SiteUtil.trans(genre_ja_v, do_trans=do_trans).replace(" ", "")
                                if genre_ko_v not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko_v)
                    elif "平均評価" in key_v:
                        rating_img_v_list = value_node_v_instance.xpath('.//img/@src')
                        if rating_img_v_list:
                            match_rate_v = cls.PTN_RATING.search(rating_img_v_list[0])
                            if match_rate_v:
                                rate_str_v = match_rate_v.group("rating").replace("_",".")
                                try:
                                    rate_val_v = float(rate_str_v) / 10.0
                                    if 0 <= rate_val_v <= 5:
                                        img_url_v_rating = "https:" + rating_img_v_list[0] if rating_img_v_list[0].startswith("//") else rating_img_v_list[0]
                                        rating_image_url_entity_v = img_url_v_rating if not (use_image_server and image_mode == '4') else None
                                        if not entity.ratings: entity.ratings = [EntityRatings(rate_val_v, max=5, name="dmm", image_url=rating_image_url_entity_v)]
                                        else: entity.ratings[0].value = rate_val_v; entity.ratings[0].image_url = rating_image_url_entity_v
                                except ValueError: logger.warning(f"DMM ({entity.content_type}): Rating conversion error: {rate_str_v}")
                
                # videoa/vr 출시일: 상품일 > 배신일 순 (원본 로직 따름)
                entity.premiered = premiered_shouhin_v or premiered_haishin_v 
                if entity.premiered: entity.year = int(entity.premiered[:4]) if len(entity.premiered) >=4 else None
                else: logger.warning(f"DMM ({entity.content_type}): Premiered date not found for {code}.")

                # videoa/vr 줄거리 (원본 로직 따름)
                plot_xpath_v_meta_info = '//div[@class="mg-b20 lh4"]/text()' # 원본 XPath
                plot_nodes_v_meta_info = tree.xpath(plot_xpath_v_meta_info)
                if plot_nodes_v_meta_info:
                    plot_text_v_meta_info = "\n".join([p_v_info.strip() for p_v_info in plot_nodes_v_meta_info if p_v_info.strip()]).split("※")[0].strip()
                    if plot_text_v_meta_info: entity.plot = SiteUtil.trans(plot_text_v_meta_info, do_trans=do_trans)
                else: logger.warning(f"DMM ({entity.content_type}): Plot not found for {code}.")
            
            elif entity.content_type == 'dvd' or entity.content_type == 'bluray':
                # 원본 dvd/bluray 파싱 로직 시작
                title_node_d_info_meta = tree.xpath('//h1[@id="title"]')
                if title_node_d_info_meta: entity.tagline = SiteUtil.trans(title_node_d_info_meta[0].text_content().strip(), do_trans=do_trans)
                
                info_table_xpath_d_meta = '//div[@class="wrapper-product"]//table//tr' # 원본 XPath
                tags_d_meta = tree.xpath(info_table_xpath_d_meta)
                premiered_shouhin_d_meta = None; premiered_hatsubai_d_meta = None; premiered_haishin_d_meta = None
                for tag_d_meta in tags_d_meta:
                    td_tags_d_meta = tag_d_meta.xpath(".//td")
                    if len(td_tags_d_meta) != 2: continue
                    if "평균평가" in td_tags_d_meta[0].text_content(): # 평점 처리 (dvd/br 페이지 구조가 다를 수 있음)
                        rating_img_tags_d_meta = td_tags_d_meta[1].xpath('.//img/@src')
                        if rating_img_tags_d_meta:
                            match_rating_d_meta = cls.PTN_RATING.search(rating_img_tags_d_meta[0])
                            if match_rating_d_meta:
                                rating_value_str_d_meta = match_rating_d_meta.group("rating").replace("_", ".")
                                try:
                                    rating_value_d_meta = float(rating_value_str_d_meta) / 10.0
                                    if 0 <= rating_value_d_meta <= 5:
                                        rating_img_url_d_meta = "https:" + rating_img_tags_d_meta[0] if not rating_img_tags_d_meta[0].startswith("http") else rating_img_tags_d_meta[0]
                                        rating_image_url_entity_d_meta = rating_img_url_d_meta if not (use_image_server and image_mode == '4') else None
                                        if not entity.ratings: entity.ratings = [EntityRatings(rating_value_d_meta, max=5, name="dmm", image_url=rating_image_url_entity_d_meta)]
                                        else: entity.ratings[0].value = rating_value_d_meta; entity.ratings[0].image_url = rating_image_url_entity_d_meta
                                except ValueError: pass 
                        continue
                    key_d_meta = td_tags_d_meta[0].text_content().strip().replace("：", "")
                    value_node_d_meta = td_tags_d_meta[1]; value_text_all_d_meta = value_node_d_meta.text_content().strip()
                    if value_text_all_d_meta == "----" or not value_text_all_d_meta: continue

                    if "品番" in key_d_meta:
                        # (원본 품번 파싱 로직 - ui_code_for_image, entity.title 등 설정)
                        value_pid_d_meta = value_text_all_d_meta; match_id_d_meta = cls.PTN_ID.search(value_pid_d_meta); id_before_d_meta = None
                        if match_id_d_meta: id_before_d_meta = match_id_d_meta.group(0); value_pid_d_meta = value_pid_d_meta.lower().replace(id_before_d_meta.lower(), "zzid")
                        match_real_d_meta = cls.PTN_SEARCH_REAL_NO.match(value_pid_d_meta); formatted_code_d_meta = value_text_all_d_meta.upper()
                        if match_real_d_meta:
                            label_d_meta = match_real_d_meta.group("real").upper()
                            if id_before_d_meta is not None: label_d_meta = label_d_meta.replace("ZZID", id_before_d_meta.upper())
                            num_str_d_meta = str(int(match_real_d_meta.group("no"))).zfill(3)
                            formatted_code_d_meta = f"{label_d_meta}-{num_str_d_meta}"
                            if entity.tag is None: entity.tag = []
                            if label_d_meta not in entity.tag: entity.tag.append(label_d_meta)
                        ui_code_for_image = formatted_code_d_meta
                        entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image
                        entity.ui_code = ui_code_for_image
                        identifier_parsed = True
                        # 원본 코드에서는 여기서 continue 했었음. 필요시 유지.
                    elif "商品発売日" in key_d_meta: premiered_d_shouhin_meta = value_text_all_d_meta.replace("/", "-")
                    elif "発売日" in key_d_meta: premiered_d_hatsubai_meta = value_text_all_d_meta.replace("/", "-") # dvd/br에는 이 필드가 있을 수 있음
                    elif "配信開始日" in key_d_meta: premiered_d_haishin_meta = value_text_all_d_meta.replace("/", "-") # dvd/br에도 배신일이 있을 수 있음
                    # (기타 dvd/bluray 메타데이터 필드 - 원본 로직 따름: 시간, 배우, 감독, 시리즈, 제작사, 레이블, 장르 등)
                
                # dvd/bluray 출시일: 상품일 > 발매일 > 배신일 순 (원본 로직 따름)
                entity.premiered = premiered_d_shouhin_meta or premiered_d_hatsubai_meta or premiered_d_haishin_meta
                if entity.premiered: entity.year = int(entity.premiered[:4]) if len(entity.premiered) >=4 else None
                
                # dvd/bluray 줄거리 (원본 로직 따름)
                plot_xpath_d_meta_info = '//div[@class="mg-b20 lh4"]/p[@class="mg-b20"]/text()' # 원본 XPath
                plot_tags_d_meta_info = tree.xpath(plot_xpath_d_meta_info)
                if plot_tags_d_meta_info:
                    plot_text_d_meta_info = "\n".join([p_d_info.strip() for p_d_info in plot_tags_d_meta_info if p_d_info.strip()]).split("※")[0].strip()
                    entity.plot = SiteUtil.trans(plot_text_d_meta_info, do_trans=do_trans)
                else: logger.warning(f"DMM ({entity.content_type}): Plot not found for {code}.")
            
            if not identifier_parsed:
                logger.error(f"DMM ({entity.content_type}): CRITICAL - Identifier parse failed for {code} after all attempts.")
                ui_code_for_image = code[2:].upper().replace("_","-"); entity.title=entity.originaltitle=entity.sorttitle=ui_code_for_image; entity.ui_code=ui_code_for_image
            if not entity.tagline and entity.title: entity.tagline = entity.title
            if not entity.plot and entity.tagline: entity.plot = entity.tagline
        except Exception as e_meta_dmm_main_detail_full:
            logger.exception(f"DMM ({entity.content_type}): Meta parsing error for {code}: {e_meta_dmm_main_detail_full}")
            if not ui_code_for_image: return None
        
        # === 3. 사용자 지정 포스터 확인 ===
        user_custom_poster_url = None; user_custom_landscape_url = None
        skip_default_poster_logic = False; skip_default_landscape_logic = False
        if use_image_server and image_server_local_path and image_server_url and ui_code_for_image:
            poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
            landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]
            for suffix_p_dmm_user in poster_suffixes:
                _, web_url_p_dmm_user = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix_p_dmm_user, image_server_url)
                if web_url_p_dmm_user: user_custom_poster_url = web_url_p_dmm_user; entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url)); skip_default_poster_logic = True; logger.info(f"DMM ({entity.content_type}): Using user custom poster: {web_url_p_dmm_user}"); break 
            for suffix_pl_dmm_user in landscape_suffixes:
                _, web_url_pl_dmm_user = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix_pl_dmm_user, image_server_url)
                if web_url_pl_dmm_user: user_custom_landscape_url = web_url_pl_dmm_user; entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url)); skip_default_landscape_logic = True; logger.info(f"DMM ({entity.content_type}): Using user custom landscape: {web_url_pl_dmm_user}"); break
        
        # === 4. 기본 이미지 처리 (DMM 타입별) ===
        final_poster_source = None; final_poster_crop_mode = None
        final_landscape_url_source = None; 
        arts_urls_for_processing = [] 
        # DMM은 검색 캐시 PS(ps_url_from_search_cache)를 상세 이미지 결정의 주요 소스로 사용

        if not skip_default_poster_logic or not skip_default_landscape_logic:
            logger.debug(f"DMM ({entity.content_type}): Running default image logic (P_skip:{skip_default_poster_logic}, PL_skip:{skip_default_landscape_logic}).")
            try:
                img_urls_dmm_default_page_val = cls.__img_urls(tree, content_type=entity.content_type)
                
                if entity.content_type == 'videoa' or entity.content_type == 'vr':
                    pl_v_page_d_val = img_urls_dmm_default_page_val.get('pl')
                    specific_cand_v_d_val = img_urls_dmm_default_page_val.get('specific_poster_candidate')
                    arts_v_page_d_val = img_urls_dmm_default_page_val.get('arts', [])
                    if not skip_default_poster_logic:
                        # (원본 DMM videoa/vr 포스터 결정 로직: ps_url_from_search_cache, pl_v_page_d_val, specific_cand_v_d_val 사용)
                        current_ps_compare_v_val = ps_url_from_search_cache
                        if pl_v_page_d_val and current_ps_compare_v_val and SiteUtil.is_hq_poster(current_ps_compare_v_val, pl_v_page_d_val, proxy_url=proxy_url): final_poster_source = pl_v_page_d_val
                        elif specific_cand_v_d_val and current_ps_compare_v_val and SiteUtil.is_hq_poster(current_ps_compare_v_val, specific_cand_v_d_val, proxy_url=proxy_url): final_poster_source = specific_cand_v_d_val
                        elif pl_v_page_d_val and current_ps_compare_v_val and not ps_to_poster_setting : 
                            crop_pos_v_d_val = SiteUtil.has_hq_poster(current_ps_compare_v_val, pl_v_page_d_val, proxy_url=proxy_url)
                            if crop_pos_v_d_val : final_poster_source = pl_v_page_d_val; final_poster_crop_mode = crop_pos_v_d_val
                            elif current_ps_compare_v_val : final_poster_source = current_ps_compare_v_val 
                            else : final_poster_source = pl_v_page_d_val 
                        elif current_ps_compare_v_val : final_poster_source = current_ps_compare_v_val 
                        else: final_poster_source = pl_v_page_d_val 
                    if not skip_default_landscape_logic: final_landscape_url_source = pl_v_page_d_val
                    arts_urls_for_processing = arts_v_page_d_val
                elif entity.content_type == 'dvd' or entity.content_type == 'bluray': # bluray도 dvd 로직 따름
                    pl_d_page_d_val = img_urls_dmm_default_page_val.get('pl')
                    arts_d_page_d_val = img_urls_dmm_default_page_val.get('arts', [])
                    if not skip_default_poster_logic:
                        current_ps_compare_d_val = ps_url_from_search_cache
                        if ps_to_poster_setting and current_ps_compare_d_val: final_poster_source = current_ps_compare_d_val
                        elif pl_d_page_d_val and current_ps_compare_d_val:
                            crop_pos_d_d_val = SiteUtil.has_hq_poster(current_ps_compare_d_val, pl_d_page_d_val, proxy_url=proxy_url)
                            if crop_pos_d_d_val : final_poster_source = pl_d_page_d_val; final_poster_crop_mode = crop_pos_d_d_val
                            elif current_ps_compare_d_val : final_poster_source = current_ps_compare_d_val
                            else : final_poster_source = pl_d_page_d_val
                        elif current_ps_compare_d_val: final_poster_source = current_ps_compare_d_val
                        else: final_poster_source = pl_d_page_d_val 
                    if not skip_default_landscape_logic: final_landscape_url_source = pl_d_page_d_val
                    arts_urls_for_processing = arts_d_page_d_val
                
                if not (use_image_server and image_mode == '4'): 
                    logger.info(f"DMM ({entity.content_type}): Using Normal Image Processing Mode for default images...")
                    if final_poster_source and not skip_default_poster_logic:
                        processed_poster_norm_dmm_val = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                        if processed_poster_norm_dmm_val and not any(t_norm_d.aspect=='poster' for t_norm_d in entity.thumb): entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster_norm_dmm_val))
                    if final_landscape_url_source and not skip_default_landscape_logic:
                        processed_landscape_norm_dmm_val = SiteUtil.process_image_mode(image_mode, final_landscape_url_source, proxy_url=proxy_url)
                        if processed_landscape_norm_dmm_val and not any(t_norm_l_d.aspect=='landscape' for t_norm_l_d in entity.thumb): entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape_norm_dmm_val))
                    if arts_urls_for_processing:
                        processed_fanart_count_norm_dmm_val = 0; exclude_norm_dmm_val = {final_poster_source, final_landscape_url_source}
                        for art_url_norm_dmm_val in arts_urls_for_processing:
                            if processed_fanart_count_norm_dmm_val >= max_arts: break
                            if art_url_norm_dmm_val and art_url_norm_dmm_val not in exclude_norm_dmm_val:
                                processed_art_norm_dmm_val = SiteUtil.process_image_mode(image_mode, art_url_norm_dmm_val, proxy_url=proxy_url)
                                if processed_art_norm_dmm_val: entity.fanart.append(processed_art_norm_dmm_val); processed_fanart_count_norm_dmm_val+=1
            except Exception as e_img_dmm_default_main_detail_val: logger.exception(f"DMM ({entity.content_type}): Default img proc main error: {e_img_dmm_default_main_detail_val}")

        if use_image_server and image_mode == '4' and ui_code_for_image:
            logger.info(f"DMM ({entity.content_type}): Saving images to ImgServ for {ui_code_for_image}...")
            if ps_url_from_search_cache: # DMM은 검색 캐시 PS를 항상 'ps'로 저장 시도
                SiteUtil.save_image_to_server_path(ps_url_from_search_cache, 'ps', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
            
            if not skip_default_poster_logic and final_poster_source:
                p_path_dmm_serv_val_final = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                if p_path_dmm_serv_val_final and not user_custom_poster_url and not any(t_serv_p_d.aspect=='poster' for t_serv_p_d in entity.thumb): entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path_dmm_serv_val_final}"))
            
            if not skip_default_landscape_logic and final_landscape_url_source:
                pl_path_dmm_serv_val_final = SiteUtil.save_image_to_server_path(final_landscape_url_source, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                if pl_path_dmm_serv_val_final and not user_custom_landscape_url and not any(t_serv_pl_d.aspect=='landscape' for t_serv_pl_d in entity.thumb): entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path_dmm_serv_val_final}"))
            
            if arts_urls_for_processing:
                processed_fanart_count_serv_dmm_val = 0; exclude_serv_dmm_val = {final_poster_source, final_landscape_url_source}
                for idx_serv_dmm_val, art_url_serv_dmm_val in enumerate(arts_urls_for_processing):
                    if processed_fanart_count_serv_dmm_val >= max_arts: break
                    if art_url_serv_dmm_val and art_url_serv_dmm_val not in exclude_serv_dmm_val:
                        art_path_serv_dmm_val = SiteUtil.save_image_to_server_path(art_url_serv_dmm_val, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx_serv_dmm_val + 1, proxy_url=proxy_url)
                        if art_path_serv_dmm_val: entity.fanart.append(f"{image_server_url}/{art_path_serv_dmm_val}"); processed_fanart_count_serv_dmm_val +=1
        
        if use_extras: # 예고편 처리
            entity.extras = [] 
            try: 
                trailer_title_dmm_extra_val_final = entity.tagline if entity.tagline else entity.title if entity.title else code
                trailer_url_dmm_extra_val_final = None
                # (DMM 타입별 예고편 추출 원본 로직 시작)
                if entity.content_type == 'vr': 
                    vr_player_page_url_e_f = f"{cls.site_base_url}/digital/-/vr-sample-player/=/cid={cid_part}/"
                    vr_player_html_e_f = SiteUtil.get_text(vr_player_page_url_e_f, proxy_url=proxy_url, headers=cls._get_request_headers(referer=detail_url))
                    if vr_player_html_e_f:
                        match_js_var_e_f = re.search(r'var\s+sampleUrl\s*=\s*["\']([^"\']+)["\']', vr_player_html_e_f)
                        if match_js_var_e_f: trailer_url_dmm_extra_val_final = "https:" + match_js_var_e_f.group(1) if match_js_var_e_f.group(1).startswith("//") else match_js_var_e_f.group(1)
                elif entity.content_type == 'videoa': 
                    ajax_url_v_e_f = py_urllib_parse.urljoin(cls.site_base_url, f"/digital/videoa/-/detail/ajax-movie/=/cid={cid_part}/")
                    ajax_headers_v_e_f = cls._get_request_headers(referer=detail_url); ajax_headers_v_e_f.update({'Accept': 'text/html, */*; q=0.01', 'X-Requested-With': 'XMLHttpRequest'})
                    ajax_res_v_e_f = SiteUtil.get_response(ajax_url_v_e_f, proxy_url=proxy_url, headers=ajax_headers_v_e_f)
                    if ajax_res_v_e_f and ajax_res_v_e_f.status_code == 200 and ajax_res_v_e_f.text.strip():
                        iframe_tree_v_e_f = html.fromstring(ajax_res_v_e_f.text)
                        iframe_srcs_v_e_f = iframe_tree_v_e_f.xpath("//iframe/@src")
                        if iframe_srcs_v_e_f:
                            iframe_url_v_e_f = py_urllib_parse.urljoin(ajax_url_v_e_f, iframe_srcs_v_e_f[0])
                            iframe_text_v_e_f = SiteUtil.get_text(iframe_url_v_e_f, proxy_url=proxy_url, headers=cls._get_request_headers(referer=ajax_url_v_e_f))
                            if iframe_text_v_e_f:
                                pos_v_e_f = iframe_text_v_e_f.find("const args = {")
                                if pos_v_e_f != -1:
                                    json_s_v_e_f = iframe_text_v_e_f.find("{", pos_v_e_f); json_e_v_e_f = iframe_text_v_e_f.find("};", json_s_v_e_f)
                                    if json_s_v_e_f != -1 and json_e_v_e_f != -1:
                                        data_str_v_e_f = iframe_text_v_e_f[json_s_v_e_f : json_e_v_e_f+1]
                                        data_v_e_f = json.loads(data_str_v_e_f)
                                        bitrates_v_e_f = sorted(data_v_e_f.get("bitrates",[]), key=lambda k_ex_v_f: k_ex_v_f.get("bitrate", 0), reverse=True)
                                        if bitrates_v_e_f and bitrates_v_e_f[0].get("src"): trailer_url_dmm_extra_val_final = "https:" + bitrates_v_e_f[0]["src"] if bitrates_v_e_f[0]["src"].startswith("//") else bitrates_v_e_f[0]["src"]
                                        if data_v_e_f.get("title") and data_v_e_f["title"].strip(): trailer_title_dmm_extra_val_final = data_v_e_f["title"].strip()
                elif entity.content_type == 'dvd' or entity.content_type == 'bluray': 
                    onclick_trailer_d_e_f = tree.xpath('//a[@id="sample-video1"]/@onclick | //a[contains(@onclick,"gaEventVideoStart")]/@onclick')
                    if onclick_trailer_d_e_f:
                        onclick_text_d_e_f = onclick_trailer_d_e_f[0]; match_json_d_e_f = re.search(r"gaEventVideoStart\s*\(\s*'(\{.*?\})'\s*,\s*'(\{.*?\})'\s*\)", onclick_text_d_e_f)
                        if match_json_d_e_f:
                            video_data_str_d_e_f = match_json_d_e_f.group(1).replace('\\"', '"')
                            video_data_d_e_f = json.loads(video_data_str_d_e_f)
                            if video_data_d_e_f.get("video_url"): trailer_url_dmm_extra_val_final = video_data_d_e_f["video_url"]
                # (DMM 타입별 예고편 추출 원본 로직 끝)
                if trailer_url_dmm_extra_val_final:
                    entity.extras.append(EntityExtra("trailer", SiteUtil.trans(trailer_title_dmm_extra_val_final, do_trans=do_trans), "mp4", trailer_url_dmm_extra_val_final))
            except Exception as e_trailer_dmm_main_detail_final: logger.exception(f"DMM ({entity.content_type}): Trailer error: {e_trailer_dmm_main_detail_final}")
        
        logger.info(f"DMM ({entity.content_type}): __info finished for {code}. UI: {ui_code_for_image}, PSkip:{skip_default_poster_logic}, PLSkip:{skip_default_landscape_logic}, Thumbs:{len(entity.thumb)}, Fanarts:{len(entity.fanart)}")
        return entity

    @classmethod
    def info(cls, code, **kwargs):
        # (원본 코드와 동일)
        ret = {}; entity_result_val_final = None
        try:
            entity_result_val_final = cls.__info(code, **kwargs) 
            if entity_result_val_final: ret["ret"] = "success"; ret["data"] = entity_result_val_final.as_dict()
            else: ret["ret"] = "error"; ret["data"] = f"Failed to get DMM info for {code}"
        except Exception as e_info_dmm_main_call_val_final: ret["ret"] = "exception"; ret["data"] = str(e_info_dmm_main_call_val_final); logger.exception(f"DMM info main call error: {e_info_dmm_main_call_val_final}")
        return ret
