# -*- coding: utf-8 -*-
import re
import traceback
from lxml import html
import os 
from framework import path_data 

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
        try:
            do_trans = kwargs.get('do_trans', True)
            proxy_url = kwargs.get('proxy_url', None)
            image_mode = kwargs.get('image_mode', '0')
            manual = kwargs.get('manual', False)

            logger.debug(f"JavDB Search: keyword='{keyword}', manual={manual}, do_trans={do_trans}, proxy_url='{proxy_url}'")

            search_keyword_for_url = keyword
            search_url = f"{cls.site_base_url}/search?q={search_keyword_for_url}&f=all"
            logger.debug(f"JavDB Search URL: {search_url}")
            
            cf_clearance_cookie_value = kwargs.get('cf_clearance_cookie', None) 
            custom_cookies = { 'over18': '1', 'locale': 'en' }
            if cf_clearance_cookie_value:
                custom_cookies['cf_clearance'] = cf_clearance_cookie_value
            else:
                logger.debug("JavDB Search: cf_clearance cookie not provided via kwargs. Continuing without it.")
            
            res_for_debug = SiteUtil.get_response_cs(search_url, proxy_url=proxy_url, cookies=custom_cookies)
            
            if res_for_debug is None:
                logger.warning(f"JavDB Search: Failed to get response (SiteUtil.get_response_cs returned None).")
                return {'ret': 'error', 'data': 'Failed to get response object (cloudscraper)'}
            
            html_content_text = res_for_debug.text

            if res_for_debug.status_code != 200:
                logger.warning(f"JavDB Search: Status code not 200. Status: {res_for_debug.status_code}, URL: {res_for_debug.url}")
                if "Due to copyright restrictions" in html_content_text or "由於版權限制" in html_content_text:
                    logger.error("JavDB Search: Access prohibited (country block).")
                    return {'ret': 'error', 'data': 'Country block detected.'}
                if "cf-challenge-running" in html_content_text or "Checking if the site connection is secure" in html_content_text:
                    logger.error("JavDB Search: Cloudflare challenge page detected.")
                    return {'ret': 'error', 'data': 'Cloudflare challenge page detected.'}
                return {'ret': 'error', 'data': f'Status: {res_for_debug.status_code}'}
            
            try:
                tree = html.fromstring(html_content_text)
            except Exception as e_parse:
                logger.error(f"JavDB Search: Failed to parse HTML: {e_parse}")
                logger.error(traceback.format_exc())
                return {'ret': 'error', 'data': 'Failed to parse HTML'}

            if tree is None:
                logger.warning("JavDB Search: Tree is None after parsing.")
                return {'ret': 'error', 'data': 'Parsed tree is None'}

            search_results = []
            item_nodes = tree.xpath('//div[contains(@class, "item")]/a[contains(@class, "box")]')

            if not item_nodes:
                no_results_message_xpath = tree.xpath('//div[contains(@class, "empty-message") and (contains(text(), "No videos found") or contains(text(), "沒有找到影片"))]')
                if no_results_message_xpath:
                    logger.info(f"JavDB Search: 'No videos found' message on page for '{keyword}'.")
                    return {'ret': 'no_match', 'data': []}
                title_match = re.search(r'<title>(.*?)</title>', html_content_text, re.IGNORECASE | re.DOTALL)
                page_title_from_text = title_match.group(1).strip() if title_match else "N/A"
                logger.warning(f"JavDB Search: No item nodes found with XPath for '{keyword}'. Page title: '{page_title_from_text}'.")
                return {'ret': 'error', 'data': f"No items found with XPath."}
            
            keyword_lower = keyword.lower()
            keyword_norm = keyword_lower.replace('-', '')
            processed_codes = set()

            for node_a_tag in item_nodes:
                try:
                    entity = EntityAVSearch(cls.site_name)
                    
                    detail_link = node_a_tag.attrib.get('href', '').strip()
                    if not detail_link: continue
                    item_code_match = re.search(r'/v/([^/?]+)', detail_link)
                    if not item_code_match: continue
                    item_code_raw = item_code_match.group(1).strip()
                    entity.code = cls.module_char + cls.site_char + item_code_raw 

                    if entity.code in processed_codes: continue
                    processed_codes.add(entity.code)

                    full_title_from_attr = node_a_tag.attrib.get('title', '').strip()
                    video_title_node = node_a_tag.xpath('.//div[@class="video-title"]')
                    visible_code_on_search = ""
                    actual_title_part = ""

                    if video_title_node:
                        strong_tag_text = video_title_node[0].xpath('./strong/text()')
                        if strong_tag_text:
                            visible_code_on_search = strong_tag_text[0].strip().upper()
                        
                        all_texts_in_video_title = video_title_node[0].xpath('.//text()')
                        combined_text = "".join([t.strip() for t in all_texts_in_video_title]).strip()
                        
                        if visible_code_on_search and combined_text.startswith(visible_code_on_search):
                            actual_title_part = combined_text[len(visible_code_on_search):].strip()
                        elif combined_text:
                            actual_title_part = combined_text
                    
                    entity.title = actual_title_part if actual_title_part else full_title_from_attr
                    if not entity.title and visible_code_on_search:
                        entity.title = visible_code_on_search
                    elif not entity.title and entity.code:
                        entity.title = entity.code[len(cls.module_char)+len(cls.site_char):]
                    
                    if hasattr(entity, 'ui_code'): 
                        entity.ui_code = visible_code_on_search if visible_code_on_search else entity.code[len(cls.module_char)+len(cls.site_char):].upper()

                    item_img_tag = node_a_tag.xpath('.//div[contains(@class, "cover")]/img/@src')
                    entity.image_url = item_img_tag[0].strip() if item_img_tag else ""
                    if entity.image_url and entity.image_url.startswith("//"):
                        entity.image_url = "https:" + entity.image_url
                    
                    entity.year = 0
                    date_meta_tag = node_a_tag.xpath('.//div[@class="meta"]/text()')
                    premiered_for_log = ""
                    if date_meta_tag:
                        date_str_raw = date_meta_tag[0].strip()
                        premiered_for_log = date_str_raw
                        year_match_ymd = re.match(r'(\d{4})[-/]\d{2}[-/]\d{2}', date_str_raw)
                        if year_match_ymd:
                            entity.year = int(year_match_ymd.group(1))
                        else:
                            year_match_mdy = re.match(r'\d{2}[-/]\d{2}[-/](\d{4})', date_str_raw)
                            if year_match_mdy:
                                entity.year = int(year_match_mdy.group(1))
                    
                    code_to_compare_score = visible_code_on_search if visible_code_on_search else entity.code[len(cls.module_char)+len(cls.site_char):]
                    code_to_compare_score_lower = code_to_compare_score.lower()
                    code_to_compare_score_norm = code_to_compare_score_lower.replace('-', '')
                    
                    current_score = 0
                    if keyword_norm == code_to_compare_score_norm:
                        current_score = 100
                    elif keyword_lower == code_to_compare_score_lower:
                        current_score = 100
                    elif keyword_lower in code_to_compare_score_lower:
                        current_score = 60
                    elif entity.title and keyword_lower in entity.title.lower():
                        current_score = 40
                    else:
                        current_score = 20
                    entity.score = current_score
                    
                    if manual: 
                        entity.title_ko = "(번역 안 함) " + entity.title
                    elif do_trans and entity.title:
                        entity.title_ko = SiteUtil.trans(entity.title, source='ja', target='ko')
                    else:
                        entity.title_ko = entity.title
                    
                    log_ui_code = entity.ui_code if hasattr(entity, 'ui_code') and entity.ui_code else visible_code_on_search
                    logger.debug(f"  JavDB Parsed item: code={entity.code}, title(orig)='{entity.title}', title(ko)='{entity.title_ko}', score={entity.score}, year={entity.year}, ui_code/visible='{log_ui_code}', date_str='{premiered_for_log}'")
                    search_results.append(entity.as_dict())

                except Exception as e_item:
                    logger.error(f"JavDB Search: Error parsing item: {e_item}")
                    logger.error(traceback.format_exc())
            
            if search_results:
                search_results = sorted(search_results, key=lambda k: k.get('score', 0), reverse=True)
            
            logger.info(f"JavDB Search for '{keyword}' found {len(search_results)} results.")
            return {'ret': 'success', 'data': search_results}

        except Exception as e_main:
            logger.exception(f"JavDB Search Main Exception: {e_main}")
            return {'ret': 'exception', 'data': str(e_main)}

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

            if res_info is None:
                logger.warning(f"JavDB Info: Failed to get response using cloudscraper for {code}.")
                return None
            
            html_info_text = res_info.text
            # (디버깅용 HTML 파일 저장 로직은 필요시 주석 해제)
            
            if res_info.status_code != 200:
                logger.warning(f"JavDB Info: Status code not 200 for {code}. Status: {res_info.status_code}.")
                return None
            
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
            
            main_poster_href_info = tree_info.xpath('//div[@class="column column-video-cover"]/a/@href')
            main_poster_img_src_info = tree_info.xpath('//div[@class="column column-video-cover"]/a/img/@src')
            pl_url = None
            if main_poster_href_info: pl_url = main_poster_href_info[0].strip()
            elif main_poster_img_src_info: pl_url = main_poster_img_src_info[0].strip()
            if pl_url and pl_url.startswith("//"): pl_url = "https:" + pl_url

            arts_urls = []
            sample_image_nodes_info = tree_info.xpath('//div[contains(@class, "tile-images")]/a[@class="tile-item"]/@href')
            for art_link in sample_image_nodes_info:
                art_full_url = art_link.strip()
                if art_full_url.startswith("//"): art_full_url = "https:" + art_full_url
                arts_urls.append(art_full_url)

            user_custom_poster_url = None; user_custom_landscape_url = None
            skip_default_poster_logic = False; skip_default_landscape_logic = False
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
            
            final_poster_source_url_or_path = None
            recommended_crop_mode = None
            if not skip_default_poster_logic and pl_url:
                final_poster_source_url_or_path, recommended_crop_mode, _ = SiteUtil.get_javdb_poster_from_pl_local(pl_url, current_ui_code_for_image, proxy_url=proxy_url)
            final_poster_crop_mode_to_use = user_defined_crop_mode if user_defined_crop_mode else recommended_crop_mode

            final_landscape_source = None
            if not skip_default_landscape_logic and pl_url: final_landscape_source = pl_url

            if not (use_image_server and image_mode == '4'):
                if final_poster_source_url_or_path and not skip_default_poster_logic:
                    processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source_url_or_path, proxy_url=proxy_url, crop_mode=final_poster_crop_mode_to_use)
                    if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                if final_landscape_source and not skip_default_landscape_logic:
                    processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_source, proxy_url=proxy_url)
                    if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))
                unique_arts_for_fanart = []
                for art_url_item in arts_urls:
                    if art_url_item not in unique_arts_for_fanart: unique_arts_for_fanart.append(art_url_item)
                for idx, art_url_item in enumerate(unique_arts_for_fanart):
                    if len(entity.fanart) >= max_arts : break
                    processed_art = SiteUtil.process_image_mode(image_mode, art_url_item, proxy_url=proxy_url)
                    if processed_art: entity.fanart.append(processed_art)
            elif use_image_server and image_mode == '4' and current_ui_code_for_image:
                if final_poster_source_url_or_path and not skip_default_poster_logic:
                    p_path = SiteUtil.save_image_to_server_path(final_poster_source_url_or_path, 'p', image_server_local_path, image_path_segment, current_ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode_to_use)
                    if p_path and not any(t.aspect == 'poster' for t in entity.thumb): entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path}"))
                if final_landscape_source and not skip_default_landscape_logic:
                    pl_path = SiteUtil.save_image_to_server_path(final_landscape_source, 'pl', image_server_local_path, image_path_segment, current_ui_code_for_image, proxy_url=proxy_url)
                    if pl_path and not any(t.aspect == 'landscape' for t in entity.thumb): entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path}"))
                unique_arts_for_fanart_server = []
                for art_url_item_s in arts_urls:
                    if art_url_item_s not in unique_arts_for_fanart_server: unique_arts_for_fanart_server.append(art_url_item_s)
                for idx, art_url_item_server in enumerate(unique_arts_for_fanart_server):
                    if len(entity.fanart) >= max_arts : break
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url_item_server, 'art', image_server_local_path, image_path_segment, current_ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                    if art_relative_path: entity.fanart.append(f"{image_server_url}/{art_relative_path}")

            if use_extras_setting:
                trailer_source_tag = tree_info.xpath('//video[@id="preview-video"]/source/@src')
                if trailer_source_tag:
                    trailer_url_raw = trailer_source_tag[0].strip()
                    if trailer_url_raw:
                        trailer_url_final = trailer_url_raw
                        if trailer_url_raw.startswith("//"):
                            trailer_url_final = "https:" + trailer_url_raw
                        elif not trailer_url_raw.startswith(("http:", "https:")):
                            logger.warning(f"JavDB Info: Trailer URL '{trailer_url_raw}' does not start with //, http, or https. Assuming https.")
                            trailer_url_final = "https:" + trailer_url_raw

                        trailer_title_base = entity.tagline if entity.tagline and entity.tagline != entity.ui_code else entity.ui_code
                        trailer_title_text = f"{trailer_title_base} - Trailer"
                        entity.extras.append(EntityExtra("trailer", trailer_title_text, "mp4", trailer_url_final))
                        logger.info(f"JavDB Info: Trailer found: {trailer_url_final}")
                else:
                    logger.debug(f"JavDB Info: No trailer <source> tag found for {code}.")

            if hasattr(entity, 'ui_code') and entity.ui_code and entity.ui_code != original_code_for_url.upper():
                new_code_value = cls.module_char + cls.site_char + entity.ui_code.lower()
                logger.debug(f"JavDB Info: Changing entity.code from '{entity.code}' to '{new_code_value}' using ui_code '{entity.ui_code}'")
                entity.code = new_code_value
            else:
                logger.debug(f"JavDB Info: entity.code ('{entity.code}') remains unchanged as ui_code is not suitable or missing for modification.")

            logger.info(f"JavDB Info Parsed: final_code='{entity.code}', ui_code='{entity.ui_code}', title(ui_code)='{entity.title}', originaltitle(ui_code)='{entity.originaltitle}', tagline(trans)='{entity.tagline}', plot(from tagline)='{entity.plot}', thumbs={len(entity.thumb)}, fanarts={len(entity.fanart)}, extras={len(entity.extras)}")
            return entity

        except Exception as e_main_info:
            logger.exception(f"JavDB __info Exception for code {code}: {e_main_info}")
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
