import re

from .constants import MGS_CODE_LEN, MGS_LABEL_MAP
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings, EntityThumb
from .plugin import P
from .site_util import SiteUtil

logger = P.logger


class SiteMgstage:
    site_name = "mgs"
    site_char = "M"
    site_base_url = "https://www.mgstage.com"
    module_char = None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/71.0.3578.98 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cookie": "coc=1;mgs_agef=1;",
    }

    PTN_SEARCH_PID = re.compile(r"\/product_detail\/(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^\d*(?P<real>[a-zA-Z]+)\-(?P<no>\d+)$")
    PTN_TEXT_SUB = [
        re.compile(r"【(?<=【)(?:MGSだけのおまけ映像付き|期間限定).*(?=】)】(:?\s?\+\d+分\s?)?"),
        re.compile(r"※通常版\+\d+分の特典映像付のスペシャルバージョン！"),
        re.compile(r"【(?<=【).+実施中(?=】)】"),
    ]
    PTN_RATING = re.compile(r"\s(?P<rating>[\d\.]+)点\s.+\s(?P<vote>\d+)\s件")

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd":
            keyword = keyword[:-3]
        keyword = keyword.replace(" ", "-")

        if cls.module_char == "C":
            module_query = "&is_dvd_product=1&type=dvd"
        elif cls.module_char == "D":
            module_query = "&is_dvd_product=0&type=haishin"
        else:
            raise ValueError(f"Class variable for 'module_char' should be either 'C' or 'D': {cls.module_char}")

        url = f"{cls.site_base_url}/search/cSearch.php?search_word={keyword}&x=0&y=0{module_query}"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)
        lists = tree.xpath('//div[@class="search_list"]/div/ul/li')
        logger.debug("mgs search kwd=%s len=%d", keyword, len(lists))

        ret = []
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                tag = node.xpath(".//a")[0]
                href = tag.attrib["href"].lower()
                match = cls.PTN_SEARCH_PID.search(href)
                if match:
                    item.code = cls.module_char + cls.site_char + match.group("code").upper()
                already_exist = False
                for exist_item in ret:
                    if exist_item["code"] == item.code:
                        already_exist = True
                        break
                if already_exist:
                    continue

                tag = node.xpath(".//img")[0]
                item.image_url = tag.attrib["src"]

                tag = node.xpath('.//a[@class="title lineclamp"]')[0]
                title = tag.text_content()
                for ptn in cls.PTN_TEXT_SUB:
                    title = ptn.sub("", title)
                item.title = item.title_ko = title.strip()

                # tmp = SiteUtil.discord_proxy_get_target(item.image_url)
                # 2021-03-22 서치에는 discord 고정 url을 사용하지 않는다. 3번
                # manual == False  때는 아예 이미치 처리를 할 필요가 없다.
                # 일치항목 찾기 때는 화면에 보여줄 필요가 있는데 3번은 하면 하지 않는다.
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    item.image_url = SiteUtil.process_image_mode(_image_mode, item.image_url, proxy_url=proxy_url)
                    if do_trans:
                        item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                else:
                    item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)

                match = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                if match:
                    item.ui_code = match.group("real") + "-" + match.group("no")
                else:
                    item.ui_code = item.code[2:]

                if item.ui_code == keyword.upper():
                    item.score = 100
                elif keyword.upper().replace(item.ui_code, "").isnumeric():
                    item.score = 100
                else:
                    item.score = 60 - (len(ret) * 10)
                if item.score < 0:
                    item.socre = 0
                ret.append(item.as_dict())
            except Exception:
                logger.exception("개별 검색 결과 처리 중 예외:")
        return sorted(ret, key=lambda k: k["score"], reverse=True)

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            data = []
            tmps = keyword.upper().replace("-", " ").split()

            # --- __search 호출 시 필요한 kwargs 추출 ---
            do_trans_arg = kwargs.get('do_trans', True)
            proxy_url_arg = kwargs.get('proxy_url', None)
            image_mode_arg = kwargs.get('image_mode', "0")
            manual_arg = kwargs.get('manual', False)
            # --- 추출 끝 ---

            if len(tmps) == 2:
                label, code_part = tmps # 변수명 명확화 (code -> code_part)
                numlabels = MGS_LABEL_MAP.get(label) or []
                if numlabels:
                    if label not in numlabels:
                        numlabels.append(label)
                    for idx, lab in enumerate(numlabels):
                        current_code_part = code_part # 루프마다 원본 code_part 사용
                        if codelen := MGS_CODE_LEN.get(lab):
                            try:
                                current_code_part = str(int(current_code_part)).zfill(codelen)
                            except ValueError:
                                pass
                        # <<< __search 호출 시 추출된 인자 사용 >>>
                        _d = cls.__search(f"{lab}-{current_code_part}",
                                          do_trans=do_trans_arg,
                                          proxy_url=proxy_url_arg,
                                          image_mode=image_mode_arg,
                                          manual=manual_arg)
                        if _d:
                            data += _d
                            if idx > 0:
                                numlabels.remove(lab)
                                numlabels.insert(0, lab)
                            break
            if not data:
                # <<< __search 호출 시 추출된 인자 사용 >>>
                data = cls.__search(keyword,
                                    do_trans=do_trans_arg,
                                    proxy_url=proxy_url_arg,
                                    image_mode=image_mode_arg,
                                    manual=manual_arg)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:") # 로깅 형식 유지
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data else "no_match"
            ret["data"] = data
        return ret


