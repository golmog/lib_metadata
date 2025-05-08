from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityMovie, EntityThumb, EntityExtra
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
        # --- kwargs에서 설정값 추출 ---
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        ps_to_poster_setting = kwargs.get('ps_to_poster', ps_to_poster)
        crop_mode_setting = kwargs.get('crop_mode', crop_mode)

        url = f"{cls.site_base_url}/{code[2:]}"
        headers = SiteUtil.default_headers.copy(); headers['Referer'] = cls.site_base_url + "/"

        tree = None
        try:
            tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=headers, verify=False)
            if tree is None or not tree.xpath('/html/body/div[5]/div[1]/div[@class="container-fluid"]'): 
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

        # --- 이미지 처리 로직 (Image Server Mode vs Normal Mode) ---
        img_urls_result = {}
        try:
            img_urls_result = cls.__img_urls(tree)
            ps_url = img_urls_result.get('ps')
            pl_url = img_urls_result.get('pl')
            arts = img_urls_result.get('arts', [])

            if use_image_server and image_mode == '4': # 이미지 서버 모드 분기
                logger.info(f"Saving images to Image Server for {code} (JavBus)...")

                final_poster_url = None; final_poster_crop = None
                if ps_to_poster_setting and ps_url: final_poster_url = ps_url
                else:
                    if pl_url and ps_url:
                        loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                        if loc: final_poster_url = pl_url; final_poster_crop = loc
                        else: final_poster_url = ps_url
                    elif ps_url: final_poster_url = ps_url

            elif not (use_image_server and image_mode == '4'): # 일반 모드
                logger.info("Using Normal Image Processing Mode (JavBus)...")
                # 원본 로직 유지
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
            logger.exception(f"JavBus: Error during image processing setup: {e_img_proc}")
        # --- 이미지 처리 설정 끝 ---

        # --- 메타데이터 파싱 시작 (JavBus) ---
        identifier_parsed = False # 식별 코드 파싱 성공 여부 플래그
        try:
            tags = tree.xpath("/html/body/div[5]/div[1]/div[2]/p") # 정보 테이블의 <p> 태그들
            if not tags: logger.warning(f"JavBus: Metadata <p> tags not found for {code}.")

            for tag in tags:
                # 각 태그 파싱 시 예외 처리 추가
                try:
                    tmps = tag.text_content().strip().split(":")
                    key = ""; value = ""
                    if len(tmps) == 2: key = tmps[0].strip(); value = tmps[1].strip()
                    elif len(tmps) == 1: value = tmps[0].strip().replace(" ", "").replace("\t", "").replace("\r\n", " ").strip()
                    if not value: continue # 값이 없으면 다음 태그로

                    # --- 識別碼 (식별 코드) 파싱 ---
                    if key == "識別碼":
                        formatted_code = value.upper()
                        try:
                            label, num = value.split("-")
                            formatted_code = f"{label}-{num.lstrip('0').zfill(3)}"
                            if entity.tag is None: entity.tag = []
                            if label not in entity.tag: entity.tag.append(label)
                        except Exception: pass 
                        entity.title = entity.originaltitle = entity.sorttitle = formatted_code
                        ui_code_for_image = formatted_code 
                        entity.ui_code = ui_code_for_image
                        identifier_parsed = True # 식별 코드 파싱 성공!
                        logger.debug(f"JavBus: Identifier parsed: {formatted_code}")
                        continue # 식별 코드 처리 후 다음 태그로

                    # --- 다른 메타데이터 파싱 (식별 코드가 파싱된 후에만 의미 있을 수 있음) ---
                    # (이하 파싱 로직은 이전과 동일하되, Optional 정보 실패는 Warning으로 처리)
                    elif key == "發行日期":
                        if value != "0000-00-00": entity.premiered = value; entity.year = int(value[:4])
                        else: entity.premiered = "1999-12-31"; entity.year = 1999
                    elif key == "長度":
                        try: entity.runtime = int(value.replace("分鐘", "").strip())
                        except Exception: logger.warning(f"JavBus: Failed to parse runtime '{value}' for {code}")
                    elif key == "導演": entity.director = value
                    elif key == "製作商":
                        entity.studio = value
                        if do_trans: entity.studio = SiteUtil.av_studio.get(value, SiteUtil.trans(value))
                        entity.studio = entity.studio.strip()
                    elif key == "系列":
                        if entity.tag is None: entity.tag = []
                        trans_series = SiteUtil.trans(value, do_trans=do_trans)
                        if trans_series and trans_series not in entity.tag: entity.tag.append(trans_series)
                    elif key == "類別":
                        entity.genre = []
                        for tmp in value.split(" "):
                            if not tmp.strip(): continue
                            if tmp in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[tmp])
                            elif tmp in SiteUtil.av_genre_ignore_ja: continue
                            else:
                                genre_tmp = SiteUtil.trans(tmp, do_trans=do_trans).replace(" ", "")
                                if genre_tmp not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_tmp)
                    elif key == "演員":
                        if "暫無出演者資訊" in value: continue
                        entity.actor = []
                        for tmp in value.split(" "):
                            if not tmp.strip(): continue
                            entity.actor.append(EntityActor(tmp.strip()))

                except Exception as e_tag_parse:
                    logger.warning(f"JavBus: Error parsing a metadata tag for {code}: {e_tag_parse}")
                    # 개별 태그 파싱 오류 시 다음 태그로 계속 진행

            # 식별 코드 파싱 실패 시 에러 로그 및 기본값 설정
            if not identifier_parsed:
                logger.error(f"JavBus: CRITICAL - Failed to parse identifier (識別碼) for {code}. Using code as title.")
                entity.title = entity.originaltitle = entity.sorttitle = code[2:].upper() # 코드를 기본 제목으로

            # Tagline/Plot 파싱 (오류 처리 강화)
            try:
                h3_tag = tree.xpath("/html/body/div[5]/h3/text()")
                if h3_tag: # h3 태그 존재 확인
                    tagline = h3_tag[0].lstrip(entity.title if entity.title else '').strip() # entity.title이 설정된 후 사용
                    entity.tagline = SiteUtil.trans(tagline, do_trans=do_trans).replace(entity.title if entity.title else '', "").replace("[배달 전용]", "").strip()
                    entity.plot = entity.tagline # Javbus는 보통 Tagline과 Plot이 동일
                else:
                    logger.warning(f"JavBus: H3 title tag (tagline/plot source) not found for {code}.")
                    # H3 없으면 plot/tagline은 비워둠
            except Exception as e_h3:
                logger.exception(f"JavBus: Error parsing H3 title for {code}: {e_h3}")

        except Exception as e_meta_main:
            logger.exception(f"JavBus: Major error during metadata parsing for {code}: {e_meta_main}")
            # 메타 파싱 중 큰 오류 발생 시, 최소한의 정보라도 있는지 확인
            if not entity.title: # 제목(식별코드 기반)조차 없다면 처리 불가
                logger.error(f"JavBus: Returning None due to critical metadata parsing failure for {code}.")
                return None
        # --- 메타데이터 파싱 끝 ---


        # --- 이미지 서버 저장 로직 (JavBus) ---
        if use_image_server and image_mode == '4' and image_server_url and image_server_local_path and ui_code_for_image:
            logger.info(f"Saving images to Image Server for {ui_code_for_image} (JavBus)...")
            ps_url = img_urls_result.get('ps')
            pl_url = img_urls_result.get('pl')
            arts = img_urls_result.get('arts', [])
            # 최종 포스터 결정 (위에서 이미 결정됨)
            final_poster_url_is = None; final_poster_crop_is = None
            if ps_to_poster_setting and ps_url: final_poster_url_is = ps_url
            else:
                if pl_url and ps_url:
                    loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                    if loc: final_poster_url_is = pl_url; final_poster_crop_is = loc
                    else: final_poster_url_is = ps_url
                elif ps_url: final_poster_url_is = ps_url

            # 저장 호출
            if ps_url: SiteUtil.save_image_to_server_path(ps_url, 'ps', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
            if pl_url:
                pl_relative_path = SiteUtil.save_image_to_server_path(pl_url, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                if pl_relative_path:
                    entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_relative_path}"))
            if final_poster_url_is:
                p_relative_path = SiteUtil.save_image_to_server_path(final_poster_url_is, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_is)
                if p_relative_path:
                    entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_relative_path}"))
            processed_fanart_count = 0
            urls_to_exclude = {final_poster_url_is, pl_url}
            for idx, art_url in enumerate(arts):
                art_index_to_save = idx + 1 # 1부터 시작
                if processed_fanart_count >= max_arts: break
                if art_url and art_url not in urls_to_exclude:
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=art_index_to_save, proxy_url=proxy_url)
                    if art_relative_path:
                        entity.fanart.append(f"{image_server_url}/{art_relative_path}"); processed_fanart_count += 1
            logger.info(f"Image Server (JavBus): Processed {processed_fanart_count} fanarts.")
        # --- 이미지 서버 저장 로직 끝 ---

        # --- 예고편 처리 (없음) ---
        # if use_extras or not use_extras: entity.extras = []

        # --- Shiroutoname 보정 (originaltitle이 있을 때만 시도) ---
        final_entity = entity # 기본 반환값은 현재 entity
        if entity.originaltitle: # 식별 코드(originaltitle)가 성공적으로 파싱되었을 때만 시도
            try:
                logger.debug(f"JavBus: Attempting Shiroutoname correction for {entity.originaltitle}")
                # SiteUtil.shiroutoname_info는 내부에서 예외처리 강화됨
                final_entity = SiteUtil.shiroutoname_info(entity) 
            except Exception as e_shirouto: # 만약을 위한 추가 예외 처리
                logger.exception(f"JavBus: Exception during Shiroutoname correction call for {entity.originaltitle}: {e_shirouto}")
                # 오류 발생 시에도 원래 entity 반환
        else:
            logger.warning(f"JavBus: Skipping Shiroutoname correction because originaltitle is missing for {code}.")

        return final_entity # 최종 entity 반환


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
