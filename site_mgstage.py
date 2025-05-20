import re
import os

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

        module_query = "&type=top"

        url = f"{cls.site_base_url}/search/cSearch.php?search_word={keyword}&x=0&y=0{module_query}"
        logger.debug(f"Using search URL: {url}")
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
                    item.score = 0
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
    search_module_query = "&type=amateur" # Ama 검색용 파라미터

    @classmethod
    def __img_urls(cls, tree):
        pl = tree.xpath('//*[@id="package"]/a/@href')
        pl = pl[0] if pl else ""
        if not pl: logger.warning(f"MGStage ({cls.module_char}): 이미지 URL (PL) 없음")
        
        ps = tree.xpath('//div[@class="detail_photo"]//img/@src')
        ps = ps[0] if ps else ""
        if not ps and pl and "pb_e_" in pl: 
            ps = pl.replace("pb_e_", "pf_o1_")
        if not ps : logger.warning(f"MGStage ({cls.module_char}): 이미지 URL (PS) 없음")
        
        arts = tree.xpath('//*[@id="sample-photo"]//ul/li/a/@href')
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
        
        logger.debug(f"Image Server Mode Check ({cls.module_char}): image_mode={image_mode}, use_image_server={use_image_server}")

        url = cls.site_base_url + f"/product/product_detail/{code[2:]}/"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)
        if tree is None: 
            logger.error(f"MGStage ({cls.module_char}): Failed to get page tree for {code}. URL: {url}")
            return None

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = []; entity.fanart = []; entity.extras = []
        ui_code_for_image = ""
        
        identifier_parsed = False
        try:
            logger.debug(f"MGStage ({cls.module_char}): Parsing metadata for {code}...")
            h1_tags = tree.xpath('//h1[@class="tag"]/text()')
            if h1_tags:
                h1_text = h1_tags[0]
                for ptn in cls.PTN_TEXT_SUB: h1_text = ptn.sub("", h1_text)
                entity.tagline = SiteUtil.trans(h1_text, do_trans=do_trans)
            else: logger.warning(f"MGStage ({cls.module_char}): H1 title tag not found for {code}.")

            info_table_xpath = '//div[@class="detail_data"]//tr'
            tr_nodes = tree.xpath(info_table_xpath)
            tmp_premiered_haishin = None 

            for tr_node in tr_nodes:
                key_node = tr_node.xpath("./th"); value_node_outer = tr_node.xpath("./td")
                if not key_node or not value_node_outer: continue
                key_text = key_node[0].text_content().strip(); value_text_content = value_node_outer[0].text_content().strip()
                value_node_instance = value_node_outer[0]

                if "品番" in key_text: 
                    match_品番 = cls.PTN_SEARCH_REAL_NO.match(value_text_content); formatted_品番 = value_text_content.upper()
                    if match_品番:
                        label = match_品番.group("real").upper(); num_str = str(int(match_品番.group("no"))).zfill(3)
                        formatted_品番 = f"{label}-{num_str}"
                        if entity.tag is None: entity.tag = []; 
                        if label not in entity.tag: entity.tag.append(label)
                    ui_code_for_image = formatted_品番; entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image
                    entity.ui_code = ui_code_for_image; identifier_parsed = True
                    logger.info(f"MGStage ({cls.module_char}): Identifier parsed as: {ui_code_for_image}")
                elif "商品発売日" in key_text: # Ama는 이 필드가 없을 수도 있지만, 있다면 참고용
                    try: 
                        if not entity.premiered: # 배신일이 아직 없으면 이걸로라도 채움 (그러나 Ama는 배신일 우선)
                            entity.premiered = value_text_content.replace("/", "-"); entity.year = int(value_text_content[:4])
                    except Exception: pass
                elif "配信開始日" in key_text: 
                    tmp_premiered_haishin = value_text_content.replace("/", "-")
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
                    entity.studio = SiteUtil.trans(studio_name, do_trans=do_trans) if do_trans else studio_name # AV_Studio 맵핑은 상위에서
                elif "ジャンル" in key_text:
                    entity.genre = []
                    for genre_a_tag in value_node_instance.xpath("./a"):
                        genre_text_ja = genre_a_tag.text_content().strip()
                        if "MGSだけのおまけ映像付き" in genre_text_ja or not genre_text_ja or genre_text_ja in SiteUtil.av_genre_ignore_ja: continue
                        if genre_text_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_text_ja])
                        else:
                            genre_ko = SiteUtil.trans(genre_text_ja, do_trans=do_trans).replace(" ", "")
                            if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
            
            if tmp_premiered_haishin: # Ama는 배신일 우선
                entity.premiered = tmp_premiered_haishin
                try: entity.year = int(tmp_premiered_haishin[:4])
                except Exception: entity.year = 0 
            elif entity.premiered is None : entity.year = 0 # 상품출시일도 없었다면
            
            plot_p_nodes = tree.xpath('//*[@id="introduction"]//p[1]')
            if plot_p_nodes:
                plot_p_node = plot_p_nodes[0]
                for br_tag in plot_p_node.xpath('.//br'): br_tag.tail = "\n" + br_tag.tail if br_tag.tail else "\n"
                plot_text_raw = plot_p_node.text_content().strip()
                if not plot_text_raw and len(tree.xpath('//*[@id="introduction"]//p')) > 1:
                    plot_p_nodes_alt = tree.xpath('//*[@id="introduction"]//p[2]')
                    if plot_p_nodes_alt:
                        for br_tag_alt in plot_p_nodes_alt[0].xpath('.//br'): br_tag_alt.tail = "\n" + br_tag_alt.tail if br_tag_alt.tail else "\n"
                        plot_text_raw = plot_p_nodes_alt[0].text_content().strip()
                if plot_text_raw:
                    for ptn_sub in cls.PTN_TEXT_SUB: plot_text_raw = ptn_sub.sub("", plot_text_raw)
                    entity.plot = SiteUtil.trans(plot_text_raw, do_trans=do_trans)
            else: logger.warning(f"MGStage ({cls.module_char}): Plot node not found for {code}.")

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
        
        user_custom_poster_url = None; user_custom_landscape_url = None
        skip_default_poster_logic = False; skip_default_landscape_logic = False
        if use_image_server and image_server_local_path and image_server_url and ui_code_for_image:
            poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
            landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]
            for suffix in poster_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                if web_url: user_custom_poster_url = web_url; entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url)); skip_default_poster_logic = True; logger.info(f"MGStage ({cls.module_char}): Using user custom poster: {web_url}"); break 
            for suffix in landscape_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                if web_url: user_custom_landscape_url = web_url; entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url)); skip_default_landscape_logic = True; logger.info(f"MGStage ({cls.module_char}): Using user custom landscape: {web_url}"); break
        
        final_poster_source = None; final_poster_crop_mode = None
        final_landscape_url_source = None; 
        arts_urls_for_processing = [] 
        ps_url_detail_page_default = None; pl_url_detail_page_default = None

        if not skip_default_poster_logic or not skip_default_landscape_logic:
            logger.debug(f"MGStage ({cls.module_char}): Running default image logic (P_skip:{skip_default_poster_logic}, PL_skip:{skip_default_landscape_logic}).")
            try:
                img_urls_result_default = cls.__img_urls(tree) 
                ps_url_detail_page_default = img_urls_result_default.get('ps') 
                pl_url_detail_page_default = img_urls_result_default.get('pl') 
                arts_urls_page_default = img_urls_result_default.get('arts', []) 

                if not skip_default_poster_logic:
                    resolved_poster_url_step1 = None; resolved_crop_mode_step1 = None
                    # 1. PS 강제 설정
                    if ps_to_poster_setting and ps_url_detail_page_default: 
                        resolved_poster_url_step1 = ps_url_detail_page_default
                    # 2. PL 크롭 설정
                    elif crop_mode_setting and pl_url_detail_page_default: 
                        resolved_poster_url_step1 = pl_url_detail_page_default
                        resolved_crop_mode_step1 = crop_mode_setting
                    # 3. PS와 PL 모두 존재 시 HQ 로직
                    elif pl_url_detail_page_default and ps_url_detail_page_default:
                        loc = SiteUtil.has_hq_poster(ps_url_detail_page_default, pl_url_detail_page_default, proxy_url=proxy_url)
                        if loc: 
                            resolved_poster_url_step1 = pl_url_detail_page_default
                            resolved_crop_mode_step1 = loc
                        else: 
                            if SiteUtil.is_hq_poster(ps_url_detail_page_default, pl_url_detail_page_default, proxy_url=proxy_url): 
                                resolved_poster_url_step1 = pl_url_detail_page_default
                            else: 
                                resolved_poster_url_step1 = ps_url_detail_page_default
                    # 4. PS만 존재 시
                    elif ps_url_detail_page_default: 
                        resolved_poster_url_step1 = ps_url_detail_page_default
                    # 5. PL만 존재하고 크롭 설정 있을 시
                    elif pl_url_detail_page_default and crop_mode_setting : 
                        resolved_poster_url_step1 = pl_url_detail_page_default
                        resolved_crop_mode_step1 = crop_mode_setting
                    
                    # --- Specific Art 후보들을 포스터로 사용 시도 (구조 변경) ---
                    # (일반 로직에서 포스터가 아직 결정되지 않았고, 아트가 있으며, PS 강제 설정이 아닐 때)
                    if not resolved_poster_url_step1 and arts_urls_page_default and not ps_to_poster_setting:
                        specific_art_candidates_ama = []
                        if arts_urls_page_default:
                            # 첫 번째 아트를 specific 후보로 추가
                            if arts_urls_page_default[0] not in specific_art_candidates_ama:
                                specific_art_candidates_ama.append(arts_urls_page_default[0])
                            
                            # 마지막 아트가 첫 번째 아트와 다르고, 리스트에 이미 없다면 추가
                            if len(arts_urls_page_default) > 1 and \
                               arts_urls_page_default[-1] != arts_urls_page_default[0] and \
                               arts_urls_page_default[-1] not in specific_art_candidates_ama:
                                specific_art_candidates_ama.append(arts_urls_page_default[-1])
                        
                        logger.debug(f"MGStage ({cls.module_char}, Ama): Specific art candidates for poster: {specific_art_candidates_ama}")

                        if ps_url_detail_page_default:
                            for sp_candidate_ama in specific_art_candidates_ama:
                                if SiteUtil.is_hq_poster(ps_url_detail_page_default, sp_candidate_ama, proxy_url=proxy_url):
                                    logger.info(f"MGStage ({cls.module_char}, Ama): Specific art ('{sp_candidate_ama}') chosen as poster.")
                                    resolved_poster_url_step1 = sp_candidate_ama
                                    resolved_crop_mode_step1 = None 
                                    break 

                    # 최종 Fallback (그래도 포스터 없으면 PS 사용)
                    if not resolved_poster_url_step1 and ps_url_detail_page_default: 
                        resolved_poster_url_step1 = ps_url_detail_page_default

                    # MGS 스타일 특별 처리
                    mgs_special_poster_filepath = None
                    attempt_mgs_special_local = False
                    if pl_url_detail_page_default and ps_url_detail_page_default and \
                       not ps_to_poster_setting and resolved_poster_url_step1 == ps_url_detail_page_default:
                        attempt_mgs_special_local = True

                    if attempt_mgs_special_local:
                        temp_filepath, _, _ = SiteUtil.get_mgs_half_pl_poster_info_local(ps_url_detail_page_default, pl_url_detail_page_default, proxy_url=proxy_url)
                        if temp_filepath and os.path.exists(temp_filepath): mgs_special_poster_filepath = temp_filepath

                    if mgs_special_poster_filepath: 
                        final_poster_source = mgs_special_poster_filepath
                        final_poster_crop_mode = None
                    else: 
                        final_poster_source = resolved_poster_url_step1
                        final_poster_crop_mode = resolved_crop_mode_step1

                if not skip_default_landscape_logic: final_landscape_url_source = pl_url_detail_page_default
                arts_urls_for_processing = arts_urls_page_default

                # --- 이미지 최종 처리 및 entity에 추가 (이미지 서버 사용 안 할 때) ---
                if not (use_image_server and image_mode == '4'):
                    # 포스터 추가
                    if final_poster_source and not skip_default_poster_logic and not any(t.aspect == 'poster' for t in entity.thumb):
                        processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                        if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                    # 랜드스케이프 추가
                    if final_landscape_url_source and not skip_default_landscape_logic and not any(t.aspect == 'landscape' for t in entity.thumb):
                        processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url_source, proxy_url=proxy_url)
                        if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))
                    
                    # 팬아트 추가
                    if arts_urls_for_processing:
                        if entity.fanart is None: entity.fanart = []
                        processed_fanart_count = len(entity.fanart) 
                        sources_to_exclude = {final_poster_source, final_landscape_url_source}
                        if pl_url_detail_page_default and mgs_special_poster_filepath and final_poster_source == mgs_special_poster_filepath:
                            sources_to_exclude.add(pl_url_detail_page_default)
                        
                        for art_url in arts_urls_for_processing:
                            if processed_fanart_count >= max_arts: break
                            if art_url and art_url not in sources_to_exclude:
                                processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                                if processed_art and processed_art not in entity.fanart: 
                                    entity.fanart.append(processed_art)
                                    processed_fanart_count += 1
            except Exception as e_img_proc_default:
                logger.exception(f"MGStage ({cls.module_char}, Ama): Error during default image processing: {e_img_proc_default}")

        if use_image_server and image_mode == '4' and ui_code_for_image:
            logger.info(f"MGStage ({cls.module_char}): Saving images to Image Server for {ui_code_for_image}...")
            #if ps_url_detail_page_default:
            #    SiteUtil.save_image_to_server_path(ps_url_detail_page_default, 'ps', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
            if not skip_default_poster_logic and final_poster_source:
                p_path = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                if p_path and not any(t.aspect == 'poster' and t.value.endswith(p_path) for t in entity.thumb): entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path}"))
            if not skip_default_landscape_logic and final_landscape_url_source:
                pl_path = SiteUtil.save_image_to_server_path(final_landscape_url_source, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                if pl_path and not any(t.aspect == 'landscape' and t.value.endswith(pl_path) for t in entity.thumb): entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path}"))
            if arts_urls_for_processing:
                processed_fanart_count_server = 0; sources_to_exclude_server = {final_poster_source, final_landscape_url_source}
                if pl_url_detail_page_default and 'mgs_special_poster_filepath' in locals() and mgs_special_poster_filepath and final_poster_source == mgs_special_poster_filepath:
                    sources_to_exclude_server.add(pl_url_detail_page_default)
                for idx, art_url in enumerate(arts_urls_for_processing):
                    if processed_fanart_count_server >= max_arts: break
                    if art_url and art_url not in sources_to_exclude_server:
                        art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                        if art_relative_path: entity.fanart.append(f"{image_server_url}/{art_relative_path}"); processed_fanart_count_server += 1
        
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
                        trailer_title_text = entity.tagline if entity.tagline else (entity.title if entity.title else code) 
                        entity.extras.append(EntityExtra("trailer", trailer_title_text, "mp4", trailer_final_url))
            except Exception as e_trailer_proc:
                logger.exception(f"MGStage ({cls.module_char}): Error processing trailer: {e_trailer_proc}")
        
        if cls.module_char == 'D': 
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