class SiteMgstageAma(SiteMgstage):
    module_char = "D"

    @classmethod
    def __img_urls(cls, tree):
        """collect raw image urls from html page (Ama Version)"""
        # Ama 버전의 이미지 추출 로직 유지
        pl = tree.xpath('//*[@id="package"]/a/@href')
        pl = pl[0] if pl else ""
        if not pl: logger.warning("Ama: 이미지 URL을 얻을 수 없음: poster large")

        ps = tree.xpath('//div[@class="detail_photo"]//img/@src')
        ps = ps[0] if ps else ""
        if not ps and pl and "pb_e_" in pl: # pl에서 ps 유추 시도
            ps = pl.replace("pb_e_", "pf_o1_")
        if not ps : logger.warning("Ama: 이미지 URL을 얻을 수 없음: poster small")

        arts = tree.xpath('//*[@id="sample-photo"]//ul/li/a/@href')
        # Ama는 pl을 변형한 세로 고화질 이미지를 arts 시작에 추가
        if pl and "pb_e_" in pl:
            arts.insert(0, pl.replace("pb_e_", "pf_e_"))

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
        ps_to_poster=False, # kwargs 우선 적용
        crop_mode=None,     # kwargs 우선 적용
        **kwargs          # kwargs 추가
    ):
        # --- kwargs에서 설정값 추출 ---
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        ps_to_poster_setting = kwargs.get('ps_to_poster', ps_to_poster)
        crop_mode_setting = kwargs.get('crop_mode', crop_mode)

        logger.debug(f"Image Server Mode Check (Ama): image_mode={image_mode}, use_image_server={use_image_server}")
        if use_image_server and image_mode == '4':
            logger.info(f"Image Server Enabled (Ama): URL={image_server_url}, LocalPath={image_server_local_path}, PathSegment={image_path_segment}")
        # --- 설정값 추출 끝 ---

        url = cls.site_base_url + f"/product/product_detail/{code[2:]}/"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = [] # 명시적 초기화
        entity.fanart = [] # 명시적 초기화
        entity.extras = [] # 명시적 초기화
        ui_code_for_image = "" # 이미지 파일명용 최종 UI 코드

        # --- 이미지 처리 로직 (Image Server Mode vs Normal Mode) ---
        img_urls_result = {} # 원본 URL 저장용
        try:
            img_urls_result = cls.__img_urls(tree) # 원본 ps, pl, arts 추출
            ps_url = img_urls_result.get('ps')
            pl_url = img_urls_result.get('pl')
            arts = img_urls_result.get('arts', [])

            if use_image_server and image_mode == '4':
                logger.info(f"Saving images to Image Server for {code} (Ama)...")
                # 최종 포스터 URL 및 크롭모드 결정 (DVD와 동일 로직 사용)
                final_poster_url = None; final_poster_crop = None
                if ps_to_poster_setting and ps_url: final_poster_url = ps_url
                else:
                    if pl_url and ps_url:
                        loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                        if loc: final_poster_url = pl_url; final_poster_crop = loc
                        else: final_poster_url = ps_url
                    elif ps_url: final_poster_url = ps_url
                logger.info(f"Image Server (Ama) Decision: Poster='{final_poster_url}'(Crop:{final_poster_crop}), Landscape='{pl_url}'")

                # UI 코드 확정 단계까지 기다려야 하므로 이미지 저장은 메타 파싱 후로 이동

            elif not (use_image_server and image_mode == '4'): # 일반 모드
                logger.info("Using Normal Image Processing Mode (Ama)...")
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
                logger.debug(f"Normal Mode (Ama): Final Thumb={entity.thumb}, Fanart Count={len(entity.fanart)}")

        except Exception as e_img_proc:
            logger.exception(f"Ama: Error during image processing setup: {e_img_proc}")
        # --- 이미지 처리 설정 끝 ---


        # --- 메타데이터 파싱 시작 (Ama) ---
        try:
            # Ama는 DVD와 동일한 페이지 구조 사용 가정 (XPath 등)
            h1_tags = tree.xpath('//h1[@class="tag"]/text()')
            if h1_tags:
                h1 = h1_tags[0]
                for ptn in cls.PTN_TEXT_SUB: h1 = ptn.sub("", h1)
                entity.tagline = SiteUtil.trans(h1, do_trans=do_trans)
            else: logger.warning("Ama: H1 title tag not found.")

            basetag = '//div[@class="detail_data"]'
            tags = tree.xpath(f"{basetag}//tr")
            tmp_premiered = None
            for tag in tags:
                key_node = tag.xpath("./th")
                value_node = tag.xpath("./td")
                if not key_node or not value_node: continue
                key = key_node[0].text_content().strip()
                value = value_node[0].text_content().strip()
                value_node_instance = value_node[0]

                if "品番" in key: # Ama도 품번 처리 동일
                    match = cls.PTN_SEARCH_REAL_NO.match(value)
                    formatted_code = value.upper()
                    if match:
                        label = match.group("real").upper()
                        num_str = str(int(match.group("no"))).zfill(3)
                        formatted_code = f"{label}-{num_str}"
                        if entity.tag is None: entity.tag = []
                        if label not in entity.tag: entity.tag.append(label)
                    entity.title = entity.originaltitle = entity.sorttitle = formatted_code
                    ui_code_for_image = formatted_code # 최종 UI 코드 확정
                    entity.ui_code = ui_code_for_image
                    logger.debug(f"Ama: 品番 파싱 완료, ui_code_for_image='{ui_code_for_image}'")
                    continue

                elif "商品発売日" in key: # Ama는 출시일 없을 수 있음
                    try: entity.premiered = value.replace("/", "-"); entity.year = int(value[:4])
                    except Exception: pass
                elif "配信開始日" in key: tmp_premiered = value.replace("/", "-") # Ama는 주로 이 날짜 사용
                elif "収録時間" in key:
                    try: entity.runtime = int(value.replace("min", "").strip())
                    except Exception: pass
                elif "出演" in key:
                    entity.actor = [EntityActor(x.strip()) for x in value_node_instance.xpath("./a/text()") if x.strip()]
                elif "監督" in key: entity.director = value # Ama는 감독 없을 수 있음
                elif "シリーズ" in key:
                    series_name = value_node_instance.xpath("./a/text()")
                    series_name = series_name[0].strip() if series_name else value
                    if entity.tag is None: entity.tag = []
                    trans_series = SiteUtil.trans(series_name, do_trans=do_trans)
                    if trans_series and trans_series not in entity.tag: entity.tag.append(trans_series)
                elif "レーベル" in key: # Ama는 레이블 대신 스튜디오일 수 있음
                    studio_name = value_node_instance.xpath("./a/text()")
                    studio_name = studio_name[0].strip() if studio_name else value
                    entity.studio = studio_name
                    if do_trans:
                        if studio_name in SiteUtil.av_studio: entity.studio = SiteUtil.av_studio[studio_name]
                        else: entity.studio = SiteUtil.change_html(SiteUtil.trans(studio_name))
                elif "ジャンル" in key:
                    entity.genre = []
                    for a_tag in value_node_instance.xpath("./a"):
                        tmp = a_tag.text_content().strip()
                        if "MGSだけのおまけ映像付き" in tmp or not tmp or tmp in SiteUtil.av_genre_ignore_ja: continue
                        if tmp in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[tmp])
                        else:
                            genre_tmp = SiteUtil.trans(tmp, do_trans=do_trans).replace(" ", "")
                            if genre_tmp not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_tmp)

            # Ama는 配信開始日 우선
            if tmp_premiered is not None:
                entity.premiered = tmp_premiered
                try: entity.year = int(tmp_premiered[:4])
                except Exception: pass
            elif entity.premiered is None: # 配信日도 없고 発売日도 없으면 연도 없음
                entity.year = None

            plot_nodes = tree.xpath('//*[@id="introduction"]//p[1]')
            if plot_nodes:
                for br in plot_nodes[0].xpath('.//br'): br.tail = "\n" + br.tail if br.tail else "\n"
                tmp = plot_nodes[0].text_content().strip()
                if not tmp and len(tree.xpath('//*[@id="introduction"]//p')) > 1:
                    plot_nodes_alt = tree.xpath('//*[@id="introduction"]//p[2]')
                    if plot_nodes_alt:
                        for br in plot_nodes_alt[0].xpath('.//br'): br.tail = "\n" + br.tail if br.tail else "\n"
                        tmp = plot_nodes_alt[0].text_content().strip()
                if tmp:
                    for ptn in cls.PTN_TEXT_SUB: tmp = ptn.sub("", tmp)
                    entity.plot = SiteUtil.trans(tmp, do_trans=do_trans)
            else: logger.warning("Ama: Plot node not found.")

            rating_tag = tree.xpath('//div[@class="user_review_head"]/p[@class="detail"]/text()')
            if rating_tag:
                match = cls.PTN_RATING.search(rating_tag[0])
                if match:
                    try:
                        rating_value = float(match.group("rating"))
                        votes = int(match.group("vote"))
                        entity.ratings = [EntityRatings(rating_value, max=5, name="mgs", votes=votes)]
                    except Exception: logger.warning("Ama: Failed to parse rating values.")

        except Exception as e_meta:
            logger.exception(f"Ama: Error during metadata parsing: {e_meta}")
        # --- 메타데이터 파싱 끝 ---


        # --- 이미지 서버 저장 로직 (Ama) ---
        if use_image_server and image_mode == '4' and image_server_url and image_server_local_path and ui_code_for_image:
            logger.info(f"Saving images to Image Server for {ui_code_for_image} (Ama)...")
            ps_url = img_urls_result.get('ps') # 이미 추출된 값 사용
            pl_url = img_urls_result.get('pl')
            arts = img_urls_result.get('arts', [])
            # 최종 포스터 결정 (DVD와 동일 로직)
            final_poster_url_is = None; final_poster_crop_is = None
            if ps_to_poster_setting and ps_url: final_poster_url_is = ps_url
            else:
                if pl_url and ps_url:
                    loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                    if loc: final_poster_url_is = pl_url; final_poster_crop_is = loc
                    else: final_poster_url_is = ps_url
                elif ps_url: final_poster_url_is = ps_url
            logger.info(f"Image Server (Ama) Final Decision: Poster='{final_poster_url_is}'(Crop:{final_poster_crop_is})")

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
                if processed_fanart_count >= max_arts: break
                if art_url and art_url not in urls_to_exclude:
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                    if art_relative_path:
                        entity.fanart.append(f"{image_server_url}/{art_relative_path}"); processed_fanart_count += 1
            logger.info(f"Image Server (Ama): Processed {processed_fanart_count} fanarts.")
        # --- 이미지 서버 저장 로직 끝 ---


        # --- 예고편 처리 시작 (Ama) ---
        # entity.extras = [] # 이미 위에서 초기화됨
        if use_extras:
            try:
                trailer_tag = tree.xpath('//*[@class="sample_movie_btn"]/a/@href')
                if trailer_tag:
                    pid = trailer_tag[0].split("/")[-1]
                    api_url = f"https://www.mgstage.com/sampleplayer/sampleRespons.php?pid={pid}"
                    api_headers = cls.headers.copy(); api_headers['Referer'] = url
                    api_headers['X-Requested-With'] = 'XMLHttpRequest'; api_headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
                    res_json = SiteUtil.get_response(api_url, proxy_url=proxy_url, headers=api_headers).json()
                    if res_json and res_json.get("url"):
                        trailer_base_url = res_json["url"].split(".ism")[0]
                        trailer_url = trailer_base_url + ".mp4"
                        trailer_title = entity.tagline if entity.tagline else (entity.title if entity.title else code)
                        entity.extras.append(EntityExtra("trailer", trailer_title, "mp4", trailer_url))
                        logger.info(f"Ama: Trailer added: {trailer_url}")
                    else: logger.warning("Ama: Trailer API response invalid.")
                else: logger.debug("Ama: Trailer button not found.")
            except Exception as e_trailer:
                logger.exception(f"Ama: Error processing trailer: {e_trailer}")
        # --- 예고편 처리 끝 ---


        # --- Shiroutoname 보정 (Ama 전용) ---
        try:
            entity = SiteUtil.shiroutoname_info(entity) # info 보정 시도
            logger.debug("Ama: Shiroutoname info applied (if found).")
        except Exception as e_shirouto:
            logger.exception("Ama: Shiroutoname 보정 중 예외: {e_shirouto}")
        # --- 보정 끝 ---

        return entity # 최종 entity 반환

    @classmethod
    def info(cls, code, **kwargs):
        # info wrapper는 각 하위 클래스가 직접 __info를 호출하도록 수정
        ret = {}
        try:
            # 자신의 __info 메소드 호출
            entity = cls.__info(code, **kwargs)
            if entity: # __info 반환값 체크 추가
                ret["ret"] = "success"
                ret["data"] = entity.as_dict()
            else: # __info가 None 등을 반환할 경우 대비
                ret["ret"] = "error"
                ret["data"] = f"Failed to get Ama info entity for {code}"
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        # else 블록 제거 (try 블록에서 ret 설정)
        return ret


