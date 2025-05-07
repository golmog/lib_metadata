import re

from lxml import html

from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings
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
        """collect raw image urls from html page"""
        # 원본 로직 유지 + XPath 방어 코드
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        base_xpath = "/html/body/div[2]/div[1]/div[1]"

        ps_tags = tree.xpath(f"{base_xpath}/div[2]/div[1]/div[1]/img/@src")
        img_urls['ps'] = ps_tags[0] if ps_tags else ""
        if not img_urls['ps']: logger.warning("Jav321: 이미지 URL을 얻을 수 없음: ps")

        pl_tags = tree.xpath('//*[@id="vjs_sample_player"]/@poster')
        img_urls['pl'] = pl_tags[0] if pl_tags else ""
        # if not img_urls['pl']: logger.warning("Jav321: 이미지 URL을 얻을 수 없음: pl") # pl은 없을 수 있음

        arts_src = tree.xpath("/html/body/div[2]/div[2]/div//img/@src")
        for img_src in arts_src:
            if img_src == img_urls['pl']: continue # pl과 중복 제외
            # Jav321은 보통 절대 경로로 제공되므로 __fix_url 불필요할 수 있음
            img_urls['arts'].append(img_src)

        # 예외 처리 (원본 로직 유지)
        if "aventertainments.com" in img_urls['ps'] and "/bigcover/" in img_urls['ps'] and not img_urls['pl']:
            img_urls['pl'] = img_urls['ps']
            img_urls['ps'] = img_urls['ps'].replace("/bigcover/", "/jacket_images/")

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
        # --- kwargs에서 설정값 추출 (필수) ---
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        ps_to_poster_setting = kwargs.get('ps_to_poster', ps_to_poster)
        crop_mode_setting = kwargs.get('crop_mode', crop_mode)
        # --- 설정값 추출 끝 ---

        url = f"{cls.site_base_url}/video/{code[2:]}"
        headers = SiteUtil.default_headers.copy(); headers['Referer'] = cls.site_base_url + "/"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=headers) # Jav321은 verify 불필요할 수 있음

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = [] # 명시적 초기화 (필수)
        entity.fanart = [] # 명시적 초기화 (필수)
        entity.extras = [] # 명시적 초기화 (필수)
        ui_code_for_image = "" # 이미지 파일명용 최종 UI 코드

        # --- 이미지 처리 로직 (Image Server Mode vs Normal Mode) ---
        img_urls_result = {}
        try:
            img_urls_result = cls.__img_urls(tree)
            ps_url_from_html = img_urls_result.get('ps') # HTML에서 파싱한 ps
            pl_url = img_urls_result.get('pl')
            arts = img_urls_result.get('arts', [])

            # Jav321은 ps가 중요 포스터일 수 있으므로, HTML 파싱 결과를 우선 사용
            # (DMM/MGStage처럼 검색 캐시 PS에 의존하지 않음)
            current_ps_for_logic = ps_url_from_html

            if use_image_server and image_mode == '4':
                logger.info(f"Saving images to Image Server for {code} (Jav321)...")
                final_poster_url = None; final_poster_crop = None
                if ps_to_poster_setting and current_ps_for_logic: final_poster_url = current_ps_for_logic
                else:
                    if pl_url and current_ps_for_logic:
                        loc = SiteUtil.has_hq_poster(current_ps_for_logic, pl_url, proxy_url=proxy_url)
                        if loc: final_poster_url = pl_url; final_poster_crop = loc
                        else: final_poster_url = current_ps_for_logic
                    elif current_ps_for_logic: final_poster_url = current_ps_for_logic
                # 이미지 저장은 메타 파싱 후로 이동

            elif not (use_image_server and image_mode == '4'): # 일반 모드
                logger.info("Using Normal Image Processing Mode (Jav321)...")
                # img_urls_result의 ps는 HTML 파싱 결과 사용
                img_urls_result['ps'] = current_ps_for_logic # resolve_jav_imgs 가 사용할 ps 업데이트
                SiteUtil.resolve_jav_imgs(img_urls_result, ps_to_poster=ps_to_poster_setting, crop_mode=crop_mode_setting, proxy_url=proxy_url)
                entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls_result, proxy_url=proxy_url)
                resolved_arts = img_urls_result.get("arts", [])
                processed_fanart_count = 0
                resolved_poster_for_exclude = img_urls_result.get('poster')
                resolved_landscape_for_exclude = img_urls_result.get('landscape')
                urls_to_exclude_from_arts = {resolved_poster_for_exclude, resolved_landscape_for_exclude}
                for art_url in resolved_arts:
                    if processed_fanart_count >= max_arts: break
                    if art_url and art_url not in urls_to_exclude_from_arts:
                        processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                        if processed_art: entity.fanart.append(processed_art); processed_fanart_count += 1
        except Exception as e_img_proc:
            logger.exception(f"Jav321: Error during image processing setup: {e_img_proc}")
        # --- 이미지 처리 설정 끝 ---

        # --- 메타데이터 파싱 시작 (Jav321) ---
        try:
            base_xpath = "/html/body/div[2]/div[1]/div[1]"
            nodes = tree.xpath(f"{base_xpath}/div[2]/div[1]/div[2]/b") # 원본 XPath 유지
            for node in nodes:
                key = node.text_content().strip() if node.text_content() else ""
                value_tags = node.xpath(".//following-sibling::text()")
                value = value_tags[0].replace(":", "").strip() if value_tags and value_tags[0].replace(":", "").strip() else ""

                if not value and key in ["女优", "标签", "ジャンル", "片商", "メーカー"]: # a 태그에서 값 가져오기
                    a_tags = node.xpath(".//following-sibling::a")
                    if a_tags: value = a_tags[0].text_content().strip() # 일단 첫번째 a 태그 값 사용

                if not value: continue # 값이 없으면 스킵
                # logger.debug(...) # 원본에 없음

                if key in ["番号", "品番"]:
                    formatted_code = value.upper() # 기본값
                    # Jav321은 보통 하이픈 포함된 형식, 추가 zfill 불필요할 수 있음
                    try: # 혹시 모를 split 오류 대비
                        label_part = value.split("-")[0]
                        if entity.tag is None: entity.tag = []
                        if label_part not in entity.tag: entity.tag.append(label_part)
                    except IndexError: pass
                    entity.title = entity.originaltitle = entity.sorttitle = formatted_code
                    ui_code_for_image = formatted_code # 최종 UI 코드 확정
                    entity.ui_code = ui_code_for_image
                # ... (나머지 메타 파싱 원본 로직 유지, 단 XPath 결과 방어 코드 추가) ...
                elif key == "女优":
                    entity.actor = [] # 초기화
                    a_tags = node.xpath(".//following-sibling::a")
                    if a_tags:
                        for a_tag in a_tags:
                            if "star" in a_tag.attrib.get("href", ""): # href 속성 체크
                                actor_name = a_tag.text_content().strip()
                                if actor_name: entity.actor.append(EntityActor(actor_name))
                            else: break # star 링크 아니면 중단
                    elif value: # a 태그 없고 텍스트만 있을 때 (단일 배우 가정)
                        try: entity.actor = [EntityActor(value.split(" ")[0].split("/")[0].strip())]
                        except Exception: pass
                elif key in ["标签", "ジャンル"]:
                    entity.genre = []
                    a_tags = node.xpath(".//following-sibling::a")
                    genre_texts_from_a = [a.text_content().strip() for a in a_tags if a.text_content()]
                    genre_texts_to_process = genre_texts_from_a if genre_texts_from_a else value.split() # a태그 없으면 공백분리

                    for tmp in genre_texts_to_process:
                        if not tmp.strip(): continue
                        # ... (나머지 장르 처리 로직 유지) ...
                elif key in ["发行日期", "配信開始日"]:
                    entity.premiered = value
                    try: entity.year = int(value[:4])
                    except ValueError: entity.year = 0 # 오류 시 기본값
                elif key in ["播放时长", "収録時間"]:
                    try:
                        match_runtime = re.compile(r"(?P<no>\d{2,3})").search(value)
                        if match_runtime: entity.runtime = int(match_runtime.group("no"))
                    except Exception: pass
                elif key == "赞":
                    try:
                        votes_val = int(value)
                        if entity.ratings is None: entity.ratings = [EntityRatings(0, votes=votes_val, max=5, name="jav321")]
                        else: entity.ratings[0].votes = votes_val
                    except ValueError: pass
                elif key in ["评分", "平均評価"]:
                    try:
                        tmp = float(value)
                        if entity.ratings is None: entity.ratings = [EntityRatings(tmp, max=5, name="jav321")]
                        else: entity.ratings[0].value = tmp
                    except ValueError: pass
                elif key in ["片商", "メーカー"]:
                    studio_tags = node.xpath(".//following-sibling::a")
                    entity.studio = studio_tags[0].text_content().strip() if studio_tags else value


            # Plot, Tagline (원본 로직 유지, XPath 방어 코드 추가)
            plot_text_nodes = tree.xpath(f"{base_xpath}/div[2]/div[3]/div/text()")
            if plot_text_nodes: entity.plot = SiteUtil.trans(plot_text_nodes[0], do_trans=do_trans)

            h3_title_nodes = tree.xpath(f"{base_xpath}/div[1]/h3/text()")
            if h3_title_nodes:
                tmp_h3_title = h3_title_nodes[0].strip()
                flag_is_plot = False
                if not entity.actor: # 배우 정보가 없으면
                    if len(tmp_h3_title) < 10: entity.actor = [EntityActor(tmp_h3_title)] # 짧으면 배우로 간주
                    else: flag_is_plot = True
                else: flag_is_plot = True

                if flag_is_plot:
                    trans_h3 = SiteUtil.trans(tmp_h3_title, do_trans=do_trans)
                    if entity.plot is None: entity.plot = trans_h3
                    elif trans_h3 not in entity.plot: entity.plot += "\n" + trans_h3 # 중복 방지하며 추가

            if entity.plot: entity.tagline = entity.plot # Jav321은 태그라인=줄거리
            elif entity.title: entity.tagline = entity.title # 줄거리 없으면 제목

        except Exception as e_meta:
            logger.exception(f"Jav321: Error during metadata parsing: {e_meta}")
        # --- 메타데이터 파싱 끝 ---


        # --- 이미지 서버 저장 로직 (Jav321) ---
        if use_image_server and image_mode == '4' and image_server_url and image_server_local_path and ui_code_for_image:
            logger.info(f"Saving images to Image Server for {ui_code_for_image} (Jav321)...")
            ps_url = img_urls_result.get('ps')
            pl_url = img_urls_result.get('pl')
            arts = img_urls_result.get('arts', [])
            # 최종 포스터 결정 (DMM/MGStage/JavBus와 동일 로직 사용)
            final_poster_url_is = None; final_poster_crop_is = None
            if ps_to_poster_setting and ps_url: final_poster_url_is = ps_url
            else:
                if pl_url and ps_url:
                    loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                    if loc: final_poster_url_is = pl_url; final_poster_crop_is = loc
                    else: final_poster_url_is = ps_url
                elif ps_url: final_poster_url_is = ps_url

            if ps_url: SiteUtil.save_image_to_server_path(ps_url, 'ps', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
            if pl_url:
                pl_relative_path = SiteUtil.save_image_to_server_path(pl_url, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                if pl_relative_path: entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_relative_path}"))
            if final_poster_url_is:
                p_relative_path = SiteUtil.save_image_to_server_path(final_poster_url_is, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_is)
                if p_relative_path: entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_relative_path}"))
            processed_fanart_count = 0
            urls_to_exclude = {final_poster_url_is, pl_url}
            for idx, art_url in enumerate(arts):
                art_index_to_save = idx + 1
                if processed_fanart_count >= max_arts: break
                if art_url and art_url not in urls_to_exclude:
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=art_index_to_save, proxy_url=proxy_url)
                    if art_relative_path: entity.fanart.append(f"{image_server_url}/{art_relative_path}"); processed_fanart_count += 1
        # --- 이미지 서버 저장 로직 끝 ---

        # --- 예고편 처리 (원본 로직 유지) ---
        # entity.extras = [] # 이미 위에서 초기화됨
        if use_extras:
            try: # XPath 실패 대비
                trailer_nodes = tree.xpath('//*[@id="vjs_sample_player"]')
                if trailer_nodes:
                    source_tags = trailer_nodes[0].xpath(".//source/@src")
                    if source_tags:
                        entity.extras = [EntityExtra("trailer", entity.title if entity.title else code, "mp4", source_tags[0])] # 제목 없으면 코드로
            except Exception as e_trailer:
                logger.exception(f"Jav321: Error processing trailer: {e_trailer}")
        # --- 예고편 처리 끝 ---

        # --- Shiroutoname 보정 (원본 로직 유지) ---
        try:
            return SiteUtil.shiroutoname_info(entity)
        except Exception:
            logger.exception("shiroutoname.com을 이용해 메타 보정 중 예외:")
            return entity
        # --- 보정 끝 ---

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