class SiteMgstageDvd(SiteMgstage):
    module_char = "C"
    search_module_query = "&type=dvd" # Dvd 검색용 파라미터

    @classmethod
    def __img_urls(cls, tree):
        ps = tree.xpath('//div[@class="detail_photo"]//img/@src')
        ps = ps[0] if ps else ""
        if not ps: logger.warning(f"MGStage ({cls.module_char}): 이미지 URL (PS) 없음")
        
        pl = tree.xpath('//*[@id="package"]/a/@href')
        pl = pl[0] if pl else ""
        # Dvd는 Ama와 같은 pf_e_ 변형 없음
        
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
        **kwargs          
    ):
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown')
        ps_to_poster_setting = ps_to_poster
        crop_mode_setting = crop_mode
        
        logger.debug(f"Image Server Mode Check ({cls.module_char}): image_mode={image_mode}, use_image_server={use_image_server}")

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

        try:
            logger.debug(f"MGStage ({cls.module_char}): Parsing metadata for {code}...")
            h1_tags = tree.xpath('//h1[@class="tag"]/text()')
            if h1_tags:
                h1_text = h1_tags[0]
                for ptn in cls.PTN_TEXT_SUB: h1_text = ptn.sub("", h1_text)
                entity.tagline = SiteUtil.trans(h1_text, do_trans=do_trans)
            else: logger.warning(f"MGStage ({cls.module_char}): H1 title tag not found for {code}.")

            info_table_xpath = '//div[@class="detail_data"]//tr'
            tr_nodes = tree.xpath(info_table_xpath)
            
            temp_shohin_hatsubai = None
            temp_haishin_kaishi = None

            for tr_node in tr_nodes:
                key_node = tr_node.xpath("./th"); value_node_outer = tr_node.xpath("./td")
                if not key_node or not value_node_outer: continue
                key_text = key_node[0].text_content().strip(); value_text_content = value_node_outer[0].text_content().strip()
                value_node_instance = value_node_outer[0]

                if "品番" in key_text: 
                    match_品番 = cls.PTN_SEARCH_REAL_NO.match(value_text_content); formatted_品番 = value_text_content.upper()
                    if match_品番:
                        label = match_品番.group("real").upper(); num_str = str(int(match_品番.group("no"))).zfill(3)
                        formatted_品番 = f"{label}-{num_str}"
                        if entity.tag is None: entity.tag = []; 
                        if label not in entity.tag: entity.tag.append(label)
                    ui_code_for_image = formatted_品番; entity.title = entity.originaltitle = entity.sorttitle = ui_code_for_image
                    entity.ui_code = ui_code_for_image; identifier_parsed = True
                    logger.info(f"MGStage ({cls.module_char}): Identifier parsed as: {ui_code_for_image}")
                elif "商品発売日" in key_text:
                    if value_text_content and value_text_content.lower() != "dvd未発売" and "----" not in value_text_content:
                        temp_shohin_hatsubai = value_text_content.replace("/", "-")
                elif "配信開始日" in key_text:
                    if value_text_content and "----" not in value_text_content:
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

            if temp_shohin_hatsubai: # 1순위: 상품 발매일
                entity.premiered = temp_shohin_hatsubai
            elif temp_haishin_kaishi: # 2순위: 전송 개시일
                entity.premiered = temp_haishin_kaishi
            
            if entity.premiered:
                try: entity.year = int(entity.premiered[:4])
                except Exception: entity.year = 0
            else:
                entity.year = 0
                logger.warning(f"MGStage ({cls.module_char}): Premiered date could not be determined for {code}.")
            
            plot_p_nodes = tree.xpath('//*[@id="introduction"]//p[1]')
            if plot_p_nodes:
                plot_p_node = plot_p_nodes[0]
                for br_tag in plot_p_node.xpath('.//br'): br_tag.tail = "\n" + br_tag.tail if br_tag.tail else "\n"
                plot_text_raw = plot_p_node.text_content().strip()
                if not plot_text_raw and len(tree.xpath('//*[@id="introduction"]//p')) > 1:
                    plot_p_nodes_alt = tree.xpath('//*[@id="introduction"]//p[2]')
                    if plot_p_nodes_alt:
                        for br_tag_alt in plot_p_nodes_alt[0].xpath('.//br'): br_tag_alt.tail = "\n" + br_tag_alt.tail if br_tag_alt.tail else "\n"
                        plot_text_raw = plot_p_nodes_alt[0].text_content().strip()
                if plot_text_raw:
                    for ptn_sub in cls.PTN_TEXT_SUB: plot_text_raw = ptn_sub.sub("", plot_text_raw)
                    entity.plot = SiteUtil.trans(plot_text_raw, do_trans=do_trans)
            else: logger.warning(f"MGStage ({cls.module_char}): Plot node not found for {code}.")

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
        except Exception as e_meta_main_dvd:
            logger.exception(f"MGStage ({cls.module_char}): Major error during metadata parsing for {code}: {e_meta_main_dvd}")
            if not ui_code_for_image: return None

        user_custom_poster_url = None; user_custom_landscape_url = None
        skip_default_poster_logic = False; skip_default_landscape_logic = False
        if use_image_server and image_server_local_path and image_server_url and ui_code_for_image:
            poster_suffixes = ["_p_user.jpg", "_p_user.png", "_p_user.webp"]
            landscape_suffixes = ["_pl_user.jpg", "_pl_user.png", "_pl_user.webp"]
            for suffix in poster_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                if web_url: user_custom_poster_url = web_url; entity.thumb.append(EntityThumb(aspect="poster", value=user_custom_poster_url)); skip_default_poster_logic = True; logger.info(f"MGStage ({cls.module_char}): Using user custom poster: {web_url}"); break 
            for suffix in landscape_suffixes:
                _, web_url = SiteUtil.get_user_custom_image_paths(image_server_local_path, image_path_segment, ui_code_for_image, suffix, image_server_url)
                if web_url: user_custom_landscape_url = web_url; entity.thumb.append(EntityThumb(aspect="landscape", value=user_custom_landscape_url)); skip_default_landscape_logic = True; logger.info(f"MGStage ({cls.module_char}): Using user custom landscape: {web_url}"); break
        
        final_poster_source = None; final_poster_crop_mode = None
        final_landscape_url_source = None; 
        arts_urls_for_processing = [] 
        ps_url_detail_page_default = None; pl_url_detail_page_default = None

        if not skip_default_poster_logic or not skip_default_landscape_logic:
            logger.debug(f"MGStage ({cls.module_char}): Running default image logic (P_skip:{skip_default_poster_logic}, PL_skip:{skip_default_landscape_logic}).")
            try:
                img_urls_result_default = cls.__img_urls(tree) 
                ps_url_detail_page_default = img_urls_result_default.get('ps') 
                pl_url_detail_page_default = img_urls_result_default.get('pl') 
                arts_urls_page_default = img_urls_result_default.get('arts', []) 

                if not skip_default_poster_logic:
                    resolved_poster_url_step1 = None; resolved_crop_mode_step1 = None
                    # 1. PS 강제 설정
                    if ps_to_poster_setting and ps_url_detail_page_default: 
                        resolved_poster_url_step1 = ps_url_detail_page_default
                    # 2. PL 크롭 설정
                    elif crop_mode_setting and pl_url_detail_page_default: 
                        resolved_poster_url_step1 = pl_url_detail_page_default
                        resolved_crop_mode_step1 = crop_mode_setting
                    # 3. PS와 PL 모두 존재 시 HQ 로직
                    elif pl_url_detail_page_default and ps_url_detail_page_default:
                        loc = SiteUtil.has_hq_poster(ps_url_detail_page_default, pl_url_detail_page_default, proxy_url=proxy_url)
                        if loc: 
                            resolved_poster_url_step1 = pl_url_detail_page_default
                            resolved_crop_mode_step1 = loc
                        else: 
                            if SiteUtil.is_hq_poster(ps_url_detail_page_default, pl_url_detail_page_default, proxy_url=proxy_url): 
                                resolved_poster_url_step1 = pl_url_detail_page_default
                            else: 
                                resolved_poster_url_step1 = ps_url_detail_page_default
                    # 4. PS만 존재 시
                    elif ps_url_detail_page_default: 
                        resolved_poster_url_step1 = ps_url_detail_page_default
                    # 5. PL만 존재하고 크롭 설정 있을 시
                    elif pl_url_detail_page_default and crop_mode_setting : 
                        resolved_poster_url_step1 = pl_url_detail_page_default
                        resolved_crop_mode_step1 = crop_mode_setting
                    
                    # --- Specific Art 후보들을 포스터로 사용 시도 (기존 로직 확장) ---
                    # (일반 로직에서 포스터가 아직 결정되지 않았고, 아트가 있으며, PS 강제 설정이 아닐 때)
                    if not resolved_poster_url_step1 and arts_urls_page_default and not ps_to_poster_setting:
                        
                        specific_art_candidates_mg = []
                        if arts_urls_page_default:
                            # 첫 번째 아트를 specific 후보로 추가
                            if arts_urls_page_default[0] not in specific_art_candidates_mg:
                                specific_art_candidates_mg.append(arts_urls_page_default[0])
                            
                            # 마지막 아트가 첫 번째 아트와 다르고, 리스트에 이미 없다면 추가
                            if len(arts_urls_page_default) > 1 and \
                               arts_urls_page_default[-1] != arts_urls_page_default[0] and \
                               arts_urls_page_default[-1] not in specific_art_candidates_mg:
                                specific_art_candidates_mg.append(arts_urls_page_default[-1])
                        
                        logger.debug(f"MGStage ({cls.module_char}): Specific art candidates for poster: {specific_art_candidates_mg}")

                        if ps_url_detail_page_default:
                            for sp_candidate_mg in specific_art_candidates_mg:
                                if SiteUtil.is_hq_poster(ps_url_detail_page_default, sp_candidate_mg, proxy_url=proxy_url):
                                    logger.info(f"MGStage ({cls.module_char}): Specific art ('{sp_candidate_mg}') chosen as poster based on HQ check with PS ('{ps_url_detail_page_default}').")
                                    resolved_poster_url_step1 = sp_candidate_mg
                                    resolved_crop_mode_step1 = None
                                    break

                    # 최종 Fallback (그래도 포스터 없으면 PS 사용)
                    if not resolved_poster_url_step1 and ps_url_detail_page_default: 
                        resolved_poster_url_step1 = ps_url_detail_page_default
                    
                    mgs_special_poster_filepath = None
                    attempt_mgs_special_local = False
                    if pl_url_detail_page_default and ps_url_detail_page_default and not ps_to_poster_setting and resolved_poster_url_step1 == ps_url_detail_page_default:
                        attempt_mgs_special_local = True
                    if attempt_mgs_special_local:
                        temp_filepath, _, _ = SiteUtil.get_mgs_half_pl_poster_info_local(ps_url_detail_page_default, pl_url_detail_page_default, proxy_url=proxy_url)
                        if temp_filepath and os.path.exists(temp_filepath): mgs_special_poster_filepath = temp_filepath

                    if mgs_special_poster_filepath: final_poster_source = mgs_special_poster_filepath; final_poster_crop_mode = None
                    else: final_poster_source = resolved_poster_url_step1; final_poster_crop_mode = resolved_crop_mode_step1

                if not skip_default_landscape_logic: final_landscape_url_source = pl_url_detail_page_default
                arts_urls_for_processing = arts_urls_page_default

                # --- 이미지 최종 처리 및 entity에 추가 (이미지 서버 사용 안 할 때) ---
                if not (use_image_server and image_mode == '4'):
                    # 포스터 추가
                    if final_poster_source and not skip_default_poster_logic and not any(t.aspect == 'poster' for t in entity.thumb):
                        processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                        if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                    # 랜드스케이프 추가
                    if final_landscape_url_source and not skip_default_landscape_logic and not any(t.aspect == 'landscape' for t in entity.thumb):
                        processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url_source, proxy_url=proxy_url)
                        if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))

                    # 팬아트 추가
                    if arts_urls_for_processing:
                        if entity.fanart is None: entity.fanart = []
                        processed_fanart_count = len(entity.fanart)
                        sources_to_exclude = {final_poster_source, final_landscape_url_source}
                        if pl_url_detail_page_default and mgs_special_poster_filepath and final_poster_source == mgs_special_poster_filepath:
                            sources_to_exclude.add(pl_url_detail_page_default)

                        for art_url in arts_urls_for_processing:
                            if processed_fanart_count >= max_arts: break
                            if art_url and art_url not in sources_to_exclude:
                                processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                                if processed_art and processed_art not in entity.fanart:
                                    entity.fanart.append(processed_art)
                                    processed_fanart_count += 1
            except Exception as e_img_proc_default_dvd:
                logger.exception(f"MGStage ({cls.module_char}): Error during default image processing: {e_img_proc_default_dvd}")

        if use_image_server and image_mode == '4' and ui_code_for_image:
            logger.info(f"MGStage ({cls.module_char}): Saving images to Image Server for {ui_code_for_image}...")
            #if ps_url_detail_page_default:
            #    SiteUtil.save_image_to_server_path(ps_url_detail_page_default, 'ps', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
            if not skip_default_poster_logic and final_poster_source:
                p_path = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                if p_path and not any(t.aspect == 'poster' and t.value.endswith(p_path) for t in entity.thumb): entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_path}"))
            if not skip_default_landscape_logic and final_landscape_url_source:
                pl_path = SiteUtil.save_image_to_server_path(final_landscape_url_source, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                if pl_path and not any(t.aspect == 'landscape' and t.value.endswith(pl_path) for t in entity.thumb): entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_path}"))
            if arts_urls_for_processing:
                processed_fanart_count_server = 0; sources_to_exclude_server = {final_poster_source, final_landscape_url_source}
                if pl_url_detail_page_default and 'mgs_special_poster_filepath' in locals() and mgs_special_poster_filepath and final_poster_source == mgs_special_poster_filepath:
                    sources_to_exclude_server.add(pl_url_detail_page_default)
                for idx, art_url in enumerate(arts_urls_for_processing):
                    if processed_fanart_count_server >= max_arts: break
                    if art_url and art_url not in sources_to_exclude_server:
                        art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                        if art_relative_path: entity.fanart.append(f"{image_server_url}/{art_relative_path}"); processed_fanart_count_server += 1
        
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
                        trailer_title_text = entity.tagline if entity.tagline else (entity.title if entity.title else code) 
                        entity.extras.append(EntityExtra("trailer", trailer_title_text, "mp4", trailer_final_url))
            except Exception as e_trailer_proc_dvd:
                logger.exception(f"MGStage ({cls.module_char}): Error processing trailer: {e_trailer_proc_dvd}")
        
        # Dvd는 Shiroutoname 보정 없음
        
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
