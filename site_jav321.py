import re

from lxml import html

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

            # 이미지 URL (방어 코드 추가)
            img_tags = tree.xpath(f"{base_xpath}/div[2]/div[1]/div[1]/img/@src")
            image_url = img_tags[0] if img_tags else ""
            if not image_url: logger.warning(f"Jav321 search: 이미지 URL 없음. Code: {item.code}")

            # 발매일 (방어 코드 추가)
            date_tags = tree.xpath(f'{base_xpath}/div[2]/div[1]/div[2]/b[contains(text(),"配信開始日")]/following-sibling::text()')
            date_str = date_tags[0].lstrip(":").strip() if date_tags and date_tags[0].lstrip(":").strip() else "1900-01-01" # 기본값
            item.desc = f"발매일: {date_str}"
            try: item.year = int(date_str[:4])
            except ValueError: item.year = 1900

            # 제목 (방어 코드 추가)
            title_tags = tree.xpath(f"{base_xpath}/div[1]/h3/text()")
            item.title = item.title_ko = title_tags[0].strip() if title_tags else "제목 없음"
            if item.title == "제목 없음": logger.warning(f"Jav321 search: 제목 없음. Code: {item.code}")


            if manual:
                _image_mode = "1" if image_mode != "0" else image_mode
                if image_url: item.image_url = SiteUtil.process_image_mode(_image_mode, image_url, proxy_url=proxy_url)
                else: item.image_url = "" # 이미지 없으면 빈 값
                if do_trans: item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
            else:
                item.image_url = image_url # 원본 URL 사용
                item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)

            # 점수 조정 (원본 로직 유지)
            if keyword.lower() != item.ui_code.lower(): item.score = 60
            if image_url and len(image_url.split("//")) > 2: item.score = 60 # 이미지 링크 깨진 경우
            
            # 필수 정보 체크 후 추가
            if item.code and item.title != "제목 없음":
                ret.append(item.as_dict())
            else:
                logger.warning(f"Jav321 search: 필수 정보 부족으로 아이템 제외. Code: {item.code}")

        except Exception as e_item: # 개별 아이템 처리 중 예외
            logger.exception(f"Jav321: 개별 검색 결과 처리 중 예외 (Keyword: {keyword}): {e_item}")
        return ret # 단일 아이템 또는 빈 리스트 반환

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

    @classmethod
    def __img_urls(cls, tree):
        """Jav321 페이지에서 PS, PL, Arts 이미지 URL들을 추출합니다."""
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        
        try:
            # 1. PS 이미지 추출
            ps_xpath = '/html/body/div[2]/div[1]/div[1]/div[2]/div[1]/div[1]/img/@src'
            ps_tags = tree.xpath(ps_xpath)
            if ps_tags:
                img_urls['ps'] = ps_tags[0].strip()
                logger.debug(f"Jav321: Found ps: {img_urls['ps']}")
            else:
                logger.warning(f"Jav321: PS 이미지 URL을 찾지 못했습니다. XPath: {ps_xpath}")

            # 2. PL 이미지 추출 (오른쪽 영역 첫 번째 이미지)
            pl_xpath = '/html/body/div[2]/div[2]/div[1]/p/a/img/@src' # 오른쪽 첫번째 이미지
            pl_tags = tree.xpath(pl_xpath)
            if pl_tags:
                img_urls['pl'] = pl_tags[0].strip()
                logger.debug(f"Jav321: Found pl (first sidebar image): {img_urls['pl']}")
            else:
                logger.warning(f"Jav321: PL 이미지 URL(사이드바 첫번째)을 찾지 못했습니다. XPath: {pl_xpath}")

            # 3. Arts 이미지 추출 (오른쪽 영역 두 번째 이미지부터)
            #    XPath 수정: position() > 1 을 사용하여 두 번째 div부터 선택
            arts_xpath = '/html/body/div[2]/div[2]/div[position()>1]//a[contains(@href, "/snapshot/")]/img/@src'
            arts_src = tree.xpath(arts_xpath)
            
            if arts_src:
                # 중복 제거 (PL과 중복될 일은 거의 없지만 안전하게) 및 순서 유지
                processed_arts = []
                pl_val = img_urls.get('pl') # PL 값 가져오기
                for img_src in arts_src:
                    current_art_url = img_src.strip()
                    if current_art_url != pl_val and current_art_url not in processed_arts: # PL과 다르고 중복 아니면 추가
                        processed_arts.append(current_art_url)
                img_urls['arts'] = processed_arts
                logger.debug(f"Jav321: Extracted {len(img_urls['arts'])} unique arts (from 2nd sidebar image onwards).")
            else:
                logger.warning(f"Jav321: Arts 이미지 URL(사이드바 두번째 이후)을 찾지 못했습니다. XPath: {arts_xpath}")

        except Exception as e_img:
            logger.exception(f"Jav321: Error extracting image URLs: {e_img}")
            img_urls = {'ps': "", 'pl': "", 'arts': []} # 오류 시 초기화

        logger.debug(f"Jav321 Final Extracted URLs: PS='{img_urls.get('ps')}', PL='{img_urls.get('pl')}', Arts Count={len(img_urls.get('arts',[]))}")
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
        ps_to_poster=False, # kwargs 우선
        crop_mode=None,     # kwargs 우선
        **kwargs          # kwargs 추가 (필수)
    ):
        # --- 설정값 추출 ---
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        ps_to_poster_setting = kwargs.get('ps_to_poster', ps_to_poster)
        crop_mode_setting = kwargs.get('crop_mode', crop_mode)
        logger.debug(f"Image Server Mode Check ({cls.module_char}): image_mode={image_mode}, use_image_server={use_image_server}")

        # --- 페이지 로딩 및 기본 Entity 초기화 ---
        url = f"{cls.site_base_url}/video/{code[2:]}"
        headers = SiteUtil.default_headers.copy(); headers['Referer'] = cls.site_base_url + "/"
        tree = None
        try:
            tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=headers)
            if tree is None or not tree.xpath('/html/body/div[2]/div[1]/div[1]'): # Jav321의 기본 컨테이너 확인
                logger.error(f"Jav321: Failed to get valid detail page tree for {code}. URL: {url}")
                return None
        except Exception as e_get_tree:
            logger.exception(f"Jav321: Exception while getting detail page for {code}: {e_get_tree}")
            return None

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = []
        entity.fanart = []
        entity.extras = []
        ui_code_for_image = "" 

        # --- 이미지 처리 로직 시작 (MGStage 방식 적용) ---
        final_poster_source = None # 최종 포스터 (URL 또는 로컬 파일 경로)
        final_poster_crop_mode = None  
        final_landscape_url = None     
        
        try:
            img_urls_result = cls.__img_urls(tree) # Jav321용 __img_urls 호출
            ps_url = img_urls_result.get('ps') # HTML에서 직접 파싱한 PS
            pl_url = img_urls_result.get('pl') # 사이드바 첫 이미지
            arts_urls = img_urls_result.get('arts', []) 

            final_landscape_url = pl_url # 기본 랜드스케이프는 pl

            # 1단계: 기본적인 포스터 후보 결정 (MGStage/DMM 등과 유사)
            resolved_poster_url_step1 = None
            resolved_crop_mode_step1 = None
            if ps_to_poster_setting and ps_url:
                resolved_poster_url_step1 = ps_url
            else:
                if crop_mode_setting and pl_url: 
                    resolved_poster_url_step1 = pl_url
                    resolved_crop_mode_step1 = crop_mode_setting
                elif pl_url and ps_url:
                    loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                    if loc: 
                        resolved_poster_url_step1 = pl_url
                        resolved_crop_mode_step1 = loc
                    else: 
                        if SiteUtil.is_hq_poster(ps_url, pl_url, proxy_url=proxy_url):
                            resolved_poster_url_step1 = pl_url
                        else: 
                            resolved_poster_url_step1 = ps_url
                elif ps_url: 
                    resolved_poster_url_step1 = ps_url
                elif pl_url: 
                    resolved_poster_url_step1 = pl_url
                    resolved_crop_mode_step1 = crop_mode_setting
            
            # Jav321은 arts에서 포스터를 찾는 로직은 불필요할 수 있음 (PS, PL 위주)
            if not resolved_poster_url_step1 and ps_url:
                resolved_poster_url_step1 = ps_url

            logger.debug(f"{cls.site_name} Step 1: Poster='{resolved_poster_url_step1}', Crop='{resolved_crop_mode_step1}'")

            # 2단계: MGStage 스타일 특별 처리 (로컬 임시 파일 사용)
            #    (Jav321도 다양한 소스의 이미지를 사용하므로 이 로직이 유용할 수 있음)
            mgs_local_poster_filepath = None 
            attempt_mgs_special_local = False
            if pl_url and ps_url and not ps_to_poster_setting:
                if resolved_poster_url_step1 == ps_url:
                    attempt_mgs_special_local = True
                    logger.debug(f"{cls.site_name}: Step 1 resulted in PS. Attempting MGS special (local) for {code}.")

            if attempt_mgs_special_local:
                logger.info(f"{cls.site_name}: Attempting MGS special poster processing (local) for {code} using pl='{pl_url}' and ps='{ps_url}'")
                temp_filepath, _, _ = SiteUtil.get_mgs_half_pl_poster_info_local(ps_url, pl_url, proxy_url=proxy_url)
                if temp_filepath and os.path.exists(temp_filepath): 
                    logger.info(f"{cls.site_name}: MGS special poster (local) successful. Using temp file: {temp_filepath}")
                    mgs_local_poster_filepath = temp_filepath
                else:
                    logger.info(f"{cls.site_name}: MGS special poster (local) failed or did not return a valid file for {code}.")
        
            # 3단계: 최종 포스터 소스 및 크롭 모드 결정
            if mgs_local_poster_filepath: 
                final_poster_source = mgs_local_poster_filepath 
                final_poster_crop_mode = None 
            else: 
                final_poster_source = resolved_poster_url_step1
                final_poster_crop_mode = resolved_crop_mode_step1
            
            logger.info(f"{cls.site_name} Final Image Decision for {code}: Poster Source='{final_poster_source}', Crop Mode='{final_poster_crop_mode}', Landscape='{final_landscape_url}'")

            # --- 이미지 처리 및 entity 할당 (일반 / 이미지 서버) ---
            # (MGStage와 동일한 로직 적용)
            # 일반 모드
            if not (use_image_server and image_mode == '4'):
                logger.info(f"{cls.site_name}: Using Normal Image Processing Mode for {code} (image_mode: {image_mode})...")
                if final_poster_source:
                    processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                    if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                if final_landscape_url:
                    processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url, proxy_url=proxy_url)
                    if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))
                # 팬아트 처리
                processed_fanart_count = 0
                sources_to_exclude_from_arts = {final_poster_source, final_landscape_url}
                if pl_url and mgs_local_poster_filepath and final_poster_source == mgs_local_poster_filepath: sources_to_exclude_from_arts.add(pl_url)
                for art_url in arts_urls:
                    if processed_fanart_count >= max_arts: break
                    if art_url and art_url not in sources_to_exclude_from_arts: 
                        processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url) 
                        if processed_art: entity.fanart.append(processed_art); processed_fanart_count += 1
                logger.debug(f"{cls.site_name} Normal Mode: Final Thumb={entity.thumb}, Fanart Count={len(entity.fanart)}")

            # 이미지 서버 모드
            # (이미지 서버 저장은 메타 파싱 후 ui_code_for_image 확정 뒤에 실행)

        except Exception as e_img_proc:
            logger.exception(f"{cls.site_name}: Error during image processing setup for {code}: {e_img_proc}")
        # --- 이미지 처리 로직 끝 ---

        # --- 메타데이터 파싱 시작 (기존 Jav321 로직 + 오류 처리) ---
        identifier_parsed = False
        try:
            base_xpath = "/html/body/div[2]/div[1]/div[1]"
            nodes = tree.xpath(f"{base_xpath}/div[2]/div[1]/div[2]/b") 
            if not nodes: logger.warning(f"Jav321: Metadata <b> tags not found for {code}.")
            
            for node in nodes:
                try:
                    key = node.text_content().strip() if node.text_content() else ""
                    value_tags = node.xpath(".//following-sibling::text()")
                    value = value_tags[0].replace(":", "").strip() if value_tags and value_tags[0].replace(":", "").strip() else ""
                    if not value and key in ["女优", "标签", "ジャンル", "片商", "メーカー", "系列"]: # a 태그에서 값 가져오기
                        a_tags = node.xpath(".//following-sibling::a")
                        if a_tags: value = a_tags[0].text_content().strip()
                    if not value: continue

                    if key in ["番号", "品番"]:
                        formatted_code = value.upper() 
                        try: 
                            label_part = value.split("-")[0]
                            if entity.tag is None: entity.tag = []
                            if label_part not in entity.tag: entity.tag.append(label_part)
                        except IndexError: pass
                        entity.title = entity.originaltitle = entity.sorttitle = formatted_code
                        ui_code_for_image = formatted_code 
                        entity.ui_code = ui_code_for_image
                        identifier_parsed = True
                        logger.debug(f"Jav321: Identifier parsed: {formatted_code}")
                        continue 
                    elif key == "女优":
                        # ... (배우 파싱) ...
                        entity.actor = []
                        a_tags = node.xpath(".//following-sibling::a[contains(@href, '/star/')]") # star 링크만 선택
                        if a_tags:
                            for a_tag in a_tags:
                                actor_name = a_tag.text_content().strip()
                                if actor_name: entity.actor.append(EntityActor(actor_name))
                        elif value: # 단일 배우 처리
                            try: entity.actor = [EntityActor(value.split(" ")[0].split("/")[0].strip())]
                            except Exception: pass
                    elif key in ["标签", "ジャンル"]:
                        # ... (장르 파싱) ...
                        entity.genre = []
                        a_tags = node.xpath(".//following-sibling::a[contains(@href, '/genre/')]") # genre 링크만 선택
                        genre_texts_to_process = [a.text_content().strip() for a in a_tags if a.text_content()]
                        if not genre_texts_to_process and value: genre_texts_to_process = value.split() # fallback

                        for tmp in genre_texts_to_process:
                            if not tmp.strip(): continue
                            if tmp in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[tmp])
                            elif tmp in SiteUtil.av_genre_ignore_ja: continue
                            else:
                                genre_tmp = SiteUtil.trans(tmp, do_trans=do_trans).replace(" ", "")
                                if genre_tmp not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_tmp)
                    elif key in ["发行日期", "配信開始日"]:
                        entity.premiered = value
                        try: entity.year = int(value[:4])
                        except ValueError: entity.year = 0 
                    elif key in ["播放时长", "収録時間"]:
                        try:
                            match_runtime = re.compile(r"(?P<no>\d{2,3})").search(value)
                            if match_runtime: entity.runtime = int(match_runtime.group("no"))
                        except Exception: pass
                    elif key == "赞":
                        # ... (평점 - Votes) ...
                        try:
                            votes_val = int(value)
                            if entity.ratings is None: entity.ratings = [EntityRatings(0, votes=votes_val, max=5, name="jav321")]
                            else: entity.ratings[0].votes = votes_val
                        except ValueError: pass
                    elif key in ["评分", "平均評価"]:
                        # ... (평점 - Value) ...
                        try:
                            tmp = float(value)
                            if entity.ratings is None: entity.ratings = [EntityRatings(tmp, max=5, name="jav321")]
                            else: entity.ratings[0].value = tmp
                        except ValueError: pass
                    elif key in ["片商", "メーカー"]:
                        # ... (스튜디오) ...
                        studio_tags = node.xpath(".//following-sibling::a[contains(@href, '/company/')]") # company 링크
                        entity.studio = studio_tags[0].text_content().strip() if studio_tags else value
                    elif key == "系列":
                        # ... (시리즈 태그) ...
                        series_tags = node.xpath(".//following-sibling::a[contains(@href, '/series/')]") # series 링크
                        series_name = series_tags[0].text_content().strip() if series_tags else value
                        if entity.tag is None: entity.tag = []
                        trans_series = SiteUtil.trans(series_name, do_trans=do_trans)
                        if trans_series and trans_series not in entity.tag: entity.tag.append(trans_series)

                except Exception as e_tag_parse:
                    logger.warning(f"Jav321: Error parsing a metadata tag for {code}: {e_tag_parse}")

            if not identifier_parsed:
                logger.error(f"Jav321: CRITICAL - Failed to parse identifier (番号/品番) for {code}.")
                entity.title = entity.originaltitle = entity.sorttitle = code[2:].upper()
                # 식별 코드 없으면 ui_code_for_image 설정 불가 -> 이미지 서버 저장 불가할 수 있음
                # ui_code_for_image = code[2:].upper() # fallback?

            # Plot, Tagline
            try:
                plot_text_nodes = tree.xpath(f"{base_xpath}/div[2]/div[3]/div/text()") # 줄거리 영역
                plot_full_text = "".join([t.strip() for t in plot_text_nodes if t.strip()]) # 줄바꿈 포함 결합
                if plot_full_text: entity.plot = SiteUtil.trans(plot_full_text, do_trans=do_trans)

                h3_title_nodes = tree.xpath(f"{base_xpath}/div[1]/h3/text()") # 제목 영역
                if h3_title_nodes:
                    tmp_h3_title = h3_title_nodes[0].strip()
                    # Jav321은 H3가 제목이므로, 줄거리/태그라인 설정 로직 제거 또는 수정 필요
                    # 현재 entity.title은 품번으로 설정되어 있음. H3는 tagline으로 사용?
                    entity.tagline = SiteUtil.trans(tmp_h3_title, do_trans=do_trans)
                    # Plot이 없으면 Tagline을 Plot으로 사용
                    if not entity.plot and entity.tagline: entity.plot = entity.tagline
                else:
                    logger.warning(f"Jav321: H3 title tag not found for {code}.")
                    if not entity.tagline and entity.title: entity.tagline = entity.title # H3 없으면 품번을 태그라인으로
            except Exception as e_h3:
                logger.exception(f"Jav321: Error parsing Plot/Tagline for {code}: {e_h3}")

        except Exception as e_meta_main:
            logger.exception(f"Jav321: Major error during metadata parsing for {code}: {e_meta_main}")
            if not identifier_parsed: return None # 식별코드조차 없으면 실패

        # --- 이미지 서버 저장 로직 (메타 파싱 후, ui_code_for_image 확정 필요) ---
        if use_image_server and image_mode == '4' and image_server_url and image_server_local_path and ui_code_for_image:
            logger.info(f"Saving images to Image Server for {ui_code_for_image} (Jav321)...")
            # --- MGStage와 동일한 이미지 서버 저장 로직 적용 ---
            if final_landscape_url:
                pl_relative_path = SiteUtil.save_image_to_server_path(final_landscape_url, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                if pl_relative_path: entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_relative_path}"))
            if final_poster_source: 
                p_relative_path = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                if p_relative_path: entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_relative_path}"))
            # 팬아트 저장
            processed_fanart_count_server = 0 
            sources_to_exclude_for_server_arts = {final_poster_source, final_landscape_url}
            if pl_url and mgs_local_poster_filepath and final_poster_source == mgs_local_poster_filepath: sources_to_exclude_for_server_arts.add(pl_url)
            for idx, art_url in enumerate(arts_urls): 
                if processed_fanart_count_server >= max_arts: break
                if art_url and art_url not in sources_to_exclude_for_server_arts:
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url) 
                    if art_relative_path: entity.fanart.append(f"{image_server_url}/{art_relative_path}"); processed_fanart_count_server += 1
            logger.info(f"{cls.site_name} Image Server: Processed {len(entity.thumb)} thumbs and {processed_fanart_count_server} fanarts for {ui_code_for_image}.")
        # --- 이미지 서버 저장 로직 끝 ---

        # --- 예고편 처리 (XPath 수정 및 오류 처리) ---
        entity.extras = [] # 초기화
        if use_extras:
            try: 
                trailer_xpath = '//*[@id="vjs_sample_player"]/source/@src' # 제공된 XPath 사용
                trailer_tags = tree.xpath(trailer_xpath)
                if trailer_tags:
                    trailer_url = trailer_tags[0].strip()
                    # URL 유효성 검사 (http로 시작하는지 등)
                    if trailer_url.startswith("http"):
                        # 제목 설정 (entity.title은 품번이므로 tagline 사용?)
                        trailer_title = entity.tagline if entity.tagline else (entity.title if entity.title else code)
                        entity.extras.append(EntityExtra("trailer", trailer_title, "mp4", trailer_url))
                        logger.info(f"Jav321: Trailer added: {trailer_url}")
                    else:
                        logger.warning(f"Jav321: Invalid trailer URL found: {trailer_url}")
                else:
                    logger.debug(f"Jav321: Trailer source tag not found for {code}. XPath: {trailer_xpath}")
            except Exception as e_trailer:
                logger.exception(f"Jav321: Error processing trailer for {code}: {e_trailer}")
        # --- 예고편 처리 끝 ---

        # --- Shiroutoname 보정 (원본 로직 유지) ---
        final_entity = entity 
        if entity.originaltitle: 
            try:
                logger.debug(f"Jav321: Attempting Shiroutoname correction for {entity.originaltitle}")
                final_entity = SiteUtil.shiroutoname_info(entity) 
            except Exception as e_shirouto: 
                logger.exception(f"Jav321: Exception during Shiroutoname correction call for {entity.originaltitle}: {e_shirouto}")
        else:
            logger.warning(f"Jav321: Skipping Shiroutoname correction because originaltitle is missing for {code}.")

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
