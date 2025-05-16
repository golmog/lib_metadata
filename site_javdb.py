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

    # search 메서드는 생략 (이전 최종본과 동일)
    @classmethod
    def search(cls, keyword, **kwargs):
        original_keyword_for_log = keyword
        try:
            do_trans = kwargs.get('do_trans', True)
            proxy_url = kwargs.get('proxy_url', None)
            image_mode = kwargs.get('image_mode', '0')
            manual = kwargs.get('manual', False)

            processed_keyword = keyword.strip().lower()
            if processed_keyword[-3:-1] == "cd":
                temp_keyword_before_cd_removal = processed_keyword
                processed_keyword = processed_keyword[:-3]
                logger.debug(f"JavDB Search: Keyword processed (cdX removed): '{processed_keyword}' from '{temp_keyword_before_cd_removal}' (original: '{original_keyword_for_log}')")

            logger.debug(f"JavDB Search: original_keyword='{original_keyword_for_log}', processed_keyword='{processed_keyword}', manual={manual}, do_trans={do_trans}, proxy_urlSet={'Yes' if proxy_url else 'No'}")

            search_keyword_for_url = py_urllib_parse.quote_plus(processed_keyword)
            search_url = f"{cls.site_base_url}/search?q={search_keyword_for_url}&f=all"
            logger.debug(f"JavDB Search URL: {search_url}")

            cf_clearance_cookie_value = kwargs.get('cf_clearance_cookie', None) 
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

            search_results = []
            item_list_xpath_expression = '//div[(contains(@class, "item-list") or contains(@class, "movie-list"))]//div[contains(@class, "item")]/a[contains(@class, "box")]'
            item_nodes = tree.xpath(item_list_xpath_expression)

            if not item_nodes: 
                no_results_message_xpath = tree.xpath('//div[contains(@class, "empty-message") and (contains(text(), "No videos found") or contains(text(), "沒有找到影片"))]')
                if no_results_message_xpath:
                    logger.info(f"JavDB Search: 'No videos found' message on page for keyword '{original_keyword_for_log}'.")
                    return {'ret': 'no_match', 'data': []}

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
                return {'ret': 'error', 'data': f"No items found using primary XPath for '{original_keyword_for_log}'. Page title: {page_title_from_text}"}

            keyword_lower_norm = processed_keyword.replace('-', '').replace(' ', '')
            processed_codes_in_search = set()

            for node_a_tag in item_nodes:
                try:
                    entity = EntityAVSearch(cls.site_name)
                    detail_link = node_a_tag.attrib.get('href', '').strip()
                    if not detail_link or not detail_link.startswith("/v/"): continue 
                    item_code_match = re.search(r'/v/([^/?]+)', detail_link)
                    if not item_code_match: continue
                    item_code_raw = item_code_match.group(1).strip()
                    entity.code = cls.module_char + cls.site_char + item_code_raw

                    if entity.code in processed_codes_in_search: continue
                    processed_codes_in_search.add(entity.code)

                    full_title_from_attr = node_a_tag.attrib.get('title', '').strip() # a 태그의 title 속성
                    video_title_node = node_a_tag.xpath('.//div[@class="video-title"]') # 제목 포함 div

                    visible_code_on_card = ""
                    actual_title_on_card = ""

                    if video_title_node:
                        strong_tag_node = video_title_node[0].xpath('./strong[1]')
                        if strong_tag_node and strong_tag_node[0].text:
                            visible_code_on_card = strong_tag_node[0].text.strip().upper()

                        temp_title_node = html.fromstring(html.tostring(video_title_node[0]))
                        for strong_el in temp_title_node.xpath('.//strong'):
                            strong_el.getparent().remove(strong_el)
                        actual_title_on_card = temp_title_node.text_content().strip()

                    if actual_title_on_card: entity.title = actual_title_on_card
                    elif full_title_from_attr: entity.title = full_title_from_attr
                    elif visible_code_on_card: entity.title = visible_code_on_card
                    else: entity.title = item_code_raw.upper() 

                    entity.ui_code = visible_code_on_card if visible_code_on_card else item_code_raw.upper()

                    item_img_tag_src = node_a_tag.xpath('.//div[contains(@class, "cover")]/img/@src')
                    entity.image_url = ""
                    if item_img_tag_src:
                        img_url_raw = item_img_tag_src[0].strip()
                        if img_url_raw.startswith("//"): entity.image_url = "https:" + img_url_raw
                        elif img_url_raw.startswith("http"): entity.image_url = img_url_raw
                        elif img_url_raw:
                            logger.warning(f"JavDB Search Item (keyword: '{original_keyword_for_log}'): Unexpected image URL format: {img_url_raw}")

                    entity.year = 0
                    date_meta_text_nodes = node_a_tag.xpath('.//div[@class="meta"]/text()')
                    premiered_date_str = ""
                    if date_meta_text_nodes:
                        for text_node_val in reversed(date_meta_text_nodes): 
                            date_str_candidate = text_node_val.strip()
                            date_match = re.match(r'\d{1,2}(?:[-/])\d{1,2}(?:[-/])(\d{4})', date_str_candidate)
                            if date_match:
                                year_str = date_match.group(1)
                                premiered_date_str = date_str_candidate
                                try: 
                                    entity.year = int(year_str)
                                except ValueError: 
                                    logger.warning(f"JavDB Search Item: Could not parse year from '{year_str}' in '{date_str_candidate}'")
                                    pass
                                break

                    # 점수 계산용 UI 코드
                    ui_code_for_score_calc = entity.ui_code
                    ui_code_for_score_norm = ui_code_for_score_calc.lower().replace('-', '').replace(' ', '')

                    current_score = 0
                    if keyword_lower_norm == ui_code_for_score_norm: current_score = 100
                    elif processed_keyword == ui_code_for_score_calc.lower(): current_score = 95
                    elif keyword_lower_norm in ui_code_for_score_norm : current_score = 85
                    elif entity.title and processed_keyword in entity.title.lower(): current_score = 60
                    else: current_score = 20
                    entity.score = current_score

                    # 번역 처리
                    if manual: entity.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + entity.title
                    elif do_trans and entity.title: entity.title_ko = SiteUtil.trans(entity.title, source='ja', target='ko')
                    else: entity.title_ko = entity.title

                    logger.debug(f"  JavDB Parsed (keyword: '{original_keyword_for_log}'): code={entity.code}, score={entity.score}, title='{entity.title_ko}', year={entity.year}, ui_code='{entity.ui_code}', date='{premiered_date_str}'")
                    search_results.append(entity.as_dict())

                except Exception as e_item_parse:
                    logger.error(f"JavDB Search Item (keyword: '{original_keyword_for_log}'): Error parsing item: {e_item_parse}")
                    logger.error(traceback.format_exc())

            if search_results:
                search_results = sorted(search_results, key=lambda k: k.get('score', 0), reverse=True)

            logger.info(f"JavDB Search for '{original_keyword_for_log}' completed. Found {len(search_results)} results.")
            return {'ret': 'success', 'data': search_results}

        except requests.exceptions.HTTPError as http_err_outer: 
            logger.error(f"JavDB Search (keyword: '{original_keyword_for_log}'): Outer HTTPError: {http_err_outer}")
            status_code_for_error = http_err_outer.response.status_code if http_err_outer.response else 'Unknown'
            return {'ret': 'error', 'data': f'HTTP Error: {status_code_for_error} for {original_keyword_for_log}'}
        except Exception as e_main_search:
            logger.exception(f"JavDB Search Main Exception for keyword '{original_keyword_for_log}': {e_main_search}")
            return {'ret': 'exception', 'data': str(e_main_search)}

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
            image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
            user_defined_crop_mode = kwargs.get('crop_mode', None)
            use_extras_setting = kwargs.get('use_extras', True)
            cf_clearance_cookie_value = kwargs.get('cf_clearance_cookie', None)

            custom_cookies = { 'over18': '1', 'locale': 'en' }
            if cf_clearance_cookie_value:
                custom_cookies['cf_clearance'] = cf_clearance_cookie_value
            
            original_code_for_url = code[len(cls.module_char) + len(cls.site_char):]
            detail_url = f"{cls.site_base_url}/v/{original_code_for_url}"
            
            logger.debug(f"JavDB Info: Accessing URL: {detail_url} for code {code}")
            res_info = SiteUtil.get_response_cs(detail_url, proxy_url=proxy_url, cookies=custom_cookies)

            if res_info is None or res_info.status_code != 200:
                logger.warning(f"JavDB Info: Failed to get page or status not 200 for {code}. Status: {res_info.status_code if res_info else 'None'}")
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
                # entity.originaltitle = actual_raw_title_text # DMM/MGStage와 일관성을 위해 originaltitle은 ui_code 유지
                entity.tagline = SiteUtil.trans(actual_raw_title_text, do_trans=do_trans, source='ja', target='ko')
            else: 
                entity.tagline = entity.ui_code 

            # 3. 나머지 상세 정보 패널 파싱
            panel_blocks_xpath = '//nav[contains(@class, "movie-panel-info")]/div[contains(@class,"panel-block")]'
            panel_blocks = tree_info.xpath(panel_blocks_xpath)
            
            rating_panel_block = tree_info.xpath('//div[@class="panel-block" and ./strong[contains(text(),"Rating:")]]')
            if not rating_panel_block : rating_panel_block = tree_info.xpath('//div[contains(@class,"panel-block") and ./strong[contains(text(),"Rating:")]]')
            if rating_panel_block:
                rating_value_text_nodes = rating_panel_block[0].xpath('./span[@class="value"]/text()')
                rating_full_text = "".join(rating_value_text_nodes).strip()
                rating_match = re.search(r'([\d\.]+)\s*,\s*by\s*([\d,]+)\s*users', rating_full_text)
                if rating_match:
                    try:
                        rating_val_original = float(rating_match.group(1))
                        votes_count = int(rating_match.group(2).replace(',', ''))
                        entity.ratings.append(EntityRatings(rating_val_original, max=5, name=cls.site_name, votes=votes_count))
                    except ValueError:
                        logger.warning(f"JavDB Info: Could not parse rating from text: '{rating_full_text}'")

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

            # --- 이미지 정보 추출 ---
            pl_url = None # 최종 PL URL
            
            # 1. 메인 커버 이미지 (PL) 추출 시도
            #    a/@href 는 비디오 재생 링크일 수 있으므로, img/@src를 우선적으로 확인
            main_cover_img_src_nodes = tree_info.xpath('//div[@class="column column-video-cover"]//img[@class="video-cover"]/@src')
            if main_cover_img_src_nodes:
                pl_url_raw = main_cover_img_src_nodes[0].strip()
                if pl_url_raw.startswith("//"):
                    pl_url = "https:" + pl_url_raw
                elif not pl_url_raw.startswith("http"): 
                    # /로 시작하는 상대경로가 아니라면 (예: 그냥 파일명), JavDB의 일반적인 이미지 URL 패턴을 따름
                    # JavDB 커버 이미지는 보통 cdn.jdbstatic.com 같은 절대 URL이거나 //로 시작함.
                    # /v/... 와 같은 링크가 여기에 들어오면 안 됨.
                    if not pl_url_raw.startswith("/"): # /로 시작하지 않는 이상한 경우
                        logger.warning(f"JavDB Info: Unexpected PL image src format (not // or http or /): {pl_url_raw}")
                        # 이 경우 처리가 애매하므로 일단 None으로 두거나, 오류 가능성 인지
                    else: # /로 시작하는 상대경로 (JavDB 커버는 보통 이런 형태 아님)
                        pl_url = py_urllib_parse.urljoin(cls.site_base_url, pl_url_raw)
                else: # http 또는 https로 시작하는 정상 URL
                    pl_url = pl_url_raw
            
            if not pl_url: # img/@src 에서 못찾았을 경우, a/@href도 확인 (매우 드문 경우)
                main_cover_a_href_nodes = tree_info.xpath('//div[@class="column column-video-cover"]/a[@data-fancybox="gallery"]/@href')
                if main_cover_a_href_nodes:
                    pl_url_raw_href = main_cover_a_href_nodes[0].strip()
                    # a/@href는 이미지 파일 URL이어야 함 (/v/... 형태의 링크는 제외)
                    if pl_url_raw_href and not pl_url_raw_href.startswith("/v/"):
                        if pl_url_raw_href.startswith("//"):
                            pl_url = "https:" + pl_url_raw_href
                        elif not pl_url_raw_href.startswith("http"):
                            if pl_url_raw_href.startswith("/"): # 사이트 루트 기준 상대 경로
                                pl_url = py_urllib_parse.urljoin(cls.site_base_url, pl_url_raw_href)
                            else:
                                logger.warning(f"JavDB Info: Unexpected PL a/@href format: {pl_url_raw_href}")
                        else:
                            pl_url = pl_url_raw_href
            
            logger.debug(f"JavDB Info: Determined pl_url = '{pl_url}'")


            arts_urls = [] 
            # 샘플 이미지는 항상 'tile-images preview-images' div 내부에 있다고 가정
            sample_image_container = tree_info.xpath('//div[contains(@class, "preview-images")]')
            if sample_image_container:
                # 컨테이너 내부의 a.tile-item 만 선택 (다른 추천 영상 링크 배제)
                sample_image_nodes_info = sample_image_container[0].xpath('./a[@class="tile-item"]/@href')
                for art_link_raw in sample_image_nodes_info:
                    art_full_url_raw = art_link_raw.strip()
                    art_full_url = None
                    if art_full_url_raw:
                        if art_full_url_raw.startswith("//"):
                            art_full_url = "https:" + art_full_url_raw
                        elif not art_full_url_raw.startswith("http"):
                            # JavDB 샘플 이미지는 보통 cdn 주소 (// 또는 http)
                            # /로 시작하는 경우는 드물지만, 있다면 urljoin
                            if art_full_url_raw.startswith("/"): 
                                art_full_url = py_urllib_parse.urljoin(cls.site_base_url, art_full_url_raw)
                            else: # 그 외의 경우는 잘못된 URL일 가능성 높음
                                logger.warning(f"JavDB Info: Unexpected art_link_raw format: {art_full_url_raw}")
                                continue # 이 URL은 arts_urls에 추가하지 않음
                        else: # http 또는 https로 시작
                            art_full_url = art_full_url_raw
                    
                    if art_full_url:
                        arts_urls.append(art_full_url)
            else:
                logger.warning(f"JavDB Info: Sample image container (div.preview-images) not found for {code}.")
            logger.debug(f"JavDB Info: Collected {len(arts_urls)} arts_urls: {arts_urls[:5]}")

            # --- 사용자 지정 이미지 확인 ---
            user_custom_poster_url = None
            user_custom_landscape_url = None
            skip_default_poster_logic = False
            skip_default_landscape_logic = False
            current_ui_code_for_image = entity.ui_code 

            if use_image_server and image_server_local_path and image_server_url and current_ui_code_for_image:
                poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
                landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]
                for suffix in poster_suffixes:
                    _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, current_ui_code_for_image, suffix, image_server_url)
                    if web_url: user_custom_poster_url = web_url; entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url)); skip_default_poster_logic = True; break 
                for suffix in landscape_suffixes:
                    _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, current_ui_code_for_image, suffix, image_server_url)
                    if web_url: user_custom_landscape_url = web_url; entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url)); skip_default_landscape_logic = True; break

            # --- 기본 포스터, 랜드스케이프, 팬아트 처리 ---
            final_poster_source_for_processing = None 
            recommended_crop_mode_from_util = None 
            
            if not skip_default_poster_logic: # 사용자 지정 포스터가 없을 때만 실행
                if pl_url: # pl_url이 정상적으로 추출되었을 때만 진행
                    log_identifier_for_util = entity.ui_code if hasattr(entity, 'ui_code') and entity.ui_code else original_code_for_url
                    poster_pil_or_url, rec_crop_mode, _ = SiteUtil.get_javdb_poster_from_pl_local(pl_url, log_identifier_for_util, proxy_url=proxy_url)
                    if poster_pil_or_url: 
                        final_poster_source_for_processing = poster_pil_or_url
                        recommended_crop_mode_from_util = rec_crop_mode
                    else: 
                        logger.warning(f"JavDB Info: get_javdb_poster_from_pl_local returned None for pl_url '{pl_url}'. Falling back.")
                        final_poster_source_for_processing = pl_url # Fallback
                        recommended_crop_mode_from_util = 'r' 
                else:
                    logger.warning(f"JavDB Info: pl_url is None. Cannot determine default poster source.")
            
            final_poster_crop_mode_to_use = user_defined_crop_mode if user_defined_crop_mode else recommended_crop_mode_from_util
            if final_poster_crop_mode_to_use is None and final_poster_source_for_processing:
                final_poster_crop_mode_to_use = 'r'

            final_landscape_source_for_processing = None
            if not skip_default_landscape_logic and pl_url: # pl_url이 있을 때만
                final_landscape_source_for_processing = pl_url

            temp_poster_file_for_server_save = None 

            if not (use_image_server and image_mode == '4'): 
                if final_poster_source_for_processing and not skip_default_poster_logic:
                    processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source_for_processing, proxy_url=proxy_url, crop_mode=final_poster_crop_mode_to_use)
                    if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))

                if final_landscape_source_for_processing and not skip_default_landscape_logic:
                    processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_source_for_processing, proxy_url=proxy_url) 
                    if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))

                unique_arts_for_fanart = []
                for art_url_item in arts_urls: 
                    if art_url_item not in unique_arts_for_fanart: unique_arts_for_fanart.append(art_url_item)
                for art_url_item in unique_arts_for_fanart:
                    if len(entity.fanart) >= max_arts : break
                    processed_art = SiteUtil.process_image_mode(image_mode, art_url_item, proxy_url=proxy_url)
                    if processed_art: entity.fanart.append(processed_art)

            elif use_image_server and image_mode == '4' and current_ui_code_for_image: 
                if final_poster_source_for_processing and not skip_default_poster_logic:
                    source_for_server_poster = final_poster_source_for_processing
                    if isinstance(final_poster_source_for_processing, Image.Image): 
                        # path_data와 os 모듈이 이 범위에서 사용 가능해야 함
                        temp_poster_file_for_server_save = os.path.join(path_data, "tmp", f"temp_poster_{current_ui_code_for_image.replace('/','_')}_{os.urandom(4).hex()}.jpg")
                        try:
                            pil_format = final_poster_source_for_processing.format if final_poster_source_for_processing.format else "JPEG"
                            os.makedirs(os.path.join(path_data, "tmp"), exist_ok=True) # tmp 폴더 생성
                            final_poster_source_for_processing.save(temp_poster_file_for_server_save, format=pil_format, quality=95)
                            source_for_server_poster = temp_poster_file_for_server_save
                        except Exception as e_temp_save:
                            logger.error(f"JavDB Info: Failed to save PIL poster to temp file: {e_temp_save}")
                            source_for_server_poster = pl_url if pl_url else None 

                    if source_for_server_poster:
                        p_path = SiteUtil.save_image_to_server_path(source_for_server_poster, 'p', image_server_local_path, image_path_segment, current_ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode_to_use)
                        if p_path and not any(t.aspect == 'poster' for t in entity.thumb): entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path}"))

                if final_landscape_source_for_processing and not skip_default_landscape_logic:
                    pl_path = SiteUtil.save_image_to_server_path(final_landscape_source_for_processing, 'pl', image_server_local_path, image_path_segment, current_ui_code_for_image, proxy_url=proxy_url)
                    if pl_path and not any(t.aspect == 'landscape' for t in entity.thumb): entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path}"))

                unique_arts_for_fanart_server = []
                for art_url_item_s in arts_urls:
                    if art_url_item_s not in unique_arts_for_fanart_server: unique_arts_for_fanart_server.append(art_url_item_s)
                for idx, art_url_item_server in enumerate(unique_arts_for_fanart_server):
                    if len(entity.fanart) >= max_arts : break
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url_item_server, 'art', image_server_local_path, image_path_segment, current_ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                    if art_relative_path: entity.fanart.append(f"{image_server_url}/{art_relative_path}")

            if temp_poster_file_for_server_save and os.path.exists(temp_poster_file_for_server_save):
                try: os.remove(temp_poster_file_for_server_save)
                except Exception as e_remove: logger.error(f"Failed to remove temp poster file {temp_poster_file_for_server_save}: {e_remove}")

            # --- 트레일러 처리 ---
            if use_extras_setting:
                trailer_source_tag = tree_info.xpath('//video[@id="preview-video"]/source/@src')
                if trailer_source_tag:
                    trailer_url_raw = trailer_source_tag[0].strip()
                    if trailer_url_raw:
                        trailer_url_final = trailer_url_raw
                        if trailer_url_raw.startswith("//"): trailer_url_final = "https:" + trailer_url_raw
                        elif not trailer_url_raw.startswith(("http:", "https:")): # 방어 코드
                            trailer_url_final = "https:" + trailer_url_raw 

                        trailer_title_base = entity.tagline if entity.tagline and entity.tagline != entity.ui_code else entity.ui_code
                        trailer_title_text = f"{trailer_title_base} - Trailer"
                        entity.extras.append(EntityExtra("trailer", trailer_title_text, "mp4", trailer_url_final))
                        logger.info(f"JavDB Info: Trailer found: {trailer_url_final}")
                else:
                    logger.debug(f"JavDB Info: No trailer <source> tag found for {code}.")

            # --- 최종 entity.code 값 변경 ---
            if hasattr(entity, 'ui_code') and entity.ui_code and entity.ui_code.lower() != original_code_for_url.lower():
                # ui_code가 있고, JavDB 내부 코드와 다를 때만 (즉, SONE-519 같이 의미있는 품번일 때)
                # 하이픈 유지하고 소문자화
                new_code_value = cls.module_char + cls.site_char + entity.ui_code.lower() 
                logger.debug(f"JavDB Info: Changing entity.code from '{entity.code}' to '{new_code_value}' using ui_code '{entity.ui_code}'")
                entity.code = new_code_value
            else:
                logger.debug(f"JavDB Info: entity.code ('{entity.code}') remains. ui_code ('{entity.ui_code}') not suitable for new code generation or same as original_code_for_url ('{original_code_for_url}').")

            logger.info(f"JavDB Info Parsed: final_code='{entity.code}', ui_code='{entity.ui_code}', title(ui_code)='{entity.title}', tagline(trans)='{entity.tagline}', plot(from tagline)='{entity.plot}', thumbs={len(entity.thumb)}, fanarts={len(entity.fanart)}, extras={len(entity.extras)}")
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
