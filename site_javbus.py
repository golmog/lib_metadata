from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityMovie, EntityThumb, EntityExtra
from .plugin import P
from .site_util import SiteUtil

import re
import os

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
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        keyword = keyword.strip().lower()
        # 2020-06-24
        if keyword[-3:-1] == "cd":
            keyword = keyword[:-3]
        keyword = keyword.replace(" ", "-")

        url = f"{cls.site_base_url}/search/{keyword}"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, verify=False)

        ret = []
        for node in tree.xpath('//a[@class="movie-box"]')[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                item.image_url = cls.__fix_url(node.xpath(".//img/@src")[0])

                tag = node.xpath(".//date")
                ui_code = tag[0].text_content().strip()
                try:
                    label, num = ui_code.split("-")  # 4자리 숫자 품번 대응
                    item.ui_code = f"{label}-{num.lstrip('0').zfill(3)}"
                except Exception:
                    item.ui_code = ui_code
                item.code = cls.module_char + cls.site_char + node.attrib["href"].split("/")[-1]
                item.desc = "발매일: " + tag[1].text_content().strip()
                item.year = int(tag[1].text_content().strip()[:4])
                item.title = item.title_ko = node.xpath(".//span/text()")[0].strip()
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    item.image_url = SiteUtil.process_image_mode(_image_mode, item.image_url, proxy_url=proxy_url)
                    if do_trans:
                        item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                else:
                    item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)

                item.score = 100 if keyword.lower() == item.ui_code.lower() else 60 - (len(ret) * 10)
                if item.score < 0:
                    item.score = 0
                # logger.debug(item)
                ret.append(item.as_dict())
            except Exception:
                logger.exception("개별 검색 결과 처리 중 예외:")
        return sorted(ret, key=lambda k: k["score"], reverse=True)

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            # __search 에 필요한 인자만 추출
            do_trans_arg = kwargs.get('do_trans', True)
            proxy_url_arg = kwargs.get('proxy_url', None)
            image_mode_arg = kwargs.get('image_mode', "0")
            manual_arg = kwargs.get('manual', False)
            # 추출된 인자로 __search 호출
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

    @classmethod
    def __img_urls(cls, tree):
        """collect raw image urls from html page"""

        # poster large
        # 보통 가로 이미지
        pl = tree.xpath('//a[@class="bigImage"]/img/@src')
        pl = pl[0] if pl else ""
        if pl:
            pl = cls.__fix_url(pl)
        else:
            logger.warning("이미지 URL을 얻을 수 없음: poster large")

        # poster small
        # 세로 이미지 / 저화질 썸네일
        ps = ""
        if pl:
            try: #
                filename = pl.split("/")[-1].replace("_b.", ".")
                ps = cls.__fix_url(f"/pics/thumb/{filename}")
            except Exception as e_ps: logger.warning(f"JavBus: ps URL 유추 실패: {e_ps}")

        arts = []
        try:
            for href in tree.xpath('//*[@id="sample-waterfall"]/a/@href'):
                arts.append(cls.__fix_url(href))
        except Exception as e_arts: logger.warning(f"JavBus: arts URL 추출 실패: {e_arts}")

        return {"ps": ps, "pl": pl, "arts": arts}

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
        crop_mode_setting = crop_mode # JavBus는 crop_mode를 잘 사용하지 않음
        
        logger.debug(f"Image Server Mode Check ({cls.site_name}): image_mode={image_mode}, use_image_server={use_image_server}")

        url = f"{cls.site_base_url}/{code[2:]}"
        headers = SiteUtil.default_headers.copy(); headers['Referer'] = cls.site_base_url + "/"
        
        tree = None
        try:
            tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=headers, verify=False) 
            if tree is None or not tree.xpath('/html/body/div[contains(@class, "container-fluid")]'): 
                logger.error(f"JavBus: Failed to get valid detail page tree for {code}. URL: {url}")
                return None
        except Exception as e_get_tree:
            logger.exception(f"JavBus: Exception while getting detail page for {code}: {e_get_tree}")
            return None

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = []
        entity.fanart = []
        entity.extras = [] 
        ui_code_for_image = "" 
        
        # === 2. 전체 메타데이터 파싱 (ui_code_for_image 확정 포함) ===
        identifier_parsed = False
        try:
            logger.debug(f"JavBus: Parsing metadata for {code}...")
            info_container_xpath = "/html/body/div[contains(@class, 'container-fluid')]"
            tags_p_info = tree.xpath(f"{info_container_xpath}//div[@class='info']/p")
            if not tags_p_info: logger.warning(f"JavBus: Metadata <p> tags in div.info not found for {code}.")

            for p_tag in tags_p_info:
                try:
                    header_span = p_tag.xpath("./span[@class='header']")
                    key_text = ""
                    value_text = ""
                    if header_span:
                        key_text = header_span[0].text_content().replace(":", "").strip()
                        value_text_nodes = header_span[0].xpath("./following-sibling::node()")
                        value_text = "".join([n.text_content().strip() if hasattr(n, 'text_content') else str(n).strip() for n in value_text_nodes]).strip()
                        if not value_text and len(p_tag.xpath("./span")) > 1:
                            value_text = " ".join(p_tag.xpath("./span[position()>1]//text()")).strip()
                    else:
                        value_text = p_tag.text_content().strip()
                    
                    if not value_text or value_text == "----": continue

                    if key_text == "識別碼":
                        formatted_code = value_text.upper()
                        try: 
                            label, num_str = formatted_code.split('-', 1)
                            num_part = ''.join(filter(str.isdigit, num_str)) 
                            if num_part: 
                                ui_code_for_image = f"{label.upper()}-{int(num_part):03d}"
                            else: 
                                ui_code_for_image = formatted_code
                        except ValueError: 
                            ui_code_for_image = formatted_code
                        
                        entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image
                        entity.ui_code = ui_code_for_image
                        if entity.tag is None: entity.tag = []
                        try: 
                            if '-' in ui_code_for_image: entity.tag.append(ui_code_for_image.split('-',1)[0].upper())
                        except: pass
                        identifier_parsed = True
                        logger.info(f"JavBus: Identifier (ui_code_for_image) parsed as: {ui_code_for_image}")
                    elif key_text == "發行日期":
                        if value_text != "0000-00-00": 
                            entity.premiered = value_text; entity.year = int(value_text[:4])
                        else: 
                            entity.premiered = "1900-01-01"; entity.year = 1900
                    elif key_text == "長度":
                        try: entity.runtime = int(value_text.replace("分鐘", "").strip())
                        except Exception: logger.warning(f"JavBus: Failed to parse runtime '{value_text}'")
                    elif key_text == "導演": 
                        director_a_tag = p_tag.xpath("./a")
                        entity.director = director_a_tag[0].text_content().strip() if director_a_tag else value_text
                    elif key_text == "製作商":
                        maker_a_tag = p_tag.xpath("./a")
                        studio_name = maker_a_tag[0].text_content().strip() if maker_a_tag else value_text
                        entity.studio = SiteUtil.trans(studio_name, do_trans=do_trans) if do_trans else studio_name
                    elif key_text == "發行商": 
                        label_a_tag = p_tag.xpath("./a")
                        label_name = label_a_tag[0].text_content().strip() if label_a_tag else value_text
                        if not entity.studio: 
                            entity.studio = SiteUtil.trans(label_name, do_trans=do_trans) if do_trans else label_name
                        if entity.tag is None: entity.tag = []
                        label_tag_candidate = ui_code_for_image.split('-',1)[0].upper() if ui_code_for_image and '-' in ui_code_for_image else None
                        if label_tag_candidate and label_tag_candidate not in entity.tag:
                            entity.tag.append(label_tag_candidate)
                    elif key_text == "系列":
                        series_a_tag = p_tag.xpath("./a")
                        series_name = series_a_tag[0].text_content().strip() if series_a_tag else value_text
                        if entity.tag is None: entity.tag = []
                        trans_series = SiteUtil.trans(series_name, do_trans=do_trans)
                        if trans_series and trans_series not in entity.tag: entity.tag.append(trans_series)
                    elif key_text == "類別":
                        entity.genre = []
                        genre_tags_a = p_tag.xpath("./span[@class='genre' and not(contains(@onmouseover, 'GLO'))]/a")
                        for a_tag_genre in genre_tags_a:
                            genre_ja = a_tag_genre.text_content().strip()
                            if not genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                    elif key_text == "演員" or (not key_text and p_tag.xpath("./span/a[contains(@href, '/star/')]")):
                        if entity.actor is None : entity.actor = []
                        actor_tags_a = p_tag.xpath("./span/a[contains(@href, '/star/')]") if not key_text else p_tag.xpath("./a[contains(@href, '/star/')]")
                        current_actors = []
                        for a_tag_actor in actor_tags_a:
                            actor_name = a_tag_actor.text_content().strip()
                            if actor_name and actor_name != "暫無出演者資訊" and actor_name not in [act.name for act in entity.actor]:
                                current_actors.append(EntityActor(actor_name))
                        if current_actors: entity.actor.extend(current_actors)
                except Exception as e_p_tag:
                    logger.warning(f"JavBus: Error parsing p_tag content for {code}: {e_p_tag}")

            if not identifier_parsed:
                logger.error(f"JavBus: CRITICAL - Failed to parse identifier (識別碼) for {code}.")
                ui_code_for_image = code[2:].upper().replace("_", "-") 
                entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image
                entity.ui_code = ui_code_for_image
                logger.warning(f"JavBus: Using fallback identifier: {ui_code_for_image}")
            
            try:
                h3_text_nodes = tree.xpath(f"{info_container_xpath}/h3/text()")
                if h3_text_nodes:
                    full_h3_text = "".join(h3_text_nodes).strip()
                    tagline_candidate = full_h3_text
                    if ui_code_for_image and full_h3_text.upper().startswith(ui_code_for_image):
                        tagline_candidate = full_h3_text[len(ui_code_for_image):].strip()
                    entity.tagline = SiteUtil.trans(tagline_candidate, do_trans=do_trans)
                    entity.plot = entity.tagline 
                else:
                    logger.warning(f"JavBus: H3 title tag (tagline/plot source) not found for {code}.")
                    if entity.title: entity.tagline = entity.plot = entity.title
            except Exception as e_h3_parse:
                logger.exception(f"JavBus: Error parsing H3 title for {code}: {e_h3_parse}")
        except Exception as e_meta_main:
            logger.exception(f"JavBus: Major error during metadata parsing for {code}: {e_meta_main}")
            if not ui_code_for_image: 
                logger.error(f"JavBus: Returning None due to critical metadata parsing failure (no identifier).")
                return None
        
        # === 3. 사용자 지정 포스터 확인 및 처리 ===
        user_custom_poster_url = None
        user_custom_landscape_url = None
        skip_default_poster_logic = False
        skip_default_landscape_logic = False

        if use_image_server and image_server_local_path and image_server_url and ui_code_for_image:
            poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
            landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]
            for suffix in poster_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                if web_url: 
                    user_custom_poster_url = web_url
                    entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url))
                    skip_default_poster_logic = True
                    logger.info(f"JavBus: Using user custom poster for {ui_code_for_image}: {web_url}")
                    break 
            for suffix in landscape_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                if web_url: 
                    user_custom_landscape_url = web_url
                    entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url))
                    skip_default_landscape_logic = True
                    logger.info(f"JavBus: Using user custom landscape for {ui_code_for_image}: {web_url}")
                    break
        
        # === 4. 기본 이미지 처리 (사용자 지정 이미지가 해당 타입을 대체하지 않은 경우) ===
        final_poster_source = None; final_poster_crop_mode = None
        final_landscape_url_source = None; 
        arts_urls_for_processing = [] 
        
        ps_url_detail_page_default = None 
        # pl_url_detail_page_default = None # JavBus의 경우 resolve_jav_imgs가 PL을 결정하므로 여기서 미리 가져올 필요는 적음

        if not skip_default_poster_logic or not skip_default_landscape_logic:
            logger.debug(f"JavBus: User custom images not fully provided ... Running default image logic.")
            try:
                img_urls_result_default = cls.__img_urls(tree) 
                ps_url_detail_page_default = img_urls_result_default.get('ps') # PS는 이후 PS 저장에 사용될 수 있음
                # JavBus는 플레이스홀더 로직 없음

                # --- B. 기본 포스터/랜드스케이프 결정 (SiteUtil.resolve_jav_imgs 사용) ---
                # resolve_jav_imgs 는 ps_to_poster_setting, crop_mode_setting을 내부적으로 고려함
                # 이 함수는 전달된 딕셔너리를 직접 수정하므로 복사본 전달
                temp_img_urls_for_resolve = img_urls_result_default.copy()
                
                if not skip_default_poster_logic or not skip_default_landscape_logic: # 둘 중 하나라도 기본 로직 필요시
                    SiteUtil.resolve_jav_imgs(temp_img_urls_for_resolve, 
                                              ps_to_poster=ps_to_poster_setting, 
                                              proxy_url=proxy_url, 
                                              crop_mode=crop_mode_setting)
                
                if not skip_default_poster_logic:
                    final_poster_source = temp_img_urls_for_resolve.get('poster')
                    final_poster_crop_mode = temp_img_urls_for_resolve.get('poster_crop')
                    logger.debug(f"JavBus (Default Logic): Poster='{final_poster_source}', Crop='{final_poster_crop_mode}'")
                
                if not skip_default_landscape_logic:
                    final_landscape_url_source = temp_img_urls_for_resolve.get('landscape')
                    logger.debug(f"JavBus (Default Logic): Landscape source: {final_landscape_url_source}")

                arts_urls_for_processing = temp_img_urls_for_resolve.get('arts', [])
                logger.debug(f"JavBus (Default Logic): Arts for processing count: {len(arts_urls_for_processing)}")

                # --- E. 일반 모드 이미지 처리 ---
                if not (use_image_server and image_mode == '4'):
                    logger.info(f"JavBus: Using Normal Image Processing Mode for default images...")
                    if final_poster_source and not skip_default_poster_logic:
                        processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                        if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                    
                    if final_landscape_url_source and not skip_default_landscape_logic:
                        processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url_source, proxy_url=proxy_url)
                        if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))
                    
                    if arts_urls_for_processing:
                        processed_fanart_count = 0
                        sources_to_exclude = {final_poster_source, final_landscape_url_source}
                        for art_url in arts_urls_for_processing:
                            if processed_fanart_count >= max_arts: break
                            if art_url and art_url not in sources_to_exclude:
                                processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                                if processed_art: entity.fanart.append(processed_art); processed_fanart_count += 1
                        logger.debug(f"JavBus (Default Logic) Normal Mode: Added {processed_fanart_count} fanarts.")
            
            except Exception as e_img_proc_default:
                logger.exception(f"JavBus: Error during default image processing logic: {e_img_proc_default}")

        # === 5. 이미지 서버 저장 로직 ===
        if use_image_server and image_mode == '4' and ui_code_for_image:
            logger.info(f"JavBus: Saving images to Image Server for {ui_code_for_image} (if any)...")
            
            # --- PS 이미지 저장 ---
            if ps_url_detail_page_default: 
                logger.debug(f"JavBus ImgServ: Attempting to save PS from: {ps_url_detail_page_default}")
                ps_server_relative_path = SiteUtil.save_image_to_server_path(ps_url_detail_page_default, 'ps', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                logger.debug(f"JavBus ImgServ: Saved PS result path: {ps_server_relative_path}")
            
            # --- 포스터 저장 ---
            if not skip_default_poster_logic and final_poster_source:
                logger.debug(f"JavBus ImgServ: Attempting to save Poster from: {final_poster_source}, Crop: {final_poster_crop_mode}")
                p_relative_path = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                logger.debug(f"JavBus ImgServ: Saved Poster result path: {p_relative_path}")
                if p_relative_path and not user_custom_poster_url: 
                    if not any(t.aspect == 'poster' and t.value.endswith(p_relative_path) for t in entity.thumb):
                        entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_relative_path}"))

            # --- 랜드스케이프 저장 ---
            if not skip_default_landscape_logic and final_landscape_url_source:
                logger.debug(f"JavBus ImgServ: Attempting to save Landscape from: {final_landscape_url_source}")
                pl_relative_path = SiteUtil.save_image_to_server_path(final_landscape_url_source, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                logger.debug(f"JavBus ImgServ: Saved Landscape result path: {pl_relative_path}")
                if pl_relative_path and not user_custom_landscape_url:
                    if not any(t.aspect == 'landscape' and t.value.endswith(pl_relative_path) for t in entity.thumb):
                        entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_relative_path}"))

            # --- 팬아트 저장 ---
            if arts_urls_for_processing:
                logger.debug(f"JavBus ImgServ: Attempting to save {len(arts_urls_for_processing)} arts.")
                processed_fanart_count_server = 0
                sources_to_exclude_server = {final_poster_source, final_landscape_url_source}
                for idx, art_url in enumerate(arts_urls_for_processing):
                    art_index_to_save = idx + 1
                    if processed_fanart_count_server >= max_arts: break
                    if art_url and art_url not in sources_to_exclude_server:
                        art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=art_index_to_save, proxy_url=proxy_url)
                        if art_relative_path:
                            entity.fanart.append(f"{image_server_url}/{art_relative_path}"); processed_fanart_count_server += 1
                logger.debug(f"JavBus ImgServ: Processed {processed_fanart_count_server} fanarts to server.")
        
        # === 6. 예고편 처리 (JavBus는 없음), Shiroutoname 보정 ===
        entity.extras = [] 
        
        final_entity = entity 
        if entity.originaltitle: 
            try: final_entity = SiteUtil.shiroutoname_info(entity) 
            except Exception as e_shirouto: logger.exception(f"JavBus: Shiroutoname correction error: {e_shirouto}")
        else: logger.warning(f"JavBus: Skipping Shiroutoname (no originaltitle for {code}).")

        logger.info(f"JavBus: __info processing finished for {code}. UI Code: {ui_code_for_image}, PosterSkip: {skip_default_poster_logic}, LandscapeSkip: {skip_default_landscape_logic}, Thumbs: {len(entity.thumb)}, Fanarts: {len(entity.fanart)}")
        return final_entity


    @classmethod
    def info(cls, code, **kwargs): # info 래퍼는 기존 유지
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            if entity is not None:
                ret["ret"] = "success"
                ret["data"] = entity.as_dict()
            else:
                ret["ret"] = "error"
                ret["data"] = f"Failed to get info entity for {code} (likely page load/parse error)"
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        return ret
