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

    @classmethod
    def __img_urls(cls, tree):
        pl = tree.xpath('//*[@id="package"]/a/@href')
        pl = pl[0] if pl else ""
        if not pl: logger.warning("Ama: 이미지 URL을 얻을 수 없음: poster large")
        ps = tree.xpath('//div[@class="detail_photo"]//img/@src')
        ps = ps[0] if ps else ""
        if not ps and pl and "pb_e_" in pl: 
            ps = pl.replace("pb_e_", "pf_o1_")
        if not ps : logger.warning("Ama: 이미지 URL을 얻을 수 없음: poster small")
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
        ps_to_poster_setting = kwargs.get('ps_to_poster', ps_to_poster) # 사용자가 명시한 ps_to_poster 설정
        crop_mode_setting = kwargs.get('crop_mode', crop_mode) # 사용자가 명시한 crop_mode 설정

        logger.debug(f"Image Server Mode Check ({cls.module_char}): image_mode={image_mode}, use_image_server={use_image_server}")
        if use_image_server and image_mode == '4':
            logger.info(f"Image Server Enabled ({cls.module_char}): URL={image_server_url}, LocalPath={image_server_local_path}, PathSegment={image_path_segment}")

        url = cls.site_base_url + f"/product/product_detail/{code[2:]}/"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = [] 
        entity.fanart = [] 
        entity.extras = [] 
        ui_code_for_image = "" 

        # --- 이미지 처리 로직 시작 ---
        final_poster_source = None # 최종 포스터로 사용될 URL 또는 로컬 파일 경로
        final_poster_crop_mode = None  # 위 소스에 적용될 crop_mode (l,r,c 또는 None)
        final_landscape_url = None     # 최종 랜드스케이프로 사용될 URL
        
        try:
            img_urls_result = cls.__img_urls(tree) 
            ps_url = img_urls_result.get('ps')
            pl_url = img_urls_result.get('pl')
            arts_urls = img_urls_result.get('arts', []) 

            final_landscape_url = pl_url # 기본 랜드스케이프는 pl로 설정

            # 1단계: 기본적인 포스터 후보 결정 (URL과 crop_mode)
            # 이 로직은 SiteUtil.resolve_jav_imgs의 내부 로직과 유사하게 동작합니다.
            resolved_poster_url_step1 = None
            resolved_crop_mode_step1 = None

            if ps_to_poster_setting and ps_url: # 사용자가 'ps를 포스터로' 설정을 켰다면
                resolved_poster_url_step1 = ps_url
                # resolved_crop_mode_step1은 None (ps는 그대로 사용)
            else:
                # 사용자가 crop_mode를 명시적으로 지정했고, pl 이미지가 있다면
                if crop_mode_setting and pl_url:
                    resolved_poster_url_step1 = pl_url
                    resolved_crop_mode_step1 = crop_mode_setting
                # pl과 ps가 모두 있다면, has_hq_poster로 비교하여 crop 위치 결정
                elif pl_url and ps_url:
                    loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                    if loc: # loc은 'l', 'r', 'c' 중 하나
                        resolved_poster_url_step1 = pl_url
                        resolved_crop_mode_step1 = loc
                    else: # has_hq_poster가 실패했거나 loc이 없다면, is_hq_poster로 pl이 ps보다 나은지 확인
                          # (세로 이미지이거나, crop 없이도 pl이 더 좋을 수 있음)
                        if SiteUtil.is_hq_poster(ps_url, pl_url, proxy_url=proxy_url):
                            resolved_poster_url_step1 = pl_url
                            # resolved_crop_mode_step1은 None (pl 전체 사용)
                        else: # pl이 ps보다 좋지 않다면 ps를 사용
                            resolved_poster_url_step1 = ps_url
                            # resolved_crop_mode_step1은 None
                elif ps_url: # pl은 없고 ps만 있다면
                    resolved_poster_url_step1 = ps_url
                    # resolved_crop_mode_step1은 None
                elif pl_url: # ps는 없고 pl만 있다면 (이 경우 ps와 비교 불가)
                    resolved_poster_url_step1 = pl_url
                    resolved_crop_mode_step1 = crop_mode_setting # 사용자 설정 crop_mode를 따름 (없으면 None)
            
            # 위에서 포스터를 결정하지 못했고, arts_urls가 있고, ps_to_poster_setting이 꺼져있다면 arts에서 시도
            if not resolved_poster_url_step1 and arts_urls and not ps_to_poster_setting:
                if ps_url and SiteUtil.is_hq_poster(ps_url, arts_urls[0], proxy_url=proxy_url):
                    resolved_poster_url_step1 = arts_urls[0]
                    # resolved_crop_mode_step1은 None
                elif ps_url and len(arts_urls) > 1 and SiteUtil.is_hq_poster(ps_url, arts_urls[-1], proxy_url=proxy_url):
                    resolved_poster_url_step1 = arts_urls[-1]
                    # resolved_crop_mode_step1은 None
            
            # 모든 시도 후에도 포스터가 없다면, 최후의 보루로 ps_url 사용
            if not resolved_poster_url_step1 and ps_url:
                resolved_poster_url_step1 = ps_url
                # resolved_crop_mode_step1은 None

            logger.debug(f"{cls.module_char} Step 1: Poster='{resolved_poster_url_step1}', Crop='{resolved_crop_mode_step1}'")

            # 2단계: MGStage 특별 처리 (로컬 임시 파일 사용)
            mgs_local_poster_filepath = None # MGStage 특별 처리로 생성된 로컬 파일 경로

            # 특별 처리를 시도할 조건:
            # 1. pl_url과 ps_url이 모두 존재해야 함.
            # 2. ps_to_poster_setting이 꺼져 있어야 함 (pl 기반의 더 나은 포스터를 찾으려는 시도).
            # 3. 1단계에서 결정된 포스터가 ps_url이거나 (즉, pl_url이 더 좋을 가능성)
            #    또는 pl_url이면서 crop이 없는 경우 (더 나은 crop이 있을 수 있음 - 이 조건은 현재 주석처리).
            attempt_mgs_special_local = False
            if pl_url and ps_url and not ps_to_poster_setting:
                if resolved_poster_url_step1 == ps_url:
                    attempt_mgs_special_local = True
                    logger.debug(f"{cls.module_char}: Step 1 resulted in PS. Attempting MGS special (local) for {code}.")
                # elif resolved_poster_url_step1 == pl_url and resolved_crop_mode_step1 is None:
                #     # 이 조건은 is_hq_poster(ps,pl)이 True여서 PL 전체가 선택된 경우와 충돌할 수 있으므로 신중해야함.
                #     # 현재는 PS가 선택된 경우에만 특별 처리를 시도하도록 함.
                #     pass

            if attempt_mgs_special_local:
                logger.info(f"{cls.module_char}: Attempting MGS special poster processing (local) for {code} using pl='{pl_url}' and ps='{ps_url}'")
                # SiteUtil.get_mgs_half_pl_poster_info_local는 (로컬파일경로, None, original_pl_url) 반환
                temp_filepath, _, _ = SiteUtil.get_mgs_half_pl_poster_info_local(ps_url, pl_url, proxy_url=proxy_url)
                
                if temp_filepath and os.path.exists(temp_filepath): # 파일 존재까지 확인
                    logger.info(f"{cls.module_char}: MGS special poster (local) successful. Using temp file: {temp_filepath}")
                    mgs_local_poster_filepath = temp_filepath
                else:
                    logger.info(f"{cls.module_char}: MGS special poster (local) failed or did not return a valid file for {code}.")
        
            # 3단계: 최종 포스터 소스 및 크롭 모드 결정
            if mgs_local_poster_filepath: # MGStage 특별 처리가 성공했다면
                final_poster_source = mgs_local_poster_filepath # 로컬 파일 경로
                final_poster_crop_mode = None # 로컬 파일은 이미 최종 크롭된 형태
            else: # 특별 처리 실패 또는 미적용 시 1단계 결과 사용
                final_poster_source = resolved_poster_url_step1
                final_poster_crop_mode = resolved_crop_mode_step1
            
            logger.info(f"{cls.module_char} Final Image Decision for {code}: Poster Source='{final_poster_source}', Crop Mode='{final_poster_crop_mode}', Landscape='{final_landscape_url}'")

            # --- 이미지 처리 및 entity 할당 (일반 모드) ---
            if not (use_image_server and image_mode == '4'):
                logger.info(f"{cls.module_char}: Using Normal Image Processing Mode for {code} (image_mode: {image_mode})...")
                
                if final_poster_source:
                    # final_poster_source는 URL 또는 로컬 파일 경로일 수 있음
                    # final_poster_crop_mode는 이 소스에 적용할 크롭 (로컬 파일이면 None)
                    processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                    if processed_poster:
                        entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                
                if final_landscape_url:
                    processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url, proxy_url=proxy_url) # 랜드스케이프는 crop_mode 없음
                    if processed_landscape:
                        entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))

                # 팬아트 처리
                processed_fanart_count = 0
                # 팬아트에서 제외할 소스들 (URL 또는 로컬 파일 경로)
                sources_to_exclude_from_arts = set()
                if final_poster_source: sources_to_exclude_from_arts.add(final_poster_source) 
                # MGStage 특별 처리로 포스터가 생성되었고, 그 원본이 pl_url이었다면, 원본 pl_url도 제외 대상에 포함
                if pl_url and mgs_local_poster_filepath and final_poster_source == mgs_local_poster_filepath:
                    sources_to_exclude_from_arts.add(pl_url)
                if final_landscape_url: sources_to_exclude_from_arts.add(final_landscape_url)
                
                for art_url in arts_urls:
                    if processed_fanart_count >= max_arts: break
                    if art_url and art_url not in sources_to_exclude_from_arts: # art_url은 항상 URL
                        processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url) # 팬아트는 crop_mode 없음
                        if processed_art: 
                            entity.fanart.append(processed_art)
                            processed_fanart_count += 1
                logger.debug(f"{cls.module_char} Normal Mode: Final Thumb={entity.thumb}, Fanart Count={len(entity.fanart)}")

        except Exception as e_img_proc:
            logger.exception(f"{cls.module_char}: Error during image processing setup for {code}: {e_img_proc}")
        # --- 이미지 처리 로직 끝 ---


        # --- 메타데이터 파싱 시작 ---
        try:
            h1_tags = tree.xpath('//h1[@class="tag"]/text()')
            if h1_tags:
                h1 = h1_tags[0]
                for ptn in cls.PTN_TEXT_SUB: h1 = ptn.sub("", h1)
                entity.tagline = SiteUtil.trans(h1, do_trans=do_trans)
            else: logger.warning(f"{cls.module_char}: H1 title tag not found.")

            basetag = '//div[@class="detail_data"]'
            tags = tree.xpath(f"{basetag}//tr")
            tmp_premiered = None
            for tag_node in tags: # 변수명 변경 tag -> tag_node
                key_node = tag_node.xpath("./th")
                value_node = tag_node.xpath("./td")
                if not key_node or not value_node: continue
                key = key_node[0].text_content().strip()
                value_content = value_node[0].text_content().strip() # text_content() 사용
                value_node_instance = value_node[0] # 하위 태그 접근용

                if "品番" in key: 
                    match = cls.PTN_SEARCH_REAL_NO.match(value_content)
                    formatted_code = value_content.upper()
                    if match:
                        label = match.group("real").upper()
                        num_str = str(int(match.group("no"))).zfill(3)
                        formatted_code = f"{label}-{num_str}"
                        if entity.tag is None: entity.tag = []
                        if label not in entity.tag: entity.tag.append(label)
                    entity.title = entity.originaltitle = entity.sorttitle = formatted_code
                    ui_code_for_image = formatted_code 
                    entity.ui_code = ui_code_for_image
                    logger.debug(f"{cls.module_char}: 品番 파싱 완료, ui_code_for_image='{ui_code_for_image}'")
                    continue

                elif "商品発売日" in key: 
                    try: entity.premiered = value_content.replace("/", "-"); entity.year = int(value_content[:4])
                    except Exception: pass
                elif "配信開始日" in key: tmp_premiered = value_content.replace("/", "-") 
                elif "収録時間" in key:
                    try: entity.runtime = int(value_content.replace("min", "").strip())
                    except Exception: pass
                elif "出演" in key:
                    actors_data = []
                    for actor_text_node in value_node_instance.xpath("./a/text()"):
                        full_text = actor_text_node.strip()
                        if not full_text:
                            continue
                        name_part = full_text.split(" ", 1)[0]
                        actors_data.append(EntityActor(name_part))
                    entity.actor = actors_data
                elif "監督" in key: entity.director = value_content 
                elif "シリーズ" in key:
                    series_name_nodes = value_node_instance.xpath("./a/text()") # 링크 텍스트 우선
                    series_name = series_name_nodes[0].strip() if series_name_nodes else value_content
                    if entity.tag is None: entity.tag = []
                    trans_series = SiteUtil.trans(series_name, do_trans=do_trans)
                    if trans_series and trans_series not in entity.tag: entity.tag.append(trans_series)
                elif "レーベル" in key: 
                    studio_name_nodes = value_node_instance.xpath("./a/text()") # 링크 텍스트 우선
                    studio_name = studio_name_nodes[0].strip() if studio_name_nodes else value_content
                    entity.studio = studio_name # 번역 전 원본 스튜디오명 저장
                    if do_trans: # 번역 적용
                        if studio_name in SiteUtil.av_studio: 
                            entity.studio = SiteUtil.av_studio[studio_name]
                        else: 
                            entity.studio = SiteUtil.change_html(SiteUtil.trans(studio_name)) # HTML 엔티티 처리 및 번역
                elif "ジャンル" in key:
                    entity.genre = []
                    for a_tag in value_node_instance.xpath("./a"):
                        genre_text = a_tag.text_content().strip()
                        if "MGSだけのおまけ映像付き" in genre_text or not genre_text or genre_text in SiteUtil.av_genre_ignore_ja: 
                            continue
                        if genre_text in SiteUtil.av_genre: 
                            entity.genre.append(SiteUtil.av_genre[genre_text])
                        else:
                            genre_tmp = SiteUtil.trans(genre_text, do_trans=do_trans).replace(" ", "")
                            if genre_tmp not in SiteUtil.av_genre_ignore_ko: 
                                entity.genre.append(genre_tmp)

            # Ama(D)는 配信開始日 우선, Dvd(C)는 商品発売日 우선 후 없으면 配信開始日
            if cls.module_char == 'D': # Ama
                if tmp_premiered is not None:
                    entity.premiered = tmp_premiered
                    try: entity.year = int(tmp_premiered[:4])
                    except Exception: pass
                elif entity.premiered is None: # 配信日도 없고 発売日도 없으면 연도 없음
                    entity.year = None
            elif cls.module_char == 'C': # Dvd
                if entity.premiered is None and tmp_premiered is not None : # 출시일 없고 배신일 있으면 배신일 사용
                    entity.premiered = tmp_premiered
                    try: entity.year = int(tmp_premiered[:4])
                    except Exception: pass
                elif entity.premiered is None:
                    entity.year = None

            plot_nodes = tree.xpath('//*[@id="introduction"]//p[1]')
            if plot_nodes:
                for br_tag in plot_nodes[0].xpath('.//br'): br_tag.tail = "\n" + br_tag.tail if br_tag.tail else "\n" # 변수명 변경 br -> br_tag
                plot_text = plot_nodes[0].text_content().strip()
                if not plot_text and len(tree.xpath('//*[@id="introduction"]//p')) > 1:
                    plot_nodes_alt = tree.xpath('//*[@id="introduction"]//p[2]')
                    if plot_nodes_alt:
                        for br_tag_alt in plot_nodes_alt[0].xpath('.//br'): br_tag_alt.tail = "\n" + br_tag_alt.tail if br_tag_alt.tail else "\n"
                        plot_text = plot_nodes_alt[0].text_content().strip()
                if plot_text:
                    for ptn in cls.PTN_TEXT_SUB: plot_text = ptn.sub("", plot_text)
                    entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
            else: logger.warning(f"{cls.module_char}: Plot node not found.")

            rating_tag_nodes = tree.xpath('//div[@class="user_review_head"]/p[@class="detail"]/text()')
            if rating_tag_nodes:
                match_rating = cls.PTN_RATING.search(rating_tag_nodes[0]) # 변수명 변경 match -> match_rating
                if match_rating:
                    try:
                        rating_value = float(match_rating.group("rating"))
                        votes = int(match_rating.group("vote"))
                        entity.ratings = [EntityRatings(rating_value, max=5, name="mgs", votes=votes)]
                    except Exception: logger.warning(f"{cls.module_char}: Failed to parse rating values.")

        except Exception as e_meta:
            logger.exception(f"{cls.module_char}: Error during metadata parsing for {code}: {e_meta}")
        # --- 메타데이터 파싱 끝 ---


        # --- 이미지 서버 저장 로직 ---
        if use_image_server and image_mode == '4' and image_server_url and image_server_local_path and ui_code_for_image:
            logger.info(f"{cls.module_char}: Saving images to Image Server for {ui_code_for_image}...")
            
            if final_landscape_url:
                # save_image_to_server_path는 URL 또는 로컬 파일 경로를 받을 수 있음
                pl_relative_path = SiteUtil.save_image_to_server_path(final_landscape_url, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url) # crop_mode 없음
                if pl_relative_path:
                    entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_relative_path}"))

            if final_poster_source: # URL 또는 로컬 파일 경로
                p_relative_path = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                if p_relative_path:
                    entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_relative_path}"))
            
            # 팬아트 저장
            processed_fanart_count_server = 0
            sources_to_exclude_for_server_arts = set() # 일반 모드와 동일한 제외 로직 사용
            if final_poster_source: sources_to_exclude_for_server_arts.add(final_poster_source)
            if pl_url and mgs_local_poster_filepath and final_poster_source == mgs_local_poster_filepath:
                sources_to_exclude_for_server_arts.add(pl_url)
            if final_landscape_url: sources_to_exclude_for_server_arts.add(final_landscape_url)

            for idx, art_url in enumerate(arts_urls): # arts_urls는 원본 URL 리스트
                if processed_fanart_count_server >= max_arts: break
                if art_url and art_url not in sources_to_exclude_for_server_arts:
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url) # crop_mode 없음
                    if art_relative_path:
                        entity.fanart.append(f"{image_server_url}/{art_relative_path}")
                        processed_fanart_count_server += 1
            logger.info(f"{cls.module_char} Image Server: Processed {len(entity.thumb)} thumbs and {processed_fanart_count_server} fanarts for {ui_code_for_image}.")
        # --- 이미지 서버 저장 로직 끝 ---

        # --- 예고편 처리 시작 ---
        if use_extras:
            try:
                trailer_tag_nodes = tree.xpath('//*[@class="sample_movie_btn"]/a/@href')
                if trailer_tag_nodes:
                    pid = trailer_tag_nodes[0].split("/")[-1]
                    api_url = f"https://www.mgstage.com/sampleplayer/sampleRespons.php?pid={pid}"
                    api_headers = cls.headers.copy()
                    api_headers['Referer'] = url 
                    api_headers['X-Requested-With'] = 'XMLHttpRequest' 
                    api_headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
                    res_json = SiteUtil.get_response(api_url, proxy_url=proxy_url, headers=api_headers).json()
                    if res_json and res_json.get("url"):
                        trailer_base_url = res_json["url"].split(".ism")[0]
                        trailer_url = trailer_base_url + ".mp4"
                        trailer_title = entity.tagline if entity.tagline else (entity.title if entity.title else code) 
                        entity.extras.append(EntityExtra("trailer", trailer_title, "mp4", trailer_url))
                        logger.info(f"{cls.module_char}: Trailer added: {trailer_url}")
                    else: logger.warning(f"{cls.module_char}: Trailer API response invalid for {pid}.")
                else: logger.debug(f"{cls.module_char}: Trailer button not found for {code}.")
            except Exception as e_trailer:
                logger.exception(f"{cls.module_char}: Error processing trailer for {code}: {e_trailer}")
        # --- 예고편 처리 끝 ---

        # --- Shiroutoname 보정 (Ama 전용이었으나, 공통으로 적용 가능하면 할 수도 있음. 현재는 Ama에만 있음) ---
        if cls.module_char == 'D': # Ama 클래스에서만 호출되도록 명시 (SiteMgstageAma에서만 이 로직이 유효하다면)
            try:
                entity = SiteUtil.shiroutoname_info(entity) 
                logger.debug(f"{cls.module_char}: Shiroutoname info applied (if found) for {code}.")
            except Exception as e_shirouto: # 변수명 변경
                logger.exception(f"{cls.module_char}: Shiroutoname 보정 중 예외 for {code}: {e_shirouto}")
        # --- 보정 끝 ---

        return entity 


    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs) # 하위 클래스의 __info 호출
            if entity: 
                ret["ret"] = "success"
                ret["data"] = entity.as_dict()
            else: 
                ret["ret"] = "error"
                ret["data"] = f"Failed to get {cls.module_char} info entity for {code}"
        except Exception as exception:
            logger.exception(f"메타 정보 처리 중 예외 ({cls.module_char}, {code}):")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        return ret


