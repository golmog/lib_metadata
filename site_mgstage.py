import re
import os

from .constants import MGS_CODE_LEN, MGS_LABEL_MAP
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings, EntityThumb
from .plugin import P
from .site_util import SiteUtil

logger = P.logger


class SiteMgstage:
    site_name = "mgsdvd"
    site_char = "M"
    site_base_url = "https://www.mgstage.com"
    module_char = None
    _ps_url_cache = {} 

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/71.0.3578.98 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cookie": "coc=1;mgs_agef=1;",
    }

    PTN_SEARCH_PID = re.compile(r"\/product_detail\/(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^\d*(?P<real>[a-zA-Z]+[0-9]*)\-(?P<no>\d+)$")
    PTN_TEXT_SUB = [
        re.compile(r"【(?<=【)(?:MGSだけのおまけ映像付き|期間限定).*(?=】)】(:?\s?\+\d+分\s?)?"),
        re.compile(r"※通常版\+\d+分の特典映像付のスペシャルバージョン！"),
        re.compile(r"【(?<=【).+実施中(?=】)】"),
    ]
    PTN_RATING = re.compile(r"\s(?P<rating>[\d\.]+)点\s.+\s(?P<vote>\d+)\s件")

    @classmethod
    def get_label_from_ui_code(cls, ui_code_str: str) -> str:
        if not ui_code_str or not isinstance(ui_code_str, str): return ""
        
        ui_code_upper = ui_code_str.upper()
        if '-' in ui_code_upper:
            return ui_code_upper.split('-', 1)[0]
        else:
            return ui_code_upper 

    @classmethod
    def __search(
        cls,
        keyword,
        do_trans=True,
        proxy_url=None, image_mode="0",
        manual=False,
        priority_label_setting_str=""
        ):

        original_keyword = keyword
        temp_keyword = original_keyword.strip().lower()
        if temp_keyword:
            temp_keyword = re.sub(r'[_-]?cd\d+$', '', temp_keyword, flags=re.I)
            temp_keyword = temp_keyword.strip(' _-')
            keyword_for_url = temp_keyword

        module_query = "&type=top"

        url = f"{cls.site_base_url}/search/cSearch.php?search_word={keyword_for_url}&x=0&y=0{module_query}"
        logger.debug(f"MGStage Search URL: {url}")

        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)
        lists = tree.xpath('//div[@class="search_list"]/div/ul/li')
        # logger.debug("mgs search kwd=%s len=%d", keyword_for_url, len(lists))

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

                if item.code and item.image_url:
                    cls._ps_url_cache[item.code] = {'ps': item.image_url}
                    logger.debug(f"MGStage Search ({cls.module_char}): Cached PS for {item.code}: {item.image_url}")

                tag = node.xpath('.//a[@class="title lineclamp"]')[0]
                title = tag.text_content()
                for ptn in cls.PTN_TEXT_SUB:
                    title = ptn.sub("", title)
                item.title = item.title_ko = title.strip()

                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    item.image_url = SiteUtil.process_image_mode(_image_mode, item.image_url, proxy_url=proxy_url)
                    if do_trans:
                        item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                else:
                    item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)

                match_ui_code = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                if match_ui_code:
                    item.ui_code = match_ui_code.group("real") + "-" + match_ui_code.group("no")
                else:
                    item.ui_code = item.code[2:]

                if item.ui_code == keyword_for_url.upper():
                    item.score = 100
                elif keyword_for_url.upper().replace(item.ui_code, "").isnumeric():
                    item.score = 100
                else:
                    item.score = 60 - (len(ret) * 10)
                if item.score < 0:
                    item.score = 0

                item_dict = item.as_dict()

                item_dict['is_priority_label_site'] = False
                item_dict['site_key'] = cls.site_name

                if item_dict.get('ui_code') and priority_label_setting_str:
                    label_to_check = cls.get_label_from_ui_code(item_dict['ui_code'])
                    if label_to_check:
                        priority_labels_set = {lbl.strip().upper() for lbl in priority_label_setting_str.split(',') if lbl.strip()}
                        if label_to_check in priority_labels_set:
                            item_dict['is_priority_label_site'] = True
                            logger.debug(f"MGStage Search ({cls.module_char}): Item '{item_dict['ui_code']}' matched priority label '{label_to_check}'. Setting is_priority_label_site=True.")

                ret.append(item_dict)

                logger.debug(f"MGStage Search: Type={item_dict['content_type']}, Score={item_dict['score']}, Code={item_dict['code']}, UI Code={item_dict['ui_code']}, Title(KO)='{item_dict['title_ko']}', Priority={item_dict['is_priority_label_site']}")
            except Exception:
                logger.exception("개별 검색 결과 처리 중 예외:")
        return sorted(ret, key=lambda k: k["score"], reverse=True)

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            original_keyword = keyword
            temp_keyword = original_keyword.strip().lower()
            if temp_keyword:
                temp_keyword = re.sub(r'[_-]?cd\d+$', '', temp_keyword, flags=re.I)
                temp_keyword = temp_keyword.strip(' _-')

            data = []
            tmps = temp_keyword.upper().replace("-", " ").split()

            # --- __search 호출 시 필요한 kwargs 추출 ---
            do_trans_arg = kwargs.get('do_trans', True)
            proxy_url_arg = kwargs.get('proxy_url', None)
            image_mode_arg = kwargs.get('image_mode', "0")
            manual_arg = kwargs.get('manual', False)
            priority_label_str_arg = kwargs.get('priority_label_setting_str', "")

            if len(tmps) == 2:
                label, code_part = tmps
                numlabels = MGS_LABEL_MAP.get(label) or []
                if numlabels:
                    if label not in numlabels:
                        numlabels.append(label)
                    for idx, lab in enumerate(numlabels):
                        current_code_part = code_part
                        if codelen := MGS_CODE_LEN.get(lab):
                            try:
                                current_code_part = str(int(current_code_part)).zfill(codelen)
                            except ValueError:
                                pass

                        _d = cls.__search(f"{lab}-{current_code_part}",
                                          do_trans=do_trans_arg,
                                          proxy_url=proxy_url_arg,
                                          image_mode=image_mode_arg,
                                          manual=manual_arg,
                                          priority_label_setting_str=priority_label_str_arg)
                        if _d:
                            data += _d
                            if idx > 0:
                                numlabels.remove(lab)
                                numlabels.insert(0, lab)
                            break
            if not data:
                data = cls.__search(keyword,
                                    do_trans=do_trans_arg,
                                    proxy_url=proxy_url_arg,
                                    image_mode=image_mode_arg,
                                    manual=manual_arg,
                                    priority_label_setting_str=priority_label_str_arg)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data else "no_match"
            ret["data"] = data
        return ret


