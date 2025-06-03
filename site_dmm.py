# -*- coding: utf-8 -*-
import json
import re
import urllib.parse as py_urllib_parse
from lxml import html, etree
import os
from PIL import Image

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
            logger.debug("Checking/Performing DMM age verification.")
            cls.last_proxy_used = proxy_url
            session_cookies = SiteUtil.session.cookies
            domain_checks = ['.dmm.co.jp', '.dmm.com']
            if any('age_check_done' in session_cookies.get_dict(domain=d) and session_cookies.get_dict(domain=d)['age_check_done'] == '1' for d in domain_checks):
                #logger.debug("Age verification cookie found in SiteUtil.session.")
                cls.age_verified = True; return True
            #logger.debug("Attempting DMM age verification via confirmation GET...")
            try:
                target_rurl = cls.fanza_av_url
                confirm_path = f"/age_check/=/declared=yes/?rurl={py_urllib_parse.quote(target_rurl, safe='')}"
                age_check_confirm_url = py_urllib_parse.urljoin(cls.site_base_url, confirm_path)
                confirm_headers = cls._get_request_headers(referer=cls.site_base_url + "/")
                confirm_response = SiteUtil.get_response( age_check_confirm_url, method='GET', proxy_url=proxy_url, headers=confirm_headers, allow_redirects=False, verify=False )
                if confirm_response.status_code == 302 and 'age_check_done=1' in confirm_response.headers.get('Set-Cookie', ''):
                    #logger.debug("Age confirmation successful via Set-Cookie.")
                    final_cookies = SiteUtil.session.cookies
                    if any('age_check_done' in final_cookies.get_dict(domain=d) and final_cookies.get_dict(domain=d)['age_check_done'] == '1' for d in domain_checks):
                        #logger.debug("age_check_done=1 confirmed in session.")
                        cls.age_verified = True; return True
                    else:
                        logger.warning("Set-Cookie received, but not updated in session. Trying manual set.")
                        SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.co.jp", path="/"); SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.com", path="/")
                        #logger.debug("Manually set age_check_done cookie.")
                        cls.age_verified = True; return True
                else: logger.warning(f"Age check failed (Status:{confirm_response.status_code} or cookie missing).")
            except Exception as e: logger.exception(f"Age verification exception: {e}")
            cls.age_verified = False; return False
        else:
            #logger.debug("Age verification already done.")
            return True

    @classmethod
    def get_label_from_ui_code(cls, ui_code_str: str) -> str:
        if not ui_code_str or not isinstance(ui_code_str, str): return ""
        ui_code_upper = ui_code_str.upper()

        # PTN_ID와 유사한 패턴으로 ID 계열 레이블 먼저 확인 (예: "16ID-045" -> "16ID")
        id_match = re.match(r'^(\d*[A-Z]+ID)', ui_code_upper) # 예: 16ID, 25ID, ID (숫자 없거나, 있거나)
        if id_match:
            return id_match.group(1)
        
        # 일반적인 경우 (하이픈 앞부분)
        if '-' in ui_code_upper:
            return ui_code_upper.split('-', 1)[0]
        
        # 하이픈 없는 경우 (예: HAGE001)
        match_alpha_prefix = re.match(r'^([A-Z]+)', ui_code_upper)
        if match_alpha_prefix:
            return match_alpha_prefix.group(1)
            
        return ui_code_upper # 최후의 경우 전체 반환

    @classmethod
    def _parse_ui_code_from_cid(cls, cid_part_raw: str, content_type: str) -> tuple:
        processed_cid = cid_part_raw.lower()
        # --- 1. (선택적) 알려진 일반 접두사/접미사 제거 ---
        # 예: h_, n_, _a, _b 등. 이 부분은 기존 코드의 것을 활용하거나 추가
        prefix_match = re.match(r'^([hn]_\d?)(.*)', processed_cid)
        if prefix_match:
            # prefix_val = prefix_match.group(1) # 필요시 보관
            processed_cid = prefix_match.group(2)
        
        suffix_strip_match = re.match(r'^(.*\d+)([a-z]+)$', processed_cid)
        if suffix_strip_match:
            processed_cid = suffix_strip_match.group(1)

        # --- 변수 초기화 ---
        label_num_ui_final = "" # 최종 UI 코드용 숫자 (3자리 패딩)
        label_num_raw_for_score = "" # 점수 계산용 원본 숫자 (패딩 없음)
        remaining_for_label_parse = processed_cid # 레이블 파싱 대상 문자열

        # expected_num_len: cid에서 숫자 부분을 식별하기 위한 기대 길이 (타입별로 다름)
        expected_num_len_for_cid_parse = 5 if content_type == 'videoa' or content_type == 'vr' else 3

        # --- 2. 숫자 부분 추출 (cid 파싱용 기대 길이 사용) ---
        extracted_num_str_intermediate = "" # 초기 숫자 추출 결과 (아직 앞 0 제거 전)

        num_match_specific_len = re.match(rf'^(.*?)(\d{{{expected_num_len_for_cid_parse}}})$', processed_cid)
        if num_match_specific_len:
            remaining_for_label_parse = num_match_specific_len.group(1)
            extracted_num_str_intermediate = num_match_specific_len.group(2)
        else: # 기대 길이의 숫자를 못 찾은 경우, 일반 숫자 추출
            num_match_general = re.match(r'^(.*?_?)(\d+)$', processed_cid)
            if num_match_general:
                remaining_for_label_parse = num_match_general.group(1)
                extracted_num_str_intermediate = num_match_general.group(2)
            else:
                logger.debug(f"DMM Parse: No numeric part found in '{processed_cid}' for cid parsing.")
                # extracted_num_str_intermediate 는 "" 유지
                
        # extracted_num_str_intermediate 에 대한 후처리
        if extracted_num_str_intermediate:
            label_num_raw_for_score = extracted_num_str_intermediate # 점수용은 초기 추출된 원본 사용

            # UI용 숫자 처리:
            # 1. 앞부분의 '0' 제거 (단, 숫자 전체가 '0' 이거나 '00' 등인 경우는 하나는 남김)
            num_stripped = extracted_num_str_intermediate.lstrip('0')
            if not num_stripped and extracted_num_str_intermediate: # 모두 0이었던 경우 (예: "0", "00")
                num_stripped = "0" # 최소한 하나의 0은 유지

            # 2. 최소 3자리로 패딩
            label_num_ui_final = num_stripped.zfill(3)
        else:
            # 숫자 부분이 아예 없었던 경우
            label_num_raw_for_score = ""
            label_num_ui_final = ""

        # --- 3. 레이블 부분 추출 (remaining_for_label_parse 사용) ---
        label_ui_part = "" # 최종 UI 코드용 레이블 (예: 16ID, SSIS)
        score_label_part = "" # 최종 점수 계산용 레이블 (예: 16id, ssis)

        if 'id' in remaining_for_label_parse.lower():
            idnn_match = re.match(r'^(.*?)(id)(\d{2})$', remaining_for_label_parse, re.I)
            if idnn_match:
                label_series = idnn_match.group(3)
                label_chars = idnn_match.group(2).lower()
                label_ui_part = (label_series + label_chars).upper() # 예: 16ID
                score_label_part = label_series + label_chars      # 예: 16id
            else:
                nnid_match = re.match(r'^(.*?)(\d{2})(id)$', remaining_for_label_parse, re.I)
                if nnid_match:
                    label_series = nnid_match.group(2)
                    label_chars = nnid_match.group(3).lower()
                    label_ui_part = (label_series + label_chars).upper() # 예: 16ID
                    score_label_part = label_series + label_chars      # 예: 16id
                # IDNN/NNID 외 'id' 포함 케이스는 아래 일반 로직에서 처리
                
        if not label_ui_part: # 'id'가 없거나, 위 특정 ID 패턴에 해당하지 않으면
            # 일반 레이블 추출: 앞의 숫자/언더스코어 제외, 알파벳으로 시작하는 부분
            general_label_match = re.match(r'^(?:\d*_)?([a-z][a-z0-9]*)$', remaining_for_label_parse, re.I)
            if general_label_match:
                extracted_label = general_label_match.group(1)
                label_ui_part = extracted_label.upper()
                score_label_part = extracted_label.lower()
            else:
                if remaining_for_label_parse: # 숫자만 있었거나 빈 문자열이 아닌 경우
                    label_ui_part = remaining_for_label_parse.upper()
                    score_label_part = remaining_for_label_parse.lower()
                else: # 숫자만 있었고, remaining_for_label_parse가 비었다면 label_ui_part도 ""
                    logger.warning(f"DMM Parse: Label part is empty after num extraction from '{cid_part_raw}'")

        # --- 4. 최종 UI 코드 조합 ---
        ui_code_final = ""
        if label_ui_part and label_num_ui_final:
            ui_code_final = f"{label_ui_part}-{label_num_ui_final}"
        elif label_ui_part:
            ui_code_final = label_ui_part.upper()
        elif label_num_ui_final: # 거의 없음
            ui_code_final = f"-{label_num_ui_final}" # 예: -045
        else: # 둘 다 없으면 원본 사용
            ui_code_final = cid_part_raw.upper()
            if not score_label_part: score_label_part = ui_code_final.lower() # 점수용 레이블도 채움

        if not score_label_part and label_ui_part: # score_label_part가 비었는데 label_ui_part는 있을 경우
            score_label_part = label_ui_part.lower()
        elif not score_label_part: # 최후의 경우
            score_label_part = cid_part_raw.lower().replace("-","") # 하이픈 제거한 원본 소문자

        logger.debug(f"DMM Parse: '{cid_part_raw}' ({content_type}) -> UI Code='{ui_code_final}', ScoreLabel='{score_label_part}', ScoreNumRaw='{label_num_raw_for_score}'")
        return ui_code_final, score_label_part, label_num_raw_for_score


    @classmethod
    def __search(
        cls,
        keyword,
        do_trans=True,
        proxy_url=None,
        image_mode="0",
        manual=False,
        priority_label_setting_str=""
        ):
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

        logger.debug(f"DMM Search: original_keyword='{original_keyword_for_log}', dmm_keyword_for_url='{dmm_keyword_for_url}', priority_label='{priority_label_setting_str}'")

        # --- 검색 URL 생성 ---
        search_params = { 'redirect': '1', 'enc': 'UTF-8', 'category': '', 'searchstr': dmm_keyword_for_url }
        search_url = f"{cls.site_base_url}/search/?{py_urllib_parse.urlencode(search_params)}"
        logger.debug(f"Search URL: {search_url}")

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
            #    logger.debug(f"DMM Search HTML content saved to: {debug_html_path} due to no items found.")
            #except Exception as e_save_html: logger.error(f"DMM Search: Failed to save HTML for no items: {e_save_html}")
            return []

        ret_temp_before_filtering = []
        score = 60

        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                href = None; original_ps_url = None; content_type = "unknown" 

                # 1. 기본적인 정보 파싱
                title_link_tags_in_node = node.xpath('.//a[.//p[contains(@class, "text-link")]]') 
                img_link_tags_in_node = node.xpath('.//a[./img[@alt="Product"]]')

                primary_href_candidate = None
                if title_link_tags_in_node and title_link_tags_in_node[0].attrib.get("href", "").lower().count('/cid=') > 0 :
                    primary_href_candidate = title_link_tags_in_node[0].attrib.get("href", "").lower()
                elif img_link_tags_in_node and img_link_tags_in_node[0].attrib.get("href", "").lower().count('/cid=') > 0 :
                    primary_href_candidate = img_link_tags_in_node[0].attrib.get("href", "").lower()

                if not primary_href_candidate:
                    logger.debug("DMM Search Item: No primary link with cid found. Skipping.")
                    continue

                href = primary_href_candidate
                #logger.debug(f"DMM Search Item: Determined href for path check: '{href}'")

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

                # 코드 추출 (href 사용)
                match_cid_s = cls.PTN_SEARCH_CID.search(href) 
                if not match_cid_s: 
                    logger.warning(f"DMM Search Item: Could not extract CID from href '{href}'. Skipping.")
                    continue
                item.code = cls.module_char + cls.site_char + match_cid_s.group("code")

                # 중복 코드 체크
                if any(i_s.get("code") == item.code and i_s.get("content_type") == item.content_type for i_s in ret_temp_before_filtering):
                    logger.debug(f"DMM Search Item: Duplicate code and type, skipping: {item.code} ({item.content_type})")
                    continue

                # 2. item.ui_code 파싱 및 설정 (DMM의 cid로부터 최종 ui_code 생성)
                cid_part_for_parse = item.code[len(cls.module_char)+len(cls.site_char):]
                parsed_ui_code, label_for_score, num_raw_for_score = cls._parse_ui_code_from_cid(cid_part_for_parse, item.content_type)
                item.ui_code = parsed_ui_code.upper()

                # 제목 접두사 추가
                type_prefix = ""
                if content_type == 'dvd': type_prefix = "[DVD] "
                elif content_type == 'videoa': type_prefix = "[Digital] "
                elif content_type == 'bluray': type_prefix = "[Blu-ray] "

                # 3. item.title 설정
                title_p_tags_node = node.xpath('.//p[contains(@class, "text-link") and contains(@class, "line-clamp-2")]')
                raw_title_node = title_p_tags_node[0].text_content().strip() if title_p_tags_node else ""
                item.title = raw_title_node if raw_title_node and raw_title_node != "Not Found" else item.ui_code
                item.title = type_prefix + item.title

                # 4. item.score 계산
                # 점수 계산용 코드 생성
                item_code_for_strict_compare = ""
                item_ui_code_base_for_score = ""
                if label_for_score and num_raw_for_score:
                    item_code_for_strict_compare = label_for_score + num_raw_for_score.zfill(5)
                    item_ui_code_base_for_score = label_for_score + num_raw_for_score
                elif item.ui_code:
                    cleaned_ui_code_for_score = item.ui_code.replace("-","").lower()
                    item_code_for_strict_compare = cleaned_ui_code_for_score
                    item_ui_code_base_for_score = cleaned_ui_code_for_score

                #logger.debug(f"DMM Score Compare: dmm_keyword_for_url='{dmm_keyword_for_url}', item_code_for_strict_compare='{item_code_for_strict_compare}', item_ui_code_base_for_score='{item_ui_code_base_for_score}'")

                # 점수 부여
                current_score_val = 0

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

                # 5. manual 플래그에 따른 item.image_url 및 item.title_ko 최종 처리
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try: item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"DMM Search: ImgProcErr (manual):{e_img}")
                    item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + type_prefix + item.title

                # 6. EntityAVSearch 객체를 dict로 변환
                item_dict = item.as_dict()

                # 7. 캐시 저장
                if item_dict.get('code') and original_ps_url and item_dict.get('content_type'):
                    code_key_cache = item_dict['code']
                    content_type_cache = item_dict['content_type']

                    if code_key_cache not in cls._ps_url_cache:
                        cls._ps_url_cache[code_key_cache] = {}

                    cls._ps_url_cache[code_key_cache][content_type_cache] = original_ps_url

                    current_main_type_cache = cls._ps_url_cache[code_key_cache].get('main_content_type')
                    should_update_main_cache = True
                    if current_main_type_cache:
                        try:
                            if cls.CONTENT_TYPE_PRIORITY.index(content_type_cache) >= cls.CONTENT_TYPE_PRIORITY.index(current_main_type_cache):
                                should_update_main_cache = False
                        except ValueError: pass 

                    if should_update_main_cache:
                        cls._ps_url_cache[code_key_cache]['main_content_type'] = content_type_cache
                    # logger.debug(f"DMM PS Cache: Updated for '{code_key_cache}', type '{content_type_cache}'. Main: '{cls._ps_url_cache[code_key_cache].get('main_content_type')}'")

                # 8. "지정 레이블 최우선" 플래그 설정
                item_dict['is_priority_label_site'] = False 
                item_dict['site_key'] = cls.site_name

                ui_code_for_label_check = item_dict.get('ui_code', "")
                if ui_code_for_label_check and priority_label_setting_str: # priority_label_setting_str은 함수 파라미터
                    label_to_check = cls.get_label_from_ui_code(ui_code_for_label_check)
                    if label_to_check:
                        priority_labels_set = {lbl.strip().upper() for lbl in priority_label_setting_str.split(',') if lbl.strip()}
                        if label_to_check in priority_labels_set:
                            item_dict['is_priority_label_site'] = True
                            # logger.debug(f"DMM Search: Item '{ui_code_for_label_check}' matched PrioLabel '{label_to_check}'. Flag set True.")

                ret_temp_before_filtering.append(item_dict) # 최종적으로 수정된 딕셔너리를 리스트에 추가
            except Exception as e_inner_loop_dmm:
                logger.exception(f"DMM Search: 아이템 처리 중 예외 (keyword: '{original_keyword_for_log}'): {e_inner_loop_dmm}")

        # --- 2단계: Blu-ray 필터링 ---
        if not ret_temp_before_filtering: return []
        filtered_after_bluray = []
        dvd_ui_codes = {item_filter.get('ui_code') for item_filter in ret_temp_before_filtering if item_filter.get('content_type') == 'dvd' and item_filter.get('ui_code')}
        for item_to_check_bluray in ret_temp_before_filtering:
            item_content_type_filter = item_to_check_bluray.get('content_type')
            item_ui_code_filter = item_to_check_bluray.get('ui_code')
            # logger.debug(f"Processing item for filtering: Code={item_to_check_bluray.get('code')}, Type={item_content_type_filter}, UI Code={item_ui_code_filter}") # 로그 레벨 조정 또는 기존 유지
            is_bluray_to_filter = item_content_type_filter == 'bluray' and item_ui_code_filter is not None
            if is_bluray_to_filter:
                dvd_exists = item_ui_code_filter in dvd_ui_codes
                # logger.debug(f"  Item is Blu-ray. DVD exists for UI Code '{item_ui_code_filter}'? {dvd_exists}")
                if dvd_exists: logger.debug(f"Excluding Blu-ray item '{item_to_check_bluray.get('code')}' because DVD version exists.")
                else: filtered_after_bluray.append(item_to_check_bluray) 
            else: filtered_after_bluray.append(item_to_check_bluray)

        # --- 2.5단계: 접두사/접미사 변형판 필터링 (DOD 및 아울렛 포함) ---
        logger.debug(f"DMM Search: Starting Variant filtering (DOD, Outlet). Items before: {len(filtered_after_bluray)}")

        title_variants_map = {}
        other_content_types = [] # DVD/Blu-ray가 아닌 타입은 그대로 유지

        for item_to_filter in filtered_after_bluray:
            content_type = item_to_filter.get('content_type')
            original_title = item_to_filter.get('title', "")

            if content_type == 'dvd' or content_type == 'bluray':
                is_outlet = original_title.startswith('【アウトレット】')
                is_dod = original_title.endswith('（DOD）')

                base_title = original_title
                if is_outlet:
                    base_title = base_title.replace('【アウトレット】', '', 1).strip()
                if is_dod:
                    base_title = base_title.replace('（DOD）', '').strip()

                # 아이템 우선순위 값 (낮을수록 좋음)
                # 0: 일반판, 1: DOD만, 2: 아울렛만, 3: 아울렛+DOD
                priority_score = 0
                if is_outlet and is_dod:
                    priority_score = 3
                elif is_outlet:
                    priority_score = 2
                elif is_dod:
                    priority_score = 1

                item_to_filter['_variant_priority'] = priority_score # 임시 필드 추가

                if base_title not in title_variants_map:
                    title_variants_map[base_title] = item_to_filter
                else:
                    # 이미 해당 기본 제목의 아이템이 있다면, 우선순위 비교
                    existing_item = title_variants_map[base_title]
                    if priority_score < existing_item.get('_variant_priority', 99):
                        # 현재 아이템이 우선순위가 더 높으면 교체
                        logger.debug(f"DMM Variant Filter: Replacing item for base title '{base_title}'. Old: '{existing_item.get('title')}' (prio {existing_item.get('_variant_priority')}), New: '{original_title}' (prio {priority_score})")
                        title_variants_map[base_title] = item_to_filter
                    elif priority_score == existing_item.get('_variant_priority', 99):
                        # 우선순위가 같다면 (예: 일반판 vs 일반판 - 거의 발생 안 함, 또는 아울렛 vs 아울렛)
                        # 여기서는 추가적인 비교 없이 기존 것을 유지하거나, 다른 기준으로 선택 (예: 코드가 더 짧은 것 등)
                        # 일단은 기존 것 유지
                        logger.debug(f"DMM Variant Filter: Item for base title '{base_title}' with same priority {priority_score}. Keeping existing: '{existing_item.get('title')}' over '{original_title}'")
                        pass

            else: # DVD/Blu-ray가 아닌 타입은 그대로 리스트에 추가
                other_content_types.append(item_to_filter)

        # title_variants_map에서 최종 선택된 아이템들을 리스트로 변환
        final_filtered_list = list(title_variants_map.values())
        final_filtered_list.extend(other_content_types) # 다른 타입 아이템들 다시 합치기

        # 임시 필드 제거
        for item_final in final_filtered_list:
            item_final.pop('_variant_priority', None)

        logger.debug(f"DMM Search: Variant filtering complete. Items after: {len(final_filtered_list)}")

        # --- 3단계: 최종 결과 처리 ---
        final_result = final_filtered_list
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
            return cls.__search(keyword_tmps_for_dmm[0] + "-" + keyword_tmps_for_dmm[1].zfill(6), 
                                do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual,
                                priority_label_setting_str=priority_label_setting_str)

        return sorted_result

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            do_trans_arg = kwargs.get('do_trans', True)
            proxy_url_arg = kwargs.get('proxy_url', None)
            image_mode_arg = kwargs.get('image_mode', "0")
            manual_arg = kwargs.get('manual', False)
            priority_label_str_arg = kwargs.get('priority_label_setting_str', "")

            data_list = cls.__search(keyword, do_trans=do_trans_arg, proxy_url=proxy_url_arg, image_mode=image_mode_arg, manual=manual_arg, priority_label_setting_str=priority_label_str_arg)

        except Exception as exception:
            logger.exception("SearchErr:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data_list else "no_match"
            ret["data"] = data_list
        return ret

    @classmethod
    def __img_urls(cls, tree, content_type='unknown', now_printing_path=None, proxy_url=None):
        #logger.debug(f"DMM __img_urls: Extracting raw image URLs for type: {content_type}")
        img_urls_dict = {'ps': "", 'pl': "", 'arts': [], 'specific_poster_candidates': []}

        try:
            if content_type == 'videoa' or content_type == 'vr':
                sample_image_links = tree.xpath('//div[@id="sample-image-block"]//a[.//img]')
                if not sample_image_links: # a 태그가 없는 경우 img src 직접 사용
                    all_img_tags_src = tree.xpath('//div[@id="sample-image-block"]//img/@src')
                    if not all_img_tags_src: return img_urls_dict # 이미지조차 없으면 반환

                    # 첫 번째 이미지를 PL로, 나머지를 Art로
                    if all_img_tags_src:
                        pl_candidate_url = py_urllib_parse.urljoin(cls.site_base_url, all_img_tags_src[0].strip())
                        # 플레이스홀더 검사
                        if not (now_printing_path and SiteUtil.are_images_visually_same(pl_candidate_url, now_printing_path, proxy_url=proxy_url)):
                            img_urls_dict['pl'] = pl_candidate_url

                        temp_arts_from_img_tags = []
                        for src in all_img_tags_src[1:]:
                            art_url = py_urllib_parse.urljoin(cls.site_base_url, src.strip())
                            if art_url != img_urls_dict.get('pl') and art_url not in temp_arts_from_img_tags:
                                # 플레이스홀더 검사
                                if not (now_printing_path and SiteUtil.are_images_visually_same(art_url, now_printing_path, proxy_url=proxy_url)):
                                    temp_arts_from_img_tags.append(art_url)
                        img_urls_dict['arts'] = temp_arts_from_img_tags
                        # specific_poster_candidates는 arts 기반으로 생성
                        if img_urls_dict['arts']:
                            img_urls_dict['specific_poster_candidates'].append(img_urls_dict['arts'][0])
                            if len(img_urls_dict['arts']) > 1 and img_urls_dict['arts'][-1] != img_urls_dict['arts'][0]:
                                img_urls_dict['specific_poster_candidates'].append(img_urls_dict['arts'][-1])
                    return img_urls_dict

                # a 태그가 있는 경우 (href 우선, 없으면 img src)
                temp_arts_list_for_processing = [] # 모든 유효한 이미지 URL (순서 유지, 중복 없음)
                seen_urls_in_videoa_vr = set() # 빠른 중복 체크용

                for a_tag in sample_image_links:
                    final_image_url = None
                    href = a_tag.attrib.get("href", "").strip()
                    img_src_list = a_tag.xpath('.//img/@src') # img 태그는 항상 있다고 가정
                    img_src = img_src_list[0].strip() if img_src_list else ""

                    # href가 이미지 URL 형태이면 href 우선
                    if href and re.search(r'\.(jpg|jpeg|png|webp)$', href, re.IGNORECASE):
                        final_image_url = py_urllib_parse.urljoin(cls.site_base_url, href)
                    elif img_src and re.search(r'\.(jpg|jpeg|png|webp)$', img_src, re.IGNORECASE): # 아니면 img_src 사용
                        final_image_url = py_urllib_parse.urljoin(cls.site_base_url, img_src)

                    if final_image_url and final_image_url not in seen_urls_in_videoa_vr:
                        # 플레이스홀더 검사
                        if not (now_printing_path and SiteUtil.are_images_visually_same(final_image_url, now_printing_path, proxy_url=proxy_url)):
                            temp_arts_list_for_processing.append(final_image_url)
                            seen_urls_in_videoa_vr.add(final_image_url)

                # PL 결정 (파일명 기반 또는 첫 번째 이미지)
                processed_pl_v = None
                for url_idx, url_item in enumerate(temp_arts_list_for_processing):
                    filename = url_item.split('/')[-1].lower()
                    is_pl_type = filename.endswith("pl.jpg") or filename.endswith("jp-0.jpg") # jp-0.jpg는 Video A 메인 이미지일 수 있음
                    if is_pl_type:
                        processed_pl_v = url_item
                        # PL로 선택된 이미지는 temp_arts_list_for_processing에서 제거 (또는 아래 arts 리스트 만들 때 제외)
                        # temp_arts_list_for_processing.pop(url_idx) # 제거 시 인덱스 문제 주의
                        break
                if not processed_pl_v and temp_arts_list_for_processing: # PL 못찾았으면 첫번째를 PL로
                    processed_pl_v = temp_arts_list_for_processing[0]

                img_urls_dict['pl'] = processed_pl_v if processed_pl_v else ""

                # Arts 및 Specific Candidates 결정 (순서 유지, 중복 없음)
                remaining_arts_v = []
                if temp_arts_list_for_processing:
                    for url_item_art in temp_arts_list_for_processing:
                        if url_item_art != processed_pl_v: # PL로 사용된 URL 제외
                            if url_item_art not in remaining_arts_v: # 이미 추가된 Art가 아니면
                                remaining_arts_v.append(url_item_art)
                img_urls_dict['arts'] = remaining_arts_v

                if remaining_arts_v: # PL 제외된 아트 목록에서 specific 후보 추출
                    img_urls_dict['specific_poster_candidates'].append(remaining_arts_v[0])
                    if len(remaining_arts_v) > 1 and remaining_arts_v[-1] != remaining_arts_v[0]:
                        img_urls_dict['specific_poster_candidates'].append(remaining_arts_v[-1])

            elif content_type == 'dvd' or content_type == 'bluray':
                # --- DVD/Blu-ray 타입 이미지 추출 로직 ---
                temp_pl_dvd = ""
                temp_arts_list_dvd = []
                seen_high_res_urls = set()

                # 1. 메인 패키지 이미지 (PL 후보)
                package_li_node = tree.xpath('//ul[@id="sample-image-block"]/li[contains(@class, "layout-sampleImage__item") and .//a[@name="package-image"]][1]')
                if package_li_node:
                    img_tag_in_pkg_li = package_li_node[0].xpath('.//img')
                    if img_tag_in_pkg_li:
                        thumb_url_raw_pkg = img_tag_in_pkg_li[0].attrib.get("data-lazy") or img_tag_in_pkg_li[0].attrib.get("src")
                        if thumb_url_raw_pkg:
                            thumb_url_pkg = thumb_url_raw_pkg.strip()
                            if not thumb_url_pkg.lower().endswith("dummy_ps.gif"):
                                if not thumb_url_pkg.startswith("http"):
                                    thumb_url_pkg = py_urllib_parse.urljoin(cls.site_base_url, thumb_url_pkg)

                                if thumb_url_pkg.endswith("ps.jpg"):
                                    temp_pl_dvd = thumb_url_pkg.replace("ps.jpg", "pl.jpg")

                                if temp_pl_dvd and not (now_printing_path and SiteUtil.are_images_visually_same(temp_pl_dvd, now_printing_path, proxy_url=proxy_url)):
                                    img_urls_dict['pl'] = temp_pl_dvd
                                    seen_high_res_urls.add(temp_pl_dvd)
                                    logger.debug(f"DMM __img_urls ({content_type}): Package Image (PL) inferred: {temp_pl_dvd}")

                if not img_urls_dict['pl']: # 위에서 PL 못 찾았으면, 기존 fn-sampleImage-imagebox 방식 시도
                    package_img_xpath_alt = '//div[@id="fn-sampleImage-imagebox"]/img/@src'
                    package_img_tags_alt = tree.xpath(package_img_xpath_alt)
                    if package_img_tags_alt:
                        raw_pkg_img_url_alt = package_img_tags_alt[0].strip()
                        if raw_pkg_img_url_alt:
                            candidate_pl_url_alt = ""
                            if raw_pkg_img_url_alt.startswith("//"): candidate_pl_url_alt = "https:" + raw_pkg_img_url_alt
                            elif not raw_pkg_img_url_alt.startswith("http"): candidate_pl_url_alt = py_urllib_parse.urljoin(cls.site_base_url, raw_pkg_img_url_alt)
                            else: candidate_pl_url_alt = raw_pkg_img_url_alt
                            if candidate_pl_url_alt and not (now_printing_path and SiteUtil.are_images_visually_same(candidate_pl_url_alt, now_printing_path, proxy_url=proxy_url)):
                                img_urls_dict['pl'] = candidate_pl_url_alt
                                seen_high_res_urls.add(candidate_pl_url_alt)
                                logger.debug(f"DMM __img_urls ({content_type}): Package Image (PL from fn-sampleImage-imagebox) extracted: {img_urls_dict['pl']}.")

                # 2. 샘플 이미지에서 Art 추출 (name="sample-image"인 것들)
                sample_li_nodes = tree.xpath('//ul[@id="sample-image-block"]/li[contains(@class, "layout-sampleImage__item") and .//a[@name="sample-image"]]')
                logger.debug(f"DMM __img_urls ({content_type}): Found {len(sample_li_nodes)} sample <li> tags for art inference.")

                for li_node in sample_li_nodes:
                    img_tag = li_node.xpath('.//img')
                    if not img_tag: continue

                    thumb_url_raw = img_tag[0].attrib.get("data-lazy") or img_tag[0].attrib.get("src")
                    if not thumb_url_raw: continue

                    thumb_url = thumb_url_raw.strip()
                    if thumb_url.lower().endswith("dummy_ps.gif"): continue
                    if not thumb_url.startswith("http"):
                        thumb_url = py_urllib_parse.urljoin(cls.site_base_url, thumb_url)
                    
                    high_res_candidate_url = None
                    # 새로운 패턴: .../xxxx-N.jpg -> .../xxxxjp-N.jpg
                    # 예: https://pics.dmm.co.jp/digital/video/venu00354/venu00354-1.jpg -> .../venu00354jp-1.jpg
                    match_new_pattern = re.search(r'^(.*)-(\d+\.(?:jpg|jpeg|png|webp))$', thumb_url, re.IGNORECASE)
                    if match_new_pattern:
                        base_path_part = match_new_pattern.group(1)
                        numeric_suffix_with_ext = match_new_pattern.group(2)
                        high_res_candidate_url = f"{base_path_part}jp-{numeric_suffix_with_ext}"
                    
                    if high_res_candidate_url and high_res_candidate_url not in seen_high_res_urls:
                        if not (now_printing_path and SiteUtil.are_images_visually_same(high_res_candidate_url, now_printing_path, proxy_url=proxy_url)):
                            temp_arts_list_dvd.append(high_res_candidate_url)
                            seen_high_res_urls.add(high_res_candidate_url)
                            # logger.debug(f"DMM DVD/BR Art: Added inferred high-res URL (-N to jp-N): {high_res_candidate_url}")
                
                img_urls_dict['arts'] = temp_arts_list_dvd
                
                # specific_poster_candidates 설정
                if temp_arts_list_dvd:
                    if temp_arts_list_dvd[0] not in img_urls_dict['specific_poster_candidates']:
                        img_urls_dict['specific_poster_candidates'].append(temp_arts_list_dvd[0])
                    if len(temp_arts_list_dvd) > 1 and \
                       temp_arts_list_dvd[-1] != temp_arts_list_dvd[0] and \
                       temp_arts_list_dvd[-1] not in img_urls_dict['specific_poster_candidates']:
                        img_urls_dict['specific_poster_candidates'].append(temp_arts_list_dvd[-1])
                # === DVD/Blu-ray 타입 이미지 추출 로직 끝 ===

            else: 
                logger.error(f"DMM __img_urls: Unknown content type '{content_type}' for image extraction.")
        
        except Exception as e_img_urls_main:
            logger.exception(f"DMM __img_urls ({content_type}): General error extracting image URLs: {e_img_urls_main}")

        logger.debug(f"DMM __img_urls ({content_type}) Final: PL='{img_urls_dict.get('pl', '')}', SpecificCandidatesCount={len(img_urls_dict.get('specific_poster_candidates',[]))}, ArtsCount={len(img_urls_dict.get('arts',[]))}.")
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
            #logger.debug(f"DMM Trailer Helper ({current_content_type_for_log}): Accessing AJAX URL: {ajax_url}")

            ajax_headers = cls._get_request_headers(referer=detail_url_for_referer)
            ajax_headers.update({'Accept': 'text/html, */*; q=0.01', 'X-Requested-With': 'XMLHttpRequest'})

            ajax_res = SiteUtil.get_response(ajax_url, proxy_url=proxy_url, headers=ajax_headers)

            if ajax_res and ajax_res.status_code == 200 and ajax_res.text.strip():
                iframe_tree = html.fromstring(ajax_res.text)
                iframe_srcs = iframe_tree.xpath("//iframe/@src")

                if iframe_srcs:
                    iframe_url = py_urllib_parse.urljoin(ajax_url, iframe_srcs[0])
                    #logger.debug(f"DMM Trailer Helper ({current_content_type_for_log}): Accessing iframe URL: {iframe_url}")
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
                    logger.debug(f"DMM VR Trailer Fallback: Found sampleUrl: {trailer_url}")
        except Exception as e_fallback:
            logger.exception(f"DMM VR Trailer Fallback: Exception for CID {cid_part}: {e_fallback}")
        return trailer_url


    @classmethod
    def __info( 
        cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, **kwargs 
    ):
        # === 1. 설정값 로드, 캐시 로드, 페이지 로딩, Entity 초기화 ===
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        ps_to_poster_labels_str = kwargs.get('ps_to_poster_labels_str', '')
        crop_mode_settings_str = kwargs.get('crop_mode_settings_str', '')

        cached_data = cls._ps_url_cache.get(code, {}) # 기존 변수명 cached_data 사용

        # content_type_from_cache 초기화 및 값 할당 (기존 변수명 사용)
        content_type_from_cache = cached_data.get('main_content_type', 'unknown') # <<--- 기본값을 'unknown' 문자열로 명시

        if (content_type_from_cache == 'unknown' or not cached_data.get(content_type_from_cache)) and cached_data: 
            found_type_from_prio = False
            for prio_type in cls.CONTENT_TYPE_PRIORITY:
                if prio_type in cached_data and cached_data.get(prio_type): 
                    content_type_from_cache = prio_type
                    found_type_from_prio = True
                    break
            if not found_type_from_prio: 
                content_type_from_cache = 'unknown'

        ps_url_from_search_cache = cached_data.get(content_type_from_cache) if content_type_from_cache != 'unknown' else None

        #logger.debug(f"DMM Info: Using content_type_from_cache: '{content_type_from_cache}' for page load. PS from cache for this type: {'Yes' if ps_url_from_search_cache else 'No'}")
        #logger.debug(f"DMM Info: Using content_type_from_cache: PS from cache: {ps_url_from_search_cache}")

        # 페이지 로드 및 파싱에 사용될 content_type (기존 변수명 current_content_type 유지)
        current_content_type = content_type_from_cache 
        if current_content_type == 'unknown':
            current_content_type = 'videoa' 
            logger.warning(f"DMM Info: content_type_from_cache is 'unknown'. Defaulting page load to '{current_content_type}'.")

        #logger.debug(f"DMM Info: Starting for {code}. Type to load: '{current_content_type}'. ImgMode: {image_mode}, UseImgServ: {use_image_server}")

        if not cls._ensure_age_verified(proxy_url=proxy_url):
            logger.error(f"DMM Info ({current_content_type}): Age verification failed for {code}.")
            return None

        cid_part = code[len(cls.module_char)+len(cls.site_char):] # 기존 방식대로 접두사 길이 사용
        detail_url = None

        if current_content_type == 'videoa' or current_content_type == 'vr':
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
        elif current_content_type == 'dvd' or current_content_type == 'bluray':
            detail_url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={cid_part}/"
        else: 
            logger.error(f"DMM Info: Invalid current_content_type '{current_content_type}'. Code: {code}")
            return None 

        logger.debug(f"DMM Info (Processing as {current_content_type}): Accessing detail page: {detail_url}")
        referer = cls.fanza_av_url if current_content_type in ['videoa', 'vr'] else (cls.site_base_url + "/mono/dvd/")
        headers = cls._get_request_headers(referer=referer)

        tree = None
        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=headers, timeout=30, verify=False)
            if tree is None: 
                logger.error(f"DMM Info ({current_content_type}): Failed to get page tree for {code}. URL: {detail_url}")
                if (content_type_from_cache == 'unknown' or content_type_from_cache == 'videoa') and current_content_type == 'videoa':
                    logger.debug(f"DMM Info: Retrying with DVD path for {code} as videoa failed.")
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
            #logger.debug(f"DMM Info (Parsing as {entity.content_type}): Metadata for {code}...")

            # --- DMM 타입별 메타데이터 파싱 로직 ---
            if entity.content_type == 'videoa' or entity.content_type == 'vr':
                # videoa/vr 파싱
                raw_title_text_v = ""
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

                        if value_text_all_v:
                            logger.debug(f"DMM Info: Parsed '品番' value from page: '{key_v}' for {code}.")

                            parsed_ui_code_page, _, _ = cls._parse_ui_code_from_cid(value_text_all_v, entity.content_type)
                            entity.ui_code = parsed_ui_code_page

                            ui_code_for_image = parsed_ui_code_page.lower()
                            entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image.upper()
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

                        if value_text_all_dvd:
                            logger.debug(f"DMM Info: Parsed '品番' value from page: '{key_dvd}' for {code}.")

                            parsed_ui_code_page, _, _ = cls._parse_ui_code_from_cid(value_text_all_dvd, entity.content_type)
                            entity.ui_code = parsed_ui_code_page

                            ui_code_for_image = parsed_ui_code_page.lower()
                            entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image.upper()
                            identifier_parsed = True
                            # logger.debug(f"DMM ({entity.content_type}): 品番 파싱 완료, ui_code_for_image='{ui_code_for_image}'")

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
        logger.debug(f"DMM Info: PS url from cache: {ps_url_from_search_cache}")

        now_printing_path = None
        if use_image_server and image_server_local_path:
            now_printing_path = os.path.join(image_server_local_path, "now_printing.jpg")
            if not os.path.exists(now_printing_path): now_printing_path = None

        # proxy_url은 __info의 파라미터로 이미 존재
        raw_image_urls = cls.__img_urls(tree, 
                                        content_type=entity.content_type, 
                                        now_printing_path=now_printing_path,
                                        proxy_url=proxy_url)

        pl_url = raw_image_urls.get('pl')
        specific_candidates_on_page = raw_image_urls.get('specific_poster_candidates', []) 
        other_arts_on_page = raw_image_urls.get('arts', [])

        final_poster_source = None
        final_poster_crop_mode = None
        final_landscape_source = None
        arts_urls_for_processing = [] 

        if not skip_default_landscape_logic:
            final_landscape_source = pl_url

        # --- 현재 아이템에 대한 PS 강제 사용 여부 및 크롭 모드 결정 ---
        apply_ps_to_poster_for_this_item = False
        forced_crop_mode_for_this_item = None

        if hasattr(entity, 'ui_code') and entity.ui_code:
            # 1. entity.ui_code에서 비교용 레이블 추출
            label_from_ui_code = ""
            if '-' in entity.ui_code:
                temp_label_part = entity.ui_code.split('-',1)[0]
                label_from_ui_code = temp_label_part.upper()

            if label_from_ui_code:
                # 2. PS 강제 사용 여부 결정
                if ps_to_poster_labels_str:
                    ps_force_labels_list = [x.strip().upper() for x in ps_to_poster_labels_str.split(',') if x.strip()]
                    if label_from_ui_code in ps_force_labels_list:
                        apply_ps_to_poster_for_this_item = True
                        logger.debug(f"[{cls.site_name} Info] PS to Poster WILL BE APPLIED for label '{label_from_ui_code}' based on settings.")

                # 3. 크롭 모드 결정 (PS 강제 사용이 아닐 때만 의미 있을 수 있음)
                if crop_mode_settings_str:
                    for line in crop_mode_settings_str.splitlines():
                        if not line.strip(): continue
                        parts = [x.strip() for x in line.split(":", 1)]
                        if len(parts) == 2:
                            setting_label = parts[0].upper()
                            setting_mode = parts[1].lower()
                            if setting_label == label_from_ui_code and setting_mode in ["r", "l", "c"]:
                                forced_crop_mode_for_this_item = setting_mode
                                logger.debug(f"[{cls.site_name} Info] Forced crop mode '{forced_crop_mode_for_this_item}' WILL BE APPLIED for label '{label_from_ui_code}'.")
                                break

            # 포스터 결정 로직 (if not skip_default_poster_logic: 내부)
            if not skip_default_poster_logic:
                # --- 우선순위 1: "포스터 예외처리 2" (사용자 지정 크롭 모드) ---
                if forced_crop_mode_for_this_item and pl_url:
                    logger.info(f"[{cls.site_name} Info] Poster determined by FORCED 'crop_mode={forced_crop_mode_for_this_item}'. Using PL: {pl_url}")
                    final_poster_source = pl_url
                    final_poster_crop_mode = forced_crop_mode_for_this_item

                # --- 위에서 사용자 지정 크롭으로 포스터가 결정되지 *않았을* 경우에만 다음 로직 진행 ---
                if final_poster_source is None: 
                    if ps_url_from_search_cache: # PS Cache가 있는 경우
                        logger.debug(f"[{cls.site_name} Info] PS cache exists ('{ps_url_from_search_cache}'). Evaluating PS-based poster options.")

                        # --- 우선순위 2 (PS 있을 때): "포스터 예외처리 1" (PS 강제 사용) ---
                        if apply_ps_to_poster_for_this_item:
                            logger.info(f"[{cls.site_name} Info] Poster determined by FORCED 'ps_to_poster' setting. Using PS: {ps_url_from_search_cache}")
                            final_poster_source = ps_url_from_search_cache
                            final_poster_crop_mode = None

                        # --- 우선순위 3 (PS 있을 때): 일반적인 포스터 결정 로직 ---
                        # (위 PS 강제 설정이 적용되지 않았을 때만 실행)
                        else: # apply_ps_to_poster_for_this_item is False
                            logger.debug(f"[{cls.site_name} Info] Applying general poster determination with PS.")

                            # 3-A: is_hq_poster
                            if pl_url and SiteUtil.is_portrait_high_quality_image(pl_url, proxy_url=proxy_url):
                                if SiteUtil.is_hq_poster(ps_url_from_search_cache, pl_url, proxy_url=proxy_url):
                                    final_poster_source = pl_url
                            if final_poster_source is None and specific_candidates_on_page:
                                for art_candidate in specific_candidates_on_page:
                                    if SiteUtil.is_portrait_high_quality_image(art_candidate, proxy_url=proxy_url):
                                        if SiteUtil.is_hq_poster(ps_url_from_search_cache, art_candidate, proxy_url=proxy_url):
                                            final_poster_source = art_candidate; break

                            # 3-B: has_hq_poster
                            if final_poster_source is None:
                                if pl_url:
                                    crop_pos = SiteUtil.has_hq_poster(ps_url_from_search_cache, pl_url, proxy_url=proxy_url)
                                    if crop_pos:
                                        final_poster_source = pl_url
                                        final_poster_crop_mode = crop_pos
                                if final_poster_source is None and specific_candidates_on_page:
                                    for art_candidate in specific_candidates_on_page:
                                        crop_pos_art = SiteUtil.has_hq_poster(ps_url_from_search_cache, art_candidate, proxy_url=proxy_url)
                                        if crop_pos_art:
                                            final_poster_source = art_candidate
                                            final_poster_crop_mode = crop_pos_art; break

                            # --- 3-C. 특수 고정 크롭 처리 (해상도 기반) ---
                            if (final_poster_source is None or final_poster_source == ps_url_from_search_cache) and pl_url:
                                logger.debug(f"DMM Poster (Priority 3-C attempt with PS): Applying fixed-size crop logic for PL: {pl_url}")
                                try:
                                    pl_image_obj_for_fixed_crop = SiteUtil.imopen(pl_url, proxy_url=proxy_url)
                                    if pl_image_obj_for_fixed_crop:
                                        img_width, img_height = pl_image_obj_for_fixed_crop.size
                                        if img_width == 800 and 438 <= img_height <= 444:
                                            crop_box_fixed = (img_width - 380, 0, img_width, img_height) 
                                            cropped_pil_object = pl_image_obj_for_fixed_crop.crop(crop_box_fixed)
                                            if cropped_pil_object:
                                                final_poster_source = cropped_pil_object 
                                                final_poster_crop_mode = None
                                                logger.info(f"DMM: Fixed-size crop applied to PL (with PS). Poster is PIL object.")
                                except Exception as e_fixed_crop_dmm_ps:
                                    logger.error(f"DMM: Error during fixed-size crop (with PS): {e_fixed_crop_dmm_ps}")

                            # --- 우선순위 4 (PS 있는 경우의 폴백): PS 사용 ---
                            if final_poster_source is None: # 위 모든 PS 기반 비교 실패 시
                                logger.debug(f"DMM Poster (Priority 4 with PS - Fallback): Using PS as poster.")
                                final_poster_source = ps_url_from_search_cache
                                final_poster_crop_mode = None

                    else: # PS Cache가 없는 경우 (그리고 위에서 사용자 지정 크롭도 적용 안됨)
                        logger.warning(f"[{cls.site_name} Info] No PS url found (ps_url_from_search_cache is None). Poster cannot be determined by PS-based logic.")
                        final_poster_source = None 
                        final_poster_crop_mode = None

                # 최종 결정된 포스터 정보 로깅
                if final_poster_source:
                    logger.debug(f"[{cls.site_name} Info] Final Poster Decision - Source type: {type(final_poster_source)}, Crop: {final_poster_crop_mode}")
                    if isinstance(final_poster_source, str): logger.debug(f"  Source URL/Path: {final_poster_source[:150]}")
                else:
                    # 이 로그는 이제 PS가 없을 때, 그리고 사용자 지정 크롭도 없을 때만 발생해야 함.
                    logger.error(f"[{cls.site_name} Info] CRITICAL: No poster source could be determined for {code}")
                    final_poster_source = None # 명시적으로 None
                    final_poster_crop_mode = None

        # 팬아트 목록 결정
        arts_urls_for_processing = []

        all_potential_arts_from_page = raw_image_urls.get('arts', [])
        # logger.debug(f"DMM Info: Potential arts from __img_urls before filtering: {len(all_potential_arts_from_page)} items.")

        if all_potential_arts_from_page and max_arts > 0:
            urls_used_as_thumb = set()
            if final_landscape_source and not skip_default_landscape_logic and isinstance(final_landscape_source, str):
                urls_used_as_thumb.add(final_landscape_source)
            if final_poster_source and not skip_default_poster_logic and isinstance(final_poster_source, str):
                urls_used_as_thumb.add(final_poster_source)

            # logger.debug(f"DMM Info: URLs used as poster/landscape (to be excluded from fanart): {urls_used_as_thumb}")

            seen_for_fanart_processing = set()
            for art_url in all_potential_arts_from_page:
                if len(arts_urls_for_processing) >= max_arts:
                    #logger.debug(f"DMM Info: Reached max_arts ({max_arts}). Stopping fanart collection.")
                    break 

                if art_url and art_url not in urls_used_as_thumb and art_url not in seen_for_fanart_processing:
                    # 플레이스홀더 검사는 __img_urls에서 이미 수행되었다고 가정.
                    # 만약 이 단계에서도 플레이스홀더를 엄격히 걸러내고 싶다면,
                    # if now_printing_path and SiteUtil.are_images_visually_same(art_url, now_printing_path, proxy_url=proxy_url):
                    #     logger.debug(f"DMM Info: Skipping fanart '{art_url}' as it is a placeholder.")
                    #     continue
                    arts_urls_for_processing.append(art_url)
                    seen_for_fanart_processing.add(art_url)

            logger.debug(f"DMM Info: Final arts_urls_for_processing (count: {len(arts_urls_for_processing)}): {arts_urls_for_processing[:3]}...")
        elif max_arts == 0:
            logger.debug(f"DMM Info: max_arts is 0. No fanart will be processed.")

        logger.debug(f"DMM ({entity.content_type}): Final Images Decision - Poster='{str(final_poster_source)[:100] if final_poster_source else 'None'}' (Crop='{final_poster_crop_mode}'), Landscape='{final_landscape_source}', Fanarts_to_process({len(arts_urls_for_processing)})='{arts_urls_for_processing[:3]}...'")

        # entity.thumb 및 entity.fanart 채우기
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
            trailer_title_for_extra = entity.tagline if entity.tagline else entity.ui_code
            trailer_url_final = None

            try:
                cid_part = code[len(cls.module_char)+len(cls.site_char):]
                detail_url_for_referer = detail_url

                if entity.content_type == 'vr':
                    trailer_url_final, title_from_json = cls._get_dmm_video_trailer_from_args_json(cid_part, detail_url_for_referer, proxy_url, entity.content_type)
                    # title_from_json은 사용하지 않음
                    if not trailer_url_final:
                        trailer_url_final = cls._get_dmm_vr_trailer_fallback(cid_part, detail_url_for_referer, proxy_url)

                elif entity.content_type == 'videoa':
                    trailer_url_final, _ = cls._get_dmm_video_trailer_from_args_json(cid_part, detail_url_for_referer, proxy_url, entity.content_type)

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
                                logger.error(f"DMM DVD/BR Trailer: JSONDecodeError - {e_json_dvd}.")
                
                if trailer_url_final:
                    entity.extras.append(EntityExtra("trailer", trailer_title_for_extra, "mp4", trailer_url_final))
            
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