# site_mgstage.py 내의 SiteMgstageDvd 클래스 정의

class SiteMgstageDvd(SiteMgstage):
    module_char = "C" # DVD 모듈 식별자

    @classmethod
    def __img_urls(cls, tree):
        """collect raw image urls from html page (DVD Version)"""
        ps = tree.xpath('//div[@class="detail_photo"]//img/@src')
        ps = ps[0] if ps else ""
        if not ps: logger.warning("Dvd: 이미지 URL을 얻을 수 없음: poster small") # 로그 메시지 수정
        pl = tree.xpath('//*[@id="package"]/a/@href')
        pl = pl[0] if pl else ""
        arts = tree.xpath('//*[@id="sample-photo"]//ul/li/a/@href')
        # DVD 버전은 Ama와 달리 pf_e_ 변환 로직 없음
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
        ps_to_poster_setting = kwargs.get('ps_to_poster', ps_to_poster) # 사용자가 명시한 ps_to_poster 설정
        crop_mode_setting = kwargs.get('crop_mode', crop_mode) # 사용자가 명시한 crop_mode 설정

        logger.debug(f"Image Server Mode Check ({cls.module_char}): image_mode={image_mode}, use_image_server={use_image_server}") # cls.module_char 사용
        if use_image_server and image_mode == '4':
            logger.info(f"Image Server Enabled ({cls.module_char}): URL={image_server_url}, LocalPath={image_server_local_path}, PathSegment={image_path_segment}") # cls.module_char 사용

        url = cls.site_base_url + f"/product/product_detail/{code[2:]}/"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"
        entity.thumb = [] 
        entity.fanart = [] 
        entity.extras = [] 
        ui_code_for_image = "" 

        # --- 이미지 처리 로직 시작 ---
        final_poster_source = None # 최종 포스터로 사용될 URL 또는 로컬 파일 경로
        final_poster_crop_mode = None  # 위 소스에 적용될 crop_mode (l,r,c 또는 None)
        final_landscape_url = None     # 최종 랜드스케이프로 사용될 URL
        
        try:
            img_urls_result = cls.__img_urls(tree) # DVD용 __img_urls 호출
            ps_url = img_urls_result.get('ps')
            pl_url = img_urls_result.get('pl')
            arts_urls = img_urls_result.get('arts', []) 

            final_landscape_url = pl_url # 기본 랜드스케이프는 pl로 설정

            # 1단계: 기본적인 포스터 후보 결정 (URL과 crop_mode)
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
            
            if not resolved_poster_url_step1 and arts_urls and not ps_to_poster_setting:
                if ps_url and SiteUtil.is_hq_poster(ps_url, arts_urls[0], proxy_url=proxy_url):
                    resolved_poster_url_step1 = arts_urls[0]
                elif ps_url and len(arts_urls) > 1 and SiteUtil.is_hq_poster(ps_url, arts_urls[-1], proxy_url=proxy_url):
                    resolved_poster_url_step1 = arts_urls[-1]
            
            if not resolved_poster_url_step1 and ps_url:
                resolved_poster_url_step1 = ps_url

            logger.debug(f"{cls.module_char} Step 1: Poster='{resolved_poster_url_step1}', Crop='{resolved_crop_mode_step1}'") # cls.module_char 사용

            # 2단계: MGStage 특별 처리 (로컬 임시 파일 사용)
            mgs_local_poster_filepath = None 

            attempt_mgs_special_local = False
            if pl_url and ps_url and not ps_to_poster_setting:
                if resolved_poster_url_step1 == ps_url:
                    attempt_mgs_special_local = True
                    logger.debug(f"{cls.module_char}: Step 1 resulted in PS. Attempting MGS special (local) for {code}.") # cls.module_char 사용

            if attempt_mgs_special_local:
                logger.info(f"{cls.module_char}: Attempting MGS special poster processing (local) for {code} using pl='{pl_url}' and ps='{ps_url}'") # cls.module_char 사용
                temp_filepath, _, _ = SiteUtil.get_mgs_half_pl_poster_info_local(ps_url, pl_url, proxy_url=proxy_url)
                
                if temp_filepath and os.path.exists(temp_filepath): 
                    logger.info(f"{cls.module_char}: MGS special poster (local) successful. Using temp file: {temp_filepath}") # cls.module_char 사용
                    mgs_local_poster_filepath = temp_filepath
                else:
                    logger.info(f"{cls.module_char}: MGS special poster (local) failed or did not return a valid file for {code}.") # cls.module_char 사용
        
            # 3단계: 최종 포스터 소스 및 크롭 모드 결정
            if mgs_local_poster_filepath: 
                final_poster_source = mgs_local_poster_filepath 
                final_poster_crop_mode = None 
            else: 
                final_poster_source = resolved_poster_url_step1
                final_poster_crop_mode = resolved_crop_mode_step1
            
            logger.info(f"{cls.module_char} Final Image Decision for {code}: Poster Source='{final_poster_source}', Crop Mode='{final_poster_crop_mode}', Landscape='{final_landscape_url}'") # cls.module_char 사용

            # --- 이미지 처리 및 entity 할당 (일반 모드) ---
            if not (use_image_server and image_mode == '4'):
                logger.info(f"{cls.module_char}: Using Normal Image Processing Mode for {code} (image_mode: {image_mode})...") # cls.module_char 사용
                
                if final_poster_source:
                    processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_source, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                    if processed_poster:
                        entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                
                if final_landscape_url:
                    processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url, proxy_url=proxy_url) 
                    if processed_landscape:
                        entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))

                processed_fanart_count = 0
                sources_to_exclude_from_arts = set()
                if final_poster_source: sources_to_exclude_from_arts.add(final_poster_source) 
                if pl_url and mgs_local_poster_filepath and final_poster_source == mgs_local_poster_filepath:
                    sources_to_exclude_from_arts.add(pl_url)
                if final_landscape_url: sources_to_exclude_from_arts.add(final_landscape_url)
                
                for art_url in arts_urls:
                    if processed_fanart_count >= max_arts: break
                    if art_url and art_url not in sources_to_exclude_from_arts: 
                        processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url) 
                        if processed_art: 
                            entity.fanart.append(processed_art)
                            processed_fanart_count += 1
                logger.debug(f"{cls.module_char} Normal Mode: Final Thumb={entity.thumb}, Fanart Count={len(entity.fanart)}") # cls.module_char 사용

        except Exception as e_img_proc:
            logger.exception(f"{cls.module_char}: Error during image processing setup for {code}: {e_img_proc}") # cls.module_char 사용
        # --- 이미지 처리 로직 끝 ---


        # --- 메타데이터 파싱 시작 ---
        try:
            h1_tags = tree.xpath('//h1[@class="tag"]/text()')
            if h1_tags:
                h1 = h1_tags[0]
                for ptn in cls.PTN_TEXT_SUB: h1 = ptn.sub("", h1)
                entity.tagline = SiteUtil.trans(h1, do_trans=do_trans)
            else: logger.warning(f"{cls.module_char}: H1 title tag not found.") # cls.module_char 사용

            basetag = '//div[@class="detail_data"]'
            tags = tree.xpath(f"{basetag}//tr")
            tmp_premiered = None # DVD에서는 배신 시작일 저장용
            for tag_node in tags: 
                key_node = tag_node.xpath("./th")
                value_node = tag_node.xpath("./td")
                if not key_node or not value_node: continue
                key = key_node[0].text_content().strip()
                value_content = value_node[0].text_content().strip() 
                value_node_instance = value_node[0] 

                if "品番" in key: 
                    match = cls.PTN_SEARCH_REAL_NO.match(value_content)
                    formatted_code = value_content.upper()
                    if match:
                        label = match.group("real").upper()
                        num_str = str(int(match.group("no"))).zfill(3)
                        formatted_code = f"{label}-{num_str}"
                        if entity.tag is None: entity.tag = []
                        if label not in entity.tag: entity.tag.append(label)
                    entity.title = entity.originaltitle = entity.sorttitle = formatted_code
                    ui_code_for_image = formatted_code 
                    entity.ui_code = ui_code_for_image
                    logger.debug(f"{cls.module_char}: 品番 파싱 완료, ui_code_for_image='{ui_code_for_image}'") # cls.module_char 사용
                    continue

                elif "商品発売日" in key: # DVD는 이 날짜를 우선 사용
                    try: entity.premiered = value_content.replace("/", "-"); entity.year = int(value_content[:4])
                    except Exception: pass
                elif "配信開始日" in key: tmp_premiered = value_content.replace("/", "-") # 출시일 없을 경우 대비해 저장
                elif "収録時間" in key:
                    try: entity.runtime = int(value_content.replace("min", "").strip())
                    except Exception: pass
                elif "出演" in key:
                    actors_data = []
                    for actor_text_node in value_node_instance.xpath("./a/text()"):
                        full_text = actor_text_node.strip()
                        if not full_text: continue
                        name_part = full_text.split(" ", 1)[0]
                        actors_data.append(EntityActor(name_part))
                    entity.actor = actors_data
                elif "監督" in key: entity.director = value_content 
                elif "シリーズ" in key:
                    series_name_nodes = value_node_instance.xpath("./a/text()") 
                    series_name = series_name_nodes[0].strip() if series_name_nodes else value_content
                    if entity.tag is None: entity.tag = []
                    trans_series = SiteUtil.trans(series_name, do_trans=do_trans)
                    if trans_series and trans_series not in entity.tag: entity.tag.append(trans_series)
                elif "レーベル" in key: 
                    studio_name_nodes = value_node_instance.xpath("./a/text()") 
                    studio_name = studio_name_nodes[0].strip() if studio_name_nodes else value_content
                    entity.studio = studio_name 
                    if do_trans: 
                        if studio_name in SiteUtil.av_studio: entity.studio = SiteUtil.av_studio[studio_name]
                        else: entity.studio = SiteUtil.change_html(SiteUtil.trans(studio_name))
                elif "ジャンル" in key:
                    entity.genre = []
                    for a_tag in value_node_instance.xpath("./a"):
                        genre_text = a_tag.text_content().strip()
                        if "MGSだけのおまけ映像付き" in genre_text or not genre_text or genre_text in SiteUtil.av_genre_ignore_ja: continue
                        if genre_text in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_text])
                        else:
                            genre_tmp = SiteUtil.trans(genre_text, do_trans=do_trans).replace(" ", "")
                            if genre_tmp not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_tmp)

            # DVD(C)는 商品発売日 우선 후 없으면 配信開始日 사용
            if cls.module_char == 'C': # DVD
                if entity.premiered is None and tmp_premiered is not None : # 출시일 없고 배신일 있으면 배신일 사용
                    entity.premiered = tmp_premiered
                    try: entity.year = int(tmp_premiered[:4])
                    except Exception: pass
                elif entity.premiered is None: # 둘 다 없으면 연도 정보 없음
                    entity.year = None
            # (Ama 로직은 여기서는 해당 없음)

            plot_nodes = tree.xpath('//*[@id="introduction"]//p[1]')
            if plot_nodes:
                for br_tag in plot_nodes[0].xpath('.//br'): br_tag.tail = "\n" + br_tag.tail if br_tag.tail else "\n" 
                plot_text = plot_nodes[0].text_content().strip()
                if not plot_text and len(tree.xpath('//*[@id="introduction"]//p')) > 1:
                    plot_nodes_alt = tree.xpath('//*[@id="introduction"]//p[2]')
                    if plot_nodes_alt:
                        for br_tag_alt in plot_nodes_alt[0].xpath('.//br'): br_tag_alt.tail = "\n" + br_tag_alt.tail if br_tag_alt.tail else "\n"
                        plot_text = plot_nodes_alt[0].text_content().strip()
                if plot_text:
                    for ptn in cls.PTN_TEXT_SUB: plot_text = ptn.sub("", plot_text)
                    entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
            else: logger.warning(f"{cls.module_char}: Plot node not found.") # cls.module_char 사용

            rating_tag_nodes = tree.xpath('//div[@class="user_review_head"]/p[@class="detail"]/text()')
            if rating_tag_nodes:
                match_rating = cls.PTN_RATING.search(rating_tag_nodes[0]) 
                if match_rating:
                    try:
                        rating_value = float(match_rating.group("rating"))
                        votes = int(match_rating.group("vote"))
                        entity.ratings = [EntityRatings(rating_value, max=5, name="mgs", votes=votes)]
                    except Exception: logger.warning(f"{cls.module_char}: Failed to parse rating values.") # cls.module_char 사용

        except Exception as e_meta:
            logger.exception(f"{cls.module_char}: Error during metadata parsing for {code}: {e_meta}") # cls.module_char 사용
        # --- 메타데이터 파싱 끝 ---


        # --- 이미지 서버 저장 로직 ---
        if use_image_server and image_mode == '4' and image_server_url and image_server_local_path and ui_code_for_image:
            logger.info(f"{cls.module_char}: Saving images to Image Server for {ui_code_for_image}...") # cls.module_char 사용
            
            if final_landscape_url:
                pl_relative_path = SiteUtil.save_image_to_server_path(final_landscape_url, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                if pl_relative_path:
                    entity.thumb.append(EntityThumb(aspect="landscape", value=f"{image_server_url}/{pl_relative_path}"))

            if final_poster_source: 
                p_relative_path = SiteUtil.save_image_to_server_path(final_poster_source, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop_mode)
                if p_relative_path:
                    entity.thumb.append(EntityThumb(aspect="poster", value=f"{image_server_url}/{p_relative_path}"))
            
            processed_fanart_count_server = 0 
            sources_to_exclude_for_server_arts = set() 
            if final_poster_source: sources_to_exclude_for_server_arts.add(final_poster_source)
            if pl_url and mgs_local_poster_filepath and final_poster_source == mgs_local_poster_filepath:
                sources_to_exclude_for_server_arts.add(pl_url)
            if final_landscape_url: sources_to_exclude_for_server_arts.add(final_landscape_url)

            for idx, art_url in enumerate(arts_urls): 
                if processed_fanart_count_server >= max_arts: break
                if art_url and art_url not in sources_to_exclude_for_server_arts:
                    art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url) 
                    if art_relative_path:
                        entity.fanart.append(f"{image_server_url}/{art_relative_path}")
                        processed_fanart_count_server += 1
            logger.info(f"{cls.module_char} Image Server: Processed {len(entity.thumb)} thumbs and {processed_fanart_count_server} fanarts for {ui_code_for_image}.") # cls.module_char 사용
        # --- 이미지 서버 저장 로직 끝 ---

        # --- 예고편 처리 시작 ---
        if use_extras:
            try:
                trailer_tag_nodes = tree.xpath('//*[@class="sample_movie_btn"]/a/@href')
                if trailer_tag_nodes:
                    pid = trailer_tag_nodes[0].split("/")[-1]
                    api_url = f"https://www.mgstage.com/sampleplayer/sampleRespons.php?pid={pid}"
                    api_headers = cls.headers.copy(); api_headers['Referer'] = url 
                    api_headers['X-Requested-With'] = 'XMLHttpRequest'; api_headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
                    res_json = SiteUtil.get_response(api_url, proxy_url=proxy_url, headers=api_headers).json()
                    if res_json and res_json.get("url"):
                        trailer_base_url = res_json["url"].split(".ism")[0]
                        trailer_url = trailer_base_url + ".mp4"
                        trailer_title = entity.tagline if entity.tagline else (entity.title if entity.title else code) 
                        entity.extras.append(EntityExtra("trailer", trailer_title, "mp4", trailer_url))
                        logger.info(f"{cls.module_char}: Trailer added: {trailer_url}") # cls.module_char 사용
                    else: logger.warning(f"{cls.module_char}: Trailer API response invalid for {pid}.") # cls.module_char 사용
                else: logger.debug(f"{cls.module_char}: Trailer button not found for {code}.") # cls.module_char 사용
            except Exception as e_trailer:
                logger.exception(f"{cls.module_char}: Error processing trailer for {code}: {e_trailer}") # cls.module_char 사용
        # --- 예고편 처리 끝 ---

        # --- Shiroutoname 보정 (DVD 클래스에서는 실행되지 않음) ---
        if cls.module_char == 'D': # 이 조건 때문에 아래 코드는 실행되지 않음
            try:
                entity = SiteUtil.shiroutoname_info(entity) 
                logger.debug(f"{cls.module_char}: Shiroutoname info applied (if found) for {code}.")
            except Exception as e_shirouto: 
                logger.exception(f"{cls.module_char}: Shiroutoname 보정 중 예외 for {code}: {e_shirouto}")
        # --- 보정 끝 ---

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