class SiteMgstageDvd(SiteMgstage):
    module_char = "C"
    # search_module_query = "&type=dvd"

    @classmethod
    def __img_urls(cls, tree):
        pl = tree.xpath('//*[@id="package"]/a/@href')
        pl = pl[0] if pl else ""

        arts = tree.xpath('//*[@id="sample-photo"]//ul/li/a/@href')
        if pl and "pb_e_" in pl:
            potential_pf = pl.replace("pb_e_", "pf_e_")
            if potential_pf not in arts:
                arts.insert(0, potential_pf)
        return {"pl": pl, "arts": arts}

    @classmethod
    def __info( 
        cls,
        code,
        do_trans=True,
        proxy_url=None,
        image_mode="0",
        max_arts=10,
        use_extras=True,
        ps_to_poster_labels_str="", 
        crop_mode_settings_str="",
        priority_label_setting_str="",
        **kwargs          
    ):
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')

        # logger.debug(f"Image Server Mode Check ({cls.module_char}): image_mode={image_mode}, use_image_server={use_image_server}")

        cached_data = cls._ps_url_cache.get(code, {}) 
        ps_url_from_search_cache = cached_data.get('ps')

        url = cls.site_base_url + f"/product/product_detail/{code[2:]}/"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)
        if tree is None:
            logger.error(f"MGStage ({cls.module_char}): Failed to get page tree for {code}. URL: {url}")
            return None

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = []; entity.fanart = []; entity.extras = []; entity.ratings = []

        ui_code_for_image = ""
        identifier_parsed = False
        original_h1_text_for_vr_check = ""

        try:
            logger.debug(f"MGStage ({cls.module_char}): Parsing metadata for {code}...")
            h1_tags = tree.xpath('//h1[@class="tag"]/text()')
            if h1_tags:
                h1_text_raw_unmodified = h1_tags[0]
                original_h1_text_for_vr_check = h1_text_raw_unmodified.strip()
                h1_text_processed = original_h1_text_for_vr_check
                for ptn in cls.PTN_TEXT_SUB: h1_text_processed = ptn.sub("", h1_text_processed)
                entity.tagline = SiteUtil.trans(h1_text_processed.strip(), do_trans=do_trans)
            else: logger.warning(f"MGStage ({cls.module_char}): H1 title tag not found for {code}.")

            info_table_xpath = '//div[@class="detail_data"]//tr'
            tr_nodes = tree.xpath(info_table_xpath)

            temp_shohin_hatsubai = None # DVD용
            temp_haishin_kaishi = None  # Ama/DVD 공용

            for tr_node in tr_nodes:
                key_node = tr_node.xpath("./th"); value_node_outer = tr_node.xpath("./td")
                if not key_node or not value_node_outer: continue
                key_text = key_node[0].text_content().strip(); value_text_content = value_node_outer[0].text_content().strip()
                value_node_instance = value_node_outer[0]

            for tr_node in tr_nodes:
                key_node = tr_node.xpath("./th"); value_node_outer = tr_node.xpath("./td")
                if not key_node or not value_node_outer: continue
                key_text = key_node[0].text_content().strip(); value_text_content = value_node_outer[0].text_content().strip()
                value_node_instance = value_node_outer[0]

                if "品番" in key_text:
                    parsed_pid_value = value_text_content
                    match_品番 = cls.PTN_SEARCH_REAL_NO.match(parsed_pid_value)
                    formatted_品番_for_assignment = parsed_pid_value

                    if match_品番:
                        label_original_case = match_品番.group("real")
                        num_str_original = match_品番.group("no")
                        num_str_filled = str(int(num_str_original)).zfill(3)
                        formatted_品番_for_assignment = f"{label_original_case}-{num_str_filled}"

                        if entity.tag is None: entity.tag = []
                        if label_original_case.upper() not in entity.tag:
                            entity.tag.append(label_original_case.upper())

                    ui_code_for_image = formatted_品番_for_assignment
                    entity.title = entity.originaltitle = entity.sorttitle = formatted_品番_for_assignment
                    entity.ui_code = formatted_品番_for_assignment
                    identifier_parsed = True
                    logger.debug(f"MGStage ({cls.module_char}): Identifier parsed as: {ui_code_for_image}")
                elif "商品発売日" in key_text:
                    if cls.module_char == 'D': # Ama (SiteMgstageAma)
                        try:
                            if not entity.premiered:
                                entity.premiered = value_text_content.replace("/", "-"); entity.year = int(value_text_content[:4])
                        except Exception: pass
                    elif cls.module_char == 'C': # Dvd (SiteMgstageDvd)
                        if value_text_content and value_text_content.lower() != "dvd未発売" and "----" not in value_text_content:
                            temp_shohin_hatsubai = value_text_content.replace("/", "-")

                elif "配信開始日" in key_text:
                    if value_text_content and "----" not in value_text_content:
                        if cls.module_char == 'D': # Ama
                            temp_haishin_kaishi = value_text_content.replace("/", "-")
                        elif cls.module_char == 'C': # Dvd
                            temp_haishin_kaishi = value_text_content.replace("/", "-")
                elif "収録時間" in key_text:
                    try: entity.runtime = int(value_text_content.replace("min", "").strip())
                    except Exception: logger.warning(f"MGStage ({cls.module_char}): Runtime parse error '{value_text_content}'")
                elif "出演" in key_text:
                    entity.actor = []
                    for actor_a_tag in value_node_instance.xpath("./a/text()"):
                        actor_name_full = actor_a_tag.strip()
                        if actor_name_full: entity.actor.append(EntityActor(actor_name_full.split(" ", 1)[0]))
                elif "監督" in key_text: entity.director = value_text_content 
                elif "シリーズ" in key_text:
                    series_a_tag = value_node_instance.xpath("./a/text()")
                    series_name = series_a_tag[0].strip() if series_a_tag else value_text_content
                    if entity.tag is None: entity.tag = []
                    trans_series = SiteUtil.trans(series_name, do_trans=do_trans)
                    if trans_series and trans_series not in entity.tag: entity.tag.append(trans_series)
                elif "レーベル" in key_text: 
                    label_a_tag = value_node_instance.xpath("./a/text()")
                    studio_name = label_a_tag[0].strip() if label_a_tag else value_text_content
                    entity.studio = SiteUtil.trans(studio_name, do_trans=do_trans) if do_trans else studio_name
                elif "ジャンル" in key_text:
                    entity.genre = []
                    for genre_a_tag in value_node_instance.xpath("./a"):
                        genre_text_ja = genre_a_tag.text_content().strip()
                        if "MGSだけのおまけ映像付き" in genre_text_ja or not genre_text_ja or genre_text_ja in SiteUtil.av_genre_ignore_ja: continue
                        if genre_text_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_text_ja])
                        else:
                            genre_ko = SiteUtil.trans(genre_text_ja, do_trans=do_trans).replace(" ", "")
                            if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)

            # 날짜 처리 (클래스별 우선순위 적용)
            if cls.module_char == 'D':
                if temp_haishin_kaishi:
                    entity.premiered = temp_haishin_kaishi
                # entity.premiered가 이미 상품출시일로 채워졌을 수도 있으므로, 배신일이 있으면 덮어씀
                if entity.premiered:
                    try: entity.year = int(entity.premiered[:4])
                    except Exception: entity.year = 0
                else: # 상품출시일도, 배신일도 없었다면
                    entity.year = 0
            elif cls.module_char == 'C': # Dvd는 상품발매일 우선
                if temp_shohin_hatsubai:
                    entity.premiered = temp_shohin_hatsubai
                elif temp_haishin_kaishi: # 상품발매일 없으면 배신일 사용
                    entity.premiered = temp_haishin_kaishi

                if entity.premiered:
                    try: entity.year = int(entity.premiered[:4])
                    except Exception: entity.year = 0
                else:
                    entity.year = 0
                    logger.warning(f"MGStage ({cls.module_char}): Premiered date could not be determined for {code}.")

            rating_p_detail_nodes = tree.xpath('//div[@class="user_review_head"]/p[@class="detail"]/text()')
            if rating_p_detail_nodes:
                match_rating_info = cls.PTN_RATING.search(rating_p_detail_nodes[0])
                if match_rating_info:
                    try:
                        rating_val = float(match_rating_info.group("rating"))
                        votes_count = int(match_rating_info.group("vote"))
                        entity.ratings = [EntityRatings(rating_val, max=5, name=cls.site_name, votes=votes_count)]
                    except Exception: logger.warning(f"MGStage ({cls.module_char}): Failed to parse rating values for {code}.")

            if not identifier_parsed:
                logger.error(f"MGStage ({cls.module_char}): CRITICAL - Failed to parse identifier for {code}.")
                ui_code_for_image = code[2:].upper().replace("_", "-") 
                entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image
                entity.ui_code = ui_code_for_image
                logger.warning(f"MGStage ({cls.module_char}): Using fallback identifier: {ui_code_for_image}")
        except Exception as e_meta_main:
            logger.exception(f"MGStage ({cls.module_char}): Major error during metadata parsing for {code}: {e_meta_main}")
            if not ui_code_for_image:
                logger.error(f"MGStage ({cls.module_char}): Returning None due to critical metadata parsing failure (no identifier).")
                return None

        label_from_ui_code_for_settings = ""
        if hasattr(entity, 'ui_code') and entity.ui_code:
            ui_code_for_image = entity.ui_code
            label_from_ui_code_for_settings = cls.get_label_from_ui_code(entity.ui_code)
            logger.debug(f"[{cls.site_name} Info] Extracted label for settings: '{label_from_ui_code_for_settings}' from ui_code '{entity.ui_code}'")
        else:
            logger.warning(f"[{cls.site_name} Info] entity.ui_code not found after parsing. Using fallback for image filenames.")
            ui_code_for_image = code[len(cls.module_char)+len(cls.site_char):].upper().replace("_", "-")

        apply_ps_to_poster_for_this_item = False
        forced_crop_mode_for_this_item = None

        # 포스터 예외처리 플래그 결정
        apply_ps_to_poster_for_this_item = False
        forced_crop_mode_for_this_item = None
        if label_from_ui_code_for_settings:
            if ps_to_poster_labels_str:
                ps_force_labels_set = {lbl.strip().upper() for lbl in ps_to_poster_labels_str.split(',') if lbl.strip()}
                if label_from_ui_code_for_settings in ps_force_labels_set:
                    apply_ps_to_poster_for_this_item = True
            if crop_mode_settings_str:
                for line in crop_mode_settings_str.splitlines():
                    if not line.strip(): continue
                    parts = [x.strip() for x in line.split(":", 1)]
                    if len(parts) == 2 and parts[0].upper() == label_from_ui_code_for_settings and parts[1].lower() in ["r", "l", "c"]:
                        forced_crop_mode_for_this_item = parts[1].lower(); break

        user_custom_poster_url = None; user_custom_landscape_url = None
        skip_default_poster_logic = False; skip_default_landscape_logic = False
        if use_image_server and image_server_local_path and image_server_url and ui_code_for_image:
            poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
            landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]
            for suffix in poster_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                if web_url: user_custom_poster_url = web_url; entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url)); skip_default_poster_logic = True; logger.debug(f"MGStage ({cls.module_char}): Using user custom poster: {web_url}"); break 
            for suffix in landscape_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                if web_url: user_custom_landscape_url = web_url; entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url)); skip_default_landscape_logic = True; logger.debug(f"MGStage ({cls.module_char}): Using user custom landscape: {web_url}"); break

        final_poster_source = None; final_poster_crop_mode = None
        final_landscape_url_source = None;
        arts_urls_for_processing = []
        mgs_special_poster_filepath = None

        # --- 기본 이미지 처리 로직 진입 조건 ---
        needs_default_image_processing = not skip_default_poster_logic or \
                                         not skip_default_landscape_logic or \
                                         (entity.fanart is None or (len(entity.fanart) < max_arts and max_arts > 0))

        if needs_default_image_processing:
            logger.debug(f"MGStage ({cls.module_char}): Running default image logic for {code} (P_skip:{skip_default_poster_logic}, PL_skip:{skip_default_landscape_logic}, FanartNeed:{entity.fanart is None or (len(entity.fanart) < max_arts and max_arts > 0)}).")

            img_urls_from_page = cls.__img_urls(tree)
            pl_url = img_urls_from_page.get('pl')
            all_arts = img_urls_from_page.get('arts', [])

            # 랜드스케이프 결정 (기본: pl_url)
            if not skip_default_landscape_logic and pl_url:
                final_landscape_url_source = pl_url

            # 포스터 결정 로직 (사용자 지정 포스터가 없을 때)
            if not skip_default_poster_logic:
                logger.debug(f"[{cls.site_name} Info] Default poster logic started. apply_ps_flag={apply_ps_to_poster_for_this_item}, forced_crop_mode='{forced_crop_mode_for_this_item}'")

                # 1. "포스터 예외처리 2" (사용자 지정 크롭 모드)
                if forced_crop_mode_for_this_item and pl_url:
                    logger.info(f"[{cls.site_name} Info] Poster determined by 예외처리 2 (크롭 지정: '{forced_crop_mode_for_this_item}'). Using PL: {pl_url}")
                    final_poster_source = pl_url
                    final_poster_crop_mode = forced_crop_mode_for_this_item

                # --- 위에서 포스터가 결정되지 않았고, PS Cache가 있는 경우 ---
                if ps_url_from_search_cache: 
                    logger.debug(f"[{cls.site_name} Info] PS cache exists. Evaluating PS-based poster options.")

                    # 2. "포스터 예외처리 1" (PS 강제 사용)
                    if apply_ps_to_poster_for_this_item:
                        logger.info(f"[{cls.site_name} Info] Poster determined by 예외처리 1 (PS 강제). Using PS: {ps_url_from_search_cache}")
                        final_poster_source = ps_url_from_search_cache
                        final_poster_crop_mode = None

                    # --- 일반적인 포스터 결정 로직 ---
                    else:
                        logger.debug(f"[{cls.site_name} Info] No forced settings applied (with PS). Applying general poster determination.")

                        specific_arts_candidates = []
                        if all_arts:
                            if all_arts[0]: specific_arts_candidates.append(all_arts[0])
                            if len(all_arts) > 1 and all_arts[-1] != all_arts[0]:
                                specific_arts_candidates.append(all_arts[-1])

                        # 3. is_hq_poster 검사
                        if pl_url and SiteUtil.is_portrait_high_quality_image(pl_url, proxy_url=proxy_url):
                            if SiteUtil.is_hq_poster(ps_url_from_search_cache, pl_url, proxy_url=proxy_url):
                                final_poster_source = pl_url
                                # final_poster_crop_mode = None
                                logger.debug(f"[{cls.site_name} Info] Poster set to PL by is_hq_poster: {pl_url}")

                        if final_poster_source is None and specific_arts_candidates:
                            for art_candidate in specific_arts_candidates:
                                if art_candidate == pl_url: 
                                    continue
                                if SiteUtil.is_portrait_high_quality_image(art_candidate, proxy_url=proxy_url):
                                    if SiteUtil.is_hq_poster(ps_url_from_search_cache, art_candidate, proxy_url=proxy_url):
                                        final_poster_source = art_candidate
                                        # final_poster_crop_mode = None
                                        logger.debug(f"[{cls.site_name} Info] Poster set to Art by is_hq_poster: {art_candidate}")
                                        break

                        # 4. MGS Special 처리
                        if (final_poster_source is None or final_poster_source == ps_url_from_search_cache) and pl_url:
                            logger.debug(f"[{cls.site_name} Info] Attempting MGS style processing for PL ('{pl_url}') & PS ('{ps_url_from_search_cache}').")
                            temp_filepath, _, _ = SiteUtil.get_mgs_half_pl_poster_info_local(ps_url_from_search_cache, pl_url, proxy_url=proxy_url)
                            if temp_filepath and os.path.exists(temp_filepath):
                                mgs_special_poster_filepath = temp_filepath
                                final_poster_source = mgs_special_poster_filepath
                                final_poster_crop_mode = None
                                logger.debug(f"[{cls.site_name} Info] MGS style processing successful. Using temp file: {mgs_special_poster_filepath}")

                        # 5. has_hq_poster 검사
                        if final_poster_source is None:
                            if pl_url:
                                crop_pos = SiteUtil.has_hq_poster(ps_url_from_search_cache, pl_url, proxy_url=proxy_url)
                                if crop_pos:
                                    final_poster_source = pl_url
                                    final_poster_crop_mode = crop_pos

                            if final_poster_source is None and specific_arts_candidates:
                                for art_candidate in specific_arts_candidates:
                                    if art_candidate == pl_url: continue
                                    crop_pos_art = SiteUtil.has_hq_poster(ps_url_from_search_cache, art_candidate, proxy_url=proxy_url)
                                    if crop_pos_art:
                                        final_poster_source = art_candidate
                                        final_poster_crop_mode = crop_pos_art; break

                        # 6. 최종 폴백: PS 사용
                        if final_poster_source is None:
                            logger.debug(f"[{cls.site_name} Info] General/MGS special failed (with PS). Falling back to PS.")
                            final_poster_source = ps_url_from_search_cache
                            final_poster_crop_mode = None

                else:
                    logger.debug(f"[{cls.site_name} Info] No PS url found. Skipping poster processing")

                # 최종 결정된 포스터 정보 로깅
                if final_poster_source:
                    logger.debug(f"[{cls.site_name} Info] Final Poster Decision - Source type: {type(final_poster_source)}, Crop: {final_poster_crop_mode}")
                    if isinstance(final_poster_source, str): logger.debug(f"  Source URL/Path: {final_poster_source[:150]}")
                else:
                    logger.error(f"[{cls.site_name} Info] CRITICAL: No poster source could be determined for {code}")
                    final_poster_source = None
                    final_poster_crop_mode = None

            # 팬아트 목록 결정
            arts_urls_for_processing = []
            if all_arts:
                temp_fanart_list_mg = []
                sources_to_exclude_for_fanart_mg = set()
                if final_landscape_url_source: sources_to_exclude_for_fanart_mg.add(final_landscape_url_source)
                if final_poster_source and isinstance(final_poster_source, str) and final_poster_source.startswith("http"):
                    sources_to_exclude_for_fanart_mg.add(final_poster_source)

                if pl_url and mgs_special_poster_filepath and final_poster_source == mgs_special_poster_filepath:
                    sources_to_exclude_for_fanart_mg.add(pl_url)

                for art_url_item_mg in all_arts:
                    if len(temp_fanart_list_mg) >= max_arts: break
                    if art_url_item_mg and art_url_item_mg not in sources_to_exclude_for_fanart_mg:
                        if art_url_item_mg not in temp_fanart_list_mg:
                            temp_fanart_list_mg.append(art_url_item_mg)
                arts_urls_for_processing = temp_fanart_list_mg

        logger.debug(f"MGStage ({cls.module_char}): Final Images Decision - Poster='{str(final_poster_source)[:100]}...' (Crop='{final_poster_crop_mode}'), Landscape='{final_landscape_url_source}', Fanarts_to_process({len(arts_urls_for_processing)})='{arts_urls_for_processing[:3]}...'")

        # --- 이미지 최종 적용 (서버 저장 또는 프록시) ---
        if use_image_server and image_mode == '4' and ui_code_for_image:
            # 포스터 저장
            if not skip_default_poster_logic and final_poster_source:
                if not any(t.aspect == 'poster' for t in entity.thumb):
                    p_path = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                    if p_path: entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path}"))
            # 랜드스케이프 저장
            if not skip_default_landscape_logic and final_landscape_url_source:
                if not any(t.aspect == 'landscape' for t in entity.thumb):
                    pl_path = SiteUtil.save_image_to_server_path(final_landscape_url_source, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                    if pl_path: entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path}"))
            # 팬아트 저장
            if arts_urls_for_processing:
                if entity.fanart is None: entity.fanart = []
                current_fanart_urls_on_server = {fanart_url for fanart_url in entity.fanart if isinstance(fanart_url, str) and fanart_url.startswith(image_server_url)}
                processed_fanart_count_server = len(current_fanart_urls_on_server)

                for idx, art_url in enumerate(arts_urls_for_processing):
                    if processed_fanart_count_server >= max_arts: break
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                    if art_relative_path:
                        full_art_url_server = f"{image_server_url}/{art_relative_path}"
                        if full_art_url_server not in current_fanart_urls_on_server:
                            entity.fanart.append(full_art_url_server)
                            current_fanart_urls_on_server.add(full_art_url_server)
                            processed_fanart_count_server +=1

        if use_extras:
            try:
                trailer_sample_btn = tree.xpath('//*[@class="sample_movie_btn"]/a/@href')
                if trailer_sample_btn:
                    pid_trailer = trailer_sample_btn[0].split("/")[-1]
                    api_url_trailer = f"https://www.mgstage.com/sampleplayer/sampleRespons.php?pid={pid_trailer}"
                    api_headers_trailer = cls.headers.copy(); api_headers_trailer['Referer'] = url 
                    api_headers_trailer['X-Requested-With'] = 'XMLHttpRequest'; api_headers_trailer['Accept'] = 'application/json, text/javascript, */*; q=0.01'
                    res_json_trailer = SiteUtil.get_response(api_url_trailer, proxy_url=proxy_url, headers=api_headers_trailer).json()
                    if res_json_trailer and res_json_trailer.get("url"):
                        trailer_base = res_json_trailer["url"].split(".ism")[0]; trailer_final_url = trailer_base + ".mp4"
                        trailer_title_text = entity.tagline if entity.tagline else entity.ui_code 
                        entity.extras.append(EntityExtra("trailer", trailer_title_text, "mp4", trailer_final_url))
            except Exception as e_trailer_proc_dvd:
                logger.exception(f"MGStage ({cls.module_char}): Error processing trailer: {e_trailer_proc_dvd}")

        final_entity = entity
        if entity.originaltitle:
            try: final_entity = SiteUtil.shiroutoname_info(entity)
            except Exception as e_shirouto_proc: logger.exception(f"MGStage (Ama): Shiroutoname error: {e_shirouto_proc}")
        else: logger.warning(f"MGStage (Ama): Skipping Shiroutoname (no originaltitle for {code}).")
        entity = final_entity

        logger.info(f"MGStage ({cls.module_char}): __info finished for {code}. UI Code: {ui_code_for_image}, PSkip: {skip_default_poster_logic}, PLSkip: {skip_default_landscape_logic}, Thumbs: {len(entity.thumb)}, Fanarts: {len(entity.fanart)}")
        return entity


    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs) 
            if entity: ret["ret"] = "success"; ret["data"] = entity.as_dict()
            else: ret["ret"] = "error"; ret["data"] = f"Failed to get MGStage ({cls.module_char}) info for {code}"
        except Exception as e: ret["ret"] = "exception"; ret["data"] = str(e); logger.exception(f"MGStage ({cls.module_char}) info error: {e}")
        return ret