class SiteMgstageDvd(SiteMgstage):
    module_char = "C"

    @classmethod
    def __img_urls(cls, tree):
        """collect raw image urls from html page (DVD Version)"""
        ps = tree.xpath('//div[@class="detail_photo"]//img/@src')
        ps = ps[0] if ps else ""
        if not ps: logger.warning("DVD: 이미지 URL을 얻을 수 없음: poster small")
        pl = tree.xpath('//*[@id="package"]/a/@href')
        pl = pl[0] if pl else ""
        arts = tree.xpath('//*[@id="sample-photo"]//ul/li/a/@href')
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
        **kwargs,
    ):
        # --- kwargs에서 설정값 추출 ---
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        # ps_to_poster, crop_mode는 인자로 직접 받지만, kwargs 우선 적용 가능 (필요시)
        ps_to_poster_setting = kwargs.get('ps_to_poster', ps_to_poster)
        crop_mode_setting = kwargs.get('crop_mode', crop_mode)

        logger.debug(f"Image Server Mode Check (DVD): image_mode={image_mode}, use_image_server={use_image_server}")
        if use_image_server and image_mode == '4':
            logger.info(f"Image Server Enabled (DVD): URL={image_server_url}, LocalPath={image_server_local_path}, PathSegment={image_path_segment}")
        # --- 설정값 추출 끝 ---

        url = cls.site_base_url + f"/product/product_detail/{code[2:]}/"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = [] # 명시적 초기화
        entity.fanart = [] # 명시적 초기화
        entity.extras = [] # 명시적 초기화
        ui_code_for_image = "" # 이미지 파일명용 최종 UI 코드

        # --- 이미지 처리 로직 (Image Server Mode vs Normal Mode) ---
        img_urls_result = {} # 원본 URL 저장용
        try:
            img_urls_result = cls.__img_urls(tree) # 원본 ps, pl, arts 추출
            ps_url = img_urls_result.get('ps')
            pl_url = img_urls_result.get('pl')
            arts = img_urls_result.get('arts', [])

            if use_image_server and image_mode == '4':
                logger.info(f"Saving images to Image Server for {code} (DVD)...")
                # 최종 포스터 URL 및 크롭모드 결정
                final_poster_url = None
                final_poster_crop = None
                if ps_to_poster_setting and ps_url:
                    final_poster_url = ps_url; final_poster_crop = None
                else:
                    if pl_url and ps_url:
                        loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                        if loc: final_poster_url = pl_url; final_poster_crop = loc
                        else: final_poster_url = ps_url; final_poster_crop = None # 크롭 불가 시 PS
                    elif ps_url: final_poster_url = ps_url; final_poster_crop = None
                logger.info(f"Image Server (DVD) Decision: Poster='{final_poster_url}'(Crop:{final_poster_crop}), Landscape='{pl_url}'")

                # UI 코드 확정 단계까지 기다려야 하므로 이미지 저장은 메타 파싱 후로 이동

            elif not (use_image_server and image_mode == '4'): # 일반 모드
                logger.info("Using Normal Image Processing Mode (DVD)...")
                # resolve_jav_imgs 호출 시 설정값 전달
                SiteUtil.resolve_jav_imgs(img_urls_result, ps_to_poster=ps_to_poster_setting, crop_mode=crop_mode_setting, proxy_url=proxy_url)
                # process_jav_imgs 호출하여 entity.thumb 채우기
                entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls_result, proxy_url=proxy_url)
                # 팬아트 처리 (resolved된 arts 사용)
                resolved_arts = img_urls_result.get("arts", [])
                processed_fanart_count = 0
                # resolve 결과에서 poster, landscape URL 가져오기 (exclude용)
                resolved_poster_for_exclude = img_urls_result.get('poster')
                resolved_landscape_for_exclude = img_urls_result.get('landscape')
                urls_to_exclude_from_arts = {resolved_poster_for_exclude, resolved_landscape_for_exclude}
                for art_url in resolved_arts:
                    if processed_fanart_count >= max_arts: break
                    if art_url and art_url not in urls_to_exclude_from_arts:
                        processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                        if processed_art: entity.fanart.append(processed_art); processed_fanart_count += 1
                logger.debug(f"Normal Mode (DVD): Final Thumb={entity.thumb}, Fanart Count={len(entity.fanart)}")

        except Exception as e_img_proc:
            logger.exception(f"DVD: Error during image processing setup: {e_img_proc}")
        # --- 이미지 처리 설정 끝 ---

        # --- 메타데이터 파싱 시작 ---
        try:
            h1_tags = tree.xpath('//h1[@class="tag"]/text()') # h1 태그 존재 여부 확인
            if h1_tags:
                h1 = h1_tags[0]
                for ptn in cls.PTN_TEXT_SUB: h1 = ptn.sub("", h1)
                entity.tagline = SiteUtil.trans(h1, do_trans=do_trans)
            else: logger.warning("DVD: H1 title tag not found.")

            basetag = '//div[@class="detail_data"]'
            tags = tree.xpath(f"{basetag}//tr")
            tmp_premiered = None
            for tag in tags:
                key_node = tag.xpath("./th")
                value_node = tag.xpath("./td")
                if not key_node or not value_node: continue # th, td 모두 있어야 함
                key = key_node[0].text_content().strip()
                value = value_node[0].text_content().strip()
                value_node_instance = value_node[0] # 하위 태그 접근 위해

                if "品番" in key:
                    match = cls.PTN_SEARCH_REAL_NO.match(value)
                    formatted_code = value.upper() # 기본값
                    if match:
                        label = match.group("real").upper()
                        num_str = str(int(match.group("no"))).zfill(3) # 길이 고정은 일단 3으로
                        # MGS_CODE_LEN 적용 (선택적)
                        # if codelen := MGS_CODE_LEN.get(label): num_str = str(int(match.group("no"))).zfill(codelen)
                        formatted_code = f"{label}-{num_str}"
                        if entity.tag is None: entity.tag = []
                        if label not in entity.tag: entity.tag.append(label)
                    entity.title = entity.originaltitle = entity.sorttitle = formatted_code
                    ui_code_for_image = formatted_code # 최종 UI 코드 확정
                    entity.ui_code = ui_code_for_image
                    logger.debug(f"DVD: 品番 파싱 완료, ui_code_for_image='{ui_code_for_image}'")
                    continue # 다음 항목으로

                elif "商品発売日" in key:
                    try: entity.premiered = value.replace("/", "-"); entity.year = int(value[:4])
                    except Exception: pass
                elif "配信開始日" in key: tmp_premiered = value.replace("/", "-")
                elif "収録時間" in key:
                    try: entity.runtime = int(value.replace("min", "").strip())
                    except Exception: pass
                elif "出演" in key:
                    entity.actor = [EntityActor(x.strip()) for x in value_node_instance.xpath("./a/text()") if x.strip()]
                elif "監督" in key: entity.director = value
                elif "シリーズ" in key:
                    series_name = value_node_instance.xpath("./a/text()") # 링크 텍스트 우선
                    series_name = series_name[0].strip() if series_name else value
                    if entity.tag is None: entity.tag = []
                    trans_series = SiteUtil.trans(series_name, do_trans=do_trans)
                    if trans_series and trans_series not in entity.tag: entity.tag.append(trans_series)
                elif "レーベル" in key: # 스튜디오/레이블 처리
                    studio_name = value_node_instance.xpath("./a/text()") # 링크 텍스트 우선
                    studio_name = studio_name[0].strip() if studio_name else value
                    entity.studio = studio_name
                    if do_trans:
                        if studio_name in SiteUtil.av_studio: entity.studio = SiteUtil.av_studio[studio_name]
                        else: entity.studio = SiteUtil.change_html(SiteUtil.trans(studio_name))
                elif "ジャンル" in key:
                    entity.genre = []
                    for a_tag in value_node_instance.xpath("./a"):
                        tmp = a_tag.text_content().strip()
                        if "MGSだけのおまけ映像付き" in tmp or not tmp or tmp in SiteUtil.av_genre_ignore_ja: continue
                        if tmp in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[tmp])
                        else:
                            genre_tmp = SiteUtil.trans(tmp, do_trans=do_trans).replace(" ", "")
                            if genre_tmp not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_tmp)

            if entity.premiered is None and tmp_premiered is not None:
                entity.premiered = tmp_premiered
                try: entity.year = int(tmp_premiered[:4])
                except Exception: pass

            plot_nodes = tree.xpath('//*[@id="introduction"]//p[1]') # 첫번째 p 태그 우선
            if plot_nodes:
                for br in plot_nodes[0].xpath('.//br'): br.tail = "\n" + br.tail if br.tail else "\n"
                tmp = plot_nodes[0].text_content().strip()
                if not tmp and len(tree.xpath('//*[@id="introduction"]//p')) > 1: # 첫 p가 비었으면 다음 p 시도
                    plot_nodes_alt = tree.xpath('//*[@id="introduction"]//p[2]')
                    if plot_nodes_alt:
                        for br in plot_nodes_alt[0].xpath('.//br'): br.tail = "\n" + br.tail if br.tail else "\n"
                        tmp = plot_nodes_alt[0].text_content().strip()

                if tmp:
                    for ptn in cls.PTN_TEXT_SUB: tmp = ptn.sub("", tmp)
                    entity.plot = SiteUtil.trans(tmp, do_trans=do_trans)
            else: logger.warning("DVD: Plot node not found.")

            rating_tag = tree.xpath('//div[@class="user_review_head"]/p[@class="detail"]/text()')
            if rating_tag:
                match = cls.PTN_RATING.search(rating_tag[0])
                if match:
                    try:
                        rating_value = float(match.group("rating"))
                        votes = int(match.group("vote"))
                        entity.ratings = [EntityRatings(rating_value, max=5, name="mgs", votes=votes)]
                    except Exception: logger.warning("DVD: Failed to parse rating values.")

        except Exception as e_meta:
            logger.exception(f"DVD: Error during metadata parsing: {e_meta}")
        # --- 메타데이터 파싱 끝 ---


        # --- 이미지 서버 저장 로직 (메타 파싱 후, ui_code_for_image 확정됨) ---
        if use_image_server and image_mode == '4' and image_server_url and image_server_local_path and ui_code_for_image:
            logger.info(f"Saving images to Image Server for {ui_code_for_image} (DVD)...")
            ps_url = img_urls_result.get('ps') # 이미 추출된 값 사용
            pl_url = img_urls_result.get('pl')
            arts = img_urls_result.get('arts', [])
            # 위에서 결정된 final_poster_url, final_poster_crop 사용
            # (주의: 일반모드 분기에서 결정된 값은 없으므로, 이미지 서버 모드 내에서 다시 결정해야 함)
            final_poster_url_is = None; final_poster_crop_is = None
            if ps_to_poster_setting and ps_url: final_poster_url_is = ps_url
            else:
                if pl_url and ps_url:
                    loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                    if loc: final_poster_url_is = pl_url; final_poster_crop_is = loc
                    else: final_poster_url_is = ps_url
                elif ps_url: final_poster_url_is = ps_url
            logger.info(f"Image Server (DVD) Final Decision: Poster='{final_poster_url_is}'(Crop:{final_poster_crop_is})")

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
                if processed_fanart_count >= max_arts: break
                if art_url and art_url not in urls_to_exclude:
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                    if art_relative_path:
                        entity.fanart.append(f"{image_server_url}/{art_relative_path}"); processed_fanart_count += 1
            logger.info(f"Image Server (DVD): Processed {processed_fanart_count} fanarts.")
        # --- 이미지 서버 저장 로직 끝 ---


        # --- 예고편 처리 시작 ---
        # entity.extras = [] # 이미 위에서 초기화됨
        if use_extras:
            try:
                trailer_tag = tree.xpath('//*[@class="sample_movie_btn"]/a/@href')
                if trailer_tag:
                    pid = trailer_tag[0].split("/")[-1]
                    api_url = f"https://www.mgstage.com/sampleplayer/sampleRespons.php?pid={pid}"
                    # API 호출 시 Referer 추가 (필요할 수 있음)
                    api_headers = cls.headers.copy()
                    api_headers['Referer'] = url # 상세 페이지 URL을 Referer로
                    api_headers['X-Requested-With'] = 'XMLHttpRequest' # AJAX 요청처럼 보이도록
                    api_headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'

                    res_json = SiteUtil.get_response(api_url, proxy_url=proxy_url, headers=api_headers).json()
                    if res_json and res_json.get("url"):
                        trailer_base_url = res_json["url"].split(".ism")[0]
                        # 고화질 mp4 URL 시도 (확장자만 변경)
                        trailer_url = trailer_base_url + ".mp4"
                        # 필요시 다른 해상도/포맷 URL 파싱 로직 추가 가능
                        trailer_title = entity.tagline if entity.tagline else (entity.title if entity.title else code) # 제목 사용
                        entity.extras.append(EntityExtra("trailer", trailer_title, "mp4", trailer_url))
                        logger.info(f"DVD: Trailer added: {trailer_url}")
                    else: logger.warning("DVD: Trailer API response invalid.")
                else: logger.debug("DVD: Trailer button not found.")
            except Exception as e_trailer:
                logger.exception(f"DVD: Error processing trailer: {e_trailer}")
        # --- 예고편 처리 끝 ---

        return entity

    @classmethod
    def info(cls, code, **kwargs):
        # info wrapper는 각 하위 클래스가 직접 __info를 호출하도록 수정
        ret = {}
        try:
            # 자신의 __info 메소드 호출
            entity = cls.__info(code, **kwargs)
            if entity: # __info 반환값 체크 추가
                ret["ret"] = "success"
                ret["data"] = entity.as_dict()
            else: # __info가 None 등을 반환할 경우 대비
                ret["ret"] = "error"
                ret["data"] = f"Failed to get DVD info entity for {code}"
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        # else 블록 제거 (try 블록에서 ret 설정)
        return ret
