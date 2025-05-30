import re
import traceback
from lxml import html
import os
from framework import path_data
from PIL import Image
from typing import Union, Tuple

from .entity_av import EntityAVSearch
from .entity_base import EntityMovie, EntityActor, EntityThumb, EntityExtra, EntityRatings
from .plugin import P
from .site_util import SiteUtil

logger = P.logger


class SiteJavbus:
    site_name = "javbus"
    site_base_url = "https://www.javbus.com"
    module_char = "C"
    site_char = "B"

    @classmethod
    def __fix_url(cls, url):
        if not url.startswith("http"):
            return cls.site_base_url + url
        return url

    @classmethod
    def _get_javbus_page_tree(cls, page_url: str, proxy_url: str = None, cf_clearance_cookie: str = None) -> Union[html.HtmlElement, None]:
        javbus_cookies = {'age': 'verified', 'dv': '1', 'existmag': 'mag'}
        if cf_clearance_cookie:
            javbus_cookies['cf_clearance'] = cf_clearance_cookie
            # logger.debug(f"SiteJavbus._get_javbus_page_tree: Using cf_clearance cookie for URL: {page_url}")

        request_headers = SiteUtil.default_headers.copy()
        request_headers['Referer'] = cls.site_base_url + "/"
        # logger.debug(f"SiteJavbus._get_javbus_page_tree: Requesting URL='{page_url}', Proxy='{proxy_url}', Cookies='{javbus_cookies}'")

        try:
            res = SiteUtil.get_response_cs(page_url, proxy_url=proxy_url, headers=request_headers, cookies=javbus_cookies, allow_redirects=True)

            if res is None or res.status_code != 200:
                status_code = res.status_code if res else "None"
                logger.warning(f"SiteJavbus._get_javbus_page_tree: Failed to get page or status not 200 for URL='{page_url}'. Status: {status_code}. Falling back to SiteUtil.get_response if configured.")

                # Cloudscraper 실패 시, SiteUtil.get_response로 fallback
                # logger.debug(f"SiteJavbus._get_javbus_page_tree: Attempting fallback with SiteUtil.get_response for URL='{page_url}'")
                res_fallback = SiteUtil.get_response(page_url, proxy_url=proxy_url, headers=request_headers, cookies=javbus_cookies, verify=False)
                if res_fallback and res_fallback.status_code == 200:
                #     logger.debug(f"SiteJavbus._get_javbus_page_tree: Fallback request successful for URL='{page_url}'.")
                    return html.fromstring(res_fallback.text)
                else:
                    status_code_fallback = res_fallback.status_code if res_fallback else "None"
                    logger.error(f"SiteJavbus._get_javbus_page_tree: Fallback request also failed for URL='{page_url}'. Status: {status_code_fallback}.")
                    return None
                # return None # get_response_cs 실패 시 여기서 None 반환 (fallback 사용 안 할 경우)

            # logger.debug(f"SiteJavbus._get_javbus_page_tree: Successfully fetched page for URL='{page_url}'. Status: {res.status_code}")
            return html.fromstring(res.text)
        
        except Exception as e:
            logger.exception(f"SiteJavbus._get_javbus_page_tree: Exception while getting or parsing page for URL='{page_url}': {e}")
            return None

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False, cf_clearance_cookie=None):
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd": keyword = keyword[:-3]
        keyword = keyword.replace(" ", "-")
        url = f"{cls.site_base_url}/search/{keyword}"

        tree = cls._get_javbus_page_tree(url, proxy_url=proxy_url, cf_clearance_cookie=cf_clearance_cookie)
        if tree is None:
            logger.warning(f"SiteJavbus.__search: _get_javbus_page_tree returned None for URL: {url}. Search will likely fail or return empty.")
            return []

        ret = []
        for node in tree.xpath('//a[@class="movie-box"]')[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                item.image_url = cls.__fix_url(node.xpath(".//img/@src")[0])
                tag = node.xpath(".//date")
                ui_code = tag[0].text_content().strip()
                try:
                    label, num = ui_code.split("-")
                    item.ui_code = f"{label}-{num.lstrip('0').zfill(3)}"
                except Exception: item.ui_code = ui_code
                item.code = cls.module_char + cls.site_char + node.attrib["href"].split("/")[-1]
                item.desc = "발매일: " + tag[1].text_content().strip()
                item.year = int(tag[1].text_content().strip()[:4])
                item.title = node.xpath(".//span/text()")[0].strip()
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    item.image_url = SiteUtil.process_image_mode(_image_mode, item.image_url, proxy_url=proxy_url)
                    item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                else:
                    item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)
                item.score = 100 if keyword.lower() == item.ui_code.lower() else 60 - (len(ret) * 10)
                if item.score < 0: item.score = 0
                ret.append(item.as_dict())
            except Exception: logger.exception("개별 검색 결과 처리 중 예외:")
        return sorted(ret, key=lambda k: k["score"], reverse=True)

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            do_trans_arg = kwargs.get('do_trans', True)
            proxy_url_arg = kwargs.get('proxy_url', None)
            image_mode_arg = kwargs.get('image_mode', "0")
            manual_arg = kwargs.get('manual', False)
            cf_clearance_cookie_arg = kwargs.get('cf_clearance_cookie', None)
            data = cls.__search(keyword, 
                                do_trans=do_trans_arg, 
                                proxy_url=proxy_url_arg, 
                                image_mode=image_mode_arg, 
                                manual=manual_arg,
                                cf_clearance_cookie=cf_clearance_cookie_arg)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data else "no_match"; ret["data"] = data
        return ret

    @classmethod
    def __img_urls(cls, tree):
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        if tree is None:
            logger.warning("JavBus __img_urls: Input tree is None. Cannot extract image URLs.")
            return img_urls

        pl_nodes = tree.xpath('//a[@class="bigImage"]/img/@src')
        pl = pl_nodes[0] if pl_nodes else ""
        if pl: pl = cls.__fix_url(pl)
        else: logger.warning("JavBus __img_urls: PL 이미지 URL을 얻을 수 없음")

        ps = ""
        if pl:
            try: 
                filename = pl.split("/")[-1].replace("_b.", ".")
                ps = cls.__fix_url(f"/pics/thumb/{filename}")
            except Exception as e_ps_infer: logger.warning(f"JavBus __img_urls: ps URL 유추 실패: {e_ps_infer}")

        arts = []
        try:
            for href_art in tree.xpath('//*[@id="sample-waterfall"]/a/@href'):
                arts.append(cls.__fix_url(href_art))
        except Exception as e_arts_extract: logger.warning(f"JavBus __img_urls: arts URL 추출 실패: {e_arts_extract}")

        img_urls["ps"] = ps
        img_urls["pl"] = pl
        img_urls["arts"] = list(dict.fromkeys(arts))
        return img_urls

    @classmethod
    def __info(
        cls,
        code, 
        do_trans=True,
        proxy_url=None,
        image_mode="0",
        max_arts=10,
        use_extras=True, 
        ps_to_poster=False,
        crop_mode=None,
        **kwargs 
    ):
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        ps_to_poster_setting = ps_to_poster 
        crop_mode_setting = crop_mode     
        cf_clearance_cookie_value_from_kwargs = kwargs.get('cf_clearance_cookie', None)

        original_code_for_url = code[len(cls.module_char) + len(cls.site_char):]
        url = f"{cls.site_base_url}/{original_code_for_url}"

        tree = cls._get_javbus_page_tree(url, proxy_url=proxy_url, cf_clearance_cookie=cf_clearance_cookie_value_from_kwargs)

        if tree is None:
            logger.error(f"JavBus __info: _get_javbus_page_tree returned None for {code}. URL: {url}")
            return None

        if not tree.xpath("//div[@class='container']//div[@class='row movie']"):
            logger.error(f"JavBus __info: Failed to get valid detail page structure for {code}. Main content div not found. URL: {url}")
            return None

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = []; entity.fanart = []; entity.extras = []; entity.ratings = []

        identifier_parsed_flag = False
        actual_raw_title_text_from_h3 = ""

        try:
            logger.debug(f"JavBus: Parsing metadata for {code}...")

            info_container_node_list = tree.xpath("//div[contains(@class, 'container')]//div[@class='col-md-3 info']")
            if not info_container_node_list:
                logger.error(f"JavBus: Info container (div.info) not found for {code}.")
                # 필수 정보 파싱 불가 시 아래 identifier_parsed_flag로 핸들링
            else:
                info_node = info_container_node_list[0]

                # 1. ui_code (識別碼) 파싱 및 title, originaltitle, sorttitle 초기화
                ui_code_val_nodes = info_node.xpath("./p[./span[@class='header' and contains(text(),'識別碼')]]/span[not(@class='header')]//text()")
                if not ui_code_val_nodes: ui_code_val_nodes = info_node.xpath("./p[./span[@class='header' and contains(text(),'識別碼')]]/text()[normalize-space()]")
                raw_ui_code = "".join(ui_code_val_nodes).strip()
                parsed_ui_code_value = ""
                if raw_ui_code:
                    try:
                        label, num_str = raw_ui_code.split('-', 1)
                        num_part = ''.join(filter(str.isdigit, num_str))
                        if num_part: parsed_ui_code_value = f"{label.upper()}-{int(num_part):03d}"
                        else: parsed_ui_code_value = raw_ui_code.upper()
                    except ValueError: parsed_ui_code_value = raw_ui_code.upper()

                entity.ui_code = parsed_ui_code_value if parsed_ui_code_value else original_code_for_url.upper()
                entity.title = entity.ui_code
                entity.originaltitle = entity.ui_code 
                entity.sorttitle = entity.ui_code
                identifier_parsed_flag = bool(parsed_ui_code_value)
                logger.debug(f"JavBus: ui_code set to: {entity.ui_code}, identifier_parsed: {identifier_parsed_flag}")

                # 2. H3 제목에서 실제 원본 제목 추출 (Tagline 용도)
                actual_raw_title_text_from_h3 = ""
                h3_node_list = tree.xpath("//div[@class='container']/h3")
                if h3_node_list:
                    full_h3_text_content = h3_node_list[0].text_content().strip()
                    if entity.ui_code and full_h3_text_content.upper().startswith(entity.ui_code):
                        actual_raw_title_text_from_h3 = full_h3_text_content[len(entity.ui_code):].strip()
                    else: actual_raw_title_text_from_h3 = full_h3_text_content

                if actual_raw_title_text_from_h3:
                    entity.tagline = SiteUtil.trans(actual_raw_title_text_from_h3, do_trans=do_trans, source='ja', target='ko')
                else: entity.tagline = entity.ui_code
                logger.debug(f"JavBus: Tagline set to: {entity.tagline}")

                # 3. 나머지 정보 직접 XPath로 추출 (info_node 기준)
                all_p_tags_in_info = info_node.xpath("./p")
                genre_header_p_node = None; actor_header_p_node = None
                for p_idx, p_tag_node_loop in enumerate(all_p_tags_in_info):
                    header_span_text_nodes = p_tag_node_loop.xpath("normalize-space(./span[@class='header']/text())")
                    if "類別:" in header_span_text_nodes or (p_tag_node_loop.get("class") == "header" and p_tag_node_loop.text_content().strip().startswith("類別")):
                        genre_header_p_node = p_tag_node_loop
                    elif "演員" in header_span_text_nodes or (p_tag_node_loop.get("class") == "star-show" and "演員" in p_tag_node_loop.xpath("normalize-space(./span[@class='header']/text())")):
                        actor_header_p_node = p_tag_node_loop

                for p_tag_node_loop_general in all_p_tags_in_info:
                    header_span_general = p_tag_node_loop_general.xpath("./span[@class='header']")
                    if not header_span_general or not header_span_general[0].text: continue
                    key_text_general = header_span_general[0].text_content().replace(":", "").strip()
                    if key_text_general in ["類別", "演員"]: continue

                    value_nodes_general = header_span_general[0].xpath("./following-sibling::node()")
                    value_parts_general = []
                    for node_item_general in value_nodes_general:
                        if hasattr(node_item_general, 'tag'):
                            if node_item_general.tag == 'a': value_parts_general.append(node_item_general.text_content().strip())
                            elif node_item_general.tag == 'span' and not node_item_general.get('class'): value_parts_general.append(node_item_general.text_content().strip())
                        elif isinstance(node_item_general, str): 
                            stripped_text_general = node_item_general.strip()
                            if stripped_text_general: value_parts_general.append(stripped_text_general)
                    value_text_general = " ".join(filter(None, value_parts_general)).strip()
                    if not value_text_general or value_text_general == "----": continue

                    if key_text_general == "發行日期":
                        if value_text_general != "0000-00-00": entity.premiered = value_text_general; entity.year = int(value_text_general[:4])
                        else: entity.premiered = "1900-01-01"; entity.year = 1900
                    elif key_text_general == "長度":
                        try: entity.runtime = int(value_text_general.replace("分鐘", "").strip())
                        except: pass
                    elif key_text_general == "導演": entity.director = value_text_general
                    elif key_text_general == "製作商": entity.studio = SiteUtil.trans(value_text_general, do_trans=do_trans, source='ja', target='ko')
                    elif key_text_general == "發行商":
                        if not entity.studio: entity.studio = SiteUtil.trans(value_text_general, do_trans=do_trans, source='ja', target='ko')
                        if entity.tag is None: entity.tag = []
                        trans_label_general = SiteUtil.trans(value_text_general, do_trans=do_trans, source='ja', target='ko')
                        if trans_label_general and trans_label_general not in entity.tag: entity.tag.append(trans_label_general)
                    elif key_text_general == "系列":
                        if entity.tag is None: entity.tag = []
                        series_name_from_a_general = header_span_general[0].xpath("./following-sibling::a[1]/text()")
                        series_final_name_general = series_name_from_a_general[0].strip() if series_name_from_a_general else value_text_general
                        trans_series_general = SiteUtil.trans(series_final_name_general, do_trans=do_trans, source='ja', target='ko')
                        if trans_series_general and trans_series_general not in entity.tag: entity.tag.append(trans_series_general)

                if genre_header_p_node is not None:
                    genre_values_p_node_list = genre_header_p_node.xpath("./following-sibling::p[1]")
                    if genre_values_p_node_list:
                        genre_values_p_actual_node = genre_values_p_node_list[0]
                        # logger.debug(f"JavBus: Genre values P tag content: {html.tostring(genre_values_p_actual_node, encoding='unicode')[:300]}")
                        
                        genre_span_tags = genre_values_p_actual_node.xpath("./span[@class='genre']")

                        if entity.genre is None: entity.genre = []

                        # logger.debug(f"JavBus: Found {len(genre_span_tags)} <span class='genre'> tags.")
                        for span_tag_genre in genre_span_tags:
                            a_tag_text_nodes = span_tag_genre.xpath("./label/a/text() | ./a/text()")
                            genre_ja = ""
                            if a_tag_text_nodes:
                                genre_ja = a_tag_text_nodes[0].strip()
                            else:
                                if not span_tag_genre.xpath("./button[@id='gr_btn']"):
                                    genre_ja = span_tag_genre.text_content().strip()

                            # logger.debug(f"  Raw genre text from span: '{genre_ja}'")
                            if not genre_ja or genre_ja == "多選提交" or genre_ja in SiteUtil.av_genre_ignore_ja: 
                                # logger.debug(f"    Skipping genre: '{genre_ja}'")
                                continue

                            if genre_ja in SiteUtil.av_genre: 
                                if SiteUtil.av_genre[genre_ja] not in entity.genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans, source='ja', target='ko').replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko and genre_ko not in entity.genre:
                                    entity.genre.append(genre_ko)
                    else:
                        logger.warning(f"JavBus: Genre values P tag (sibling of header) not found for {code}.")
                else:
                    logger.warning(f"JavBus: Genre header P tag ('類別') not found for {code}.")

                if actor_header_p_node is not None:
                    actor_values_p_node = actor_header_p_node.xpath("./following-sibling::p[1]")
                    if actor_values_p_node:
                        actor_a_tags = actor_values_p_node[0].xpath(".//span[@class='genre']/a")
                        if entity.actor is None: entity.actor = []
                        for a_tag_actor in actor_a_tags:
                            actor_name = a_tag_actor.text_content().strip()
                            if actor_name and actor_name != "暫無出演者資訊":
                                if not any(act.originalname == actor_name for act in entity.actor):
                                    actor_entity = EntityActor(actor_name); actor_entity.name = actor_name
                                    entity.actor.append(actor_entity)
                else: logger.warning(f"JavBus: Actor header P tag ('演員') not found for {code}.")

            if not identifier_parsed_flag and entity.ui_code:
                identifier_parsed_flag = True

            if not entity.plot:
                if entity.tagline and entity.tagline != entity.ui_code: entity.plot = entity.tagline
                elif actual_raw_title_text_from_h3 and actual_raw_title_text_from_h3 != entity.ui_code: 
                    entity.plot = SiteUtil.trans(actual_raw_title_text_from_h3, do_trans=do_trans, source='ja', target='ko')
                elif entity.ui_code: entity.plot = entity.ui_code 

        except Exception as e_meta_main:
            logger.exception(f"JavBus: Major error during metadata parsing for {code}: {e_meta_main}")
            if not (hasattr(entity, 'ui_code') and entity.ui_code) : return None

        current_ui_code_for_image = entity.ui_code
        user_custom_poster_url = None; user_custom_landscape_url = None
        skip_default_poster_logic = False; skip_default_landscape_logic = False

        if use_image_server and image_server_local_path and image_server_url and current_ui_code_for_image:
            logger.debug(f"JavBus: Checking for user custom images for {current_ui_code_for_image}")
            poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
            landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]
            for suffix in poster_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, current_ui_code_for_image, suffix, image_server_url)
                if web_url:
                    user_custom_poster_url = web_url
                    if not any(t.aspect == 'poster' and t.value == user_custom_poster_url for t in entity.thumb):
                        entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url))
                    skip_default_poster_logic = True; logger.debug(f"JavBus: Using user custom poster: {web_url}"); break
            for suffix in landscape_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, current_ui_code_for_image, suffix, image_server_url)
                if web_url:
                    user_custom_landscape_url = web_url
                    if not any(t.aspect == 'landscape' and t.value == user_custom_landscape_url for t in entity.thumb):
                        entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url))
                    skip_default_landscape_logic = True; logger.debug(f"JavBus: Using user custom landscape: {web_url}"); break

        final_poster_source = None; final_poster_crop_mode = None
        final_landscape_url_source = None
        arts_urls_for_processing = []
        mgs_style_poster_filepath = None # SiteUtil.get_mgs_half_pl_poster_info_local 결과 (파일 경로)

        needs_default_image_processing = not skip_default_poster_logic or \
                                         not skip_default_landscape_logic or \
                                         (entity.fanart is not None and (len(entity.fanart) < max_arts and max_arts > 0))

        if needs_default_image_processing:
            logger.debug(f"JavBus: Running default image logic for {code} (P_skip:{skip_default_poster_logic}, L_skip:{skip_default_landscape_logic}, FanartNeed:{entity.fanart is not None and (len(entity.fanart) < max_arts and max_arts > 0)}).")

            img_urls_from_page = cls.__img_urls(tree)
            ps_url = img_urls_from_page.get('ps')
            pl_url = img_urls_from_page.get('pl')
            all_arts_from_page = img_urls_from_page.get('arts', [])

            if not skip_default_landscape_logic and pl_url:
                final_landscape_url_source = pl_url
                logger.debug(f"JavBus: Default landscape set to PL: {pl_url}")

            if not skip_default_poster_logic:
                if ps_to_poster_setting:
                    if ps_url:
                        logger.debug(f"JavBus: Poster determined by 'PS to Poster' setting: {ps_url}")
                        final_poster_source = ps_url
                    elif pl_url:
                        logger.debug(f"JavBus: 'PS to Poster' set, but PS missing. Using PL as poster: {pl_url}")
                        final_poster_source = pl_url
                    final_poster_crop_mode = None
                elif crop_mode_setting and pl_url:
                    logger.debug(f"JavBus: Poster determined by user 'crop_mode_setting' ('{crop_mode_setting}') with PL: {pl_url}")
                    final_poster_source = pl_url
                    final_poster_crop_mode = crop_mode_setting
                else:
                    if ps_url:
                        specific_arts_candidates = []
                        if all_arts_from_page:
                            if all_arts_from_page[0] not in specific_arts_candidates: specific_arts_candidates.append(all_arts_from_page[0])
                            if len(all_arts_from_page) > 1 and all_arts_from_page[-1] != all_arts_from_page[0] and all_arts_from_page[-1] not in specific_arts_candidates:
                                specific_arts_candidates.append(all_arts_from_page[-1])

                        if pl_url and SiteUtil.is_portrait_high_quality_image(pl_url, proxy_url=proxy_url):
                            if SiteUtil.is_hq_poster(ps_url, pl_url, proxy_url=proxy_url):
                                final_poster_source = pl_url
                                logger.debug(f"JavBus: Poster set to PL by is_hq_poster: {pl_url}")

                        if final_poster_source is None and specific_arts_candidates:
                            for art_candidate in specific_arts_candidates:
                                if SiteUtil.is_portrait_high_quality_image(art_candidate, proxy_url=proxy_url):
                                    if SiteUtil.is_hq_poster(ps_url, art_candidate, proxy_url=proxy_url):
                                        final_poster_source = art_candidate
                                        logger.debug(f"JavBus: Poster set to Art by is_hq_poster: {art_candidate}")
                                        break
                        if final_poster_source is None:
                            if pl_url:
                                crop_pos = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                                if crop_pos:
                                    final_poster_source = pl_url
                                    final_poster_crop_mode = crop_pos
                                    logger.debug(f"JavBus: Poster set to PL by has_hq_poster (crop: {crop_pos}): {pl_url}")

                            if final_poster_source is None and specific_arts_candidates:
                                for art_candidate in specific_arts_candidates:
                                    crop_pos_art = SiteUtil.has_hq_poster(ps_url, art_candidate, proxy_url=proxy_url)
                                    if crop_pos_art:
                                        final_poster_source = art_candidate
                                        final_poster_crop_mode = crop_pos_art
                                        logger.debug(f"JavBus: Poster set to Art by has_hq_poster (crop: {crop_pos_art}): {art_candidate}")
                                        break
                    else:
                        logger.debug("JavBus: PS URL is missing, skipping is_hq_poster/has_hq_poster. Proceeding to MGS-style or PL.")

                    # --- 3-C. MGS-style 처리 시도 (get_mgs_half_pl_poster_info_local) ---
                    if ps_url and (final_poster_source is None or final_poster_source == ps_url):
                        if pl_url:
                            logger.debug(f"JavBus: Attempting MGS-style processing for PL ('{pl_url}') with PS ('{ps_url}').")
                            temp_filepath, _, _ = SiteUtil.get_mgs_half_pl_poster_info_local(ps_url, pl_url, proxy_url=proxy_url)
                            if temp_filepath and os.path.exists(temp_filepath):
                                mgs_style_poster_filepath = temp_filepath
                                final_poster_source = mgs_style_poster_filepath
                                final_poster_crop_mode = None
                                logger.debug(f"JavBus: MGS-style processing successful. Using temp file: {final_poster_source}")
                            else:
                                logger.debug(f"JavBus: MGS-style processing failed or no suitable file.")
                        else:
                            logger.debug("JavBus: PL URL is missing, cannot attempt MGS-style processing.")
                    elif not ps_url:
                        logger.debug("JavBus: PS URL is missing, cannot attempt MGS-style processing.")

                    if final_poster_source is None:
                        if ps_url:
                            logger.debug(f"JavBus: General logic and MGS-style failed/skipped. Falling back to PS: {ps_url}")
                            final_poster_source = ps_url
                            final_poster_crop_mode = None
                        elif pl_url:
                            logger.debug(f"JavBus: General logic and MGS-style failed/skipped. No PS. Falling back to PL: {pl_url}")
                            final_poster_source = pl_url
                            final_poster_crop_mode = crop_mode_setting if crop_mode_setting else 'r'
                        else:
                            logger.warning(f"JavBus: No PS or PL available for fallback poster.")

                if final_poster_source is None:
                    if pl_url:
                        logger.warning(f"JavBus: CRITICAL - No poster source could be determined. Using PL as last resort: {pl_url}")
                        final_poster_source = pl_url
                        final_poster_crop_mode = 'r'
                    else:
                        logger.error(f"JavBus: CRITICAL - No poster source could be determined for {code}, and no PL available.")

            if all_arts_from_page:
                temp_fanart_list_jb = []
                sources_to_exclude_for_fanart_jb = set()
                if final_landscape_url_source: sources_to_exclude_for_fanart_jb.add(final_landscape_url_source)
                if isinstance(final_poster_source, str) and final_poster_source.startswith("http"):
                    sources_to_exclude_for_fanart_jb.add(final_poster_source)

                if mgs_style_poster_filepath and final_poster_source == mgs_style_poster_filepath and pl_url:
                    sources_to_exclude_for_fanart_jb.add(pl_url)

                for art_url_item_jb in all_arts_from_page:
                    if len(temp_fanart_list_jb) >= max_arts: break
                    if art_url_item_jb and art_url_item_jb not in sources_to_exclude_for_fanart_jb:
                        if art_url_item_jb not in temp_fanart_list_jb:
                            temp_fanart_list_jb.append(art_url_item_jb)
                arts_urls_for_processing = temp_fanart_list_jb
                logger.debug(f"JavBus: Fanart candidates ({len(arts_urls_for_processing)}): {arts_urls_for_processing[:3]}")

        logger.debug(f"JavBus: Final Images Decision - Poster='{str(final_poster_source)[:100]}...' (Type: {type(final_poster_source)}, Crop='{final_poster_crop_mode}'), Landscape='{final_landscape_url_source}', Fanarts_to_process({len(arts_urls_for_processing)})")

        if use_image_server and image_mode == '4' and current_ui_code_for_image:
            logger.debug(f"JavBus: Saving images to Image Server for {current_ui_code_for_image}...")
            if not skip_default_poster_logic and final_poster_source:
                p_path = SiteUtil.save_image_to_server_path(
                    final_poster_source, 'p', image_server_local_path, image_path_segment,
                    current_ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode
                )
                if p_path and not any(t.aspect == 'poster' and t.value.endswith(p_path) for t in entity.thumb):
                    entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path}"))
            if not skip_default_landscape_logic and final_landscape_url_source:
                pl_path = SiteUtil.save_image_to_server_path(
                    final_landscape_url_source, 'pl', image_server_local_path, image_path_segment,
                    current_ui_code_for_image, proxy_url=proxy_url
                )
                if pl_path and not any(t.aspect == 'landscape' and t.value.endswith(pl_path) for t in entity.thumb):
                    entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path}"))
            if arts_urls_for_processing:
                current_fanart_server_count = len([fa for fa in entity.fanart if fa.startswith(image_server_url)])
                for idx, art_url in enumerate(arts_urls_for_processing):
                    if current_fanart_server_count >= max_arts: break
                    art_relative_path = SiteUtil.save_image_to_server_path(
                        art_url, 'art', image_server_local_path, image_path_segment,
                        current_ui_code_for_image, art_index=len(entity.fanart) + 1, proxy_url=proxy_url
                    )
                    if art_relative_path:
                        full_art_url = f"{image_server_url}/{art_relative_path}"
                        if full_art_url not in entity.fanart:
                            entity.fanart.append(full_art_url)
                            current_fanart_server_count +=1
        else:
            if not skip_default_poster_logic and final_poster_source:
                poster_value_for_process = final_poster_source

                if not isinstance(final_poster_source, str) or not (final_poster_source.startswith("http") or os.path.exists(final_poster_source)):
                    logger.warning(f"JavBus: Invalid poster_source type for process_image_mode: {type(final_poster_source)}. Using PL fallback.")
                    poster_value_for_process = pl_url if pl_url else None

                if poster_value_for_process and not any(t.aspect == 'poster' for t in entity.thumb):
                    processed_poster_url = SiteUtil.process_image_mode(image_mode, poster_value_for_process, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                    if processed_poster_url:
                        entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster_url))

            if not skip_default_landscape_logic and final_landscape_url_source:
                if not any(t.aspect == 'landscape' for t in entity.thumb):
                    processed_landscape_url = SiteUtil.process_image_mode(image_mode, final_landscape_url_source, proxy_url=proxy_url)
                    if processed_landscape_url:
                        entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape_url))

            if arts_urls_for_processing:
                current_fanart_processed_count = len(entity.fanart)
                for art_url in arts_urls_for_processing:
                    if current_fanart_processed_count >= max_arts: break
                    processed_art_url = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                    if processed_art_url and processed_art_url not in entity.fanart:
                        entity.fanart.append(processed_art_url)
                        current_fanart_processed_count += 1

        if use_extras:
            try:
                script_text_nodes = tree.xpath("//script[contains(text(), 'var gid =')]/text()")
                if script_text_nodes:
                    script_content = script_text_nodes[0]
                    gid_match = re.search(r"var gid = (\d+);", script_content)
                    uc_match = re.search(r"var uc = (\d+);", script_content)
                    if gid_match and uc_match:
                        gid = gid_match.group(1); uc = uc_match.group(1)
                        magnet_api_url = f"{cls.site_base_url}/ajax/uncledatoolsbyajax.php?gid={gid}&lang=zh&uc={uc}"
                        api_headers = SiteUtil.default_headers.copy()
                        api_headers['Referer'] = url
                        api_headers['X-Requested-With'] = 'XMLHttpRequest'
                        api_headers['Accept'] = 'text/html, */*; q=0.01'
                        magnet_cookies = {'age': 'verified', 'dv': '1', 'existmag': 'mag'}
                        if cf_clearance_cookie_value_from_kwargs:
                            magnet_cookies['cf_clearance'] = cf_clearance_cookie_value_from_kwargs
                        magnet_tree = SiteUtil.get_tree(magnet_api_url, headers=api_headers, cookies=magnet_cookies, proxy_url=proxy_url)
                        if magnet_tree is not None:
                            magnet_links = magnet_tree.xpath("//tr/td[1]/a/@href")
                            magnet_titles = magnet_tree.xpath("//tr/td[1]/a/text()")
                            for i, magnet_link in enumerate(magnet_links):
                                if "magnet:?xt=urn:btih:" in magnet_link:
                                    title_extra = magnet_titles[i].strip() if i < len(magnet_titles) else f"Magnet {i+1}"
                                    entity.extras.append(EntityExtra("magnet", title_extra, "magnet", magnet_link))
            except Exception as e_extra: logger.exception(f"JavBus: Error processing extras (magnets): {e_extra}")

        final_entity = entity
        if final_entity.ui_code:
            try: final_entity = SiteUtil.shiroutoname_info(final_entity)
            except Exception as e_shirouto: logger.exception(f"JavBus: Shiroutoname correction error for {final_entity.ui_code}: {e_shirouto}")

        if hasattr(final_entity, 'ui_code') and final_entity.ui_code and final_entity.ui_code.lower() != original_code_for_url.lower():
            new_code_value = cls.module_char + cls.site_char + final_entity.ui_code.lower()
            logger.debug(f"JavBus: Code changed by shiroutoname from {final_entity.code} to {new_code_value}")
            final_entity.code = new_code_value

        if mgs_style_poster_filepath and os.path.exists(mgs_style_poster_filepath):
            try:
                os.remove(mgs_style_poster_filepath)
                logger.debug(f"JavBus: Removed MGS-style temp poster file: {mgs_style_poster_filepath}")
            except Exception as e_remove_temp:
                logger.error(f"JavBus: Failed to remove MGS-style temp poster file {mgs_style_poster_filepath}: {e_remove_temp}")

        logger.info(f"JavBus: __info finished for {code}. UI Code: {final_entity.ui_code if hasattr(final_entity, 'ui_code') else 'N/A'}, Thumbs: {len(final_entity.thumb)}, Fanarts: {len(final_entity.fanart)}")
        return final_entity

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
                ret["data"] = f"Failed to get JavBus info for {code} (__info returned None)."
        except Exception as e:
            ret["ret"] = "exception"
            ret["data"] = str(e)
            logger.exception(f"JavBus info (outer) error for code {code}: {e}")
        return ret
