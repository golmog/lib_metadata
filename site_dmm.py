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
    PTN_SEARCH_REAL_NO = re.compile(r"^([hn]_)?\d*(?P<real>[a-zA-Z]+)(?P<no>\d+)([a-zA-Z]+)?$")
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

        # --- 키워드 전처리 ---
        original_keyword_for_log = keyword
        keyword_processed = keyword.strip().lower()
        if keyword_processed[-3:-1] == "cd": keyword_processed = keyword_processed[:-3]

        # DMM 검색용 키워드 생성
        dmm_search_keyword_temp = keyword_processed.replace("-", " ")
        keyword_tmps_for_dmm = dmm_search_keyword_temp.split(" ")

        if len(keyword_tmps_for_dmm) == 2: 
            dmm_keyword_for_url = keyword_tmps_for_dmm[0] + keyword_tmps_for_dmm[1].zfill(5)
        else: 
            dmm_keyword_for_url = dmm_search_keyword_temp.replace(" ", "")

        logger.debug(f"DMM Search: original_keyword='{original_keyword_for_log}', dmm_keyword_for_url='{dmm_keyword_for_url}'")

        # --- 검색 URL 생성 ---
        search_params = { 'redirect': '1', 'enc': 'UTF-8', 'category': '', 'searchstr': dmm_keyword_for_url }
        search_url = f"{cls.site_base_url}/search/?{py_urllib_parse.urlencode(search_params)}"
        logger.debug(f"Using new search URL (v2): {search_url}")

        search_headers = cls._get_request_headers(referer=cls.fanza_av_url)
        tree = None
        try:
            tree = SiteUtil.get_tree(search_url, proxy_url=proxy_url, headers=search_headers, allow_redirects=True)
            if tree is None: 
                logger.warning(f"DMM Search: Search tree is None for '{original_keyword_for_log}'. URL: {search_url}")
                return []
            title_tags_check = tree.xpath('//title/text()')
            if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]: 
                logger.error(f"DMM Search: Age page received for '{original_keyword_for_log}'.")
                return []
        except Exception as e: 
            logger.exception(f"DMM Search: Failed to get tree for '{original_keyword_for_log}': {e}")
            return []

        # --- 검색 결과 목록 추출 XPath ---
        list_xpath_options = [
            '//div[contains(@class, "border-r") and contains(@class, "border-b") and contains(@class, "border-gray-300")]',
            '//div[contains(@class, "grid-cols-4")]//div[contains(@class, "border-r") and contains(@class, "border-b")]', # (Fallback)
        ]

        lists = []
        for xpath_expr in list_xpath_options:
            try:
                lists = tree.xpath(xpath_expr)
                if lists:
                    logger.debug(f"DMM Search: Found {len(lists)} item blocks using XPath: {xpath_expr}")
                    break
            except Exception as e_xpath: 
                logger.warning(f"DMM Search: XPath error with '{xpath_expr}' for '{original_keyword_for_log}': {e_xpath}")

        if not lists: 
            logger.debug(f"DMM Search: No item blocks found using any XPath for '{original_keyword_for_log}'.")
            # HTML 저장 로직
            #try:
            #    import os; from framework import path_data; import html as lxml_html
            #    debug_html_path = os.path.join(path_data, 'tmp', f'dmm_search_fail_{original_keyword_for_log.replace("/", "_")}.html')
            #    os.makedirs(os.path.join(path_data, 'tmp'), exist_ok=True)
            #    with open(debug_html_path, 'w', encoding='utf-8') as f:
            #        f.write(etree.tostring(tree, pretty_print=True, encoding='unicode'))
            #    logger.info(f"DMM Search HTML content saved to: {debug_html_path} due to no items found.")
            #except Exception as e_save_html: logger.error(f"DMM Search: Failed to save HTML for no items: {e_save_html}")
            return []

        ret = []; score = 60

        # --- 1단계: 모든 검색 결과 일단 파싱하여 ret 리스트 생성 ---
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                href = None; original_ps_url = None; content_type = "unknown" 

                # --- 링크 및 이미지 URL 추출 (새로운 HTML 구조에 맞게 node 내부에서 다시 XPath 적용) ---
                title_link_tags_in_node = node.xpath('.//a[.//p[contains(@class, "text-link")]]') 
                img_link_tags_in_node = node.xpath('.//a[./img[@alt="Product"]]') # alt="Product" 이미지를 가진 a

                primary_href_candidate = None
                # 제목 링크가 존재하고, 그 href에 cid가 있다면 우선 사용
                if title_link_tags_in_node and title_link_tags_in_node[0].attrib.get("href", "").lower().count('/cid=') > 0 :
                    primary_href_candidate = title_link_tags_in_node[0].attrib.get("href", "").lower()
                # 그렇지 않고 이미지 링크가 존재하고, 그 href에 cid가 있다면 사용
                elif img_link_tags_in_node and img_link_tags_in_node[0].attrib.get("href", "").lower().count('/cid=') > 0 :
                    primary_href_candidate = img_link_tags_in_node[0].attrib.get("href", "").lower()

                if not primary_href_candidate:
                    logger.debug("DMM Search Item: No primary link with cid found. Skipping.")
                    continue

                href = primary_href_candidate # 최종 href 할당 (경로 필터링에 사용)
                logger.debug(f"DMM Search Item: Determined href for path check: '{href}'")

                # 경로 필터링 (href 사용)
                try:
                    parsed_url = py_urllib_parse.urlparse(href)
                    path_from_url = parsed_url.path
                except Exception as e_url_parse_item_loop:
                    logger.error(f"DMM Search Item: Failed to parse href '{href}': {e_url_parse_item_loop}")
                    continue

                is_videoa_path = path_from_url.startswith("/digital/videoa/")
                is_dvd_path = path_from_url.startswith("/mono/dvd/")
                if not (is_videoa_path or is_dvd_path):
                    #logger.debug(f"DMM Search Item: Path ('{path_from_url}' from href '{href}') filtered. Skipping.")
                    continue

                # 작은 포스터(PS) URL 추출 (node 기준 상대 경로)
                ps_img_src_list = node.xpath('.//img[@alt="Product"]/@src')
                if ps_img_src_list:
                    original_ps_url = ps_img_src_list[0]
                    if original_ps_url.startswith("//"): original_ps_url = "https:" + original_ps_url

                if not original_ps_url: # PS 이미지가 없으면 아이템 처리 불가
                    logger.debug("DMM Search Item: No PS image found. Skipping.")
                    continue
                item.image_url = original_ps_url

                # content_type 결정
                is_bluray = False
                bluray_span = node.xpath('.//span[contains(@class, "text-blue-600") and contains(text(), "Blu-ray")]') # node 기준
                if bluray_span: is_bluray = True

                if is_bluray: content_type = 'bluray'
                elif is_videoa_path: content_type = "videoa"
                elif is_dvd_path: content_type = "dvd"
                item.content_type = content_type

                # 제목 추출 (node 기준 상대 경로)
                title_p_tags = node.xpath('.//p[contains(@class, "text-link") and contains(@class, "line-clamp-2")]')
                raw_title = title_p_tags[0].text_content().strip() if title_p_tags else ""
                item.title = raw_title

                # 매뉴얼 모드 이미지 처리
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try: item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"DMM Search: ImgProcErr (manual):{e_img}")

                # 코드 추출 (href 사용)
                match_cid_s = cls.PTN_SEARCH_CID.search(href) 
                if not match_cid_s: 
                    logger.warning(f"DMM Search Item: Could not extract CID from href '{href}'. Skipping.")
                    continue
                item.code = cls.module_char + cls.site_char + match_cid_s.group("code")

                # 중복 코드 체크
                if any(i_s.get("code") == item.code and i_s.get("content_type") == item.content_type for i_s in ret):
                    logger.debug(f"DMM Search Item: Duplicate code and type, skipping: {item.code} ({item.content_type})")
                    continue

                # 제목 접두사 추가
                type_prefix = ""
                if content_type == 'dvd': type_prefix = "[DVD] "
                elif content_type == 'videoa': type_prefix = "[Digital] "
                elif content_type == 'bluray': type_prefix = "[Blu-ray] "
                if not item.title or item.title == "Not Found":
                    match_real_no_for_title = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                    default_title = match_real_no_for_title.group("real").upper() + "-" + str(int(match_real_no_for_title.group("no"))).zfill(3) if match_real_no_for_title else item.code[2:].upper()
                    item.title = type_prefix + default_title
                
                # 캐시 저장
                if item.code and original_ps_url and content_type:
                    existing_cache_entry = cls._ps_url_cache.get(item.code)
                    should_update_cache = True 
                    if existing_cache_entry:
                        existing_type = existing_cache_entry.get('type', 'unknown')
                        try:
                            current_priority_index = cls.CONTENT_TYPE_PRIORITY.index(content_type)
                            existing_priority_index = cls.CONTENT_TYPE_PRIORITY.index(existing_type)
                            if current_priority_index >= existing_priority_index: should_update_cache = False
                        except ValueError: pass 
                    if should_update_cache: cls._ps_url_cache[item.code] = {'ps': original_ps_url, 'type': content_type}

                # 번역
                if manual:
                    item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + type_prefix + item.title
                else:
                    trans_title = SiteUtil.trans(item.title, do_trans=do_trans) if do_trans and item.title else item.title
                    item.title_ko = type_prefix + trans_title

                # 점수 계산
                item_label_part_from_item = ""
                item_number_part_from_item = ""

                source_for_item_parts = item.code[2:].lower()
                match_for_item_parts = cls.PTN_SEARCH_REAL_NO.search(source_for_item_parts)

                if match_for_item_parts:
                    item_label_part_from_item = match_for_item_parts.group("real")
                    item_number_part_from_item = match_for_item_parts.group("no")
                else:
                    temp_code_for_fallback_score = source_for_item_parts
                    m_fallback_score = re.match(r"([a-z]+)(\d+.*)", temp_code_for_fallback_score)
                    if m_fallback_score:
                        item_label_part_from_item = m_fallback_score.group(1)
                        num_match_fallback = re.match(r"(\d+)", m_fallback_score.group(2))
                        item_number_part_from_item = num_match_fallback.group(1) if num_match_fallback else ""
                    else:
                        item_label_part_from_item = temp_code_for_fallback_score
                    logger.warning(f"DMM Score: PTN_SEARCH_REAL_NO failed for item parts from '{source_for_item_parts}'. Fallback: label='{item_label_part_from_item}', num='{item_number_part_from_item}'")

                # 비교용 5자리 패딩된 아이템 코드 생성
                item_code_for_strict_compare = ""
                if item_label_part_from_item and item_number_part_from_item:
                    item_code_for_strict_compare = item_label_part_from_item + item_number_part_from_item.zfill(5)
                elif item_label_part_from_item:
                    item_code_for_strict_compare = item_label_part_from_item

                item_ui_code_base_for_score = ""
                if match_for_item_parts :
                    item_ui_code_base_for_score = match_for_item_parts.group("real") + match_for_item_parts.group("no")
                else:
                    item_ui_code_base_for_score = source_for_item_parts

                current_score_val = 0

                logger.debug(f"DMM Score Compare: dmm_keyword_for_url='{dmm_keyword_for_url}', item_code_for_strict_compare='{item_code_for_strict_compare}', item_ui_code_base_for_score='{item_ui_code_base_for_score}'")

                # 점수 부여 (원본 우선순위 복원)
                if dmm_keyword_for_url and item_code_for_strict_compare and dmm_keyword_for_url == item_code_for_strict_compare: 
                    current_score_val = 100
                elif item_ui_code_base_for_score == dmm_keyword_for_url:
                    current_score_val = 100
                elif item_ui_code_base_for_score.replace("0", "") == dmm_keyword_for_url.replace("0", ""): 
                    current_score_val = 80
                elif dmm_keyword_for_url in item_ui_code_base_for_score: 
                    current_score_val = score
                elif len(keyword_tmps_for_dmm) == 2 and keyword_tmps_for_dmm[0] in item.code.lower() and keyword_tmps_for_dmm[1] in item.code.lower(): 
                    current_score_val = score
                elif len(keyword_tmps_for_dmm) > 0 and \
                    (keyword_tmps_for_dmm[0] in item.code.lower() or \
                    (len(keyword_tmps_for_dmm) > 1 and keyword_tmps_for_dmm[1] in item.code.lower())): 
                    current_score_val = 60
                else: 
                    current_score_val = 20

                item.score = current_score_val
                if current_score_val < 100 and score > 20: score -= 5

                # UI 코드 생성
                match_real_no_ui = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                if match_real_no_ui:
                    real_ui = match_real_no_ui.group("real").upper(); no_ui = match_real_no_ui.group("no")
                    try: item.ui_code = f"{real_ui}-{str(int(no_ui)).zfill(3)}"
                    except ValueError: item.ui_code = f"{real_ui}-{no_ui}"
                else: 
                    tmp_ui = item.code[2:].upper();
                    if tmp_ui.startswith("H_"): tmp_ui = tmp_ui[2:]
                    m_ui_fallback = re.match(r"([a-zA-Z]+)(\d+.*)", tmp_ui) 
                    if m_ui_fallback:
                        real_f = m_ui_fallback.group(1); rest_f = m_ui_fallback.group(2); num_m_f = re.match(r"(\d+)", rest_f)
                        item.ui_code = f"{real_f}-{str(int(num_m_f.group(1))).zfill(3)}" if num_m_f else f"{real_f}-{rest_f}"
                    else: item.ui_code = tmp_ui

                logger.debug(f"DMM Item Processed: Type={item.content_type}, Score={item.score}, Code={item.code}, UI Code={item.ui_code}, Title(KO)='{item.title_ko}'")
                ret.append(item.as_dict())
            except Exception as e_inner_loop: 
                logger.exception(f"DMM Search: 아이템 처리 중 예외 발생 (original keyword: '{original_keyword_for_log}'): {e_inner_loop}")

        # --- 2단계: Blu-ray 필터링 ---
        if not ret: return []
        filtered_ret = []
        dvd_ui_codes = {item_filter.get('ui_code') for item_filter in ret if item_filter.get('content_type') == 'dvd' and item_filter.get('ui_code')}
        for item_to_check_bluray in ret:
            item_content_type_filter = item_to_check_bluray.get('content_type')
            item_ui_code_filter = item_to_check_bluray.get('ui_code')
            # logger.debug(f"Processing item for filtering: Code={item_to_check_bluray.get('code')}, Type={item_content_type_filter}, UI Code={item_ui_code_filter}") # 로그 레벨 조정 또는 기존 유지
            is_bluray_to_filter = item_content_type_filter == 'bluray' and item_ui_code_filter is not None
            if is_bluray_to_filter:
                dvd_exists = item_ui_code_filter in dvd_ui_codes
                # logger.debug(f"  Item is Blu-ray. DVD exists for UI Code '{item_ui_code_filter}'? {dvd_exists}")
                if dvd_exists: logger.info(f"Excluding Blu-ray item '{item_to_check_bluray.get('code')}' because DVD version exists.")
                else: filtered_ret.append(item_to_check_bluray) 
            else: filtered_ret.append(item_to_check_bluray)

        # --- 3단계: 최종 결과 처리 ---
        final_result = filtered_ret
        logger.debug(f"DMM Search: Filtered result count: {len(final_result)} for '{original_keyword_for_log}'")
        sorted_result = sorted(final_result, key=lambda k: k.get("score", 0), reverse=True)
        if sorted_result:
            log_count = min(len(sorted_result), 5)
            logger.debug(f"DMM Search: Top {log_count} results for '{original_keyword_for_log}':")
            for idx, item_log_final in enumerate(sorted_result[:log_count]):
                logger.debug(f"  {idx+1}. Score={item_log_final.get('score')}, Type={item_log_final.get('content_type')}, Code={item_log_final.get('code')}, UI Code={item_log_final.get('ui_code')}, Title='{item_log_final.get('title_ko')}'")

        # --- 재시도 로직 (keyword_tmps_for_dmm 사용) ---
        if not sorted_result and len(keyword_tmps_for_dmm) == 2 and len(keyword_tmps_for_dmm[1]) == 5: # 5자리 숫자일 때
            new_dmm_keyword_retry = keyword_tmps_for_dmm[0] + keyword_tmps_for_dmm[1].zfill(6) # 6자리로 재시도
            logger.debug(f"DMM Search: No results for '{original_keyword_for_log}', retrying with 6-digit number: {new_dmm_keyword_retry}")
            new_keyword_for_retry = keyword_tmps_for_dmm[0] + "-" + keyword_tmps_for_dmm[1].zfill(6)
            return cls.__search(new_keyword_for_retry, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)

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
        logger.debug(f"DMM __img_urls: Extracting raw image URLs for type: {content_type}")
        img_urls_dict = {'ps': "", 'pl': "", 'arts': [], 'specific_poster_candidates': []}
        
        try:
            if content_type == 'videoa' or content_type == 'vr':
                # --- Videoa/VR 타입 이미지 추출 로직 ---
                sample_image_links = tree.xpath('//div[@id="sample-image-block"]//a[.//img]')
                if not sample_image_links:
                    all_img_tags = tree.xpath('//div[@id="sample-image-block"]//img/@src')
                    if not all_img_tags: return img_urls_dict
                    img_urls_dict['pl'] = py_urllib_parse.urljoin(cls.site_base_url, all_img_tags[0].strip()) if all_img_tags else ""
                    temp_arts = [py_urllib_parse.urljoin(cls.site_base_url, src.strip()) for src in all_img_tags[1:] if src.strip()]
                    img_urls_dict['arts'] = list(dict.fromkeys(temp_arts))
                    return img_urls_dict

                temp_arts_list_for_processing = []
                for a_tag in sample_image_links:
                    final_image_url = None
                    href = a_tag.attrib.get("href", "").strip()
                    if href and re.search(r'\.(jpg|jpeg|png|webp)$', href, re.IGNORECASE):
                        final_image_url = py_urllib_parse.urljoin(cls.site_base_url, href)
                    else:
                        img_src_list = a_tag.xpath('.//img/@src')
                        if img_src_list:
                            src = img_src_list[0].strip()
                            if src and re.search(r'\.(jpg|jpeg|png|webp)$', src, re.IGNORECASE):
                                final_image_url = py_urllib_parse.urljoin(cls.site_base_url, src)
                    if final_image_url:
                        temp_arts_list_for_processing.append(final_image_url)

                processed_pl_v = None
                specific_candidates_v_list = []
                remaining_arts_v = []

                # 1. PL 결정 (파일명 기반)
                for url in temp_arts_list_for_processing:
                    filename = url.split('/')[-1].lower()
                    is_pl_type = filename.endswith("pl.jpg") or filename.endswith("jp-0.jpg")
                    if is_pl_type and processed_pl_v is None:
                        processed_pl_v = url
                        break
                
                # PL을 못 찾았지만 temp_arts_list_for_processing에 이미지가 있다면, 첫 번째를 PL로 간주
                if not processed_pl_v and temp_arts_list_for_processing:
                    processed_pl_v = temp_arts_list_for_processing[0]

                # 2. Arts 및 Specific Candidates 결정
                #    Arts: PL로 사용된 URL을 제외한 모든 이미지
                #    Specific Candidates: Arts 중에서 첫 번째와 마지막 이미지를 후보로 추가
                if temp_arts_list_for_processing:
                    for url in temp_arts_list_for_processing:
                        if url != processed_pl_v:
                            if url not in remaining_arts_v:
                                remaining_arts_v.append(url)
                    
                    # remaining_arts_v (PL 제외된 아트 목록)에서 specific 후보 추출
                    if remaining_arts_v:
                        # 첫 번째 아트를 specific 후보로 추가
                        if remaining_arts_v[0] not in specific_candidates_v_list:
                            specific_candidates_v_list.append(remaining_arts_v[0])
                        
                        # 마지막 아트가 첫 번째 아트와 다르고, 리스트에 이미 없다면 추가
                        if len(remaining_arts_v) > 1 and \
                           remaining_arts_v[-1] != remaining_arts_v[0] and \
                           remaining_arts_v[-1] not in specific_candidates_v_list:
                            specific_candidates_v_list.append(remaining_arts_v[-1])
                
                img_urls_dict['pl'] = processed_pl_v if processed_pl_v else ""
                img_urls_dict['specific_poster_candidates'] = specific_candidates_v_list
                img_urls_dict['arts'] = list(dict.fromkeys(remaining_arts_v))

            elif content_type == 'dvd' or content_type == 'bluray':
                # --- DVD/Blu-ray 타입 이미지 추출 로직 ---
                pl_xpath = '//div[@id="fn-sampleImage-imagebox"]/img/@src'
                pl_tags = tree.xpath(pl_xpath)
                raw_pl = pl_tags[0].strip() if pl_tags else ""
                temp_pl_dvd = ""
                if raw_pl:
                    if raw_pl.startswith("//"): temp_pl_dvd = "https:" + raw_pl
                    elif not raw_pl.startswith("http"): temp_pl_dvd = py_urllib_parse.urljoin(cls.site_base_url, raw_pl)
                    else: temp_pl_dvd = raw_pl
                    img_urls_dict['pl'] = temp_pl_dvd
                    logger.debug(f"DMM __img_urls ({content_type}): PL extracted: {temp_pl_dvd}.")

                temp_arts_list_dvd = []
                try:
                    sample_thumbs_container = tree.xpath('//ul[@id="sample-image-block"]')
                    if sample_thumbs_container:
                        thumb_img_tags = sample_thumbs_container[0].xpath('.//li[contains(@class, "slick-slide")]//a[contains(@class, "fn-sample-image")]/img')
                        # 좀 더 일반적인 방법: li 하위의 img 태그 (data-lazy 또는 src 사용)
                        # thumb_img_tags = sample_thumbs_container[0].xpath('.//li//img')
                        for img_tag in thumb_img_tags:
                            art_url_raw = img_tag.attrib.get("data-lazy") or img_tag.attrib.get("src")
                            if art_url_raw:
                                art_url = art_url_raw.strip()
                                if art_url.lower().endswith("dummy_ps.gif"): continue
                                if not art_url.startswith("http"):
                                    art_url = py_urllib_parse.urljoin(cls.site_base_url, art_url)
                                if art_url == img_urls_dict['pl']: continue
                                if art_url not in temp_arts_list_dvd: temp_arts_list_dvd.append(art_url)
                        logger.debug(f"DMM __img_urls ({content_type}): Found {len(temp_arts_list_dvd)} potential art thumbnails.")
                    else:
                        logger.warning(f"DMM __img_urls ({content_type}): Art thumbnail container (ul#sample-image-block) not found.")
                except Exception as e_dvd_art:
                    logger.error(f"DMM __img_urls ({content_type}): Error extracting DVD/BR arts: {e_dvd_art}")

                img_urls_dict['arts'] = temp_arts_list_dvd
                
                # DVD/Blu-ray도 specific_poster_candidates 로직 적용 (arts 리스트 기반)
                specific_candidates_dvd_list = []
                if temp_arts_list_dvd:
                    if temp_arts_list_dvd[0] not in specific_candidates_dvd_list:
                        specific_candidates_dvd_list.append(temp_arts_list_dvd[0])
                    if len(temp_arts_list_dvd) > 1 and \
                       temp_arts_list_dvd[-1] != temp_arts_list_dvd[0] and \
                       temp_arts_list_dvd[-1] not in specific_candidates_dvd_list:
                        specific_candidates_dvd_list.append(temp_arts_list_dvd[-1])
                img_urls_dict['specific_poster_candidates'] = specific_candidates_dvd_list

            else: 
                logger.error(f"DMM __img_urls: Unknown content type '{content_type}' for image extraction.")

        except Exception as e:
            logger.exception(f"DMM __img_urls ({content_type}): Error extracting image URLs: {e}")

        logger.debug(f"DMM __img_urls ({content_type}) returning: PL='{img_urls_dict['pl']}', SpecificCandidatesCount={len(img_urls_dict['specific_poster_candidates'])}, ArtsCount={len(img_urls_dict['arts'])}.")
        return img_urls_dict


    @classmethod
    def _get_dmm_video_trailer_from_args_json(cls, cid_part, detail_url_for_referer, proxy_url=None, current_content_type_for_log="video"):
        """
        DMM의 videoa 및 새로운 VR 타입 예고편 추출 헬퍼.
        AJAX -> iframe -> args JSON 파싱하여 (trailer_url, trailer_title) 반환.
        실패 시 (None, None) 반환.
        """
        trailer_url = None
        trailer_title_from_json = None # JSON에서 가져온 제목

        try:
            ajax_url = py_urllib_parse.urljoin(cls.site_base_url, f"/digital/videoa/-/detail/ajax-movie/=/cid={cid_part}/")
            logger.debug(f"DMM Trailer Helper ({current_content_type_for_log}): Accessing AJAX URL: {ajax_url}")

            ajax_headers = cls._get_request_headers(referer=detail_url_for_referer)
            ajax_headers.update({'Accept': 'text/html, */*; q=0.01', 'X-Requested-With': 'XMLHttpRequest'})

            ajax_res = SiteUtil.get_response(ajax_url, proxy_url=proxy_url, headers=ajax_headers)

            if ajax_res and ajax_res.status_code == 200 and ajax_res.text.strip():
                iframe_tree = html.fromstring(ajax_res.text)
                iframe_srcs = iframe_tree.xpath("//iframe/@src")

                if iframe_srcs:
                    iframe_url = py_urllib_parse.urljoin(ajax_url, iframe_srcs[0])
                    logger.debug(f"DMM Trailer Helper ({current_content_type_for_log}): Accessing iframe URL: {iframe_url}")
                    iframe_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=cls._get_request_headers(referer=ajax_url))

                    if iframe_text:
                        match_args_json = re.search(r'(?:const|var|let)?\s*args\s*=\s*(\{.*?\});', iframe_text, re.DOTALL)
                        if match_args_json:
                            json_data_str = match_args_json.group(1)
                            try:
                                data_json = json.loads(json_data_str)
                                bitrates = sorted(data_json.get("bitrates",[]), key=lambda k: isinstance(k.get("bitrate"), int) and k.get("bitrate", 0), reverse=True) # bitrate가 숫자인 경우에만 정렬, 아니면 순서대로

                                if bitrates and isinstance(bitrates[0], dict) and bitrates[0].get("src"):
                                    trailer_url_raw = bitrates[0]["src"]
                                    trailer_url = "https:" + trailer_url_raw if trailer_url_raw.startswith("//") else trailer_url_raw
                                elif data_json.get("src"): # bitrates 없고 최상위 src
                                    trailer_url_raw = data_json.get("src")
                                    trailer_url = "https:" + trailer_url_raw if trailer_url_raw.startswith("//") else trailer_url_raw

                                if data_json.get("title") and data_json.get("title").strip():
                                    trailer_title_from_json = data_json.get("title").strip()

                            except json.JSONDecodeError as e_json:
                                logger.error(f"DMM Trailer Helper ({current_content_type_for_log}): JSONDecodeError - {e_json}. Data: {json_data_str[:200]}...")
                        else:
                            logger.warning(f"DMM Trailer Helper ({current_content_type_for_log}): 'args' JSON not found in iframe for CID: {cid_part}")
                    else:
                        logger.warning(f"DMM Trailer Helper ({current_content_type_for_log}): Failed to get iframe content for CID: {cid_part}")
                else:
                    logger.warning(f"DMM Trailer Helper ({current_content_type_for_log}): No iframe in AJAX response for CID: {cid_part}")
            else:
                status_code = ajax_res.status_code if ajax_res else "None"
                logger.warning(f"DMM Trailer Helper ({current_content_type_for_log}): AJAX request failed for CID: {cid_part}. Status: {status_code}")
        except Exception as e_helper:
            logger.exception(f"DMM Trailer Helper ({current_content_type_for_log}): Exception for CID {cid_part}: {e_helper}")

        return trailer_url, trailer_title_from_json

    @classmethod
    def _get_dmm_vr_trailer_fallback(cls, cid_part, detail_url_for_referer, proxy_url=None):
        """
        DMM VR 타입 예고편 추출의 이전 방식 Fallback.
        sampleUrl JavaScript 변수를 파싱.
        """
        trailer_url = None
        try:
            vr_player_page_url = f"{cls.site_base_url}/digital/-/vr-sample-player/=/cid={cid_part}/"
            logger.debug(f"DMM VR Trailer Fallback: Accessing player page: {vr_player_page_url}")
            vr_player_html = SiteUtil.get_text(vr_player_page_url, proxy_url=proxy_url, headers=cls._get_request_headers(referer=detail_url_for_referer))
            if vr_player_html:
                match_js_var = re.search(r'var\s+sampleUrl\s*=\s*["\']([^"\']+)["\']', vr_player_html)
                if match_js_var:
                    trailer_url_raw = match_js_var.group(1)
                    trailer_url = "https:" + trailer_url_raw if trailer_url_raw.startswith("//") else trailer_url_raw
                    logger.info(f"DMM VR Trailer Fallback: Found sampleUrl: {trailer_url}")
        except Exception as e_fallback:
            logger.exception(f"DMM VR Trailer Fallback: Exception for CID {cid_part}: {e_fallback}")
        return trailer_url


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
        crop_mode_setting = crop_mode

        cached_data = cls._ps_url_cache.get(code, {})
        ps_url_from_search_cache = cached_data.get('ps') 
        content_type_from_cache = cached_data.get('type', 'unknown')

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
        entity.thumb = []; entity.fanart = []; entity.extras = []; entity.ratings = []
        ui_code_for_image = ""; entity.content_type = current_content_type # 최종 확정된 타입 entity에 저장

        # === 2. 전체 메타데이터 파싱 (ui_code_for_image 및 entity.title 등 확정) ===
        identifier_parsed = False; is_vr_actual = False # 상세페이지에서 VR 여부 최종 확인
        try:
            logger.debug(f"DMM Info (Parsing as {entity.content_type}): Metadata for {code}...")

            # --- DMM 타입별 메타데이터 파싱 로직 ---
            if entity.content_type == 'videoa' or entity.content_type == 'vr':
                # videoa/vr 파싱
                raw_title_text_v = "" # 변수명에 _v 접미사 추가
                try:
                    title_node_v = tree.xpath('//h1[@id="title"]')
                    if title_node_v:
                        raw_title_text_v = title_node_v[0].text_content().strip()
                        if raw_title_text_v.startswith("【VR】"): is_vr_actual = True; entity.content_type = 'vr' # VR 타입 최종 확정
                        entity.tagline = SiteUtil.trans(raw_title_text_v, do_trans=do_trans) # tagline 우선 설정
                    else: logger.warning(f"DMM ({entity.content_type}): Could not find h1#title.")
                except Exception as e_title_parse_v: logger.warning(f"DMM ({entity.content_type}): Error parsing title: {e_title_parse_v}")

                info_table_xpath_v = '//table[contains(@class, "mg-b20")]//tr'

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
                        if match_id_v: id_before_v = match_id_v.group(0); value_pid_v = value_pid_v.lower().replace(id_before_v.lower(), "zzid") # 소문자 변환 후 치환

                        match_real_v = cls.PTN_SEARCH_REAL_NO.search(value_pid_v) 
                        
                        formatted_code_v = value_text_all_v.upper() # 기본값: 원본 품번 문자열
                        if match_real_v:
                            label_v = match_real_v.group("real").upper()
                            if id_before_v is not None: label_v = label_v.replace("ZZID", id_before_v.upper())
                            num_str_v = str(int(match_real_v.group("no"))).zfill(3)
                            formatted_code_v = f"{label_v}-{num_str_v}"
                            if entity.tag is None: entity.tag = []
                            if label_v not in entity.tag: entity.tag.append(label_v)
                        else:
                            # PTN_SEARCH_REAL_NO.search 실패 시 폴백 로직
                            logger.warning(f"DMM Info ({entity.content_type}): PTN_SEARCH_REAL_NO failed for '{value_pid_v}'. Applying fallback for UI code.")

                            # 첫번째 문자열 그룹과 그 뒤 숫자 그룹 추출
                            m_fallback_v = re.match(r"([a-zA-Z]+)(\d+)", value_pid_v, re.I) # 원본 value_pid_v 사용
                            if m_fallback_v:
                                label_fallback_v = m_fallback_v.group(1).upper()
                                number_fallback_v_str = m_fallback_v.group(2)
                                try:
                                    # 숫자 부분만 사용하고 3자리로 패딩
                                    formatted_code_v = f"{label_fallback_v}-{str(int(number_fallback_v_str)).zfill(3)}"
                                except ValueError:
                                    # 숫자 변환 실패 시, 문자열 부분 + 원래 숫자 문자열 사용
                                    formatted_code_v = f"{label_fallback_v}-{number_fallback_v_str}"
                                logger.debug(f"DMM Info ({entity.content_type}): Fallback UI code set to '{formatted_code_v}' from '{value_pid_v}'.")
                            else:
                                # 이것도 실패하면 그냥 원본 대문자 사용
                                formatted_code_v = value_text_all_v.upper()
                                logger.warning(f"DMM Info ({entity.content_type}): Fallback UI code extraction failed for '{value_pid_v}'. Using original uppercase from HTML: '{formatted_code_v}'.")

                        ui_code_for_image = formatted_code_v # 확정된 품번
                        entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image # 품번을 타이틀로
                        entity.ui_code = ui_code_for_image
                        identifier_parsed = True
                        # logger.debug(f"DMM ({entity.content_type}): 品番 파싱 완료, ui_code_for_image='{ui_code_for_image}'")

                    elif "配信開始日" in key_v:
                        premiered_haishin_v = value_text_all_v.replace("/", "-")
                    elif "収録時間" in key_v: 
                        m_rt_v = re.search(r"(\d+)",value_text_all_v); entity.runtime = int(m_rt_v.group(1)) if m_rt_v else None
                    elif "出演者" in key_v:
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

                rating_text_node = tree.xpath('//p[contains(@class, "d-review__average")]/strong/text()')
                if rating_text_node:
                    rating_text = rating_text_node[0].strip()
                    rating_match_text = re.search(r'([\d\.]+)\s*点', rating_text)
                    if rating_match_text:
                        try:
                            rate_val_text = float(rating_match_text.group(1))
                            if 0 <= rate_val_text <= 5:
                                # entity.ratings는 이미 []로 초기화되었으므로 바로 append
                                entity.ratings.append(EntityRatings(rate_val_text, max=5, name="dmm"))
                        except ValueError:
                            logger.warning(f"DMM ({entity.content_type}): Text-based rating conversion error: {rating_text}")
                else:
                    logger.debug(f"DMM ({entity.content_type}): Text-based rating element (d-review__average) not found.")

                # videoa/vr 출시일: 상품일 > 배신일 순
                entity.premiered = premiered_shouhin_v or premiered_haishin_v 
                if entity.premiered: entity.year = int(entity.premiered[:4]) if len(entity.premiered) >=4 else None
                else: logger.warning(f"DMM ({entity.content_type}): Premiered date not found for {code}.")

                # videoa/vr 줄거리
                plot_xpath_v_meta_info = '//div[@class="mg-b20 lh4"]/text()'
                plot_nodes_v_meta_info = tree.xpath(plot_xpath_v_meta_info)
                if plot_nodes_v_meta_info:
                    plot_text_v_meta_info = "\n".join([p_v_info.strip() for p_v_info in plot_nodes_v_meta_info if p_v_info.strip()]).split("※")[0].strip()
                    if plot_text_v_meta_info: entity.plot = SiteUtil.trans(plot_text_v_meta_info, do_trans=do_trans)
                else: logger.warning(f"DMM ({entity.content_type}): Plot not found for {code}.")

            elif entity.content_type == 'dvd' or entity.content_type == 'bluray':
                title_node_dvd = tree.xpath('//h1[@id="title"]')
                if title_node_dvd: 
                    entity.tagline = SiteUtil.trans(title_node_dvd[0].text_content().strip(), do_trans=do_trans)

                info_table_xpath_dvd = '//div[contains(@class, "wrapper-product")]//table[contains(@class, "mg-b20")]//tr'
                table_rows_dvd = tree.xpath(info_table_xpath_dvd)

                premiered_shouhin_dvd = None 
                premiered_hatsubai_dvd = None  
                premiered_haishin_dvd = None   

                if not table_rows_dvd:
                    logger.warning(f"DMM ({entity.content_type}): No <tr> tags found in the info table using XPath: {info_table_xpath_dvd}")

                for row_dvd in table_rows_dvd: 
                    tds_dvd = row_dvd.xpath("./td") 
                    if len(tds_dvd) != 2: 
                        continue

                    key_dvd = tds_dvd[0].text_content().strip().replace("：", "")
                    value_node_dvd = tds_dvd[1]
                    value_text_all_dvd = value_node_dvd.text_content().strip()

                    if value_text_all_dvd == "----" or not value_text_all_dvd: 
                        continue

                    # --- 테이블 내부 항목 파싱 (videoa/vr 로직을 여기에 적용) ---
                    if "品番" in key_dvd:
                        value_pid_dvd = value_text_all_dvd; match_id_dvd = cls.PTN_ID.search(value_pid_dvd); id_before_dvd = None
                        if match_id_dvd: id_before_dvd = match_id_dvd.group(0); value_pid_dvd = value_pid_dvd.lower().replace(id_before_dvd.lower(), "zzid")

                        match_real_dvd = cls.PTN_SEARCH_REAL_NO.search(value_pid_dvd)
                        
                        formatted_code_dvd = value_text_all_dvd.upper() # 기본값: 원본 품번 문자열
                        if match_real_dvd:
                            label_dvd = match_real_dvd.group("real").upper()
                            if id_before_dvd is not None: label_dvd = label_dvd.replace("ZZID", id_before_dvd.upper())
                            num_str_dvd = str(int(match_real_dvd.group("no"))).zfill(3)
                            formatted_code_dvd = f"{label_dvd}-{num_str_dvd}"
                            if entity.tag is None: entity.tag = []
                            if label_dvd not in entity.tag: entity.tag.append(label_dvd)
                        else:
                            # PTN_SEARCH_REAL_NO.search 실패 시 폴백 로직
                            logger.warning(f"DMM Info ({entity.content_type}): PTN_SEARCH_REAL_NO failed for '{value_pid_dvd}'. Applying fallback for UI code.")
                            
                            # 마찬가지로 h_ 제거 로직 불필요
                            m_fallback_dvd = re.match(r"([a-zA-Z]+)(\d+)", value_pid_dvd, re.I) # 원본 value_pid_dvd 사용
                            if m_fallback_dvd:
                                label_fallback_dvd = m_fallback_dvd.group(1).upper()
                                number_fallback_dvd_str = m_fallback_dvd.group(2)
                                try:
                                    formatted_code_dvd = f"{label_fallback_dvd}-{str(int(number_fallback_dvd_str)).zfill(3)}"
                                except ValueError:
                                    formatted_code_dvd = f"{label_fallback_dvd}-{number_fallback_dvd_str}"
                                logger.debug(f"DMM Info ({entity.content_type}): Fallback UI code set to '{formatted_code_dvd}' from '{value_pid_dvd}'.")
                            else:
                                formatted_code_dvd = value_text_all_dvd.upper() # HTML에서 가져온 원본 품번 문자열 사용
                                logger.warning(f"DMM Info ({entity.content_type}): Fallback UI code extraction failed for '{value_pid_dvd}'. Using original uppercase from HTML: '{formatted_code_dvd}'.")

                        ui_code_for_image = formatted_code_dvd
                        entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image
                        entity.ui_code = ui_code_for_image
                        identifier_parsed = True
                    elif "収録時間" in key_dvd: 
                        m_rt_dvd = re.search(r"(\d+)",value_text_all_dvd)
                        if m_rt_dvd: entity.runtime = int(m_rt_dvd.group(1))
                    elif "出演者" in key_dvd:
                        actors_dvd = [a.strip() for a in value_node_dvd.xpath('.//a/text()') if a.strip()]
                        if actors_dvd: entity.actor = [EntityActor(name) for name in actors_dvd]
                        elif value_text_all_dvd != '----': entity.actor = [EntityActor(n.strip()) for n in value_text_all_dvd.split('/') if n.strip()]
                    elif "監督" in key_dvd:
                        directors_dvd = [d.strip() for d in value_node_dvd.xpath('.//a/text()') if d.strip()]
                        if directors_dvd: entity.director = directors_dvd[0] 
                        elif value_text_all_dvd != '----': entity.director = value_text_all_dvd
                    elif "シリーズ" in key_dvd:
                        if entity.tag is None: entity.tag = []
                        series_dvd = [s.strip() for s in value_node_dvd.xpath('.//a/text()') if s.strip()]
                        s_name_dvd = None
                        if series_dvd: s_name_dvd = series_dvd[0]
                        elif value_text_all_dvd != '----': s_name_dvd = value_text_all_dvd
                        if s_name_dvd:
                            trans_s_name_dvd = SiteUtil.trans(s_name_dvd, do_trans=do_trans)
                            if trans_s_name_dvd not in entity.tag: entity.tag.append(trans_s_name_dvd)
                    elif "メーカー" in key_dvd:
                        if entity.studio is None: 
                            makers_dvd = [mk.strip() for mk in value_node_dvd.xpath('.//a/text()') if mk.strip()]
                            m_name_dvd = None
                            if makers_dvd: m_name_dvd = makers_dvd[0]
                            elif value_text_all_dvd != '----': m_name_dvd = value_text_all_dvd
                            if m_name_dvd: entity.studio = SiteUtil.trans(m_name_dvd, do_trans=do_trans)
                    elif "レーベル" in key_dvd:
                        labels_dvd = [lb.strip() for lb in value_node_dvd.xpath('.//a/text()') if lb.strip()]
                        l_name_dvd = None
                        if labels_dvd: l_name_dvd = labels_dvd[0]
                        elif value_text_all_dvd != '----': l_name_dvd = value_text_all_dvd
                        if l_name_dvd:
                            if do_trans: entity.studio = SiteUtil.av_studio.get(l_name_dvd, SiteUtil.trans(l_name_dvd))
                            else: entity.studio = l_name_dvd
                    elif "ジャンル" in key_dvd:
                        if entity.genre is None: entity.genre = []
                        for genre_ja_tag_dvd in value_node_dvd.xpath('.//a'):
                            genre_ja_dvd = genre_ja_tag_dvd.text_content().strip()
                            if not genre_ja_dvd or "％OFF" in genre_ja_dvd or genre_ja_dvd in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja_dvd in SiteUtil.av_genre: 
                                if SiteUtil.av_genre[genre_ja_dvd] not in entity.genre: entity.genre.append(SiteUtil.av_genre[genre_ja_dvd])
                            else:
                                genre_ko_dvd = SiteUtil.trans(genre_ja_dvd, do_trans=do_trans).replace(" ", "")
                                if genre_ko_dvd not in SiteUtil.av_genre_ignore_ko and genre_ko_dvd not in entity.genre : entity.genre.append(genre_ko_dvd)

                    # 출시일 관련 정보 수집
                    elif "商品発売日" in key_dvd: premiered_shouhin_dvd = value_text_all_dvd.replace("/", "-")
                    elif "発売日" in key_dvd: premiered_hatsubai_dvd = value_text_all_dvd.replace("/", "-")
                    elif "配信開始日" in key_dvd: premiered_haishin_dvd = value_text_all_dvd.replace("/", "-")

                # 평점 추출
                rating_text_node_dvd_specific = tree.xpath('//p[contains(@class, "dcd-review__average")]/strong/text()')
                if rating_text_node_dvd_specific:
                    rating_text = rating_text_node_dvd_specific[0].strip()
                    try:
                        rate_val = float(rating_text)
                        if 0 <= rate_val <= 5: 
                            if not entity.ratings: entity.ratings.append(EntityRatings(rate_val, max=5, name="dmm"))
                    except ValueError:
                        rating_match = re.search(r'([\d\.]+)\s*点?', rating_text)
                        if rating_match:
                            try:
                                rate_val = float(rating_match.group(1))
                                if 0 <= rate_val <= 5: 
                                    if not entity.ratings: entity.ratings.append(EntityRatings(rate_val, max=5, name="dmm"))
                            except ValueError:
                                logger.warning(f"DMM ({entity.content_type}): Rating conversion error (after regex): {rating_text}")
                else:
                    logger.debug(f"DMM ({entity.content_type}): DVD/BR specific rating element (dcd-review__average) not found.")

                # 출시일 최종 결정
                entity.premiered = premiered_shouhin_dvd or premiered_hatsubai_dvd or premiered_haishin_dvd
                if entity.premiered: 
                    try: entity.year = int(entity.premiered[:4])
                    except ValueError: logger.warning(f"DMM ({entity.content_type}): Year parse error from '{entity.premiered}'")
                else:
                    logger.warning(f"DMM ({entity.content_type}): Premiered date not found for {code}.")

                plot_xpath_dvd_specific = '//div[@class="mg-b20 lh4"]/p[@class="mg-b20"]/text()'
                plot_nodes_dvd_specific = tree.xpath(plot_xpath_dvd_specific)
                if plot_nodes_dvd_specific:
                    plot_text = "\n".join([p.strip() for p in plot_nodes_dvd_specific if p.strip()]).split("※")[0].strip()
                    if plot_text: entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                else: 
                    logger.warning(f"DMM ({entity.content_type}): Plot not found for {code} using XPath: {plot_xpath_dvd_specific}")

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
                if web_url_p_dmm_user: user_custom_poster_url = web_url_p_dmm_user; entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url)); skip_default_poster_logic = True; break 
            for suffix_pl_dmm_user in landscape_suffixes:
                _, web_url_pl_dmm_user = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix_pl_dmm_user, image_server_url)
                if web_url_pl_dmm_user: user_custom_landscape_url = web_url_pl_dmm_user; entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url)); skip_default_landscape_logic = True; break

        # === 4. 이미지 정보 추출 및 처리 ===
        raw_image_urls = cls.__img_urls(tree, content_type=entity.content_type)
        pl_on_page = raw_image_urls.get('pl')
        specific_candidates_on_page = raw_image_urls.get('specific_poster_candidates', []) 
        other_arts_on_page = raw_image_urls.get('arts', [])

        final_poster_source = None
        final_poster_crop_mode = None
        final_landscape_source = None
        arts_urls_for_processing = [] 

        if not skip_default_landscape_logic: # 랜드스케이프는 PL을 기본으로 사용
            final_landscape_source = pl_on_page

        if not skip_default_poster_logic: # 포스터 결정 로직
            fixed_crop_applied_for_bluray = False
            # --- 특수 Blu-ray 800x442 처리 로직 ---
            if entity.content_type == 'bluray' and pl_on_page:
                try:
                    pl_image_obj_for_fixed_crop = SiteUtil.imopen(pl_on_page, proxy_url=proxy_url)
                    if pl_image_obj_for_fixed_crop:
                        img_width, img_height = pl_image_obj_for_fixed_crop.size
                        if img_width == 800 and 438 <= img_height <= 444:
                            crop_box_fixed = (img_width - 380, 0, img_width, img_height) 
                            final_poster_pil_object = pl_image_obj_for_fixed_crop.crop(crop_box_fixed)
                            if final_poster_pil_object:
                                final_poster_source = final_poster_pil_object 
                                final_poster_crop_mode = None 
                                fixed_crop_applied_for_bluray = True
                except Exception as e_fixed_crop_bluray:
                    logger.error(f"DMM Blu-ray: Error during fixed crop: {e_fixed_crop_bluray}")


            if not fixed_crop_applied_for_bluray:
                # videoa 또는 vr 타입일 경우
                if entity.content_type == 'videoa' or entity.content_type == 'vr':
                    if ps_to_poster_setting and ps_url_from_search_cache:
                        final_poster_source = ps_url_from_search_cache
                    elif pl_on_page and ps_url_from_search_cache and SiteUtil.is_hq_poster(ps_url_from_search_cache, pl_on_page, proxy_url=proxy_url):
                        final_poster_source = pl_on_page
                    else:
                        specific_found = False
                        if ps_url_from_search_cache:
                            for sp_candidate in specific_candidates_on_page:
                                if SiteUtil.is_hq_poster(ps_url_from_search_cache, sp_candidate, proxy_url=proxy_url):
                                    final_poster_source = sp_candidate
                                    specific_found = True
                                    break
                        if not specific_found:
                            if pl_on_page and ps_url_from_search_cache:
                                crop_pos = SiteUtil.has_hq_poster(ps_url_from_search_cache, pl_on_page, proxy_url=proxy_url)
                                if crop_pos : final_poster_source = pl_on_page; final_poster_crop_mode = crop_pos
                                else: final_poster_source = ps_url_from_search_cache
                            elif ps_url_from_search_cache : final_poster_source = ps_url_from_search_cache
                            elif pl_on_page: final_poster_source = pl_on_page; final_poster_crop_mode = crop_mode_setting 
                            elif specific_candidates_on_page: final_poster_source = specific_candidates_on_page[0]
                            else: final_poster_source = None

                # dvd 또는 일반 bluray (800x442 아닌 경우) 타입일 경우
                elif entity.content_type == 'dvd' or entity.content_type == 'bluray':
                    if ps_to_poster_setting and ps_url_from_search_cache:
                        final_poster_source = ps_url_from_search_cache
                    elif pl_on_page and ps_url_from_search_cache:
                        crop_pos = SiteUtil.has_hq_poster(ps_url_from_search_cache, pl_on_page, proxy_url=proxy_url)
                        if crop_pos: final_poster_source = pl_on_page; final_poster_crop_mode = crop_pos
                        elif SiteUtil.is_hq_poster(ps_url_from_search_cache, pl_on_page, proxy_url=proxy_url): final_poster_source = pl_on_page 
                        else:
                            specific_found_dvd = False
                            if ps_url_from_search_cache:
                                for sp_candidate_dvd in specific_candidates_on_page:
                                    if SiteUtil.is_hq_poster(ps_url_from_search_cache, sp_candidate_dvd, proxy_url=proxy_url):
                                        final_poster_source = sp_candidate_dvd
                                        specific_found_dvd = True
                                        break
                            if not specific_found_dvd:
                                final_poster_source = ps_url_from_search_cache
                    elif ps_url_from_search_cache: final_poster_source = ps_url_from_search_cache
                    elif pl_on_page: final_poster_source = pl_on_page; final_poster_crop_mode = crop_mode_setting
                    elif specific_candidates_on_page: final_poster_source = specific_candidates_on_page[0]
                    else: final_poster_source = None

            if final_poster_source is None and ps_url_from_search_cache and not ps_to_poster_setting:
                logger.debug(f"DMM ({entity.content_type}): No poster source determined, falling back to cached PS: {ps_url_from_search_cache}")
                final_poster_source = ps_url_from_search_cache
            elif final_poster_source is None:
                logger.warning(f"DMM ({entity.content_type}): No poster source could be determined for {code}.")

        # 4. 팬아트 목록 결정
        potential_fanart_candidates = []
        potential_fanart_candidates.extend(specific_candidates_on_page)
        potential_fanart_candidates.extend(other_arts_on_page) 

        urls_used_as_thumb = set()
        if final_landscape_source and not skip_default_landscape_logic:
            urls_used_as_thumb.add(final_landscape_source)
        if final_poster_source and not skip_default_poster_logic and isinstance(final_poster_source, str):
            urls_used_as_thumb.add(final_poster_source)

        temp_unique_fanarts = []
        seen_for_temp_unique = set()
        for art_url in potential_fanart_candidates:
            if art_url and art_url not in urls_used_as_thumb and art_url not in seen_for_temp_unique:
                temp_unique_fanarts.append(art_url)
                seen_for_temp_unique.add(art_url)
        arts_urls_for_processing = temp_unique_fanarts[:max_arts]

        logger.debug(f"DMM ({entity.content_type}): Final Images Decision - Poster='{final_poster_source}' (Crop='{final_poster_crop_mode}'), Landscape='{final_landscape_source}', Fanarts_to_process({len(arts_urls_for_processing)})='{arts_urls_for_processing[:3]}...'")

        # 5. entity.thumb 및 entity.fanart 채우기
        if not (use_image_server and image_mode == '4'):
            if final_poster_source and not skip_default_poster_logic:
                if not any(t.aspect == 'poster' for t in entity.thumb):
                    processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                    if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))

            if final_landscape_source and not skip_default_landscape_logic:
                if not any(t.aspect == 'landscape' for t in entity.thumb):
                    processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_source, proxy_url=proxy_url)
                    if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))

            for art_url_item in arts_urls_for_processing:
                processed_art = SiteUtil.process_image_mode(image_mode, art_url_item, proxy_url=proxy_url)
                if processed_art: entity.fanart.append(processed_art)

        elif use_image_server and image_mode == '4' and ui_code_for_image:
            if final_poster_source and not skip_default_poster_logic:
                if not any(t.aspect == 'poster' for t in entity.thumb):
                    p_path = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                    if p_path: entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path}"))

            if final_landscape_source and not skip_default_landscape_logic:
                if not any(t.aspect == 'landscape' for t in entity.thumb):
                    pl_path = SiteUtil.save_image_to_server_path(final_landscape_source, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                    if pl_path: entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path}"))

            for idx, art_url_item_server in enumerate(arts_urls_for_processing):
                art_relative_path = SiteUtil.save_image_to_server_path(art_url_item_server, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                if art_relative_path: entity.fanart.append(f"{image_server_url}/{art_relative_path}")

        if use_extras:
            entity.extras = [] 
            # 기본 트레일러 제목 설정 (JSON에서 못 가져올 경우 사용)
            default_trailer_title = entity.tagline if entity.tagline and entity.tagline != entity.ui_code else entity.ui_code
            trailer_url_final = None
            trailer_title_to_use = default_trailer_title

            try:
                if entity.content_type == 'vr':
                    # 1차 시도: 새로운 args JSON 방식
                    trailer_url_final, title_from_json = cls._get_dmm_video_trailer_from_args_json(cid_part, detail_url, proxy_url, entity.content_type)
                    if title_from_json: trailer_title_to_use = title_from_json

                    # 2차 시도 (Fallback): 이전 sampleUrl 방식
                    if not trailer_url_final:
                        logger.info(f"DMM VR Trailer: New method failed for {cid_part}. Trying fallback (old sampleUrl method).")
                        trailer_url_final = cls._get_dmm_vr_trailer_fallback(cid_part, detail_url, proxy_url)

                elif entity.content_type == 'videoa':
                    trailer_url_final, title_from_json = cls._get_dmm_video_trailer_from_args_json(cid_part, detail_url, proxy_url, entity.content_type)
                    if title_from_json: trailer_title_to_use = title_from_json

                elif entity.content_type == 'dvd' or entity.content_type == 'bluray': 
                    onclick_trailer = tree.xpath('//a[@id="sample-video1"]/@onclick | //a[contains(@onclick,"gaEventVideoStart")]/@onclick')
                    if onclick_trailer:
                        match_json = re.search(r"gaEventVideoStart\s*\(\s*'(\{.*?\})'\s*,\s*'(\{.*?\})'\s*\)", onclick_trailer[0])
                        if match_json:
                            video_data_str = match_json.group(1).replace('\\"', '"')
                            try:
                                video_data = json.loads(video_data_str)
                                if video_data.get("video_url"):
                                    trailer_url_final = video_data["video_url"]
                            except json.JSONDecodeError as e_json_dvd:
                                logger.error(f"DMM DVD/BR Trailer: JSONDecodeError - {e_json_dvd}. Data: {video_data_str[:100]}")
                if trailer_url_final:
                    final_trans_trailer_title = SiteUtil.trans(trailer_title_to_use, do_trans=do_trans)
                    entity.extras.append(EntityExtra("trailer", final_trans_trailer_title, "mp4", trailer_url_final))
            except Exception as e_trailer_main: 
                logger.exception(f"DMM ({entity.content_type}): Main trailer processing error: {e_trailer_main}")

        logger.info(f"DMM ({entity.content_type}): __info finished for {code}. UI: {ui_code_for_image}, PSkip:{skip_default_poster_logic}, PLSkip:{skip_default_landscape_logic}, Thumbs:{len(entity.thumb)}, Fanarts:{len(entity.fanart)}")
        return entity

    @classmethod
    def info(cls, code, **kwargs):
        ret = {}; entity_result_val_final = None
        try:
            entity_result_val_final = cls.__info(code, **kwargs) 
            if entity_result_val_final: ret["ret"] = "success"; ret["data"] = entity_result_val_final.as_dict()
            else: ret["ret"] = "error"; ret["data"] = f"Failed to get DMM info for {code}"
        except Exception as e_info_dmm_main_call_val_final: ret["ret"] = "exception"; ret["data"] = str(e_info_dmm_main_call_val_final); logger.exception(f"DMM info main call error: {e_info_dmm_main_call_val_final}")
        return ret
