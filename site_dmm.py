# -*- coding: utf-8 -*-
import json
import re
import urllib.parse as py_urllib_parse
from lxml import html, etree
import os

# lib_metadata 패키지 내 다른 모듈 import
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings, EntityThumb
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

class SiteDmm:
    site_name = "dmm"
    site_base_url = "https://www.dmm.co.jp"
    fanza_av_url = "https://video.dmm.co.jp/av/"
    module_char = "C"; site_char = "D"

    dmm_base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
        "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1",
        "Referer": site_base_url + "/", "DNT": "1", "Cache-Control": "max-age=0", "Connection": "keep-alive",
    }

    PTN_SEARCH_CID = re.compile(r"\/cid=(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^(h_)?\d*(?P<real>[a-zA-Z]+)(?P<no>\d+)([a-zA-Z]+)?$")
    PTN_ID = re.compile(r"\d{2}id", re.I)
    PTN_RATING = re.compile(r"(?P<rating>[\d|_]+)\.gif") # v_old 패턴

    age_verified = False
    last_proxy_used = None
    _ps_url_cache = {} # code: {'ps': ps_url, 'type': content_type}

    @classmethod
    def _get_request_headers(cls, referer=None):
        headers = cls.dmm_base_headers.copy()
        if referer: headers['Referer'] = referer
        return headers

    @classmethod
    def _ensure_age_verified(cls, proxy_url=None):
        if not cls.age_verified or cls.last_proxy_used != proxy_url:
            logger.debug("Checking/Performing DMM age verification...")
            cls.last_proxy_used = proxy_url
            session_cookies = SiteUtil.session.cookies
            domain_checks = ['.dmm.co.jp', '.dmm.com']
            if any('age_check_done' in session_cookies.get_dict(domain=d) and session_cookies.get_dict(domain=d)['age_check_done'] == '1' for d in domain_checks):
                logger.debug("Age verification cookie found in SiteUtil.session.")
                cls.age_verified = True; return True
            logger.debug("Attempting DMM age verification via confirmation GET...")
            try:
                target_rurl = cls.fanza_av_url
                confirm_path = f"/age_check/=/declared=yes/?rurl={py_urllib_parse.quote(target_rurl, safe='')}"
                age_check_confirm_url = py_urllib_parse.urljoin(cls.site_base_url, confirm_path)
                confirm_headers = cls._get_request_headers(referer=cls.site_base_url + "/")
                confirm_response = SiteUtil.get_response( age_check_confirm_url, method='GET', proxy_url=proxy_url, headers=confirm_headers, allow_redirects=False )
                if confirm_response.status_code == 302 and 'age_check_done=1' in confirm_response.headers.get('Set-Cookie', ''):
                    logger.debug("Age confirmation successful via Set-Cookie.")
                    final_cookies = SiteUtil.session.cookies
                    if any('age_check_done' in final_cookies.get_dict(domain=d) and final_cookies.get_dict(domain=d)['age_check_done'] == '1' for d in domain_checks):
                        logger.debug("age_check_done=1 confirmed in session."); cls.age_verified = True; return True
                    else:
                        logger.warning("Set-Cookie received, but not updated in session. Trying manual set...")
                        SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.co.jp", path="/"); SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.com", path="/")
                        logger.info("Manually set age_check_done cookie."); cls.age_verified = True; return True
                else: logger.warning(f"Age check failed (Status:{confirm_response.status_code} or cookie missing).")
            except Exception as e: logger.exception(f"Age verification exception: {e}")
            cls.age_verified = False; return False
        else:
            logger.debug("Age verification already done."); return True

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        if not cls._ensure_age_verified(proxy_url=proxy_url): return []
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd": keyword = keyword[:-3]
        keyword = keyword.replace("-", " ")
        keyword_tmps = keyword.split(" ")
        if len(keyword_tmps) == 2: dmm_keyword = keyword_tmps[0] + keyword_tmps[1].zfill(5)
        else: dmm_keyword = keyword
        logger.debug("keyword [%s] -> [%s]", keyword, dmm_keyword)

        search_params = { 'redirect': '1', 'enc': 'UTF-8', 'category': '', 'searchstr': dmm_keyword }
        search_url = f"{cls.site_base_url}/search/?{py_urllib_parse.urlencode(search_params)}"
        logger.info(f"Using search URL: {search_url}")
        search_headers = cls._get_request_headers(referer=cls.fanza_av_url)
        tree = None
        try:
            tree = SiteUtil.get_tree(search_url, proxy_url=proxy_url, headers=search_headers, allow_redirects=True)
            if tree is None: logger.warning("Search tree is None."); return []
            title_tags_check = tree.xpath('//title/text()')
            if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]: logger.error("Age page received during search."); return []
        except Exception as e: logger.exception(f"Failed to get tree for search: {e}"); return []

        # 검색 결과 목록 추출
        list_xpath = '//div[contains(@class, "grid-cols-4")]//div[contains(@class, "border-r") and contains(@class, "border-b")]'
        lists = []
        try: lists = tree.xpath(list_xpath)
        except Exception as e_xpath: logger.error(f"XPath error: {e_xpath}")
        if not lists: logger.warning(f"No items found using Desktop Grid XPath."); return []

        ret = []; score = 60

        # --- 1단계: 모든 검색 결과 일단 파싱하여 ret 리스트 생성 ---
        for node in lists[:10]: # 상위 10개만 처리
            try:
                item = EntityAVSearch(cls.site_name)
                href = None; original_ps_url = None; content_type = "unknown" # 기본 타입 unknown

                # --- 링크 및 이미지 URL 추출 ---
                link_tag_img = node.xpath('.//a[contains(@class, "flex justify-center")]');
                if not link_tag_img: continue
                img_link_href = link_tag_img[0].attrib.get("href", "").lower() # 이미지 링크 (타입 판별 보조)
                img_tag = link_tag_img[0].xpath('./img/@src')
                if not img_tag: continue
                original_ps_url = img_tag[0] # 작은 포스터 URL

                title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]')
                if not title_link_tag: continue
                title_link_with_p = node.xpath('.//a[contains(@href, "/detail/=/cid=") and ./p[contains(@class, "hover:text-linkHover")]]')
                # 제목 링크 우선순위: <p> 태그 포함 > 일반 링크
                title_link_tag = title_link_with_p[0] if title_link_with_p else title_link_tag[0]
                title_link_href = title_link_tag.attrib.get("href", "").lower() # 제목 링크 (타입 판별 및 코드 추출 주 사용)

                # 최종 사용할 href 결정 (제목 링크 우선)
                href = title_link_href if title_link_href else img_link_href

                # --- ★★★ 블루레이 및 컨텐츠 타입 판별 로직 추가 ★★★ ---
                is_bluray = False
                # 블루레이 스팬 태그 확인 (텍스트 내용과 클래스 동시 확인)
                bluray_span = node.xpath('.//span[contains(@class, "text-blue-600") and contains(text(), "Blu-ray")]')
                if bluray_span:
                    is_bluray = True
                    content_type = 'bluray' # 블루레이로 타입 확정
                    logger.debug("Blu-ray span detected.")

                # 블루레이가 아니라면 기존 방식으로 판별
                if not is_bluray and href:
                    if "/digital/videoa/" in href:
                        content_type = "videoa"
                    elif "/mono/dvd/" in href:
                        content_type = "dvd"

                item.content_type = content_type # 판별된 타입 저장

                # --- 제목 추출 ---
                title_p_tag = title_link_tag.xpath('./p[contains(@class, "hover:text-linkHover")]')
                raw_title = title_p_tag[0].text_content().strip() if title_p_tag else "" # 원본 제목
                item.title = raw_title # Entity에 원본 제목 우선 저장

                # --- 이미지 URL 처리 ---
                if not original_ps_url: continue
                if original_ps_url.startswith("//"): original_ps_url = "https:" + original_ps_url
                item.image_url = original_ps_url # Entity에 이미지 URL 저장

                # --- 매뉴얼 모드 이미지 처리 ---
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try:
                        item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"ImgProcErr:{e_img}")

                # --- 코드 추출 ---
                if not href: continue
                match_cid = cls.PTN_SEARCH_CID.search(href)
                if match_cid:
                    item.code = cls.module_char + cls.site_char + match_cid.group("code")
                else:
                    logger.warning(f"Could not extract CID from href: {href}")
                    continue # 코드 없으면 처리 불가

                # --- 중복 코드 체크 ---
                # code 비교는 여기서 수행 (타입별 중복 허용 안 함)
                if any(i.get("code") == item.code for i in ret):
                    logger.debug(f"Duplicate code found, skipping: {item.code}")
                    continue

                # --- 제목 접두사 추가 ---
                type_prefix = ""
                if content_type == 'dvd': type_prefix = "[DVD] "
                elif content_type == 'videoa': type_prefix = "[VideoA] "
                elif content_type == 'bluray': type_prefix = "[Blu-ray] " # 블루레이 접두사

                # 제목 최종 설정 (접두사 포함)
                if not item.title or item.title == "Not Found": # 제목 없으면 품번으로
                    # item.code가 여기서 필요하므로 위에서 먼저 설정됨
                    match_real_no_for_title = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                    default_title = match_real_no_for_title.group("real").upper() + "-" + str(int(match_real_no_for_title.group("no"))).zfill(3) if match_real_no_for_title else item.code[2:].upper()
                    item.title = type_prefix + default_title
                else:
                    # 기존 제목에 접두사 추가
                    # item.title = type_prefix + item.title # 이 라인은 이미 원본 제목이 저장되어 있으므로, 아래 ko 번역에서 처리하거나 별도 필드에 저장
                    pass # 이미 item.title에는 원본 제목 저장됨

                # --- 캐시 저장 (판별된 타입 포함) ---
                if item.code and original_ps_url:
                    cls._ps_url_cache[item.code] = {'ps': original_ps_url, 'type': content_type}
                    logger.debug(f"Stored ps & type for {item.code} in cache: {content_type}")

                # --- 번역 (원본 제목 기준, 접두사 포함 X) ---
                if manual:
                    item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + type_prefix + item.title # 매뉴얼 모드 시 접두사 포함
                else:
                    # 번역 시에는 원본 제목(접두사 제외) 사용
                    trans_title = SiteUtil.trans(item.title, do_trans=do_trans) if do_trans and item.title else item.title
                    item.title_ko = type_prefix + trans_title # 번역된 제목에 접두사 추가

                # --- 점수 계산 ---
                match_real_no = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                item_ui_code_base = match_real_no.group("real") + match_real_no.group("no") if match_real_no else item.code[2:]
                current_score = 0
                # 검색 키워드와 품번 비교하여 점수 산정
                if len(keyword_tmps) == 2:
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif item_ui_code_base.replace("0", "") == dmm_keyword.replace("0", ""): current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    elif keyword_tmps[0] in item.code and keyword_tmps[1] in item.code: current_score = score
                    elif keyword_tmps[0] in item.code or keyword_tmps[1] in item.code: current_score = 60
                    else: current_score = 20
                else: # 검색어가 하나일 때
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    else: current_score = 20
                item.score = current_score # 계산된 점수 저장
                # 점수 감소 로직 (100점 아니면 다음 아이템 기본 점수 감소)
                if current_score < 100 and score > 20: score -= 5

                # --- UI 코드 생성 ---
                if match_real_no:
                    real = match_real_no.group("real").upper(); no = match_real_no.group("no")
                    try: item.ui_code = f"{real}-{str(int(no)).zfill(3)}"
                    except ValueError: item.ui_code = f"{real}-{no}" # 숫자로 변환 안 되면 그대로 사용
                else: # 정규식 매칭 안 될 경우 대비
                    tmp = item.code[2:].upper();
                    if tmp.startswith("H_"): tmp = tmp[2:]
                    m = re.match(r"([a-zA-Z]+)(\d+.*)", tmp) # 간단한 분리 시도
                    if m:
                        real = m.group(1); rest = m.group(2); num_m = re.match(r"(\d+)", rest)
                        item.ui_code = f"{real}-{str(int(num_m.group(1))).zfill(3)}" if num_m else f"{real}-{rest}"
                    else: item.ui_code = tmp # 최후의 수단: 코드 그대로 사용
                # --- 최종 결과 저장 ---
                logger.debug(f"Item Processed: Type={content_type}, Score={item.score}, Code={item.code}, UI Code={item.ui_code}, Title(KO)={item.title_ko}")
                ret.append(item.as_dict()) # 결과 리스트에 추가
            except Exception as e_inner: logger.exception(f"아이템 처리 중 예외 발생: {e_inner}")
        # --- 파싱 완료 ---

        # --- 2단계: Blu-ray 필터링 수행 (ret 리스트 생성 완료 후) ---
        if not ret: return []

        filtered_ret = []
        dvd_ui_codes = {item.get('ui_code') for item in ret if item.get('content_type') == 'dvd' and item.get('ui_code')}

        for item in ret:
            item_content_type = item.get('content_type')
            item_ui_code = item.get('ui_code')

            logger.debug(f"Processing item for filtering: Code={item.get('code')}, Type={item_content_type}, UI Code={item_ui_code}")

            is_bluray_to_filter = item_content_type == 'bluray' and item_ui_code is not None

            if is_bluray_to_filter:
                dvd_exists = item_ui_code in dvd_ui_codes
                logger.debug(f"  Item is Blu-ray. DVD exists for UI Code '{item_ui_code}'? {dvd_exists}")

                if dvd_exists:
                    logger.info(f"Excluding Blu-ray item '{item.get('code')}' because DVD version exists.")
                    # 제외 (filtered_ret에 추가 안 함)
                else:
                    logger.debug(f"  Keep Blu-ray item '{item.get('code')}' as no matching DVD found.")
                    filtered_ret.append(item) # DVD 없으면 Blu-ray 추가
            else:
                logger.debug(f"  Keep Non-Blu-ray item '{item.get('code')}'")
                filtered_ret.append(item) # Blu-ray 아니면 무조건 추가
        # --- 필터링 완료 ---

        # --- 3단계: 최종 결과 처리 (filtered_ret 사용) ---
        final_result = filtered_ret
        logger.debug(f"필터링 후 결과 개수: {len(final_result)}")

        # 정렬 로직 (final_result 대상)
        type_priority = {'dvd': 1, 'videoa': 2, 'bluray': 3, 'unknown': 99}
        sorted_result = sorted(final_result, key=lambda k: k.get("score", 0), reverse=True)
        sorted_result = sorted(sorted_result, key=lambda k: type_priority.get(k.get('content_type', 'unknown'), 99))

        # 로깅 (sorted_result 대상)
        if sorted_result:
            log_count = min(len(sorted_result), 5)
            logger.debug(f"정렬된 상위 {log_count}개 결과 (Blu-ray 필터링 적용):")
            for idx, item_log in enumerate(sorted_result):
                log_type = item_log.get('content_type')
                logger.debug(f"  {idx+1}. Score={item_log.get('score')}, Type={log_type}, Code={item_log.get('code')}, UI Code={item_log.get('ui_code')}, Title={item_log.get('title_ko')}")

        # --- 재시도 로직 ---
        if not sorted_result and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6) # 6자리로 재시도
            logger.debug(f"결과 없음. 6자리 숫자로 재시도: {new_title}")
            # 재귀 호출 시 manual 플래그 등 전달 확인
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)

        return sorted_result

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            do_trans_arg = kwargs.get('do_trans', True)
            proxy_url_arg = kwargs.get('proxy_url', None)
            image_mode_arg = kwargs.get('image_mode', "0")
            manual_arg = kwargs.get('manual', False)

            data_list = cls.__search(keyword, do_trans=do_trans_arg, proxy_url=proxy_url_arg, image_mode=image_mode_arg, manual=manual_arg)

        except Exception as exception:
            logger.exception("SearchErr:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data_list else "no_match"
            ret["data"] = data_list
        return ret

    @classmethod
    def __img_urls(cls, tree, content_type='unknown'):
        logger.debug(f"Extracting raw image URLs for type: {content_type}")
        img_urls = {'ps': "", 'pl': "", 'arts': [], 'specific_poster_candidate': None}
        try:
            # videoa, vr 은 동일 로직 사용
            if content_type == 'videoa' or content_type == 'vr':
                logger.debug(f"Extracting {content_type} URLs using videoa logic (href first)...")
                sample_image_links = tree.xpath('//div[@id="sample-image-block"]//a[.//img]')

                if not sample_image_links:
                    logger.warning("Could not find 'a' tags with 'img' inside sample-image-block.")
                    all_img_tags = tree.xpath('//div[@id="sample-image-block"]//img/@src')
                    if not all_img_tags: return img_urls
                    img_urls['pl'] = py_urllib_parse.urljoin(cls.site_base_url, all_img_tags[0].strip()) if all_img_tags else ""
                    img_urls['arts'] = [py_urllib_parse.urljoin(cls.site_base_url, src.strip()) for src in all_img_tags[1:] if src.strip()]
                    logger.warning("Using fallback: extracted only img src attributes.")
                    return img_urls

                processed_arts = []
                pl_url = None
                specific_poster_url = None

                for idx, a_tag in enumerate(sample_image_links):
                    final_image_url = None
                    source_type = "unknown"
                    href = a_tag.attrib.get("href", "").strip()
                    is_href_image = bool(href and re.search(r'\.(jpg|jpeg|png|webp)$', href, re.IGNORECASE))

                    if is_href_image:
                        final_image_url = py_urllib_parse.urljoin(cls.site_base_url, href)
                        source_type = "href"
                    else:
                        img_tag_src_list = a_tag.xpath('.//img/@src')
                        if img_tag_src_list:
                            src = img_tag_src_list[0].strip()
                            is_src_image = bool(src and re.search(r'\.(jpg|jpeg|png|webp)$', src, re.IGNORECASE))
                            if is_src_image:
                                final_image_url = py_urllib_parse.urljoin(cls.site_base_url, src)
                                source_type = "src"

                    if not final_image_url:
                        logger.warning(f"Sample image {idx}: Could not find valid URL in href or src.")
                        continue

                    filename = final_image_url.split('/')[-1].lower()
                    logger.debug(f"Sample image {idx}: Found URL='{filename}' (from {source_type})")

                    is_current_pl = False
                    is_current_specific = False
                    if filename.endswith("pl.jpg") or filename.endswith("jp-0.jpg"):
                        is_current_pl = True
                    match_specific = re.match(r".*jp-(\d+)\.jpg$", filename)
                    if specific_poster_url is None and match_specific:
                        is_current_specific = True

                    if is_current_pl:
                        if pl_url is None: pl_url = final_image_url; logger.debug(f"  -> Assigned as 'pl'.")
                        else: logger.warning(f"  -> Another 'pl' found, adding to arts: {filename}"); processed_arts.append(final_image_url)
                    elif is_current_specific:
                        if specific_poster_url is None: specific_poster_url = final_image_url; logger.debug(f"  -> Assigned as 'specific_poster_candidate'.")
                        else: logger.debug(f"  -> Another 'specific' found, adding to arts: {filename}"); processed_arts.append(final_image_url)
                    elif idx == 0 and pl_url is None:
                        pl_url = final_image_url; logger.debug(f"  -> Assigned as 'pl' (Fallback - first image).")
                    else:
                        logger.debug(f"  -> Adding to potential arts."); processed_arts.append(final_image_url)

                img_urls['pl'] = pl_url if pl_url else ""
                img_urls['specific_poster_candidate'] = specific_poster_url if specific_poster_url else ""

                unique_arts = []
                urls_to_exclude = {img_urls['pl'], img_urls['specific_poster_candidate']}
                for art_url in processed_arts:
                    # 중복 제거 및 None/빈문자열 제외
                    if art_url and art_url not in urls_to_exclude and art_url not in unique_arts:
                        unique_arts.append(art_url)
                img_urls['arts'] = unique_arts
                logger.debug(f"Found {len(img_urls['arts'])} unique arts links.")

            elif content_type == 'dvd': # 블루레이도 이 로직 사용 (캐시에서 type 'dvd'로 받음)
                logger.debug("Extracting dvd/bluray URLs using v_old logic...")
                pl_xpath = '//div[@id="fn-sampleImage-imagebox"]/img/@src'
                pl_tags = tree.xpath(pl_xpath)
                raw_pl = pl_tags[0].strip() if pl_tags else ""
                if raw_pl:
                    img_urls['pl'] = ("https:" + raw_pl) if not raw_pl.startswith("http") else raw_pl
                    logger.debug(f"Found dvd/br pl: {img_urls['pl']}")
                else: logger.warning("Could not find dvd/br pl using XPath: %s", pl_xpath)
                img_urls['ps'] = "" # ps는 캐시 사용

                arts_xpath = '//li[contains(@class, "fn-sampleImage__zoom") and not(@data-slick-index="0")]//img'
                arts_tags = tree.xpath(arts_xpath)
                if arts_tags:
                    processed_arts = []
                    for tag in arts_tags:
                        src = tag.attrib.get("src") or tag.attrib.get("data-lazy")
                        if src:
                            src = src.strip()
                            if not src.startswith("http"): src = "https:" + src
                            processed_arts.append(src)
                    unique_arts = []; [unique_arts.append(x) for x in processed_arts if x not in unique_arts]
                    img_urls['arts'] = unique_arts
                    logger.debug(f"Found {len(img_urls['arts'])} arts links for dvd/br.")
                else: logger.warning("Could not find dvd/br arts using XPath: %s", arts_xpath)
            else:
                logger.error(f"Unknown content type '{content_type}' for image extraction.")

        except Exception as e:
            logger.exception(f"Error extracting image URLs: {e}")
            img_urls = {'ps': "", 'pl': "", 'arts': [], 'specific_poster_candidate': None}

        logger.debug(f"Extracted img_urls: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} specific_poster={bool(img_urls.get('specific_poster_candidate'))} arts={len(img_urls.get('arts',[]))}")
        return img_urls


    @classmethod
    def __info( cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, ps_to_poster=False, crop_mode=None, **kwargs ):
        try:
            from lxml import html
        except ImportError:
            logger.error("lxml library is required for HTML parsing but not installed.")
            html = None
        logger.info(f"Getting detail info for {code}")
        cached_data = cls._ps_url_cache.get(code, {})
        ps_url_from_cache = cached_data.get('ps')
        content_type = cached_data.get('type', 'unknown')
        original_search_content_type = content_type

        if ps_url_from_cache: logger.debug(f"Using cached ps_url: {ps_url_from_cache}")
        else: logger.warning(f"ps_url for {code} not found in cache.")

        if not cls._ensure_age_verified(proxy_url=proxy_url): raise Exception(f"Age verification failed for {code}.")

        cid_part = code[2:]
        detail_url = None
        is_vr_content = False

        # --- 이미지 서버 관련 설정값 추출 ---
        use_image_server = kwargs.get('use_image_server', False)
        image_server_url = kwargs.get('image_server_url', '').rstrip('/') if use_image_server else ''
        image_server_local_path = kwargs.get('image_server_local_path', '') if use_image_server else ''
        image_path_segment = kwargs.get('url_prefix_segment', 'unknown/unknown') # logic_jav_censored에서 'jav/cen' 조합 전달 가정
        ps_to_poster_setting = kwargs.get('ps_to_poster', False) # ps_to_poster 값 추출

        logger.debug(f"Image Server Mode Check: image_mode={image_mode}, use_image_server={use_image_server}")
        if use_image_server and image_mode == '4':
            logger.info(f"Image Server Enabled: URL={image_server_url}, LocalPath={image_server_local_path}, PathSegment={image_path_segment}")
        # --- 이미지 서버 설정 끝 ---

        # 상세 페이지 URL 생성
        if content_type == 'videoa' or content_type == 'vr':
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
        elif content_type == 'dvd':
            detail_url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={cid_part}/"
        else:
            logger.warning(f"Unknown type '{content_type}'. Trying 'videoa' path.")
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
            content_type = 'videoa'

        logger.info(f"Accessing DMM detail page (Processing as: {content_type}, Original search type: {original_search_content_type}): {detail_url}")
        referer_url = cls.fanza_av_url if content_type in ['videoa', 'vr'] else (cls.site_base_url + "/mono/dvd/")
        info_headers = cls._get_request_headers(referer=referer_url)
        tree = None
        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=info_headers, timeout=30)
            if tree is None: raise Exception(f"get_tree returned None for {detail_url}.")
        except Exception as e: logger.exception(f"Failed get/process detail tree: {e}"); raise

        entity = EntityMovie(cls.site_name, code); entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"

        entity.thumb = []
        entity.fanart = []

        ui_code_for_image = "" # 이미지 파일명용 최종 UI 코드

        # === 디지털 비디오 / VR 처리 (videoa, vr) ===
        if content_type == 'videoa' or content_type == 'vr':
            logger.debug(f"Processing as VIDEOA/VR type...")
            try:
                # 제목 파싱 및 VR 플래그 설정 (Videoa/VR 블록 내에서)
                raw_title_text = ""
                try:
                    title_node = tree.xpath('//h1[@id="title"]')
                    if title_node:
                        raw_title_text = title_node[0].text_content().strip()
                        if raw_title_text.startswith("【VR】"): is_vr_content = True
                        entity.tagline = raw_title_text
                    else: logger.warning("Videoa/VR: Could not find h1#title.")
                except Exception as e_title_parse: logger.warning(f"Videoa/VR: Error parsing title: {e_title_parse}")
                if entity.tagline: entity.tagline = SiteUtil.trans(entity.tagline, do_trans=do_trans)

                # 이미지 URL 추출 (Videoa/VR)
                ps_url = ps_url_from_cache
                pl_url = None; arts = []; final_poster_url = None; final_poster_crop = None
                pl_valid = False; is_pl_vertical = False; is_pl_landscape = False
                specific_valid = False; is_specific_vertical = False
                ps_valid = bool(ps_url)

                try:
                    raw_img_urls = cls.__img_urls(tree, content_type='videoa')
                    pl_url = raw_img_urls.get('pl')
                    specific_poster_candidate = raw_img_urls.get('specific_poster_candidate')
                    arts = raw_img_urls.get('arts', [])

                    if pl_url:
                        try:
                            im_pl = SiteUtil.imopen(pl_url, proxy_url=proxy_url)
                            if im_pl: pl_valid = True; w, h = im_pl.size; is_pl_vertical = w < h; is_pl_landscape = w > h
                        except Exception: pass

                    if specific_poster_candidate:
                        try: # specific 후보 유효성 및 세로 여부만 체크
                            im_spec = SiteUtil.imopen(specific_poster_candidate, proxy_url=proxy_url)
                            if im_spec: specific_valid = True; w, h = im_spec.size; is_specific_vertical = w < h
                        except Exception: pass

                    # 1순위: PL 처리
                    if pl_valid and ps_valid:
                        # <<< 수정: is_hq_poster / has_hq_poster 호출 유지 >>>
                        if is_pl_vertical and SiteUtil.is_hq_poster(ps_url, pl_url, proxy_url=proxy_url):
                            final_poster_url = pl_url; final_poster_crop = None
                        elif is_pl_landscape:
                            crop_pos = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                            if crop_pos: final_poster_url = pl_url; final_poster_crop = crop_pos
                    # 2순위: Specific 처리
                    if final_poster_url is None and specific_valid and ps_valid:
                        if is_specific_vertical and SiteUtil.is_hq_poster(ps_url, specific_poster_candidate, proxy_url=proxy_url):
                            final_poster_url = specific_poster_candidate; final_poster_crop = None
                        else: # specific이 가로여도 크롭 시도
                            crop_pos_spec = SiteUtil.has_hq_poster(ps_url, specific_poster_candidate, proxy_url=proxy_url)
                            if crop_pos_spec: final_poster_url = specific_poster_candidate; final_poster_crop = crop_pos_spec
                    # 3순위: Fallback
                    if final_poster_url is None:
                        if ps_valid: final_poster_url = ps_url; final_poster_crop = None
                        else: final_poster_url = None

                    logger.info(f"Final Image Decision (Videoa/VR): Poster='{final_poster_url}'(Crop:{final_poster_crop}), Landscape will use PL ('{pl_url}') if available.")
                except Exception as e_img_det: logger.exception(f"Videoa/VR: Error determining final images: {e_img_det}")

                # 메타데이터 파싱 (Videoa/VR)
                info_table_xpath = '//table[contains(@class, "mg-b20")]//tr'
                tags = tree.xpath(info_table_xpath)
                premiered_shouhin = None; premiered_haishin = None
                for tag in tags:
                    key_node = tag.xpath('./td[@class="nw"]/text()')
                    value_node_list = tag.xpath('./td[not(@class="nw")]')
                    if not key_node or not value_node_list: continue
                    key = key_node[0].strip().replace("：", "")
                    value_node = value_node_list[0]; value_text_all = value_node.text_content().strip()
                    if value_text_all == "----" or not value_text_all: continue

                    if "品番" in key:
                        value = value_text_all; match_id = cls.PTN_ID.search(value); id_before = None
                        if match_id: id_before = match_id.group(0); value = value.lower().replace(id_before, "zzid")
                        match_real = cls.PTN_SEARCH_REAL_NO.match(value); formatted_code = value_text_all.upper()
                        if match_real:
                            label = match_real.group("real").upper()
                            if id_before is not None: label = label.replace("ZZID", id_before.upper())
                            num_str = str(int(match_real.group("no"))).zfill(3)
                            formatted_code = f"{label}-{num_str}"
                            if entity.tag is None: entity.tag = []
                            if label not in entity.tag: entity.tag.append(label)
                        entity.title = entity.originaltitle = entity.sorttitle = formatted_code
                        ui_code_for_image = formatted_code # 테이블 파싱 값으로 확정
                        entity.ui_code = ui_code_for_image
                        logger.debug(f"Videoa/VR: 品番 파싱 완료, ui_code_for_image='{ui_code_for_image}'")
                        continue
                    # ... (나머지 Videoa/VR 메타데이터 파싱 로직 유지) ...
                    elif "配信開始日" in key: premiered_haishin = value_text_all.replace("/", "-")
                    elif "商品発売日" in key: premiered_shouhin = value_text_all.replace("/", "-")
                    elif "収録時間" in key: m=re.search(r"(\d+)",value_text_all); entity.runtime = int(m.group(1)) if m else None
                    elif "出演者" in key:
                        actors = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                        if actors: entity.actor = [EntityActor(name) for name in actors]
                        elif value_text_all != '----': entity.actor = [EntityActor(n.strip()) for n in value_text_all.split('/') if n.strip()]
                    elif "監督" in key:
                        directors = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                        entity.director = directors[0] if directors else (value_text_all if value_text_all != '----' else None)
                    elif "シリーズ" in key:
                        if entity.tag is None: entity.tag = []
                        series = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                        s_name = series[0] if series else (value_text_all if value_text_all != '----' else None)
                        if s_name and SiteUtil.trans(s_name, do_trans=do_trans) not in entity.tag: entity.tag.append(SiteUtil.trans(s_name, do_trans=do_trans))
                    elif "メーカー" in key:
                        if entity.studio is None:
                            makers = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                            m_name = makers[0] if makers else (value_text_all if value_text_all != '----' else None)
                            if m_name: entity.studio = SiteUtil.trans(m_name, do_trans=do_trans)
                    elif "レーベル" in key:
                        labels = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                        l_name = labels[0] if labels else (value_text_all if value_text_all != '----' else None)
                        if l_name:
                            if do_trans: entity.studio = SiteUtil.av_studio.get(l_name, SiteUtil.trans(l_name))
                            else: entity.studio = l_name
                    elif "ジャンル" in key:
                        entity.genre = []
                        for genre_ja_tag in value_node.xpath('.//a'):
                            genre_ja = genre_ja_tag.text_content().strip();
                            if not genre_ja or "％OFF" in genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                    elif "平均評価" in key:
                        rating_img = value_node.xpath('.//img/@src')
                        if rating_img:
                            match_rate = cls.PTN_RATING.search(rating_img[0])
                            if match_rate:
                                rate_str = match_rate.group("rating").replace("_",".")
                                try:
                                    rate_val = float(rate_str) / 10.0
                                    if 0 <= rate_val <= 5:
                                        img_url = "https:" + rating_img[0] if rating_img[0].startswith("//") else rating_img[0]
                                        rating_image_url_for_entity = img_url if not (use_image_server and image_mode == '4') else None
                                        if not entity.ratings: entity.ratings = [EntityRatings(rate_val, max=5, name="dmm", image_url=rating_image_url_for_entity)]
                                        else: entity.ratings[0].value = rate_val; entity.ratings[0].image_url = rating_image_url_for_entity
                                except ValueError: logger.warning(f"Rating conversion error (videoa): {rate_str}")

                final_premiered = premiered_shouhin or premiered_haishin
                if final_premiered: entity.premiered = final_premiered; entity.year = int(final_premiered[:4]) if final_premiered and len(final_premiered) >= 4 else None
                else: logger.warning("Videoa/VR: Premiered date not found."); entity.premiered = None; entity.year = None

                plot_xpath = '//div[@class="mg-b20 lh4"]/text()'
                plot_nodes = tree.xpath(plot_xpath)
                if plot_nodes:
                    plot_text = "\n".join([p.strip() for p in plot_nodes if p.strip()]).split("※")[0].strip()
                    if plot_text: entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                else: logger.warning("Videoa/VR: Plot not found.")

                # 이미지 처리 및 저장 (Videoa/VR 블록 내에서)
                if use_image_server and image_mode == '4' and image_server_url and image_server_local_path and ui_code_for_image:
                    logger.info(f"Saving images to Image Server for {ui_code_for_image} (Videoa/VR)...")
                    # PS 저장
                    if ps_url: SiteUtil.save_image_to_server_path(ps_url, 'ps', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                    # PL 저장 및 Landscape 썸네일 생성
                    if pl_url:
                        pl_relative_path = SiteUtil.save_image_to_server_path(pl_url, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                        if pl_relative_path:
                            landscape_server_url = f"{image_server_url}/{pl_relative_path}"
                            entity.thumb.append(EntityThumb(aspect="landscape", value=landscape_server_url))
                            logger.info(f"Image Server: Landscape thumb generated (from PL): {landscape_server_url}")
                    # 최종 Poster (p) 저장 및 썸네일 생성
                    if final_poster_url:
                        p_relative_path = SiteUtil.save_image_to_server_path(final_poster_url, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop)
                        if p_relative_path:
                            poster_server_url = f"{image_server_url}/{p_relative_path}"
                            entity.thumb.append(EntityThumb(aspect="poster", value=poster_server_url))
                            logger.info(f"Image Server: Poster thumb generated: {poster_server_url}")
                    # Arts 저장 및 팬아트 생성
                    processed_fanart_count = 0
                    urls_to_exclude_from_arts = {final_poster_url, pl_url}
                    for idx, art_url in enumerate(arts):
                        if processed_fanart_count >= max_arts: break
                        if art_url and art_url not in urls_to_exclude_from_arts:
                            art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                            if art_relative_path:
                                fanart_server_url = f"{image_server_url}/{art_relative_path}"
                                entity.fanart.append(fanart_server_url); processed_fanart_count += 1
                    logger.info(f"Image Server: Processed {processed_fanart_count} fanarts...")

                elif not (use_image_server and image_mode == '4'): # 일반 모드 (Videoa/VR)
                    logger.info("Using Normal Image Processing Mode (Videoa/VR)...")

                    if pl_url and pl_url != final_poster_url: # PL이 있고 포스터로 안 쓰였으면
                        try:
                            processed_landscape = SiteUtil.process_image_mode(image_mode, pl_url, proxy_url=proxy_url)
                            if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))
                        except Exception as e_proc_land: logger.error(f"Error processing landscape (normal): {e_proc_land}")
                    if final_poster_url:
                        processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_url, proxy_url=proxy_url, crop_mode=final_poster_crop)
                        if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                    processed_fanart_count = 0
                    urls_to_exclude_from_arts = {final_poster_url, pl_url}
                    for art_url in arts:
                        if processed_fanart_count >= max_arts: break
                        if art_url and art_url not in urls_to_exclude_from_arts:
                            processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                            if processed_art: entity.fanart.append(processed_art); processed_fanart_count += 1
                    logger.debug(f"Normal Mode (Videoa/VR): Final Thumb={entity.thumb}, Fanart Count={len(entity.fanart)}")


                # 예고편 처리 (Videoa/VR)
                entity.extras = []
                if use_extras:
                    trailer_title = entity.tagline if entity.tagline else raw_title_text if raw_title_text else code
                    trailer_url = None
                    try:
                        if is_vr_content:
                            vr_player_page_url = f"{cls.site_base_url}/digital/-/vr-sample-player/=/cid={cid_part}/"
                            player_headers = cls._get_request_headers(referer=detail_url)
                            vr_player_html = SiteUtil.get_text(vr_player_page_url, proxy_url=proxy_url, headers=player_headers)
                            if vr_player_html:
                                match_js_var = re.search(r'var\s+sampleUrl\s*=\s*["\']([^"\']+)["\']', vr_player_html)
                                if match_js_var: trailer_url = "https:" + match_js_var.group(1) if match_js_var.group(1).startswith("//") else match_js_var.group(1)
                        else:
                            ajax_url = py_urllib_parse.urljoin(cls.site_base_url, f"/digital/videoa/-/detail/ajax-movie/=/cid={cid_part}/")
                            ajax_headers = cls._get_request_headers(referer=detail_url); ajax_headers['Accept'] = 'text/html, */*; q=0.01'; ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                            ajax_response = SiteUtil.get_response(ajax_url, proxy_url=proxy_url, headers=ajax_headers)
                            if ajax_response and ajax_response.status_code == 200 and ajax_response.text.strip():
                                iframe_tree = html.fromstring(ajax_response.text)
                                iframe_srcs = iframe_tree.xpath("//iframe/@src")
                                if iframe_srcs:
                                    iframe_url = py_urllib_parse.urljoin(ajax_url, iframe_srcs[0])
                                    iframe_headers = cls._get_request_headers(referer=ajax_url)
                                    iframe_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=iframe_headers)
                                    if iframe_text:
                                        pos = iframe_text.find("const args = {")
                                        if pos != -1:
                                            json_start = iframe_text.find("{", pos); json_end = iframe_text.find("};", json_start)
                                            if json_start != -1 and json_end != -1:
                                                data_str = iframe_text[json_start : json_end+1]
                                                data = json.loads(data_str)
                                                bitrates = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                                if bitrates and bitrates[0].get("src"): trailer_url = "https:" + bitrates[0]["src"] if bitrates[0]["src"].startswith("//") else bitrates[0]["src"]
                                                if data.get("title") and data["title"].strip(): trailer_title = data["title"].strip()
                        if trailer_url:
                            final_trailer_title = SiteUtil.trans(trailer_title, do_trans=do_trans) if do_trans else trailer_title
                            entity.extras.append(EntityExtra("trailer", final_trailer_title, "mp4", trailer_url))
                    except Exception as extra_e: logger.exception(f"Videoa/VR: Error processing trailer: {extra_e}")

            except Exception as e_parse_videoa_vr_main:
                logger.exception(f"Error processing VIDEOA/VR metadata block: {e_parse_videoa_vr_main}")


        # === DVD / 블루레이 처리 (dvd, bluray) ===
        elif content_type == 'dvd':
            logger.debug(f"Processing as DVD/BLURAY type...")
            try:
                # 제목 파싱 (DVD/BR 블록 내에서)
                raw_title_text = ""
                try:
                    title_node = tree.xpath('//h1[@id="title"]')
                    if title_node: raw_title_text = title_node[0].text_content().strip(); entity.tagline = raw_title_text
                    else: logger.warning("DVD/BR: Could not find h1#title.")
                except Exception as e_title_parse: logger.warning(f"DVD/BR: Error parsing title: {e_title_parse}")
                if entity.tagline: entity.tagline = SiteUtil.trans(entity.tagline, do_trans=do_trans)

                # 이미지 URL 추출 (DVD/BR)
                ps_url = ps_url_from_cache
                pl_url = None
                arts = []
                final_poster_url = None
                final_poster_crop = None

                try:
                    raw_img_urls = cls.__img_urls(tree, content_type='dvd')
                    pl_url = raw_img_urls.get('pl') # 원본 PL URL (주로 가로)
                    arts = raw_img_urls.get('arts', [])

                    # 최종 포스터 결정 로직 (DVD/BR)
                    ps_valid = bool(ps_url); pl_valid = bool(pl_url)
                    if ps_to_poster_setting and ps_valid:
                        final_poster_url = ps_url; final_poster_crop = None
                    else:
                        if pl_valid and ps_valid:
                            loc = SiteUtil.has_hq_poster(ps_url, pl_url, proxy_url=proxy_url)
                            if loc: final_poster_url = pl_url; final_poster_crop = loc
                            else: final_poster_url = ps_url; final_poster_crop = None # 크롭 불가 시 PS
                        elif ps_valid: final_poster_url = ps_url; final_poster_crop = None # PL 없으면 PS
                        else: final_poster_url = None
                    logger.info(f"Final Image Decision (DVD/BR): Poster='{final_poster_url}'(Crop:{final_poster_crop}), Landscape='{pl_url}'") # Landscape는 항상 PL
                except Exception as e_img_det: logger.exception(f"DVD/BR: Error determining final images: {e_img_det}")

                # 메타데이터 파싱 (DVD/BR)
                info_table_xpath = '//div[@class="wrapper-product"]//table//tr'
                tags = tree.xpath(info_table_xpath)
                premiered_shouhin = None; premiered_hatsubai = None; premiered_haishin = None
                for tag in tags:
                    td_tags = tag.xpath(".//td")
                    if len(td_tags) != 2: continue
                    if "평균평가" in td_tags[0].text_content(): # 평점 처리
                        rating_img_tags = td_tags[1].xpath('.//img/@src')
                        if rating_img_tags:
                            match_rating = cls.PTN_RATING.search(rating_img_tags[0])
                            if match_rating:
                                rating_value_str = match_rating.group("rating").replace("_", ".")
                                try:
                                    rating_value = float(rating_value_str) / 10.0
                                    if 0 <= rating_value <= 5:
                                        rating_img_url = "https:" + rating_img_tags[0] if not rating_img_tags[0].startswith("http") else rating_img_tags[0]
                                        rating_image_url_for_entity = rating_img_url if not (use_image_server and image_mode == '4') else None
                                        if not entity.ratings: entity.ratings = [EntityRatings(rating_value, max=5, name="dmm", image_url=rating_image_url_for_entity)]
                                        else: entity.ratings[0].value = rating_value; entity.ratings[0].image_url = rating_image_url_for_entity
                                except ValueError: pass # 변환 오류 무시
                        continue
                    key = td_tags[0].text_content().strip().replace("：", "")
                    value_node = td_tags[1]; value_text_all = value_node.text_content().strip()
                    if value_text_all == "----" or not value_text_all: continue

                    if "品番" in key:
                        value = value_text_all; match_id = cls.PTN_ID.search(value); id_before = None
                        if match_id: id_before = match_id.group(0); value = value.lower().replace(id_before, "zzid")
                        match_real = cls.PTN_SEARCH_REAL_NO.match(value); formatted_code = value_text_all.upper()
                        if match_real:
                            label = match_real.group("real").upper()
                            if id_before is not None: label = label.replace("ZZID", id_before.upper())
                            num_str = str(int(match_real.group("no"))).zfill(3)
                            formatted_code = f"{label}-{num_str}"
                            if entity.tag is None: entity.tag = []
                            if label not in entity.tag: entity.tag.append(label)
                        entity.title = entity.originaltitle = entity.sorttitle = formatted_code
                        ui_code_for_image = formatted_code # 테이블 파싱 값으로 확정
                        entity.ui_code = ui_code_for_image
                        logger.debug(f"DVD/BR: 品番 파싱 완료, ui_code_for_image='{ui_code_for_image}'")
                        continue
                    # ... (나머지 DVD/BR 메타데이터 파싱 로직 유지) ...
                    elif "商品発売日" in key: premiered_shouhin = value_text_all.replace("/", "-")
                    elif "発売日" in key: premiered_hatsubai = value_text_all.replace("/", "-")
                    elif "配信開始日" in key: premiered_haishin = value_text_all.replace("/", "-")
                    elif "収録時間" in key:
                        match_runtime = re.search(r"(\d+)", value_text_all)
                        if match_runtime: entity.runtime = int(match_runtime.group(1))
                    elif "出演者" in key:
                        entity.actor = []
                        for a_tag in value_node.xpath(".//a"):
                            actor_name = a_tag.text_content().strip()
                            if actor_name and actor_name != "▼すべて表示する": entity.actor.append(EntityActor(actor_name))
                    elif "監督" in key:
                        a_tags = value_node.xpath(".//a"); entity.director = a_tags[0].text_content().strip() if a_tags else value_text_all
                    elif "シリーズ" in key:
                        if entity.tag is None: entity.tag = []
                        a_tags = value_node.xpath(".//a"); series_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                        if series_name and SiteUtil.trans(series_name, do_trans=do_trans) not in entity.tag: entity.tag.append(SiteUtil.trans(series_name, do_trans=do_trans))
                    elif "メーカー" in key:
                        if entity.studio is None:
                            a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                            entity.studio = SiteUtil.trans(studio_name, do_trans=do_trans)
                    elif "レーベル" in key:
                        a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                        if do_trans: entity.studio = SiteUtil.av_studio.get(studio_name, SiteUtil.trans(studio_name))
                        else: entity.studio = studio_name
                    elif "ジャンル" in key:
                        entity.genre = []
                        for tag_a in value_node.xpath(".//a"):
                            genre_ja = tag_a.text_content().strip();
                            if "％OFF" in genre_ja or not genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)

                final_premiered = premiered_shouhin or premiered_hatsubai or premiered_haishin
                if final_premiered: entity.premiered = final_premiered; entity.year = int(final_premiered[:4]) if final_premiered and len(final_premiered) >= 4 else None
                else: logger.warning("DVD/BR: Premiered date not found."); entity.premiered = None; entity.year = None

                plot_xpath = '//div[@class="mg-b20 lh4"]/p[@class="mg-b20"]/text()'
                plot_tags = tree.xpath(plot_xpath)
                if plot_tags:
                    plot_text = "\n".join([p.strip() for p in plot_tags if p.strip()]).split("※")[0].strip()
                    entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                else: logger.warning("DVD/BR: Plot not found.")

                # 이미지 처리 및 저장 (DVD/BR 블록 내에서)
                if use_image_server and image_mode == '4' and image_server_url and image_server_local_path and ui_code_for_image:
                    logger.info(f"Saving images to Image Server for {ui_code_for_image} (DVD/BR)...")
                    # PS 저장
                    if ps_url: SiteUtil.save_image_to_server_path(ps_url, 'ps', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                    # PL 저장 및 Landscape 썸네일 생성
                    if pl_url:
                        pl_relative_path = SiteUtil.save_image_to_server_path(pl_url, 'pl', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url)
                        if pl_relative_path:
                            landscape_server_url = f"{image_server_url}/{pl_relative_path}"
                            entity.thumb.append(EntityThumb(aspect="landscape", value=landscape_server_url))
                            logger.info(f"Image Server: Landscape thumb generated (from PL): {landscape_server_url}")
                    # 최종 Poster (p) 저장 및 썸네일 생성
                    if final_poster_url:
                        p_relative_path = SiteUtil.save_image_to_server_path(final_poster_url, 'p', image_server_local_path, image_path_segment, ui_code_for_image, proxy_url=proxy_url, crop_mode=final_poster_crop)
                        if p_relative_path:
                            poster_server_url = f"{image_server_url}/{p_relative_path}"
                            entity.thumb.append(EntityThumb(aspect="poster", value=poster_server_url))
                            logger.info(f"Image Server: Poster thumb generated: {poster_server_url}")
                    # Arts 저장 및 팬아트 생성
                    processed_fanart_count = 0
                    urls_to_exclude_from_arts = {final_poster_url, pl_url}
                    for idx, art_url in enumerate(arts):
                        if processed_fanart_count >= max_arts: break
                        if art_url and art_url not in urls_to_exclude_from_arts:
                            art_relative_path = SiteUtil.save_image_to_server_path(art_url, 'art', image_server_local_path, image_path_segment, ui_code_for_image, art_index=idx + 1, proxy_url=proxy_url)
                            if art_relative_path:
                                fanart_server_url = f"{image_server_url}/{art_relative_path}"
                                entity.fanart.append(fanart_server_url); processed_fanart_count += 1
                    logger.info(f"Image Server: Processed {processed_fanart_count} fanarts...")

                elif not (use_image_server and image_mode == '4'): # 일반 모드 (DVD/BR)
                    logger.info("Using Normal Image Processing Mode (DVD/BR)...")
                    temp_img_urls_for_resolve = {'ps': ps_url, 'pl': pl_url, 'arts': arts}
                    SiteUtil.resolve_jav_imgs(temp_img_urls_for_resolve, ps_to_poster=ps_to_poster_setting, proxy_url=proxy_url, crop_mode=crop_mode)
                    entity.thumb = SiteUtil.process_jav_imgs(image_mode, temp_img_urls_for_resolve, proxy_url=proxy_url)
                    entity.fanart = []
                    resolved_arts = temp_img_urls_for_resolve.get("arts", [])
                    processed_fanart_count = 0
                    urls_to_exclude_from_arts = {temp_img_urls_for_resolve.get('poster'), temp_img_urls_for_resolve.get('landscape')}
                    for art_url in resolved_arts:
                        if processed_fanart_count >= max_arts: break
                        if art_url and art_url not in urls_to_exclude_from_arts:
                            processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                            if processed_art: entity.fanart.append(processed_art); processed_fanart_count += 1
                    logger.debug(f"Normal Mode (DVD/BR): Final Thumb={entity.thumb}, Fanart Count={len(entity.fanart)}")


                # 예고편 처리 (DVD/BR)
                entity.extras = []
                if use_extras:
                    trailer_title = entity.tagline if entity.tagline else raw_title_text if raw_title_text else code
                    trailer_url = None
                    try:
                        ajax_url_xpath = '//a[@id="sample-video1"]/@data-video-url'
                        ajax_url_tags = tree.xpath(ajax_url_xpath)
                        if ajax_url_tags:
                            ajax_relative_url = ajax_url_tags[0]; ajax_full_url = py_urllib_parse.urljoin(detail_url, ajax_relative_url)
                            ajax_headers = cls._get_request_headers(referer=detail_url); ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                            ajax_response_text = SiteUtil.get_text(ajax_full_url, proxy_url=proxy_url, headers=ajax_headers)
                            if ajax_response_text:
                                iframe_tree = html.fromstring(ajax_response_text); iframe_srcs = iframe_tree.xpath("//iframe/@src")
                                if iframe_srcs:
                                    iframe_url = py_urllib_parse.urljoin(ajax_full_url, iframe_srcs[0])
                                    iframe_headers = cls._get_request_headers(referer=ajax_full_url)
                                    iframe_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=iframe_headers)
                                    if iframe_text:
                                        pos = iframe_text.find("const args = {")
                                        if pos != -1:
                                            json_start = iframe_text.find("{", pos); json_end = iframe_text.find("};", json_start)
                                            if json_start != -1 and json_end != -1:
                                                data_str = iframe_text[json_start : json_end+1]
                                                data = json.loads(data_str)
                                                bitrates = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                                if bitrates and bitrates[0].get("src"): trailer_url = "https:" + bitrates[0]["src"] if not bitrates[0]["src"].startswith("http") else bitrates[0]["src"]
                                                if data.get("title") and data["title"].strip(): trailer_title = data["title"].strip()
                        if not trailer_url:
                            onclick_xpath = '//a[@id="sample-video1"]/@onclick'
                            onclick_tags = tree.xpath(onclick_xpath)
                            if onclick_tags:
                                onclick_text = onclick_tags[0]; match_json = re.search(r"gaEventVideoStart\('(\{.*?\})','(\{.*?\})'\)", onclick_text)
                                if match_json:
                                    video_data_str = match_json.group(1)
                                    video_data = json.loads(video_data_str.replace('\\"', '"'))
                                    if video_data.get("video_url"): trailer_url = video_data["video_url"]
                        if trailer_url:
                            final_trailer_title = SiteUtil.trans(trailer_title, do_trans=do_trans) if do_trans else trailer_title
                            entity.extras.append(EntityExtra("trailer", final_trailer_title, "mp4", trailer_url))
                    except Exception as extra_e: logger.exception(f"DVD/BR: Error processing trailer: {extra_e}")


            except Exception as e_parse_dvd_br_main:
                logger.exception(f"Error processing DVD/BLURAY metadata block: {e_parse_dvd_br_main}")

        else:
            logger.error(f"Cannot process info: Final content type '{content_type}' is unknown or invalid for {code}")


        # --- 공통 후처리 ---
        if not entity.tagline and entity.title: entity.tagline = entity.title
        # 제목이 비어있으면 ui_code_for_image (품번 테이블에서 추출) 사용
        if not entity.title:
            final_code = ui_code_for_image if ui_code_for_image else code[2:].upper() # fallback
            entity.title = entity.originaltitle = entity.sorttitle = final_code
            if not entity.tagline: entity.tagline = entity.title

        # 최종 entity 로그
        thumb_len = len(entity.thumb) if isinstance(entity.thumb, list) else 0
        fanart_len = len(entity.fanart) if isinstance(entity.fanart, list) else 0
        actor_len = len(entity.actor) if isinstance(entity.actor, list) else 0
        extras_len = len(entity.extras) if isinstance(entity.extras, list) else 0
        final_ui_code_log = getattr(entity, 'ui_code', 'N/A') # ui_code 없으면 N/A
        logger.debug(f"Final Parsed Entity: Title='{entity.title}', Tagline='{entity.tagline}', UI Code='{final_ui_code_log}', Premiered='{entity.premiered}', Thumb={thumb_len}, Fanart={fanart_len}, Actors={actor_len}, Extras={extras_len}")
        return entity
    # --- __info 메소드 끝 ---


    @classmethod
    def info(cls, code, **kwargs):
        # (이전 통합 버전과 동일)
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            if entity:
                ret["ret"] = "success"
                ret["data"] = entity.as_dict()
            else:
                ret["ret"] = "error"
                ret["data"] = f"Failed to get info entity for {code}"
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        return ret
