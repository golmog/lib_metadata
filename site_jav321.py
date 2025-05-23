import re
import os

from lxml import html
from copy import deepcopy

from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings, EntityThumb
from .plugin import P
from .site_util import SiteUtil

logger = P.logger


class SiteJav321:
    site_name = "jav321"
    site_base_url = "https://www.jav321.com"
    module_char = "C"
    site_char = "T"
    _ps_url_cache = {} 

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        search_keyword_for_api = keyword.lower() 
        if search_keyword_for_api.endswith("cd"):
            search_keyword_for_api = search_keyword_for_api[:-2]

        url = f"{cls.site_base_url}/search"
        headers = SiteUtil.default_headers.copy()
        headers['Referer'] = cls.site_base_url + "/"
        
        res = SiteUtil.get_response(url, proxy_url=proxy_url, headers=headers, post_data={"sn": search_keyword_for_api})

        if res is None:
            logger.error(f"Jav321 Search: Failed to get response for API keyword '{search_keyword_for_api}'.")
            return []

        if not res.history or not res.url.startswith(cls.site_base_url + "/video/"):
            logger.debug(f"Jav321 Search: No direct match for API keyword '{search_keyword_for_api}'. Final URL: {res.url}")
            return []

        ret = []
        try:
            item = EntityAVSearch(cls.site_name)
            
            code_from_url_path = res.url.split("/")[-1] 
            item.code = cls.module_char + cls.site_char + code_from_url_path 
            
            item.ui_code = keyword.upper()
            
            base_xpath = "/html/body/div[2]/div[1]/div[1]"
            tree = html.fromstring(res.text)

            img_tag_node = tree.xpath(f"{base_xpath}/div[2]/div[1]/div[1]/img")
            raw_ps_url = ""
            if img_tag_node:
                src_attr = img_tag_node[0].attrib.get('src')
                onerror_attr = img_tag_node[0].attrib.get('onerror')
                if src_attr and src_attr.strip(): 
                    raw_ps_url = src_attr.strip()
                elif onerror_attr: 
                    parsed_onerror_url = cls._process_jav321_url_from_attribute(onerror_attr)
                    if parsed_onerror_url: raw_ps_url = parsed_onerror_url
            
            processed_image_url = ""
            if raw_ps_url:
                temp_url = raw_ps_url.lower()
                if temp_url.startswith("http://"): temp_url = "https://" + temp_url[len("http://"):]
                processed_image_url = temp_url
            
            if not processed_image_url: logger.warning(f"Jav321 Search: Image URL not found for code: {item.code}")

            date_tags = tree.xpath(f'{base_xpath}/div[2]/div[1]/div[2]/b[contains(text(),"配信開始日")]/following-sibling::text()')
            date_str = date_tags[0].lstrip(":").strip() if date_tags and date_tags[0].lstrip(":").strip() else "1900-01-01"
            item.desc = f"발매일: {date_str}"
            try: item.year = int(date_str[:4])
            except ValueError: item.year = 1900

            title_tags = tree.xpath(f"{base_xpath}/div[1]/h3/text()")
            item.title = item.title_ko = title_tags[0].strip() if title_tags else "제목 없음"
            if item.title == "제목 없음": logger.warning(f"Jav321 Search: Title not found for code: {item.code}")

            if manual:
                _image_mode = "1" if image_mode != "0" else image_mode
                if processed_image_url: item.image_url = SiteUtil.process_image_mode(_image_mode, processed_image_url, proxy_url=proxy_url)
                else: item.image_url = ""
                if do_trans: item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
            else:
                item.image_url = processed_image_url
                item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)

            normalized_input_keyword = keyword.lower().replace("-","").replace(" ","")
            normalized_item_uicode = item.ui_code.lower().replace("-","").replace(" ","")

            if normalized_input_keyword == normalized_item_uicode:
                item.score = 100
            else:
                item.score = 60 
                logger.warning(f"Jav321 Search Score: Mismatch after normalization. InputKeyword='{keyword}' (norm='{normalized_input_keyword}'), ItemUICode='{item.ui_code}' (norm='{normalized_item_uicode}'). Score set to 60.")

            if item.code and item.title != "제목 없음":
                if item.code and processed_image_url:
                    cls._ps_url_cache[item.code] = processed_image_url
                    logger.debug(f"Jav321 Search: Stored ps_url for {item.code} in cache: {processed_image_url}")
                ret.append(item.as_dict())
                logger.debug(f"Jav321 Search Item Added: code={item.code}, ui_code={item.ui_code}, score={item.score}, title='{item.title_ko}'")
            else:
                logger.warning(f"Jav321 Search: Item excluded. Code: {item.code}, Title: {item.title}")

        except Exception as e_item_search:
            logger.exception(f"Jav321 Search: Error processing item for API keyword '{search_keyword_for_api}': {e_item_search}")
        return ret

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            do_trans_arg = kwargs.get('do_trans', True)
            proxy_url_arg = kwargs.get('proxy_url', None)
            image_mode_arg = kwargs.get('image_mode', "0")
            manual_arg = kwargs.get('manual', False)
            data = cls.__search(keyword,
                                do_trans=do_trans_arg,
                                proxy_url=proxy_url_arg,
                                image_mode=image_mode_arg,
                                manual=manual_arg)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data else "no_match"; ret["data"] = data
        return ret

    @staticmethod
    def _process_jav321_url_from_attribute(url_attribute_value):
        """
        img 태그의 src 또는 onerror 속성값에서 Jav321 관련 URL을 추출하고 처리합니다.
        onerror의 경우 "this.src='...'" 패턴을 파싱합니다.
        반환값은 소문자화, https 변환된 URL이거나, 유효하지 않으면 None입니다.
        """
        if not url_attribute_value:
            return None
        
        raw_url = ""
        if "this.src='" in url_attribute_value: # onerror 형태
            url_match = re.search(r"this\.src='([^']+)'", url_attribute_value)
            if url_match:
                raw_url = url_match.group(1).strip()
        else: # src 형태 (또는 onerror가 아니지만 URL일 수 있는 경우)
            raw_url = url_attribute_value.strip()

        if not raw_url:
            return None

        # jav321.com 또는 pics.dmm.co.jp 등의 유효한 도메인인지 체크 (선택적)
        # if not ("jav321.com" in raw_url or "pics.dmm.co.jp" in raw_url):
        #     logger.debug(f"Jav321 URL Process: Skipping non-target domain URL: {raw_url}")
        #     return None
            
        processed_url = raw_url.lower()
        if processed_url.startswith("http://"):
            processed_url = "https://" + processed_url[len("http://"):]
        # //netloc//path 형태의 더블 슬래시는 .lower()나 replace에 의해 변경되지 않음.
        
        return processed_url

    @classmethod
    def __img_urls(cls, tree):
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        
        try:
            # 1. PS 이미지 추출 (src 우선, 없으면 onerror)
            ps_xpath = '/html/body/div[2]/div[1]/div[1]/div[2]/div[1]/div[1]/img'
            ps_img_node = tree.xpath(ps_xpath)
            if ps_img_node:
                src_val = ps_img_node[0].attrib.get('src')
                onerror_val = ps_img_node[0].attrib.get('onerror')
                
                url_candidate_ps = None
                if src_val and src_val.strip(): # src 값 우선
                    url_candidate_ps = cls._process_jav321_url_from_attribute(src_val)
                if not url_candidate_ps and onerror_val: # src 없거나 처리 실패 시 onerror
                    url_candidate_ps = cls._process_jav321_url_from_attribute(onerror_val)
                
                if url_candidate_ps: 
                    img_urls['ps'] = url_candidate_ps
                    logger.debug(f"Jav321 ImgUrls: PS URL='{img_urls['ps']}' (From src: {bool(src_val and src_val.strip() and img_urls['ps'] == cls._process_jav321_url_from_attribute(src_val))})")
                else: logger.warning(f"Jav321 ImgUrls: PS URL not found.")
            else: logger.warning(f"Jav321 ImgUrls: PS tag not found.")

            # 2. PL 이미지 추출 (사이드바 첫번째, src 우선)
            pl_xpath = '/html/body/div[2]/div[2]/div[1]/p/a/img'
            pl_img_node = tree.xpath(pl_xpath)
            if pl_img_node:
                src_val = pl_img_node[0].attrib.get('src')
                onerror_val = pl_img_node[0].attrib.get('onerror')
                
                url_candidate_pl = None
                if src_val and src_val.strip():
                    url_candidate_pl = cls._process_jav321_url_from_attribute(src_val)
                if not url_candidate_pl and onerror_val:
                    url_candidate_pl = cls._process_jav321_url_from_attribute(onerror_val)

                if url_candidate_pl:
                    img_urls['pl'] = url_candidate_pl
                    logger.debug(f"Jav321 ImgUrls: PL URL='{img_urls['pl']}' (From src: {bool(src_val and src_val.strip() and img_urls['pl'] == cls._process_jav321_url_from_attribute(src_val))})")
                else: logger.warning(f"Jav321 ImgUrls: PL (sidebar first) URL not found.")
            else: logger.warning(f"Jav321 ImgUrls: PL (sidebar first) tag not found.")

            # 3. Arts 이미지 추출 (사이드바 두 번째 이후, src 우선)
            arts_xpath = '/html/body/div[2]/div[2]/div[position()>1]//a[contains(@href, "/snapshot/")]/img'
            arts_img_nodes = tree.xpath(arts_xpath)
            temp_arts_list = []
            if arts_img_nodes:
                for img_node in arts_img_nodes:
                    src_val = img_node.attrib.get('src')
                    onerror_val = img_node.attrib.get('onerror')
                    
                    url_candidate_art = None
                    if src_val and src_val.strip():
                        url_candidate_art = cls._process_jav321_url_from_attribute(src_val)
                    if not url_candidate_art and onerror_val:
                        url_candidate_art = cls._process_jav321_url_from_attribute(onerror_val)
                    
                    if url_candidate_art: temp_arts_list.append(url_candidate_art)
            
            img_urls['arts'] = list(dict.fromkeys(temp_arts_list)) # 중복 제거
            
        except Exception as e_img_extract:
            logger.exception(f"Jav321 ImgUrls: Error extracting image URLs: {e_img_extract}")
        
        logger.debug(f"Jav321 ImgUrls Final: PS='{img_urls['ps']}', PL='{img_urls['pl']}', Arts({len(img_urls['arts'])})='{img_urls['arts'][:3]}...'")
        return img_urls


    @staticmethod
    def _clean_value(value_str):
        """주어진 문자열 값에서 앞뒤 공백 및 특정 접두사(': ')를 제거합니다."""
        if isinstance(value_str, str):
            cleaned = value_str.strip()
            if cleaned.startswith(": "):
                return cleaned[2:].strip()
            return cleaned
        return value_str


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
        # === 1. 설정값 로드, 페이지 로딩, Entity 초기화 ===
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        ps_to_poster_setting = ps_to_poster
        crop_mode_setting = crop_mode
        
        logger.debug(f"Jav321 Info: Starting for {code}. ImageMode: {image_mode}, UseImgServ: {use_image_server}")

        url = f"{cls.site_base_url}/video/{code[2:]}"
        headers = SiteUtil.default_headers.copy(); headers['Referer'] = cls.site_base_url + "/"
        tree = None
        try:
            tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=headers)
            if tree is None or not tree.xpath('/html/body/div[2]/div[1]/div[1]'): 
                logger.error(f"Jav321: Failed to get valid detail page tree for {code}. URL: {url}")
                return None
        except Exception as e_get_tree:
            logger.exception(f"Jav321: Exception while getting detail page for {code}: {e_get_tree}")
            return None

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = []; entity.fanart = []; entity.extras = []
        ui_code_for_image = ""

        ps_url_from_search_cache = cls._ps_url_cache.get(code)
        if ps_url_from_search_cache:
            logger.debug(f"Jav321: Found PS URL in cache for {code}: {ps_url_from_search_cache}")
        else:
            logger.debug(f"Jav321: No PS URL found in cache for {code}.")

        # === 2. 전체 메타데이터 파싱 (ui_code_for_image 확정 포함) ===
        identifier_parsed = False 
        raw_h3_title_text = "" # H3 제목 저장용
        try:
            logger.debug(f"Jav321: Parsing metadata for {code}...")

            # --- 제목(Tagline) 파싱 ---
            tagline_h3_nodes = tree.xpath('/html/body/div[2]/div[1]/div[1]/div[1]/h3')
            if tagline_h3_nodes:
                h3_node = tagline_h3_nodes[0]
                try:
                    h3_clone = deepcopy(h3_node)
                    for small_tag_node in h3_clone.xpath('.//small'):
                        small_tag_node.getparent().remove(small_tag_node) 
                    raw_h3_title_text = h3_clone.text_content().strip() 
                except Exception as e_remove_small_tag:
                    logger.warning(f"Jav321: Failed to remove <small> from H3, using full text. Error: {e_remove_small_tag}")
                    raw_h3_title_text = h3_node.text_content().strip()
            else: 
                logger.warning(f"Jav321: H3 title tag not found for {code}.")

            # --- 줄거리(Plot) 파싱 ---
            plot_div_nodes = tree.xpath('/html/body/div[2]/div[1]/div[1]/div[2]/div[3]/div')
            if plot_div_nodes:
                plot_full_text = plot_div_nodes[0].text_content().strip()
                if plot_full_text: 
                    entity.plot = SiteUtil.trans(cls._clean_value(plot_full_text), do_trans=do_trans)
            else:
                logger.warning(f"Jav321: Plot div (original XPath) not found for {code}.")

            # --- 부가 정보 파싱 (div class="col-md-9" 내부) ---
            info_container_node_list = tree.xpath('//div[contains(@class, "panel-body")]//div[contains(@class, "col-md-9")]')

            if info_container_node_list:
                info_node = info_container_node_list[0]
                all_b_tags = info_node.xpath("./b")

                for b_tag_key_node in all_b_tags:
                    current_key = cls._clean_value(b_tag_key_node.text_content()).replace(":", "")
                    if not current_key: continue

                    if current_key == "品番":
                        pid_value_nodes = b_tag_key_node.xpath("./following-sibling::text()[1][normalize-space()]")
                        pid_value_raw = pid_value_nodes[0].strip() if pid_value_nodes else ""
                        pid_value_cleaned = cls._clean_value(pid_value_raw)
                        if pid_value_cleaned:
                            formatted_pid = pid_value_cleaned.upper()
                            try: 
                                label_pid_val, num_pid_val = formatted_pid.split('-', 1)
                                ui_code_for_image = f"{label_pid_val.upper()}-{num_pid_val}"
                            except ValueError: 
                                ui_code_for_image = formatted_pid
                            entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image 
                            entity.ui_code = ui_code_for_image; identifier_parsed = True
                            logger.info(f"Jav321: Identifier (ui_code_for_image) parsed: {ui_code_for_image}")
                            if entity.tag is None: entity.tag = []
                            if '-' in ui_code_for_image and ui_code_for_image.split('-',1)[0].upper() not in entity.tag:
                                entity.tag.append(ui_code_for_image.split('-',1)[0].upper())

                    elif current_key == "出演者":
                        if entity.actor is None: entity.actor = []
                        if entity.actor is None: entity.actor = []
                        actor_a_tags = b_tag_key_node.xpath("./following-sibling::a[contains(@href, '/star/')]")
                        temp_actor_names = set()
                        for actor_link in actor_a_tags:
                            actor_name_raw = actor_link.text_content().strip()
                            actor_name_cleaned = cls._clean_value(actor_name_raw) # 배우 이름 클리닝
                            if actor_name_cleaned: temp_actor_names.add(actor_name_cleaned)

                        for name_item in temp_actor_names:
                            if not any(ea_item.name == name_item for ea_item in entity.actor):
                                entity.actor.append(EntityActor(name_item))

                    elif current_key == "メーカー":
                        studio_name_raw = ""
                        maker_a_tag = b_tag_key_node.xpath("./following-sibling::a[1][contains(@href, '/company/')]")
                        if maker_a_tag:
                            studio_name_raw = maker_a_tag[0].text_content().strip()
                        else:
                            maker_text_node = b_tag_key_node.xpath("./following-sibling::text()[1][normalize-space()]")
                            if maker_text_node:
                                studio_name_raw = maker_text_node[0].strip()

                        cleaned_studio_name = cls._clean_value(studio_name_raw)
                        if cleaned_studio_name:
                            entity.studio = SiteUtil.trans(cleaned_studio_name, do_trans=do_trans)

                    elif current_key == "ジャンル":
                        if entity.genre is None: entity.genre = []
                        genre_a_tags = b_tag_key_node.xpath("./following-sibling::a[contains(@href, '/genre/')]")
                        temp_genre_list = []
                        for genre_link in genre_a_tags:
                            genre_ja_raw = genre_link.text_content().strip()
                            genre_ja_cleaned = cls._clean_value(genre_ja_raw) # 장르 이름 클리닝
                            if not genre_ja_cleaned or genre_ja_cleaned in SiteUtil.av_genre_ignore_ja: continue

                            if genre_ja_cleaned in SiteUtil.av_genre: temp_genre_list.append(SiteUtil.av_genre[genre_ja_cleaned])
                            else:
                                genre_ko_item = SiteUtil.trans(genre_ja_cleaned, do_trans=do_trans).replace(" ", "")
                                if genre_ko_item not in SiteUtil.av_genre_ignore_ko: temp_genre_list.append(genre_ko_item)
                        if temp_genre_list: entity.genre = list(set(temp_genre_list))

                    elif current_key == "配信開始日":
                        date_val_nodes = b_tag_key_node.xpath("./following-sibling::text()[1][normalize-space()]")
                        date_val_raw = date_val_nodes[0].strip() if date_val_nodes else ""
                        date_val_cleaned = cls._clean_value(date_val_raw)
                        if date_val_cleaned: 
                            entity.premiered = date_val_cleaned.replace("/", "-")
                            if len(entity.premiered) >= 4 and entity.premiered[:4].isdigit():
                                try: entity.year = int(entity.premiered[:4])
                                except ValueError: entity.year = 0
                            else: entity.year = 0

                    elif current_key == "収録時間":
                        time_val_nodes = b_tag_key_node.xpath("./following-sibling::text()[1][normalize-space()]")
                        time_val_raw = time_val_nodes[0].strip() if time_val_nodes else ""
                        time_val_cleaned = cls._clean_value(time_val_raw)
                        if time_val_cleaned:
                            match_rt = re.search(r"(\d+)", time_val_cleaned)
                            if match_rt: entity.runtime = int(match_rt.group(1))

                    elif current_key == "シリーズ":
                        series_name_raw = ""
                        series_a_tag = b_tag_key_node.xpath("./following-sibling::a[1][contains(@href, '/series/')]")
                        if series_a_tag:
                            series_name_raw = series_a_tag[0].text_content().strip()
                        else:
                            series_text_node = b_tag_key_node.xpath("./following-sibling::text()[1][normalize-space()]")
                            if series_text_node:
                                series_name_raw = series_text_node[0].strip()

                        series_name_cleaned = cls._clean_value(series_name_raw)
                        if series_name_cleaned:
                            if entity.tag is None: entity.tag = []
                            trans_series = SiteUtil.trans(series_name_cleaned, do_trans=do_trans)
                            if trans_series and trans_series not in entity.tag: 
                                entity.tag.append(trans_series)

                    elif current_key == "平均評価":
                        rating_val_nodes = b_tag_key_node.xpath("./following-sibling::text()[1][normalize-space()]")
                        rating_val_raw = rating_val_nodes[0].strip() if rating_val_nodes else ""
                        rating_val_cleaned = cls._clean_value(rating_val_raw)
                        if rating_val_cleaned:
                            try: 
                                rating_float = float(rating_val_cleaned)
                                if entity.ratings is None: entity.ratings = [EntityRatings(rating_float, max=5, name=cls.site_name)]
                                else: entity.ratings[0].value = rating_float
                            except ValueError: logger.warning(f"Jav321: Could not parse rating value '{rating_val_cleaned}'")
            else: 
                logger.warning(f"Jav321: Main info container (col-md-9) not found for {code}.")

            # Tagline 최종 설정 (H3 제목에서 품번 제외)
            if raw_h3_title_text and ui_code_for_image:
                tagline_candidate_text = raw_h3_title_text
                if raw_h3_title_text.upper().startswith(ui_code_for_image): # 품번으로 시작하면 제거
                    tagline_candidate_text = raw_h3_title_text[len(ui_code_for_image):].strip()
                entity.tagline = SiteUtil.trans(cls._clean_value(tagline_candidate_text), do_trans=do_trans)
            elif raw_h3_title_text: 
                entity.tagline = SiteUtil.trans(cls._clean_value(raw_h3_title_text), do_trans=do_trans)

            if not identifier_parsed:
                logger.error(f"Jav321: CRITICAL - Identifier parse failed for {code} from any source.")
                ui_code_for_image = code[2:].upper().replace("_", "-") 
                entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image
                entity.ui_code = ui_code_for_image
            
            # 최종 정리 (plot, tagline 등)
            if entity.title: entity.title = cls._clean_value(entity.title) # 품번으로 설정된 title도 클리닝
            if entity.originaltitle: entity.originaltitle = cls._clean_value(entity.originaltitle)
            if entity.sorttitle: entity.sorttitle = cls._clean_value(entity.sorttitle)
            if not entity.tagline and entity.title: entity.tagline = entity.title
            if not entity.plot and entity.tagline: entity.plot = entity.tagline 
            elif not entity.plot and entity.title: entity.plot = entity.title # Plot도 최종적으로 없으면 Title

        except Exception as e_meta_main_final:
            logger.exception(f"Jav321: Major error during metadata parsing for {code}: {e_meta_main_final}")
            if not ui_code_for_image: return None

        # === 3. 사용자 지정 포스터 확인 및 처리 ===
        user_custom_poster_url = None
        user_custom_landscape_url = None
        skip_default_poster_logic = False
        skip_default_landscape_logic = False

        if not ui_code_for_image and hasattr(entity, 'ui_code') and entity.ui_code:
            ui_code_for_image = entity.ui_code
        elif not ui_code_for_image: 
            ui_code_for_image = code[len(cls.module_char) + len(cls.site_char):].upper().replace("_", "-")
            logger.warning(f"Jav321: ui_code_for_image was not set during metadata parsing, using fallback: {ui_code_for_image}")

        if use_image_server and image_server_local_path and image_server_url and ui_code_for_image:
            poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
            landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]

            for suffix in poster_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(
                    image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url
                )
                if web_url:
                    user_custom_poster_url = web_url
                    entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url))
                    skip_default_poster_logic = True
                    logger.info(f"Jav321: Using user custom poster for {ui_code_for_image}: {web_url}")
                    break 

            for suffix in landscape_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(
                    image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url
                )
                if web_url:
                    user_custom_landscape_url = web_url
                    entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url))
                    skip_default_landscape_logic = True
                    logger.info(f"Jav321: Using user custom landscape for {ui_code_for_image}: {web_url}")
                    break

        # === 4. 기본 이미지 처리: 사용자 지정 이미지가 없거나, 팬아트가 더 필요한 경우 실행 ===
        # 최종적으로 entity.thumb과 entity.fanart에 추가될 이미지 소스들
        final_poster_source = None         # 포스터로 사용될 최종 이미지 소스 (URL 또는 로컬 파일 경로)
        final_poster_crop_mode = None    # final_poster_source에 적용될 크롭 모드
        final_landscape_url_source = None  # 랜드스케이프로 사용될 최종 이미지 URL
        arts_urls_for_processing = []      # 팬아트로 사용될 최종 이미지 URL 목록

        # 상세 페이지에서 파싱한 이미지 URL들
        ps_from_detail_page = None
        pl_from_detail_page = None
        all_arts_from_page = []

        # 플레이스홀더 이미지("now_printing.jpg")의 로컬 경로
        now_printing_path = None

        # 유효 후보 변수 초기화
        valid_ps_candidate = None
        valid_pl_candidate = None
        jav321_special_poster_filepath = None

        # 기본 이미지 로직 실행 조건 결정
        # 사용자 지정 포스터/랜드스케이프가 없거나, 필요한 팬아트 수가 부족할 때
        needs_default_image_processing = not skip_default_poster_logic or \
                                         not skip_default_landscape_logic or \
                                         (entity.fanart is None or (len(entity.fanart) < max_arts and max_arts > 0))

        if needs_default_image_processing:
            logger.debug(f"Jav321: Running default image logic for {code} (P_skip:{skip_default_poster_logic}, PL_skip:{skip_default_landscape_logic}, FanartNeed:{entity.fanart is None or (len(entity.fanart) < max_arts and max_arts > 0)})...")
            try:
                # 4-1. 상세 페이지에서 PS, PL, Arts 이미지 URL 파싱
                img_urls_from_page = cls.__img_urls(tree) 
                ps_from_detail_page = img_urls_from_page.get('ps')
                pl_from_detail_page = img_urls_from_page.get('pl')
                all_arts_from_page = img_urls_from_page.get('arts', []) 

                # 4-2. 플레이스홀더("now_printing.jpg") 이미지 경로 설정 (이미지 서버 사용 시)
                if use_image_server and image_server_local_path:
                    now_printing_path = os.path.join(image_server_local_path, "now_printing.jpg")
                    if not os.path.exists(now_printing_path): 
                        now_printing_path = None 

                # --- A. 포스터 소스 결정 ---
                if not skip_default_poster_logic:
                    temp_poster_step1 = None 
                    temp_crop_step1 = None 

                    # 4-A-1. 유효한 PS 후보 결정 (플레이스홀더 아닌 것)
                    is_ps_detail_placeholder = False
                    if ps_from_detail_page and now_printing_path and SiteUtil.are_images_visually_same(ps_from_detail_page, now_printing_path, proxy_url=proxy_url):
                        is_ps_detail_placeholder = True

                    if ps_from_detail_page and not is_ps_detail_placeholder:
                        valid_ps_candidate = ps_from_detail_page
                    elif ps_url_from_search_cache:
                        is_search_ps_placeholder = False
                        if now_printing_path and SiteUtil.are_images_visually_same(ps_url_from_search_cache, now_printing_path, proxy_url=proxy_url):
                            is_search_ps_placeholder = True
                        if not is_search_ps_placeholder: valid_ps_candidate = ps_url_from_search_cache
                        elif ps_from_detail_page : valid_ps_candidate = ps_from_detail_page

                    # 4-A-2. 유효한 PL 후보 결정 (플레이스홀더 아닌 것)
                    if pl_from_detail_page:
                        is_pl_detail_placeholder = False
                        if now_printing_path and SiteUtil.are_images_visually_same(pl_from_detail_page, now_printing_path, proxy_url=proxy_url):
                            is_pl_detail_placeholder = True
                        if not is_pl_detail_placeholder: valid_pl_candidate = pl_from_detail_page

                    # 4-A-3. 일반적인 포스터 결정 로직 (MGStage 방식 기반)
                    # temp_poster_step1 과 temp_crop_step1 은 이 블록 이전에 None 으로 초기화되어 있어야 함 (이미 그렇게 되어 있음)
                    if ps_to_poster_setting and valid_ps_candidate:
                        temp_poster_step1 = valid_ps_candidate
                        # temp_crop_step1 은 None으로 유지
                    elif crop_mode_setting and valid_pl_candidate:
                        temp_poster_step1 = valid_pl_candidate
                        temp_crop_step1 = crop_mode_setting
                    elif valid_pl_candidate and valid_ps_candidate: # PS, PL 모두 유효할 때 HQ 로직
                        loc = SiteUtil.has_hq_poster(valid_ps_candidate, valid_pl_candidate, proxy_url=proxy_url)
                        if loc:
                            temp_poster_step1 = valid_pl_candidate
                            temp_crop_step1 = loc
                        elif SiteUtil.is_hq_poster(valid_ps_candidate, valid_pl_candidate, proxy_url=proxy_url):
                            temp_poster_step1 = valid_pl_candidate
                    elif valid_ps_candidate and not valid_pl_candidate and not crop_mode_setting and not ps_to_poster_setting:
                        pass
                    elif valid_pl_candidate and crop_mode_setting and not ps_to_poster_setting:
                        temp_poster_step1 = valid_pl_candidate
                        temp_crop_step1 = crop_mode_setting

                    # 4-A-4. Specific Art 후보를 포스터로 사용 시도 (일반 로직에서 포스터 못 정했고, PS강제설정 아닐 때)
                    if not temp_poster_step1 and not ps_to_poster_setting:
                        actual_arts_for_specific = [art for art in all_arts_from_page if art != valid_pl_candidate] if valid_pl_candidate else all_arts_from_page

                        specific_art_candidates = []
                        if actual_arts_for_specific:
                            # 첫 번째 아트를 specific 후보로 추가
                            if actual_arts_for_specific[0] not in specific_art_candidates:
                                specific_art_candidates.append(actual_arts_for_specific[0])

                            # 마지막 아트가 첫 번째 아트와 다르고, 리스트에 이미 없다면 추가
                            if len(actual_arts_for_specific) > 1 and \
                               actual_arts_for_specific[-1] != actual_arts_for_specific[0] and \
                               actual_arts_for_specific[-1] not in specific_art_candidates:
                                specific_art_candidates.append(actual_arts_for_specific[-1])

                        logger.debug(f"Jav321: Specific art candidates for poster: {specific_art_candidates}")

                        if valid_ps_candidate:
                            for sp_candidate in specific_art_candidates:
                                if SiteUtil.is_hq_poster(valid_ps_candidate, sp_candidate, proxy_url=proxy_url):
                                    logger.info(f"Jav321: Specific art ('{sp_candidate}') chosen as poster based on HQ check with PS ('{valid_ps_candidate}').")
                                    temp_poster_step1 = sp_candidate
                                    temp_crop_step1 = None
                                    break
                    
                    # 4-A-5. 최종 Fallback (그래도 포스터 없으면 유효 PS 사용)
                    if not temp_poster_step1 and valid_ps_candidate:
                        logger.debug(f"Jav321: Fallback to PS as poster after PL and Art checks failed.")
                        temp_poster_step1 = valid_ps_candidate
                        temp_crop_step1 = None # PS는 보통 크롭 없이 사용

                    logger.debug(f"Jav321: After general poster logic (including art check): temp_poster_step1='{temp_poster_step1}', temp_crop_step1='{temp_crop_step1}'")

                    title_for_vr_check = entity.title if entity.title else "" 
                    is_vr_content = title_for_vr_check.lower().startswith('[vr]') or \
                                    title_for_vr_check.lower().startswith('【vr】')

                    skip_mgs_for_vr_and_use_ps = False
                    # 조건: 일반 로직 결과가 PS이고, VR 콘텐츠이며, PS 강제 설정이 아니고, 유효 PS가 있을 때
                    if temp_poster_step1 == valid_ps_candidate and \
                       is_vr_content and \
                       not ps_to_poster_setting and \
                       valid_ps_candidate:
                        skip_mgs_for_vr_and_use_ps = True
                        logger.info(f"Jav321: VR content ('{title_for_vr_check}') and general logic chose PS. Skipping MGS style processing and using PS ('{valid_ps_candidate}') as poster.")

                    # 4-A-6. MGS 스타일 특별 처리 시도 (VR 스킵 조건이 False일 때만)
                    if not skip_mgs_for_vr_and_use_ps:
                        attempt_special_local = False
                        general_logic_chose_ps = (temp_poster_step1 == valid_ps_candidate)

                        if valid_pl_candidate and valid_ps_candidate and \
                           not ps_to_poster_setting and general_logic_chose_ps:
                            try:
                                pl_image_for_check = SiteUtil.imopen(valid_pl_candidate, proxy_url=proxy_url)
                                if pl_image_for_check:
                                    pl_width, pl_height = pl_image_for_check.size
                                    if pl_height > 0 : 
                                        original_pl_ratio = pl_width / pl_height
                                        half_pl_width = pl_width / 2
                                        is_original_pl_very_wide = (original_pl_ratio >= 1.7)
                                        is_half_cut_shape_acceptable = False 
                                        if half_pl_width > 0: 
                                            is_half_cut_shape_acceptable = (pl_height <= half_pl_width * 1.2)
                                        if is_original_pl_very_wide and is_half_cut_shape_acceptable:
                                            attempt_special_local = True
                                else:
                                    logger.warning(f"Jav321: Could not open PL ('{valid_pl_candidate}') for MGS style eligibility check (imopen failed).")
                            except Exception as e_ratio_check:
                                logger.error(f"Jav321: Error during PL MGS style eligibility check: {e_ratio_check}")
                        
                        if attempt_special_local:
                            logger.debug(f"Jav321: Attempting MGS style processing for PL ('{valid_pl_candidate}').")
                            _temp_filepath, _, _ = SiteUtil.get_mgs_half_pl_poster_info_local(valid_ps_candidate, valid_pl_candidate, proxy_url=proxy_url)
                            if _temp_filepath and os.path.exists(_temp_filepath): 
                                jav321_special_poster_filepath = _temp_filepath # 기존 변수명 사용
                                logger.info(f"Jav321: MGS style processing successful. Using temp file: {jav321_special_poster_filepath}")
                            else:
                                logger.debug(f"Jav321: MGS style processing did not yield a valid file for PL ('{valid_pl_candidate}').")

                    # 4-A-7. 최종 포스터 소스 및 크롭 모드 결정
                    if skip_mgs_for_vr_and_use_ps:
                        final_poster_source = valid_ps_candidate 
                        final_poster_crop_mode = None
                    elif jav321_special_poster_filepath:
                        final_poster_source = jav321_special_poster_filepath
                        final_poster_crop_mode = None 
                    else:
                        final_poster_source = temp_poster_step1
                        final_poster_crop_mode = temp_crop_step1

                    logger.debug(f"Jav321: Final poster decision: source='{final_poster_source}', crop='{final_poster_crop_mode}'")

                # --- B. 랜드스케이프 소스 결정 ---
                if not skip_default_landscape_logic: 
                    final_landscape_url_source = valid_pl_candidate

                # --- C. 팬아트 목록 결정 ---
                temp_fanart_list = []
                if all_arts_from_page:
                    sources_to_exclude_for_fanart = set() # 팬아트에서 제외할 URL 집합
                    if final_landscape_url_source: 
                        sources_to_exclude_for_fanart.add(final_landscape_url_source)
                    if final_poster_source and isinstance(final_poster_source, str) and final_poster_source.startswith("http"):
                        sources_to_exclude_for_fanart.add(final_poster_source)

                    # MGS 스타일 처리로 포스터가 생성된 경우, 원본 PL도 팬아트에서 제외
                    if valid_pl_candidate and jav321_special_poster_filepath and final_poster_source == jav321_special_poster_filepath:
                        sources_to_exclude_for_fanart.add(valid_pl_candidate)
                        logger.debug(f"Jav321: Excluding original PL ('{valid_pl_candidate}') from fanart as MGS style poster was generated.")

                    for art_url in all_arts_from_page:
                        if len(temp_fanart_list) >= max_arts: break # 최대 개수 도달 시 중단
                        if art_url and art_url not in sources_to_exclude_for_fanart: # 제외 대상 아니고
                            is_art_placeholder = False
                            if now_printing_path and SiteUtil.are_images_visually_same(art_url, now_printing_path, proxy_url=proxy_url):
                                is_art_placeholder = True # 플레이스홀더인지 확인
                            if not is_art_placeholder and art_url not in temp_fanart_list: # 플레이스홀더 아니고 중복 아니면 추가
                                temp_fanart_list.append(art_url)
                arts_urls_for_processing = temp_fanart_list
                logger.debug(f"Jav321: Final fanart list ({len(arts_urls_for_processing)} items): {arts_urls_for_processing[:3]}...")

                # --- D. 이미지 최종 처리 및 entity.thumb, entity.fanart에 추가 (이미지 서버 사용 안 할 때) ---
                if not (use_image_server and image_mode == '4'):
                    # 포스터 추가
                    if final_poster_source and not skip_default_poster_logic and not any(t.aspect == 'poster' for t in entity.thumb):
                        processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                        if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                    # 랜드스케이프 추가
                    if final_landscape_url_source and not skip_default_landscape_logic and not any(t.aspect == 'landscape' for t in entity.thumb):
                        processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url_source, proxy_url=proxy_url) # 랜드스케이프는 크롭 없음
                        if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))

                    # 팬아트 추가
                    if entity.fanart is None: entity.fanart = [] # None 방지
                    for art_url_item in arts_urls_for_processing:
                        if len(entity.fanart) >= max_arts: break # 이미 추가된 팬아트 수도 고려
                        processed_art = SiteUtil.process_image_mode(image_mode, art_url_item, proxy_url=proxy_url)
                        if processed_art and processed_art not in entity.fanart : # 중복 방지
                            entity.fanart.append(processed_art)
            except Exception as e_img_proc_default:
                logger.exception(f"Jav321: Error during default image processing logic for {code}: {e_img_proc_default}")

        # === 5. 이미지 서버 저장 로직 (ui_code_for_image 사용) ===
        if use_image_server and image_mode == '4' and ui_code_for_image:
            logger.info(f"Jav321: Saving images to Image Server for {ui_code_for_image}...")

            # PS 저장 (플레이스홀더 아닌 유효 PS 결정 로직 강화)
            ps_to_save_on_server = None
            # 1. 상세 페이지 PS가 유효하면 사용
            if ps_from_detail_page: 
                is_ps_detail_placeholder_for_save = False
                if now_printing_path and SiteUtil.are_images_visually_same(ps_from_detail_page, now_printing_path, proxy_url=proxy_url):
                    is_ps_detail_placeholder_for_save = True
                if not is_ps_detail_placeholder_for_save:
                    ps_to_save_on_server = ps_from_detail_page
            # 2. 상세 페이지 PS가 부적합하고, 검색 캐시 PS가 유효하면 사용
            if not ps_to_save_on_server and ps_url_from_search_cache:
                is_search_ps_placeholder_for_save = False
                if now_printing_path and SiteUtil.are_images_visually_same(ps_url_from_search_cache, now_printing_path, proxy_url=proxy_url):
                    is_search_ps_placeholder_for_save = True
                if not is_search_ps_placeholder_for_save: 
                    ps_to_save_on_server = ps_url_from_search_cache

            #if ps_to_save_on_server:
            #    SiteUtil.save_image_to_server_path(ps_to_save_on_server, 'ps', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
            #else:
            #    logger.debug(f"Jav321 ImgServ: No valid PS image to save for {ui_code_for_image}.")

            # 포스터 저장
            if not skip_default_poster_logic and final_poster_source and not any(t.aspect == 'poster' for t in entity.thumb):
                p_path = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                if p_path: entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path}"))

            # 랜드스케이프 저장
            if not skip_default_landscape_logic and final_landscape_url_source and not any(t.aspect == 'landscape' for t in entity.thumb):
                pl_path = SiteUtil.save_image_to_server_path(final_landscape_url_source, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                if pl_path: entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path}"))

            # 팬아트 저장
            if arts_urls_for_processing:
                if entity.fanart is None: entity.fanart = []
                # 현재 서버에 저장된 팬아트 URL들을 가져와서 중복 저장 방지 (좀 더 정확한 방법)
                current_fanart_urls_on_server = set()
                for thumb_entry in entity.thumb: # 사용자 지정 팬아트도 있을 수 있으므로 entity.thumb도 확인
                    if thumb_entry.aspect == 'fanart' and isinstance(thumb_entry.value, str):
                        current_fanart_urls_on_server.add(thumb_entry.value)
                for fanart_url_str in entity.fanart: # 기존 entity.fanart에 있는 URL도 확인
                    if isinstance(fanart_url_str, str):
                        current_fanart_urls_on_server.add(fanart_url_str)

                processed_fanart_count_server = len(current_fanart_urls_on_server)

                # 팬아트 제외 로직 (이전과 동일)
                sources_to_exclude_server = set()
                if final_landscape_url_source: sources_to_exclude_server.add(final_landscape_url_source)
                if final_poster_source and isinstance(final_poster_source, str) and final_poster_source.startswith("http"):
                    sources_to_exclude_server.add(final_poster_source)
                # valid_pl_candidate는 이 범위에서 사용 가능하도록 위에서 결정되어야 함 (현재 코드에서는 needs_default_image_processing 블록 내에 있음)
                # 이 부분을 사용하려면 valid_pl_candidate를 needs_default_image_processing 블록 밖에서도 접근 가능하게 하거나,
                # 또는 pl_from_detail_page를 직접 사용 (단, 플레이스홀더일 수 있음)
                # 여기서는 MGS 스타일 처리가 적용된 경우를 가정하므로, jav321_special_poster_filepath와 final_poster_source를 사용
                if jav321_special_poster_filepath and final_poster_source == jav321_special_poster_filepath and pl_from_detail_page: # 원본 PL이 존재할 때
                    sources_to_exclude_server.add(pl_from_detail_page)


                for idx, art_url_item_server in enumerate(arts_urls_for_processing):
                    if processed_fanart_count_server >= max_arts: break
                    if art_url_item_server and art_url_item_server not in sources_to_exclude_server:
                        art_relative_path = SiteUtil.save_image_to_server_path(art_url_item_server, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                        if art_relative_path:
                            full_art_url_server = f"{image_server_url}/{art_relative_path}"
                            if full_art_url_server not in current_fanart_urls_on_server: # 중복 저장 방지
                                entity.fanart.append(full_art_url_server)
                                current_fanart_urls_on_server.add(full_art_url_server) # 새로 추가된 것도 집합에 반영
                                processed_fanart_count_server +=1

        # === 6. 예고편 처리, Shiroutoname 보정 등 ===
        entity.extras = [] 
        if use_extras:
            try: 
                trailer_xpath = '//*[@id="vjs_sample_player"]/source/@src'
                trailer_tags = tree.xpath(trailer_xpath)
                if trailer_tags:
                    trailer_url = trailer_tags[0].strip()
                    if trailer_url.startswith("http"):
                        trailer_title = entity.tagline if entity.tagline else (entity.title if entity.title else code)
                        entity.extras.append(EntityExtra("trailer", trailer_title, "mp4", trailer_url))
                else:
                    logger.debug(f"Jav321: Trailer source tag not found for {code}.")
            except Exception as e_trailer:
                logger.exception(f"Jav321: Error processing trailer for {code}: {e_trailer}")

        # Shiroutoname 보정
        final_entity = entity 
        if entity.originaltitle: 
            try:
                final_entity = SiteUtil.shiroutoname_info(entity) 
            except Exception as e_shirouto: 
                logger.exception(f"Jav321: Exception during Shiroutoname correction call for {entity.originaltitle}: {e_shirouto}")
        else:
            logger.warning(f"Jav321: Skipping Shiroutoname correction because originaltitle is missing for {code}.")

        logger.info(f"Jav321: __info processing finished for {code}. UI Code: {ui_code_for_image}, PosterSkip: {skip_default_poster_logic}, LandscapeSkip: {skip_default_landscape_logic}, EntityThumbs: {len(entity.thumb)}, EntityFanarts: {len(entity.fanart)}")
        return final_entity


    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            if entity:
                ret["ret"] = "success"; ret["data"] = entity.as_dict()
            else:
                ret["ret"] = "error"; ret["data"] = f"Failed to get Jav321 info entity for {code}"
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        return ret
