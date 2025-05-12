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
    module_char = "D"
    site_char = "T"
    _ps_url_cache = {} 

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        if keyword[-3:-1] == "cd":
            keyword = keyword[:-3]
        keyword = keyword.lower().replace(" ", "-")

        url = f"{cls.site_base_url}/search"
        headers = SiteUtil.default_headers.copy()
        headers['Referer'] = cls.site_base_url + "/"
        res = SiteUtil.get_response(url, proxy_url=proxy_url, headers=headers, post_data={"sn": keyword.lower()})

        if not res.history or not res.url.startswith(cls.site_base_url + "/video/"): # 리다이렉션이 없거나, 비디오 상세 페이지가 아니면 결과 없음
            logger.debug(f"Jav321: 검색 결과 없음 또는 직접 매칭 안 됨. Keyword: {keyword}, Final URL: {res.url}")
            return []

        ret = []
        try:
            item = EntityAVSearch(cls.site_name)
            # URL에서 코드 추출
            item.code = cls.module_char + cls.site_char + res.url.split("/")[-1].upper() # 대문자 통일
            item.score = 100 # 직접 매칭된 경우이므로 100점
            item.ui_code = keyword.upper() # 검색어를 UI 코드로 사용

            base_xpath = "/html/body/div[2]/div[1]/div[1]"
            tree = html.fromstring(res.text)

            # 이미지 URL
            img_tags = tree.xpath(f"{base_xpath}/div[2]/div[1]/div[1]/img/@src")
            original_ps_url = img_tags[0] if img_tags else ""
            if not original_ps_url: 
                logger.warning(f"Jav321 search: 이미지 URL 없음. Code: {item.code}")

            # 발매일
            date_tags = tree.xpath(f'{base_xpath}/div[2]/div[1]/div[2]/b[contains(text(),"配信開始日")]/following-sibling::text()')
            date_str = date_tags[0].lstrip(":").strip() if date_tags and date_tags[0].lstrip(":").strip() else "1900-01-01" # 기본값
            item.desc = f"발매일: {date_str}"
            try: item.year = int(date_str[:4])
            except ValueError: item.year = 1900

            # 제목
            title_tags = tree.xpath(f"{base_xpath}/div[1]/h3/text()")
            item.title = item.title_ko = title_tags[0].strip() if title_tags else "제목 없음"
            if item.title == "제목 없음": logger.warning(f"Jav321 search: 제목 없음. Code: {item.code}")


            if manual:
                _image_mode = "1" if image_mode != "0" else image_mode
                if original_ps_url: 
                    item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                else: 
                    item.image_url = ""
                if do_trans: 
                    item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
            else:
                item.image_url = original_ps_url
                item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)

            # 점수 조정
            if keyword.lower() != item.ui_code.lower(): item.score = 60
            if original_ps_url and len(original_ps_url.split("//")) > 2:
                item.score = 60 
            
            # 필수 정보 체크 후 추가
            if item.code and item.title != "제목 없음":
                if item.code and original_ps_url:
                    cls._ps_url_cache[item.code] = original_ps_url
                    logger.debug(f"Jav321 Search: Stored ps_url for {item.code} in cache: {original_ps_url}")
                ret.append(item.as_dict())
            else:
                logger.warning(f"Jav321 search: 필수 정보 부족으로 아이템 제외. Code: {item.code}")

        except Exception as e_item:
            logger.exception(f"Jav321: 개별 검색 결과 처리 중 예외 (Keyword: {keyword}): {e_item}")
        return ret

    @classmethod
    def search(cls, keyword, **kwargs):
        # 원본 search 로직 유지 + kwargs 필터링 (필수)
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
    def _get_jav321_url_from_onerror(onerror_attr):
        """onerror 속성값에서 Jav321 URL을 추출합니다."""
        if not onerror_attr or "this.src='" not in onerror_attr:
            return None
        try:
            # this.src='...' 패턴에서 URL 부분만 추출
            url_match = re.search(r"this\.src='([^']+)'", onerror_attr)
            if url_match:
                url = url_match.group(1)
                if "jav321.com" in url:
                    return url.strip()
        except Exception as e:
            logger.warning(f"Jav321: Error parsing onerror attribute '{onerror_attr}': {e}")
        return None

    @classmethod
    def __img_urls(cls, tree):
        """Jav321 페이지에서 PS, PL, Arts 이미지 URL들을 추출합니다."""
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        
        try:
            # 1. PS 이미지 추출 (onerror 우선)
            ps_xpath = '/html/body/div[2]/div[1]/div[1]/div[2]/div[1]/div[1]/img' # img 태그 자체 선택
            ps_img_tags = tree.xpath(ps_xpath)
            if ps_img_tags:
                img_tag = ps_img_tags[0]
                onerror_url = cls._get_jav321_url_from_onerror(img_tag.attrib.get('onerror'))
                if onerror_url:
                    img_urls['ps'] = onerror_url
                    logger.debug(f"Jav321: Found ps via onerror: {img_urls['ps']}")
                else: # onerror 없거나 유효하지 않으면 src 사용
                    src_url = img_tag.attrib.get('src')
                    if src_url:
                        img_urls['ps'] = src_url.strip()
                        logger.debug(f"Jav321: Found ps via src (onerror failed): {img_urls['ps']}")
                    else:
                        logger.warning(f"Jav321: PS 이미지 URL을 onerror와 src 모두에서 찾지 못했습니다.")
            else:
                logger.warning(f"Jav321: PS 이미지 태그를 찾지 못했습니다. XPath: {ps_xpath}")

            # 2. PL 이미지 추출 (오른쪽 첫번째 이미지, onerror 우선)
            pl_xpath = '/html/body/div[2]/div[2]/div[1]/p/a/img' # img 태그 자체 선택
            pl_img_tags = tree.xpath(pl_xpath)
            if pl_img_tags:
                img_tag = pl_img_tags[0]
                onerror_url = cls._get_jav321_url_from_onerror(img_tag.attrib.get('onerror'))
                if onerror_url:
                    img_urls['pl'] = onerror_url
                    logger.debug(f"Jav321: Found pl via onerror: {img_urls['pl']}")
                else: # onerror 없거나 유효하지 않으면 src 사용
                    src_url = img_tag.attrib.get('src')
                    if src_url:
                        img_urls['pl'] = src_url.strip()
                        logger.debug(f"Jav321: Found pl via src (onerror failed): {img_urls['pl']}")
                    else:
                        logger.warning(f"Jav321: PL 이미지 URL(사이드바 첫번째)을 onerror와 src 모두에서 찾지 못했습니다.")
            else:
                logger.warning(f"Jav321: PL 이미지 태그(사이드바 첫번째)를 찾지 못했습니다. XPath: {pl_xpath}")

            # 3. Arts 이미지 추출 (오른쪽 두 번째 이후, onerror 우선)
            arts_xpath = '/html/body/div[2]/div[2]/div[position()>1]//a[contains(@href, "/snapshot/")]/img' # img 태그 자체 선택
            arts_img_tags = tree.xpath(arts_xpath)
            
            processed_arts = []
            if arts_img_tags:
                logger.debug(f"Jav321: Found {len(arts_img_tags)} potential art image tags in sidebar.")
                
                for img_tag in arts_img_tags:
                    current_art_url = None
                    onerror_url = cls._get_jav321_url_from_onerror(img_tag.attrib.get('onerror'))
                    if onerror_url:
                        current_art_url = onerror_url
                    else: # onerror 없으면 src 사용
                        src_url = img_tag.attrib.get('src')
                        if src_url: current_art_url = src_url.strip()
                    
                    if current_art_url:
                        # PL과 중복 체크 및 리스트 추가 (중복 방지)
                        if current_art_url != img_urls.get('pl') and current_art_url not in processed_arts:
                            processed_arts.append(current_art_url)
                
                img_urls['arts'] = processed_arts
                logger.debug(f"Jav321: Extracted {len(img_urls['arts'])} unique arts (onerror preferred).")
            else:
                logger.warning(f"Jav321: Arts 이미지 태그(사이드바 두번째 이후)를 찾지 못했습니다. XPath: {arts_xpath}")

        except Exception as e_img:
            logger.exception(f"Jav321: Error extracting image URLs: {e_img}")
            img_urls = {'ps': "", 'pl': "", 'arts': []} 

        logger.debug(f"Jav321 Final Extracted URLs: PS='{img_urls.get('ps')}', PL='{img_urls.get('pl')}', Arts Count={len(img_urls.get('arts',[]))}")
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
                
                for b_tag_key_node in all_b_tags: # 원본 루프 변수명 사용 가정
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
                        # 시리즈는 링크일 수도, 텍스트일 수도 있음 (Jav321 원본 코드 참고 필요)
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

        if use_image_server and image_server_local_path and image_server_url and ui_code_for_image: # ui_code_for_image가 확정된 상태여야 함
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
        
        # === 4. 기본 이미지 처리 (사용자 지정 이미지가 해당 타입을 대체하지 않은 경우) ===
        final_poster_source = None; final_poster_crop_mode = None
        final_landscape_url_source = None; 
        arts_urls_for_processing = [] 
        
        ps_url_from_search_cache = cls._ps_url_cache.get(code)
        is_placeholder_poster_default = False 
        ps_url_detail_page_default = None 
        pl_url_detail_page_default = None

        if not skip_default_poster_logic or not skip_default_landscape_logic:
            logger.debug(f"Jav321: User custom images not fully provided ... Running default image logic.")
            try:
                # HTML에서 기본 이미지 URL들 가져오기
                img_urls_result_default = cls.__img_urls(tree) 
                ps_url_detail_page_default = img_urls_result_default.get('ps') 
                pl_url_detail_page_default = img_urls_result_default.get('pl') 
                arts_urls_page_default = img_urls_result_default.get('arts', []) 

                # --- A. 플레이스홀더 확인 (기본 포스터 로직이 필요할 때만) ---
                if not skip_default_poster_logic and ps_url_detail_page_default and image_server_local_path:
                    now_printing_path = os.path.join(image_server_local_path, "now_printing.jpg")
                    if os.path.exists(now_printing_path):
                        if SiteUtil.are_images_visually_same(ps_url_detail_page_default, now_printing_path, proxy_url=proxy_url):
                            is_placeholder_poster_default = True # is_placeholder_poster_default 값 설정
                            logger.warning(f"Jav321 (Default Logic): Detailed page PS for {code} is a placeholder!")
                
                # --- B. 기본 포스터 결정 (사용자 지정 포스터가 없을 때만) ---
                if not skip_default_poster_logic:
                    if is_placeholder_poster_default: # 상세 PS가 플레이스홀더인 경우
                        if ps_url_from_search_cache:
                            final_poster_source = ps_url_from_search_cache
                        else:
                            final_poster_source = ps_url_detail_page_default # 플레이스홀더라 해도 일단 사용
                        final_poster_crop_mode = None
                        logger.info(f"Jav321 (Default Logic): Placeholder PS. Using '{final_poster_source}' as poster.")
                    else: # 상세 PS가 플레이스홀더가 아닌 경우 (또는 검사 불가)
                        resolved_poster_url_step1 = None
                        resolved_crop_mode_step1 = None
                        specific_art_candidate = arts_urls_page_default[0] if arts_urls_page_default else None

                        # 1단계: 기본 후보 결정 (ps_to_poster, crop_mode, has_hq_poster 등 사용)
                        # 이 로직은 specific_art_candidate를 아직 직접적으로 사용하지 않음.
                        if ps_to_poster_setting and ps_url_detail_page_default: 
                            resolved_poster_url_step1 = ps_url_detail_page_default
                        elif crop_mode_setting and pl_url_detail_page_default: 
                            resolved_poster_url_step1 = pl_url_detail_page_default
                            resolved_crop_mode_step1 = crop_mode_setting
                        elif pl_url_detail_page_default and ps_url_detail_page_default:
                            loc = SiteUtil.has_hq_poster(ps_url_detail_page_default, pl_url_detail_page_default, proxy_url=proxy_url)
                            if loc: 
                                resolved_poster_url_step1 = pl_url_detail_page_default
                                resolved_crop_mode_step1 = loc
                            elif SiteUtil.is_hq_poster(ps_url_detail_page_default, pl_url_detail_page_default, proxy_url=proxy_url): 
                                resolved_poster_url_step1 = pl_url_detail_page_default
                            else: 
                                resolved_poster_url_step1 = ps_url_detail_page_default
                        elif ps_url_detail_page_default: 
                            resolved_poster_url_step1 = ps_url_detail_page_default
                        elif pl_url_detail_page_default: 
                            resolved_poster_url_step1 = pl_url_detail_page_default
                            resolved_crop_mode_step1 = crop_mode_setting # crop_mode_setting 사용
                        
                        # resolved_poster_url_step1이 결정 안됐으면 ps_url_detail_page_default를 기본으로
                        if not resolved_poster_url_step1 and ps_url_detail_page_default:
                            resolved_poster_url_step1 = ps_url_detail_page_default
                        
                        logger.debug(f"Jav321 (Default Logic) Step 1: Base Poster='{resolved_poster_url_step1}', Base Crop='{resolved_crop_mode_step1}'")

                        # 1.5단계: Specific Art 후보 고려 (DMM 스타일)
                        #    - ps_url_from_search_cache (검색 결과 썸네일) 또는 ps_url_detail_page_default (상세 페이지 썸네일)와
                        #      specific_art_candidate (Arts 목록의 첫 번째 이미지)를 비교.
                        #    - is_hq_poster 통과 시, specific_art_candidate를 포스터로 사용.
                        #      이 경우, resolved_poster_url_step1과 resolved_crop_mode_step1을 덮어씀.
                        
                        comparison_ps_for_specific_art = ps_url_from_search_cache if ps_url_from_search_cache else ps_url_detail_page_default
                        if specific_art_candidate and comparison_ps_for_specific_art:
                            logger.debug(f"Jav321 (Default Logic) Step 1.5: Checking specific_art_candidate '{specific_art_candidate}' against '{comparison_ps_for_specific_art}'")
                            if SiteUtil.is_hq_poster(comparison_ps_for_specific_art, specific_art_candidate, proxy_url=proxy_url):
                                logger.info(f"Jav321 (Default Logic) Step 1.5: specific_art_candidate IS HQ. Using it as poster.")
                                resolved_poster_url_step1 = specific_art_candidate
                                resolved_crop_mode_step1 = None # specific_art_candidate는 보통 세로형 포스터로 간주, 크롭 불필요
                            else:
                                logger.debug(f"Jav321 (Default Logic) Step 1.5: specific_art_candidate IS NOT HQ.")
                        else:
                            logger.debug(f"Jav321 (Default Logic) Step 1.5: No specific_art_candidate or comparison_ps to check.")


                        # 2단계: MGS 스타일 특별 처리 (get_mgs_half_pl_poster_info_local 사용)
                        #    - 이 단계는 resolved_poster_url_step1 (1 또는 1.5단계 결과)과 pl_url_detail_page_default를 사용.
                        jav321_special_poster_filepath = None
                        attempt_special_local = False
                        # 조건: PL이 있고, (1 또는 1.5단계에서 결정된) 포스터 소스가 PL이며, ps_to_poster 설정이 아니고, 상세페이지 PS도 있어야 함
                        if pl_url_detail_page_default and \
                            resolved_poster_url_step1 == pl_url_detail_page_default and \
                            not ps_to_poster_setting and \
                            ps_url_detail_page_default: # 상세페이지 PS가 있어야 MGS 스타일 비교 가능
                            attempt_special_local = True
                        
                        if attempt_special_local:
                            logger.info(f"Jav321 (Default Logic) Step 2: Attempting MGS-style special poster processing for {code}")
                            # MGS 함수는 (임시파일경로, 크롭모드(None), 원본PL) 반환
                            temp_filepath, _, _ = SiteUtil.get_mgs_half_pl_poster_info_local(ps_url_detail_page_default, pl_url_detail_page_default, proxy_url=proxy_url)
                            if temp_filepath and os.path.exists(temp_filepath): 
                                jav321_special_poster_filepath = temp_filepath
                                logger.info(f"Jav321 (Default Logic) Step 2: MGS-style special poster successful: {jav321_special_poster_filepath}")
                            else:
                                logger.info(f"Jav321 (Default Logic) Step 2: MGS-style special poster failed for {code}.")
                        
                        # 3단계: 최종 포스터 결정
                        if jav321_special_poster_filepath: # 2단계 성공 시
                            final_poster_source = jav321_special_poster_filepath
                            final_poster_crop_mode = None # MGS 스타일은 이미 크롭 완료된 포스터
                        else: # 2단계 실패 또는 해당 안 됨 -> 1/1.5단계 결과 사용
                            final_poster_source = resolved_poster_url_step1
                            final_poster_crop_mode = resolved_crop_mode_step1
                        
                        logger.debug(f"Jav321 (Default Logic) Final decided poster: Source='{final_poster_source}', Crop='{final_poster_crop_mode}'")

                # --- C. 기본 랜드스케이프 소스 결정 (사용자 지정 랜드스케이프가 없을 때만) ---
                if not skip_default_landscape_logic:
                    if is_placeholder_poster_default: # 상세 PS가 플레이스홀더면 기본 랜드스케이프 없음
                        final_landscape_url_source = None
                    else:
                        final_landscape_url_source = pl_url_detail_page_default # 상세 페이지 PL을 기본 랜드스케이프로
                    logger.debug(f"Jav321 (Default Logic): Default landscape source: {final_landscape_url_source}")
                
                # --- D. 기본 아트 URL 리스트 (상세 PS가 플레이스홀더가 아닐 때만) ---
                if is_placeholder_poster_default: 
                    arts_urls_for_processing = []
                else:
                    arts_urls_for_processing = arts_urls_page_default
                logger.debug(f"Jav321 (Default Logic): Arts for processing count: {len(arts_urls_for_processing)}")

                # --- E. 일반 모드 이미지 처리 (use_image_server=False or image_mode != '4') ---
                if not (use_image_server and image_mode == '4'):
                    logger.info(f"Jav321: Using Normal Image Processing Mode for default images...")
                    # 포스터 썸네일 추가 (사용자 지정이 아니고, 소스가 있을 때)
                    if final_poster_source and not skip_default_poster_logic:
                        processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                        if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                    
                    # 랜드스케이프 썸네일 추가 (사용자 지정이 아니고, 소스가 있을 때)
                    if final_landscape_url_source and not skip_default_landscape_logic:
                        processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url_source, proxy_url=proxy_url)
                        if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))
                    
                    # 팬아트 추가
                    if arts_urls_for_processing:
                        processed_fanart_count = 0
                        sources_to_exclude = {final_poster_source, final_landscape_url_source}
                        # MGS 특별 처리로 포스터가 로컬 파일이면, 그 원본 PL도 제외 대상
                        if pl_url_detail_page_default and 'jav321_special_poster_filepath' in locals() and jav321_special_poster_filepath and final_poster_source == jav321_special_poster_filepath:
                            sources_to_exclude.add(pl_url_detail_page_default)

                        for art_url in arts_urls_for_processing:
                            if processed_fanart_count >= max_arts: break
                            if art_url and art_url not in sources_to_exclude:
                                processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                                if processed_art: entity.fanart.append(processed_art); processed_fanart_count += 1
                        logger.debug(f"Jav321 (Default Logic) Normal Mode: Added {processed_fanart_count} fanarts.")
            
            except Exception as e_img_proc_default:
                logger.exception(f"Jav321: Error during default image processing logic: {e_img_proc_default}")

        # === 5. 이미지 서버 저장 로직 ===
        if use_image_server and image_mode == '4' and ui_code_for_image:
            logger.info(f"Jav321: Saving images to Image Server for {ui_code_for_image} (if any)...")
            
            # --- PS 이미지 저장 ---
            ps_to_save_on_server = None
            if not is_placeholder_poster_default and ps_url_detail_page_default:
                ps_to_save_on_server = ps_url_detail_page_default
            elif is_placeholder_poster_default and ps_url_from_search_cache:
                ps_to_save_on_server = ps_url_from_search_cache
            elif not ps_url_detail_page_default and ps_url_from_search_cache:
                ps_to_save_on_server = ps_url_from_search_cache
            elif ps_url_detail_page_default:
                ps_to_save_on_server = ps_url_detail_page_default
            
            logger.debug(f"Jav321 ImgServ: Determined ps_to_save_on_server='{ps_to_save_on_server}'")
            if ps_to_save_on_server:
                logger.debug(f"Jav321 ImgServ: Attempting to save PS from: {ps_to_save_on_server}")
                ps_server_relative_path = SiteUtil.save_image_to_server_path(
                    ps_to_save_on_server, 'ps', 
                    image_server_local_path, image_path_segment, ui_code_for_image, 
                    proxy_url=proxy_url
                )
                logger.debug(f"Jav321 ImgServ: Saved PS result path: {ps_server_relative_path}")
            
            # --- 포스터 저장 (사용자 지정 포스터가 아니고, 기본 로직으로 소스가 결정되었을 때) ---
            if not skip_default_poster_logic and final_poster_source:
                logger.debug(f"Jav321 ImgServ: Attempting to save Poster from: {final_poster_source}, Crop: {final_poster_crop_mode}")
                p_relative_path = SiteUtil.save_image_to_server_path(
                    final_poster_source, 'p', 
                    image_server_local_path, image_path_segment, ui_code_for_image, 
                    proxy_url=proxy_url, crop_mode=final_poster_crop_mode
                )
                logger.debug(f"Jav321 ImgServ: Saved Poster result path: {p_relative_path}")
                # entity.thumb에 추가하는 로직: 사용자 지정이 아닐 때만 (이미 3단계에서 추가됨)
                if p_relative_path and not user_custom_poster_url: 
                    # 중복 추가 방지 (이미 일반 모드에서 추가되었을 수 있음)
                    if not any(t.aspect == 'poster' and t.value.endswith(p_relative_path) for t in entity.thumb):
                        entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_relative_path}"))
            
            # --- 랜드스케이프 저장 (사용자 지정 랜드스케이프가 아니고, 기본 로직으로 소스가 결정되었을 때) ---
            if not skip_default_landscape_logic and final_landscape_url_source:
                logger.debug(f"Jav321 ImgServ: Attempting to save Landscape from: {final_landscape_url_source}")
                pl_relative_path = SiteUtil.save_image_to_server_path(
                    final_landscape_url_source, 'pl', 
                    image_server_local_path, image_path_segment, ui_code_for_image, 
                    proxy_url=proxy_url
                )
                logger.debug(f"Jav321 ImgServ: Saved Landscape result path: {pl_relative_path}")
                if pl_relative_path and not user_custom_landscape_url:
                    if not any(t.aspect == 'landscape' and t.value.endswith(pl_relative_path) for t in entity.thumb):
                        entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_relative_path}"))

            # --- 팬아트 저장 (항상 기본 로직, arts_urls_for_processing 사용) ---
            if arts_urls_for_processing:
                logger.debug(f"Jav321 ImgServ: Attempting to save {len(arts_urls_for_processing)} arts.")
                processed_fanart_count_server = 0
                sources_to_exclude_server = {final_poster_source, final_landscape_url_source}
                if pl_url_detail_page_default and 'jav321_special_poster_filepath' in locals() and jav321_special_poster_filepath and final_poster_source == jav321_special_poster_filepath:
                    sources_to_exclude_server.add(pl_url_detail_page_default)

                for idx, art_url in enumerate(arts_urls_for_processing):
                    art_index_to_save = idx + 1
                    if processed_fanart_count_server >= max_arts: break
                    if art_url and art_url not in sources_to_exclude_server:
                        art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=art_index_to_save, proxy_url=proxy_url)
                        if art_relative_path:
                            entity.fanart.append(f"{image_server_url}/{art_relative_path}"); processed_fanart_count_server += 1
                logger.debug(f"Jav321 ImgServ: Processed {processed_fanart_count_server} fanarts to server.")

        # === 6. 예고편 처리, Shiroutoname 보정 등 ===
        # 예고편 처리 (Jav321)
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
        # 원본 info wrapper 로직 유지 + kwargs 전달 (필수)
        ret = {}
        try:
            entity = cls.__info(code, **kwargs) # kwargs 전달
            if entity: # entity None 체크 추가
                ret["ret"] = "success"; ret["data"] = entity.as_dict()
            else:
                ret["ret"] = "error"; ret["data"] = f"Failed to get Jav321 info entity for {code}"
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        return ret
