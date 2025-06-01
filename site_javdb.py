# -*- coding: utf-8 -*-
import re
import traceback
from lxml import html
import os 
from framework import path_data
from PIL import Image
import urllib.parse as py_urllib_parse
import requests

from .entity_av import EntityAVSearch
from .entity_base import EntityMovie, EntityActor, EntityThumb, EntityExtra, EntityRatings
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

class SiteJavdb:
    site_name = 'javdb'
    site_base_url = 'https://javdb.com'
    module_char = 'C'
    site_char = 'J'

    @classmethod
    def __search(
        cls,
        keyword,
        do_trans=True,
        proxy_url=None,
        image_mode="0",
        manual=False,
        cf_clearance_cookie_value='',
        priority_label_setting_str=""
        ):
        original_keyword_for_log = keyword
        keyword_processed = keyword.strip().lower()
        if keyword_processed[-3:-1] == "cd": keyword_processed = keyword_processed[:-3]

        logger.debug(f"JavDB Search: original_keyword='{original_keyword_for_log}', keyword_processed='{keyword_processed}', manual={manual}, do_trans={do_trans}, proxy_urlSet={'Yes' if proxy_url else 'No'}")

        search_keyword_for_url = py_urllib_parse.quote_plus(keyword_processed)
        search_url = f"{cls.site_base_url}/search?q={search_keyword_for_url}&f=all"
        logger.debug(f"JavDB Search URL: {search_url}")

        custom_cookies = { 'over18': '1', 'locale': 'en' }
        if cf_clearance_cookie_value:
            custom_cookies['cf_clearance'] = cf_clearance_cookie_value
        else:
            logger.debug(f"JavDB Search: cf_clearance cookie not provided for keyword '{original_keyword_for_log}'. This might lead to Cloudflare challenges.")

        res_for_search = SiteUtil.get_response_cs(search_url, proxy_url=proxy_url, cookies=custom_cookies)

        if res_for_search is None:
            logger.error(f"JavDB Search: Failed to get response from SiteUtil.get_response_cs for '{original_keyword_for_log}'. Proxy used: {'Yes' if proxy_url else 'No'}. Check SiteUtil logs for specific error (e.g., 403).")
            return {'ret': 'error', 'data': f"Failed to get response object for '{original_keyword_for_log}'. Check proxy, network, or SiteUtil logs."}

        html_content_text = res_for_search.text

        if res_for_search.status_code != 200:
            logger.warning(f"JavDB Search: Status code {res_for_search.status_code} for URL: {res_for_search.url} (keyword: '{original_keyword_for_log}')")
            if "cf-error-details" in html_content_text or "Cloudflare to restrict access" in html_content_text:
                logger.error(f"JavDB Search: Cloudflare restriction page detected for '{original_keyword_for_log}' (potentially IP block or stricter rules).")
                return {'ret': 'error', 'data': 'Cloudflare restriction page (possibly IP block).'}
            if "Due to copyright restrictions" in html_content_text or "由於版權限制" in html_content_text:
                logger.error(f"JavDB Search: Access prohibited for '{original_keyword_for_log}' (country block).")
                return {'ret': 'error', 'data': 'Country block detected by JavDB.'}
            if "cf-challenge-running" in html_content_text or "Checking if the site connection is secure" in html_content_text or "Verifying you are human" in html_content_text:
                logger.error(f"JavDB Search: Cloudflare challenge page detected for '{original_keyword_for_log}'. cf_clearance cookie might be invalid or missing.")
                return {'ret': 'error', 'data': 'Cloudflare JS challenge page detected.'}
            return {'ret': 'error', 'data': f'HTTP Status: {res_for_search.status_code} for {original_keyword_for_log}.'}

        try:
            tree = html.fromstring(html_content_text)
        except Exception as e_parse:
            logger.error(f"JavDB Search: Failed to parse HTML for '{original_keyword_for_log}': {e_parse}")
            logger.error(traceback.format_exc())
            return {'ret': 'error', 'data': f"Failed to parse HTML content for '{original_keyword_for_log}'."}

        if tree is None:
            logger.warning(f"JavDB Search: Tree is None after parsing for '{original_keyword_for_log}'.")
            return {'ret': 'error', 'data': f"Parsed tree is None for '{original_keyword_for_log}'."}

        final_search_results_list = []
        keyword_lower_norm = keyword_processed.replace('-', '').replace(' ', '')
        processed_codes_in_search = set()

        item_list_xpath_expression = '//div[(contains(@class, "item-list") or contains(@class, "movie-list"))]//div[contains(@class, "item")]/a[contains(@class, "box")]'
        item_nodes = tree.xpath(item_list_xpath_expression)

        if not item_nodes: 
            no_results_message_xpath = tree.xpath('//div[contains(@class, "empty-message") and (contains(text(), "No videos found") or contains(text(), "沒有找到影片"))]')
            if no_results_message_xpath:
                logger.info(f"JavDB Search: 'No videos found' message on page for keyword '{original_keyword_for_log}'.")
                return []

            # --- XPath 실패 시 HTML 저장 로직 ---
            try:
                safe_keyword_for_filename = original_keyword_for_log.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')

                unique_suffix = os.urandom(4).hex() 

                debug_filename = f"javdb_xpath_fail_{safe_keyword_for_filename}_{unique_suffix}.html"
                debug_html_path = os.path.join(path_data, 'tmp', debug_filename)

                os.makedirs(os.path.join(path_data, 'debug'), exist_ok=True) 
                with open(debug_html_path, 'w', encoding='utf-8') as f:
                    f.write(html_content_text)
                logger.info(f"JavDB Search: XPath failed. HTML content for '{original_keyword_for_log}' saved to: {debug_html_path}")
            except Exception as e_save_html:
                logger.error(f"JavDB Search: Failed to save HTML content on XPath failure for '{original_keyword_for_log}': {e_save_html}")
            # --- HTML 저장 로직 끝 ---

            title_match = re.search(r'<title>(.*?)</title>', html_content_text, re.IGNORECASE | re.DOTALL)
            page_title_from_text = title_match.group(1).strip() if title_match else "N/A"
            logger.warning(f"JavDB Search: No item nodes found with XPath ('{item_list_xpath_expression}') for keyword '{original_keyword_for_log}'. Page title: '{page_title_from_text}'. HTML saved (if successful).")
            return []

        # --- 검색 결과 아이템 처리 루프 ---
        for node_a_tag in item_nodes[:10]:
            try:
                item = EntityAVSearch(cls.site_name)

                detail_link = node_a_tag.attrib.get('href', '').strip()
                if not detail_link or not detail_link.startswith("/v/"): 
                    logger.debug(f"JavDB Search Item: Invalid detail_link '{detail_link}'. Skipping.")
                    continue 

                item_code_match = re.search(r'/v/([^/?]+)', detail_link)
                if not item_code_match: 
                    logger.debug(f"JavDB Search Item: Could not extract item_code_raw from detail_link '{detail_link}'. Skipping.")
                    continue

                item_code_raw = item_code_match.group(1).strip()
                item.code = cls.module_char + cls.site_char + item_code_raw 

                # 중복된 item.code (모듈+사이트+ID) 방지
                if item.code in processed_codes_in_search:
                    logger.debug(f"JavDB Search Item: Duplicate item.code '{item.code}'. Skipping.")
                    continue
                processed_codes_in_search.add(item.code)

                # --- 나머지 정보 파싱 (기존 로직 유지) ---
                full_title_from_attr = node_a_tag.attrib.get('title', '').strip()
                video_title_node = node_a_tag.xpath('.//div[@class="video-title"]')

                visible_code_on_card = "" # 카드에 표시되는 품번 (예: "ABC-123")
                actual_title_on_card = "" # 카드에 표시되는 실제 제목

                if video_title_node:
                    strong_tag_node = video_title_node[0].xpath('./strong[1]')
                    if strong_tag_node and strong_tag_node[0].text:
                        visible_code_on_card = strong_tag_node[0].text.strip().upper()

                    temp_title_node = html.fromstring(html.tostring(video_title_node[0])) # 복사본으로 작업
                    for strong_el in temp_title_node.xpath('.//strong'): # 모든 strong 태그 제거
                        strong_el.getparent().remove(strong_el)
                    actual_title_on_card = temp_title_node.text_content().strip()

                # 제목 설정 우선순위
                if actual_title_on_card: item.title = actual_title_on_card
                elif full_title_from_attr: item.title = full_title_from_attr # a 태그의 title 속성
                elif visible_code_on_card: item.title = visible_code_on_card # 카드 품번
                else: item.title = item_code_raw.upper() # 최후에는 JavDB 내부 ID

                # ui_code는 카드에 보이는 품번 우선, 없으면 JavDB 내부 ID
                item.ui_code = visible_code_on_card if visible_code_on_card else item_code_raw.upper()
                
                # 이미지 URL
                item_img_tag_src = node_a_tag.xpath('.//div[contains(@class, "cover")]/img/@src')
                item.image_url = ""
                if item_img_tag_src:
                    img_url_raw = item_img_tag_src[0].strip()
                    if img_url_raw.startswith("//"): item.image_url = "https:" + img_url_raw
                    elif img_url_raw.startswith("http"): item.image_url = img_url_raw
                    # JavDB는 보통 // 아니면 http(s)로 시작. 상대경로 거의 없음.

                # 출시년도
                item.year = 0 # 기본값
                date_meta_text_nodes = node_a_tag.xpath('.//div[@class="meta"]/text()')
                premiered_date_str = "" # 디버깅용
                if date_meta_text_nodes:
                    for text_node_val in reversed(date_meta_text_nodes): # 뒤에서부터 찾아야 날짜일 확률 높음
                        date_str_candidate = text_node_val.strip()
                        # JavDB 날짜 형식 예: "2023-01-15", "15/01/2023" 등 다양할 수 있으므로, 연도만 정확히 추출
                        date_match_year_only = re.search(r'(\d{4})', date_str_candidate) # 4자리 숫자(연도) 찾기
                        if date_match_year_only:
                            premiered_date_str = date_str_candidate # 참고용 날짜 문자열
                            try: item.year = int(date_match_year_only.group(1))
                            except ValueError: pass
                            break # 연도 찾으면 중단

                # 번역 처리 (manual 플래그 및 do_trans에 따라)
                if manual: 
                    # image_mode는 logic_jav_censored에서 처리하므로 여기서는 원본 URL 반환
                    item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                elif do_trans and item.title: 
                    item.title_ko = SiteUtil.trans(item.title, source='ja', target='ko')
                else: 
                    item.title_ko = item.title

                # 점수 계산 (keyword_lower_norm은 전처리된 검색어)
                current_score = 0
                # item.ui_code를 정규화하여 비교
                item_ui_code_norm = item.ui_code.lower().replace('-', '').replace(' ', '')
                if keyword_lower_norm == item_ui_code_norm: current_score = 100
                elif keyword_processed == item.ui_code.lower(): current_score = 95 # 하이픈 포함 원본 검색어와 일치
                elif keyword_lower_norm in item_ui_code_norm : current_score = 85 # 정규화된 ui_code에 포함
                elif item.title and keyword_processed in item.title.lower(): current_score = 60 # 제목에 포함
                else: current_score = 20
                item.score = current_score

                item_dict = item.as_dict()

                item_dict['is_priority_label_site'] = False 
                item_dict['site_key'] = cls.site_name 

                if item_dict.get('ui_code') and priority_label_setting_str:
                    label_to_check = ""
                    if '-' in item_dict['ui_code']:
                        label_to_check = item_dict['ui_code'].split('-', 1)[0].upper()
                    else:
                        match_label_no_hyphen = re.match(r'^([A-Z]+)', item_dict['ui_code'].upper())
                        if match_label_no_hyphen: label_to_check = match_label_no_hyphen.group(1)
                        else: label_to_check = item_dict['ui_code'].upper()

                    if label_to_check:
                        priority_labels_set = {lbl.strip().upper() for lbl in priority_label_setting_str.split(',') if lbl.strip()}
                        if label_to_check in priority_labels_set:
                            item_dict['is_priority_label_site'] = True
                            logger.debug(f"JavDB Search: Item '{item_dict['ui_code']}' matched priority label '{label_to_check}'. Setting is_priority_label_site=True.")
                
                final_search_results_list.append(item_dict)
                # logger.debug(f"  JavDB Parsed: code={item.code}, score={item.score}, title='{item.title_ko}', year={item.year}, ui_code='{item.ui_code}'")

            except Exception as e_item_parse:
                logger.error(f"JavDB Search Item (keyword: '{original_keyword_for_log}'): Error parsing item: {e_item_parse}")
                logger.error(traceback.format_exc())
                # 개별 아이템 파싱 실패 시 해당 아이템은 건너뛰고 계속 진행
        
        # 루프 종료 후 정렬된 결과 반환
        return sorted(final_search_results_list, key=lambda k: k.get("score", 0), reverse=True)


    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            do_trans_arg = kwargs.get('do_trans', True)
            proxy_url_arg = kwargs.get('proxy_url', None)
            image_mode_arg = kwargs.get('image_mode', '0')
            manual_arg = kwargs.get('manual', False)
            cf_clearance_cookie_value_arg = kwargs.get('cf_clearance_cookie_value', '')
            priority_label_str_arg = kwargs.get('priority_label_setting_str', "")
            data = cls.__search(keyword,
                                do_trans=do_trans_arg,
                                proxy_url=proxy_url_arg,
                                image_mode=image_mode_arg,
                                manual=manual_arg,
                                cf_clearance_cookie_value=cf_clearance_cookie_value_arg,
                                priority_label_setting_str=priority_label_str_arg)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data else "no_match"; ret["data"] = data
        return ret


    @classmethod
    def get_label_from_ui_code(cls, ui_code_str: str) -> str:
        if not ui_code_str or not isinstance(ui_code_str, str): 
            return ""
        ui_code_upper = ui_code_str.upper()
        if '-' in ui_code_upper:
            return ui_code_upper.split('-', 1)[0]
        else: 
            # 하이픈 없는 경우, 보통 레이블과 숫자가 붙어있음 (예: SIRO1234)
            # 앞부분의 연속된 알파벳을 레이블로 간주
            match = re.match(r'^([A-Z]+)', ui_code_upper)
            if match:
                return match.group(1)
            return ui_code_upper # 그래도 안되면 전체 반환 (숫자만 있는 경우 등)

    @classmethod
    def __info(cls, code, **kwargs):
        try:
            do_trans = kwargs.get('do_trans', True)
            proxy_url = kwargs.get('proxy_url', None)
            image_mode = kwargs.get('image_mode', '0')
            max_arts = kwargs.get('max_arts', 10) 
            use_image_server = kwargs.get('use_image_server', False)
            image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
            image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
            image_path_segment = kwargs.get('url_prefix_segment', 'jav/db') 
            user_defined_crop_mode = kwargs.get('crop_mode', None) # LogicJavCensored에서 전달된 사용자 지정 크롭 모드
            # ps_to_poster_labels_str은 JavDB에 의미 없으므로 받지 않거나 무시
            # priority_label_setting_str도 info 단계에서는 직접 사용 안함 (search에서 is_priority_label_site 플래그로 반영)
            use_extras_setting = kwargs.get('use_extras', True)
            cf_clearance_cookie_value = kwargs.get('cf_clearance_cookie', None)
            # crop_mode_settings_str은 사용자 UI의 "포스터 예외처리 2" 설정 문자열
            crop_mode_settings_str = kwargs.get('crop_mode_settings_str', '')


            custom_cookies = { 'over18': '1', 'locale': 'en' }
            if cf_clearance_cookie_value:
                custom_cookies['cf_clearance'] = cf_clearance_cookie_value

            original_code_for_url = code[len(cls.module_char) + len(cls.site_char):] # JavDB 내부 ID
            detail_url = f"{cls.site_base_url}/v/{original_code_for_url}"

            # logger.debug(f"JavDB Info: Accessing URL: {detail_url} for code {code}")
            res_info = SiteUtil.get_response_cs(detail_url, proxy_url=proxy_url, cookies=custom_cookies)

            if res_info is None or res_info.status_code != 200:
                status_code_val = res_info.status_code if res_info else "None"
                logger.warning(f"JavDB Info: Failed to get page or status not 200 for {code}. Status: {status_code_val}")
                if res_info and ("cf-error-details" in res_info.text or "Cloudflare to restrict access" in res_info.text):
                    logger.error(f"JavDB Info: Cloudflare restriction page detected for {code}.")
                return None

            html_info_text = res_info.text
            tree_info = html.fromstring(html_info_text)
            if tree_info is None:
                logger.warning(f"JavDB Info: Failed to parse detail page HTML for {code}.")
                return None

            entity = EntityMovie(cls.site_name, code)
            entity.country = ['일본'] 
            entity.mpaa = '청소년 관람불가'
            entity.thumb = []
            entity.fanart = []
            entity.extras = []
            entity.ratings = [] 

            # === 1. 메타데이터 파싱 시작 ===
            # 1. ui_code 파싱
            ui_code_from_panel = ""
            id_panel_block = tree_info.xpath('//div[@class="panel-block" and ./strong[contains(text(),"ID:")]]')
            if id_panel_block:
                id_label_part_nodes = id_panel_block[0].xpath('./span[@class="value"]/a/text()')
                id_num_part_nodes = id_panel_block[0].xpath('./span[@class="value"]/text()')
                ui_code_parts = []
                if id_label_part_nodes: ui_code_parts.append(id_label_part_nodes[0].strip().upper())
                for node_text in id_num_part_nodes:
                    cleaned_text = node_text.strip()
                    if cleaned_text and cleaned_text != '-': ui_code_parts.append(cleaned_text)
                ui_code_from_panel = "".join(ui_code_parts).replace(" ", "")

            h2_visible_code = ""
            h2_title_node_check = tree_info.xpath('//h2[@class="title is-4"]/strong[1]/text()')
            if h2_title_node_check: h2_visible_code = h2_title_node_check[0].strip().upper()

            entity.ui_code = ui_code_from_panel if ui_code_from_panel else (h2_visible_code if h2_visible_code else original_code_for_url.upper())
            entity.title = entity.ui_code
            entity.originaltitle = entity.ui_code 
            entity.sorttitle = entity.ui_code

            # 2. 실제 원본 제목 (Tagline 용도) 파싱
            actual_raw_title_text = ""
            h2_title_node = tree_info.xpath('//h2[@class="title is-4"]')
            if h2_title_node:
                current_title_node = h2_title_node[0].xpath('./strong[@class="current-title"]/text()')
                if current_title_node: actual_raw_title_text = current_title_node[0].strip()
                elif not actual_raw_title_text: 
                    all_strong_in_h2 = h2_title_node[0].xpath('./strong')
                    if len(all_strong_in_h2) > 1 and all_strong_in_h2[1].text:
                        actual_raw_title_text = all_strong_in_h2[1].text.strip()

            if actual_raw_title_text and actual_raw_title_text != entity.ui_code:
                entity.tagline = SiteUtil.trans(actual_raw_title_text, do_trans=do_trans, source='ja', target='ko')
            else: 
                entity.tagline = entity.ui_code 

            # 3. 나머지 상세 정보 패널 파싱
            if entity.ratings is None: entity.ratings = [] 

            panel_blocks_xpath = '//nav[contains(@class, "movie-panel-info")]/div[contains(@class,"panel-block")]'
            panel_blocks = tree_info.xpath(panel_blocks_xpath)

            for block in panel_blocks:
                strong_tag = block.xpath('./strong/text()')
                if not strong_tag: continue
                key = strong_tag[0].strip().lower().replace(':', '')
                value_nodes_a = block.xpath('./span[@class="value"]//a/text()')
                value_nodes_text = block.xpath('./span[@class="value"]/text()')
                value_text_combined = ""
                if value_nodes_a: value_text_combined = " ".join([v.strip() for v in value_nodes_a if v.strip()]).strip()
                elif value_nodes_text: value_text_combined = " ".join([v.strip() for v in value_nodes_text if v.strip()]).strip()
                if not value_text_combined: value_text_combined = block.xpath('normalize-space(./span[@class="value"])')

                if key == 'released date':
                    entity.premiered = value_text_combined
                    if entity.premiered:
                        try: entity.year = int(entity.premiered[:4])
                        except ValueError: logger.warning(f"JavDB Info: Year parse error from '{entity.premiered}'")
                elif key == 'duration':
                    duration_match = re.search(r'(\d+)', value_text_combined)
                    if duration_match: entity.runtime = int(duration_match.group(1))
                elif key == 'rating':
                    rating_match = re.search(r'([\d\.]+)\s*,\s*by\s*([\d,]+)\s*users', value_text_combined)
                    if rating_match:
                        try:
                            rating_val_original = float(rating_match.group(1))
                            votes_count = int(rating_match.group(2).replace(',', ''))
                            entity.ratings.append(EntityRatings(rating_val_original, max=5, name=cls.site_name, votes=votes_count))
                        except ValueError:
                            logger.warning(f"JavDB Info: Could not parse rating from text: '{value_text_combined}' for code {code}")
                elif key == 'director' and value_text_combined.lower() != 'n/a':
                    entity.director = SiteUtil.trans(value_text_combined, do_trans=do_trans, source='ja', target='ko')
                elif key == 'maker' and value_text_combined.lower() != 'n/a':
                    entity.studio = SiteUtil.trans(value_text_combined, do_trans=do_trans, source='ja', target='ko')
                elif key == 'series' and value_text_combined.lower() != 'n/a':
                    if entity.tag is None: entity.tag = []
                    series_name = SiteUtil.trans(value_text_combined, do_trans=do_trans, source='ja', target='ko')
                    if series_name not in entity.tag: entity.tag.append(series_name)
                elif key == 'tags': 
                    if entity.genre is None: entity.genre = []
                    genre_tags_from_panel = block.xpath('./span[@class="value"]/a/text()')
                    for genre_name_raw in genre_tags_from_panel:
                        genre_name = genre_name_raw.strip()
                        if genre_name:
                            trans_genre = SiteUtil.trans(genre_name, do_trans=do_trans, source='ja', target='ko')
                            if trans_genre not in entity.genre: entity.genre.append(trans_genre)
                elif key == 'actor(s)':
                    if entity.actor is None: entity.actor = []
                    actor_nodes_with_gender = block.xpath('./span[@class="value"]/a')
                    for actor_node in actor_nodes_with_gender:
                        actor_name_tag = actor_node.xpath('./text()')
                        gender_symbol_node = actor_node.xpath('./following-sibling::strong[1][self::strong[@class="symbol female"]]')
                        if actor_name_tag and gender_symbol_node: 
                            actor_name_original_lang = actor_name_tag[0].strip()
                            if actor_name_original_lang and actor_name_original_lang.lower() != 'n/a':
                                actor_entity = EntityActor(SiteUtil.trans(actor_name_original_lang, do_trans=do_trans, source='ja', target='ko'))
                                actor_entity.originalname = actor_name_original_lang
                                entity.actor.append(actor_entity)

            if not entity.plot:
                if entity.tagline and entity.tagline != entity.ui_code:
                    entity.plot = entity.tagline


            label_from_ui_code_for_settings = ""
            if hasattr(entity, 'ui_code') and entity.ui_code:
                ui_code_for_image = entity.ui_code
                label_from_ui_code_for_settings = cls.get_label_from_ui_code(entity.ui_code)
                logger.debug(f"[{cls.site_name} Info] Extracted label for settings: '{label_from_ui_code_for_settings}' from ui_code '{entity.ui_code}'")
            else:
                logger.warning(f"[{cls.site_name} Info] entity.ui_code not found after parsing. Using fallback for image filenames.")
                ui_code_for_image = code[len(cls.module_char)+len(cls.site_char):].upper().replace("_", "-")

            forced_crop_mode_for_this_item = None

            # 포스터 예외처리 플래그 결정
            forced_crop_mode_for_this_item = None
            if label_from_ui_code_for_settings:
                if crop_mode_settings_str:
                    for line in crop_mode_settings_str.splitlines():
                        if not line.strip(): continue
                        parts = [x.strip() for x in line.split(":", 1)]
                        if len(parts) == 2 and parts[0].upper() == label_from_ui_code_for_settings and parts[1].lower() in ["r", "l", "c"]:
                            forced_crop_mode_for_this_item = parts[1].lower(); break

            # === 3. 사용자 지정 포스터 확인 및 처리 ===
            user_custom_poster_url = None; user_custom_landscape_url = None
            skip_default_poster_logic = False; skip_default_landscape_logic = False
            if use_image_server and image_server_local_path and image_server_url and ui_code_for_image:
                poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
                landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]
                for suffix in poster_suffixes:
                    _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                    if web_url: user_custom_poster_url = web_url; entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url)); skip_default_poster_logic = True; logger.debug(f"MGStage ({cls.module_char}): Using user custom poster: {web_url}"); break 
                for suffix in landscape_suffixes:
                    _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                    if web_url: user_custom_landscape_url = web_url; entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url)); skip_default_landscape_logic = True; logger.debug(f"MGStage ({cls.module_char}): Using user custom landscape: {web_url}"); break

            # --- 기본 이미지 처리 로직 진입 조건 ---
            needs_default_image_processing = not skip_default_poster_logic or \
                                            not skip_default_landscape_logic or \
                                            (entity.fanart is None or (len(entity.fanart) < max_arts and max_arts > 0))

            # === 4. 기본 이미지 처리: 사용자 지정 이미지가 없거나, 팬아트가 더 필요한 경우 실행 ===
            final_poster_source = None; final_poster_crop_mode = None
            final_landscape_url_source = None
            fixed_size_crop_applied = False

            # === 2. 이미지 URL 추출 (PL, Arts) ===
            pl_url_raw = None

            main_cover_img_src_nodes = tree_info.xpath('//div[@class="column column-video-cover"]//img[@class="video-cover"]/@src')
            if main_cover_img_src_nodes:
                pl_url_raw = main_cover_img_src_nodes[0].strip()
                if pl_url_raw.startswith("//"):
                    pl_url = "https:" + pl_url_raw
                elif not pl_url_raw.startswith("http"): 
                    if pl_url_raw.startswith("/"):
                        pl_url = py_urllib_parse.urljoin(cls.site_base_url, pl_url_raw)
                    else: 
                        logger.warning(f"JavDB Info: Unexpected PL image src format (not // or http or /): {pl_url_raw}")
                else:
                    pl_url = pl_url_raw
            logger.debug(f"JavDB Info: Determined pl_url = '{pl_url}' after parsing page.")

            arts_urls = [] 
            sample_image_container = tree_info.xpath('//div[contains(@class, "preview-images")]')
            if sample_image_container:
                sample_image_nodes_info = sample_image_container[0].xpath('./a[@class="tile-item"]/@href')
                for art_link_raw in sample_image_nodes_info:
                    art_full_url_raw = art_link_raw.strip()
                    art_full_url = None
                    if art_full_url_raw:
                        if art_full_url_raw.startswith("//"): art_full_url = "https:" + art_full_url_raw
                        elif not art_full_url_raw.startswith("http"):
                            if art_full_url_raw.startswith("/"): art_full_url = py_urllib_parse.urljoin(cls.site_base_url, art_full_url_raw)
                            else: logger.warning(f"JavDB Info: Unexpected art_link_raw format: {art_full_url_raw}"); continue
                        else: art_full_url = art_full_url_raw
                    if art_full_url: arts_urls.append(art_full_url)
            logger.debug(f"JavDB Info: Collected {len(arts_urls)} arts_urls: {arts_urls[:5]}")

            # === 3. 사용자 지정 이미지 로드 ===
            skip_default_poster_logic = False
            skip_default_landscape_logic = False
            current_ui_code_for_image = entity.ui_code 
            if use_image_server and image_server_local_path and image_server_url and current_ui_code_for_image:
                poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
                landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]
                for suffix in poster_suffixes:
                    _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, current_ui_code_for_image, suffix, image_server_url)
                    if web_url: 
                        if not any(t.aspect == 'poster' and t.value == web_url for t in entity.thumb):
                            entity.thumb.append(EntityThumb(aspect="poster", value=web_url))
                        skip_default_poster_logic = True; logger.info(f"JavDB Info: Using user custom poster: {web_url}"); break 
                for suffix in landscape_suffixes:
                    _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, current_ui_code_for_image, suffix, image_server_url)
                    if web_url: 
                        if not any(t.aspect == 'landscape' and t.value == web_url for t in entity.thumb):
                            entity.thumb.append(EntityThumb(aspect="landscape", value=web_url))
                        skip_default_landscape_logic = True; logger.info(f"JavDB Info: Using user custom landscape: {web_url}"); break

            # === 4. 기본 이미지 처리 시작 ===
            final_poster_source = None 
            final_poster_crop_mode = None 
            final_landscape_url_source = None
            temp_poster_file_for_server_save = None

            if needs_default_image_processing:

                # 4-A. 유효한 PL URL 확정 (플레이스홀더 검사)
                valid_pl_url = None
                if pl_url:
                    is_placeholder = False
                    if use_image_server and image_server_local_path:
                        placeholder_path = os.path.join(image_server_local_path, 'javdb_no_img.jpg')
                        if os.path.exists(placeholder_path):
                            if SiteUtil.are_images_visually_same(pl_url, placeholder_path, proxy_url=proxy_url):
                                is_placeholder = True
                                logger.info(f"JavDB Info: PL URL ('{pl_url}') is a placeholder (javdb_no_img.jpg).")
                        # else: logger.debug(f"JavDB Info: Placeholder javdb_no_img.jpg not found at {placeholder_path}.")

                    if not is_placeholder:
                        valid_pl_url = pl_url

                if not valid_pl_url and not skip_default_poster_logic: # skip_default_poster_logic이 True면 이 로그는 불필요
                    logger.warning(f"JavDB Info: No valid PL URL for default poster (either not found on page or was placeholder). Code: {code}")

                # 랜드스케이프 소스 결정
                if valid_pl_url and not skip_default_landscape_logic:
                    final_landscape_url_source = valid_pl_url

                # --- 현재 아이템에 대한 크롭 모드 결정 ---
                forced_crop_mode_for_this_item = None

                if hasattr(entity, 'ui_code') and entity.ui_code:
                    # 1. entity.ui_code에서 비교용 레이블 추출
                    label_from_ui_code = ""
                    if '-' in entity.ui_code:
                        temp_label_part = entity.ui_code.split('-',1)[0]
                        label_from_ui_code = temp_label_part.upper()

                    if label_from_ui_code:
                        # 2. 크롭 모드 결정
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

                # 4-B. 포스터 결정 로직 (valid_pl_url이 있을 때만)
                if valid_pl_url and not skip_default_poster_logic:
                    final_poster_source = None 
                    final_poster_crop_mode = None     

                    # Prio 1: 사용자 지정 크롭 모드
                    if forced_crop_mode_for_this_item and pl_url: # 또는 valid_pl_candidate
                        logger.debug(f"[{cls.site_name} Info] Poster determined by FORCED 'crop_mode={forced_crop_mode_for_this_item}'. Using PL: {pl_url}")
                        final_poster_source = pl_url
                        final_poster_crop_mode = forced_crop_mode_for_this_item

                    # Prio 2: 해상도 기반 고정 크기 크롭
                    if final_poster_source is None:
                        try:
                            pl_image_obj_for_fixed_crop = SiteUtil.imopen(valid_pl_url, proxy_url=proxy_url)
                            if pl_image_obj_for_fixed_crop:
                                img_width, img_height = pl_image_obj_for_fixed_crop.size
                                if img_width == 800 and 438 <= img_height <= 444:
                                    crop_box_fixed = (img_width - 380, 0, img_width, img_height) 
                                    cropped_pil_object = pl_image_obj_for_fixed_crop.crop(crop_box_fixed)
                                    if cropped_pil_object:
                                        final_poster_source = cropped_pil_object
                                        final_poster_crop_mode = None
                                        logger.info(f"JavDB Poster (Prio 3): Fixed-size crop applied. Poster is PIL object.")
                        except Exception as e_fixed_crop:
                            logger.error(f"JavDB Poster (Prio 3): Error during fixed-size crop: {e_fixed_crop}")

                    # Prio 3: JavDB 스타일 PL 처리
                    if final_poster_source is None:
                        log_identifier_for_util = entity.ui_code if hasattr(entity, 'ui_code') and entity.ui_code else original_code_for_url
                        try:
                            poster_pil_or_url, rec_crop_mode_javdb, _ = SiteUtil.get_javdb_poster_from_pl_local(valid_pl_url, log_identifier_for_util, proxy_url=proxy_url)
                            if poster_pil_or_url: 
                                final_poster_source = poster_pil_or_url
                                final_poster_crop_mode = rec_crop_mode_javdb
                                logger.info(f"JavDB Poster (Prio 4): JavDB-style PL processing applied. Type: {type(final_poster_source)}, Crop: {final_poster_crop_mode}")
                        except Exception as e_javdb_style:
                            logger.error(f"JavDB Poster (Prio 4): Error during JavDB-style PL processing: {e_javdb_style}")

                    # Prio 4: 최종 일반 크롭 ('r')
                    if final_poster_source is None: 
                        logger.debug(f"JavDB Poster (Prio 5 - Fallback): Applying default right-crop to PL: {valid_pl_url}")
                        final_poster_source = valid_pl_url
                        final_poster_crop_mode = 'r'

                # 최종 결정된 포스터 정보 로깅
                if final_poster_source:
                    logger.debug(f"[{cls.site_name} Info] Final Poster Decision - Source type: {type(final_poster_source)}, Crop: {final_poster_crop_mode}")
                    if isinstance(final_poster_source, str): logger.debug(f"  Source URL/Path: {final_poster_source[:150]}")
                else:
                    logger.error(f"[{cls.site_name} Info] CRITICAL: No poster source could be determined for {code}")
                    final_poster_source = None
                    final_poster_crop_mode = None

            # === 5. 이미지 최종 적용 (서버 저장 또는 프록시) ===
            # 5-A. 이미지 서버 사용 시
            if use_image_server and image_mode == '4' and current_ui_code_for_image: 
                if final_poster_source and not skip_default_poster_logic:
                    if not any(t.aspect == 'poster' for t in entity.thumb):
                        source_for_server_poster = final_poster_source
                        if isinstance(final_poster_source, Image.Image):
                            temp_poster_file_for_server_save = os.path.join(path_data, "tmp", f"temp_poster_javdb_{current_ui_code_for_image.replace('/','_')}_{os.urandom(4).hex()}.jpg")
                            try:
                                pil_img_to_save = final_poster_source
                                if pil_img_to_save.mode not in ('RGB', 'L'): pil_img_to_save = pil_img_to_save.convert('RGB')
                                os.makedirs(os.path.join(path_data, "tmp"), exist_ok=True)
                                pil_img_to_save.save(temp_poster_file_for_server_save, format="JPEG", quality=95)
                                source_for_server_poster = temp_poster_file_for_server_save
                            except Exception as e_temp_save:
                                logger.error(f"JavDB Info: Failed to save PIL poster: {e_temp_save}. Fallback to PL URL.")
                                source_for_server_poster = valid_pl_url if valid_pl_url else None
                        if source_for_server_poster:
                            p_path = SiteUtil.save_image_to_server_path(source_for_server_poster, 'p', image_server_local_path, image_path_segment, current_ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                            if p_path: entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path}"))

                if final_landscape_url_source and not skip_default_landscape_logic:
                    if not any(t.aspect == 'landscape' for t in entity.thumb):
                        pl_path = SiteUtil.save_image_to_server_path(final_landscape_url_source, 'pl', image_server_local_path, image_path_segment, current_ui_code_for_image, proxy_url=proxy_url)
                        if pl_path: entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path}"))

                if arts_urls:
                    if entity.fanart is None: entity.fanart = []
                    unique_arts_for_fanart_server = []
                    for art_url_item_s in arts_urls: 
                        if not (final_poster_source == art_url_item_s and final_poster_crop_mode is None):
                            if art_url_item_s not in unique_arts_for_fanart_server: unique_arts_for_fanart_server.append(art_url_item_s)
                    current_fanart_server_count = len([fa_url for fa_url in entity.fanart if isinstance(fa_url, str) and fa_url.startswith(image_server_url)])
                    for idx, art_url_item_server in enumerate(unique_arts_for_fanart_server):
                        if current_fanart_server_count >= max_arts : break
                        art_relative_path = SiteUtil.save_image_to_server_path(art_url_item_server, 'art', image_server_local_path, image_path_segment, current_ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                        if art_relative_path: 
                            full_art_url = f"{image_server_url}/{art_relative_path}"
                            if full_art_url not in entity.fanart: entity.fanart.append(full_art_url); current_fanart_server_count += 1

            # 5-B. 이미지 서버 사용 안 할 때
            else: 
                if final_poster_source and not skip_default_poster_logic:
                    if not any(t.aspect == 'poster' for t in entity.thumb):
                        processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                        if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))

                if final_landscape_url_source and not skip_default_landscape_logic:
                    if not any(t.aspect == 'landscape' for t in entity.thumb):
                        processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url_source, proxy_url=proxy_url) 
                        if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))

                if arts_urls:
                    if entity.fanart is None: entity.fanart = []
                    unique_arts_for_fanart = []
                    for art_url_item in arts_urls: 
                        if not (final_poster_source == art_url_item and final_poster_crop_mode is None):
                            if art_url_item not in unique_arts_for_fanart: unique_arts_for_fanart.append(art_url_item)
                    for art_url_item in unique_arts_for_fanart:
                        if len(entity.fanart) >= max_arts : break
                        processed_art = SiteUtil.process_image_mode(image_mode, art_url_item, proxy_url=proxy_url)
                        if processed_art and processed_art not in entity.fanart: entity.fanart.append(processed_art)

            if temp_poster_file_for_server_save and os.path.exists(temp_poster_file_for_server_save):
                try: os.remove(temp_poster_file_for_server_save)
                except Exception as e_remove: logger.error(f"JavDB Info: Failed to remove temp poster: {e_remove}")

            # === 6. 트레일러 처리 ===
            if use_extras_setting:
                trailer_source_tag = tree_info.xpath('//video[@id="preview-video"]/source/@src')
                if trailer_source_tag:
                    trailer_url_raw = trailer_source_tag[0].strip()
                    if trailer_url_raw:
                        trailer_url_final = trailer_url_raw
                        if trailer_url_raw.startswith("//"): trailer_url_final = "https:" + trailer_url_raw
                        elif not trailer_url_raw.startswith(("http:", "https:")): 
                            if trailer_url_raw.startswith("/"): trailer_url_final = py_urllib_parse.urljoin(cls.site_base_url, trailer_url_raw)
                            else: trailer_url_final = "https:" + trailer_url_raw 

                        trailer_title_base = entity.tagline if entity.tagline and entity.tagline != entity.ui_code else entity.ui_code
                        trailer_title_text = f"{trailer_title_base} - Trailer"
                        entity.extras.append(EntityExtra("trailer", trailer_title_text, "mp4", trailer_url_final))

            # === 7. 최종 entity.code 값 변경 (ui_code 기반) ===
            if hasattr(entity, 'ui_code') and entity.ui_code and entity.ui_code.lower() != original_code_for_url.lower():
                new_code_value = cls.module_char + cls.site_char + entity.ui_code.lower() 
                entity.code = new_code_value

            logger.info(f"JavDB Info Parsed: final_code='{entity.code}', ui_code='{entity.ui_code}', Thumbs: {len(entity.thumb)}, Fanarts: {len(entity.fanart)}, Extras: {len(entity.extras)}")
            return entity

        except Exception as e_main_info:
            logger.exception(f"JavDB __info Exception for input code {code}: {e_main_info}")
            return None

    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            if entity:
                ret["ret"] = "success"
                ret["data"] = entity.as_dict()
            else:
                ret["ret"] = "error"
                ret["data"] = f"Failed to get JavDB info for {code} (__info returned None)."
        except Exception as e:
            ret["ret"] = "exception"
            ret["data"] = str(e)
            logger.exception(f"JavDB info (outer) error for code {code}: {e}")
        return ret
