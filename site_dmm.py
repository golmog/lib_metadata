# -*- coding: utf-8 -*-
import json
import re
import urllib.parse as py_urllib_parse
from lxml import html, etree

# lib_metadata 패키지 내 다른 모듈 import
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings, EntityThumb
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

class SiteDmm:
    # ... (클래스 변수, 헤더, 정규식, 상태 변수 등은 이전 통합 버전과 동일하게 유지) ...
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
        # (이전 통합 버전의 개선된 연령 확인 로직 그대로 사용)
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
        # 연령 확인
        if not cls._ensure_age_verified(proxy_url=proxy_url): return []

        # 키워드 정규화
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd": keyword = keyword[:-3]
        keyword = keyword.replace("-", " ")
        keyword_tmps = keyword.split(" ")
        if len(keyword_tmps) == 2: dmm_keyword = keyword_tmps[0] + keyword_tmps[1].zfill(5)
        else: dmm_keyword = keyword
        logger.debug("keyword [%s] -> [%s]", keyword, dmm_keyword)

        # 검색 요청
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
        lists = []; logger.debug(f"Attempting XPath (Desktop Grid): {list_xpath}")
        try: lists = tree.xpath(list_xpath)
        except Exception as e_xpath: logger.error(f"XPath error: {e_xpath}")
        logger.debug(f"Found {len(lists)} items using Desktop Grid XPath.")
        if not lists: logger.warning(f"No items found using Desktop Grid XPath."); return []

        # 결과 처리를 위한 변수 초기화
        ret = []; score = 60 # score 기본값 (부분일치 시 사용)

        # 검색 결과 아이템 루프 (최대 10개)
        for node in lists[:10]:
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
                    elif "/mono/dvd/" in href: # DVD 경로 확인
                        content_type = "dvd"
                    # 다른 타입 판별 로직 추가 가능
                # --- 타입 판별 로직 끝 ---

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
                elif content_type == 'videoa': type_prefix = "[DigitalVideo] "
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

        # --- ★★★ 결과 정렬 로직 (우선순위 적용) ★★★ ---
        # 우선순위 정의: 숫자가 낮을수록 우선 (1: DVD, 2: videoa, 3: bluray)
        type_priority = {
            'dvd': 1,
            'videoa': 2,
            'bluray': 3,
            'unknown': 99 # 기타는 맨 뒤로
        }

        # 1단계: 점수로 내림차순 정렬
        ret_sorted_by_score = sorted(ret, key=lambda k: k.get("score", 0), reverse=True)
        # 2단계: 점수가 같은 그룹 내에서 타입 우선순위(숫자가 낮은 것 우선)로 오름차순 재정렬
        sorted_ret = sorted(
            ret_sorted_by_score,
            key=lambda k: type_priority.get(k.get('content_type', 'unknown'), 99)
        )
        logger.debug(f"정렬 후 결과 개수: {len(sorted_ret)}")
        if sorted_ret: # 정렬된 결과가 있을 경우 상위 몇개 로그 출력
            log_count = min(len(sorted_ret), 5) # 최대 5개
            logger.debug(f"정렬된 상위 {log_count}개 결과:")
            for idx, item_log in enumerate(sorted_ret[:log_count]):
                logger.debug(f"  {idx+1}. Score={item_log.get('score')}, Type={item_log.get('content_type')}, Code={item_log.get('code')}, Title={item_log.get('title_ko')}")
        # --- 정렬 로직 끝 ---

        # --- 재시도 로직 ---
        if not sorted_ret and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6) # 6자리로 재시도
            logger.debug(f"결과 없음. 6자리 숫자로 재시도: {new_title}")
            # 재귀 호출 시 manual 플래그 등 전달 확인
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)

        return sorted_ret

    @classmethod
    def search(cls, keyword, **kwargs):
        # (이전 통합 버전과 동일)
        ret = {}
        try: data_list = cls.__search(keyword, **kwargs)
        except Exception as exception: logger.exception("SearchErr:"); ret["ret"] = "exception"; ret["data"] = str(exception)
        else: ret["ret"] = "success" if data_list else "no_match"; ret["data"] = data_list
        return ret

    @classmethod
    def __img_urls(cls, tree, content_type='unknown'):
        logger.debug(f"Extracting raw image URLs for type: {content_type}")
        # 반환 딕셔너리에 'specific_poster_candidate' 키 추가 및 초기화
        img_urls = {'ps': "", 'pl': "", 'arts': [], 'specific_poster_candidate': None}
        try:
            if content_type == 'videoa' or content_type == 'vr': # VR도 videoa XPath 사용 가정
                logger.debug(f"Extracting {content_type} URLs using videoa logic...")
                pl_base_xpath = '//div[@id="sample-image-block"]'
                # img 태그 자체를 가져와 src와 filename 확인
                all_img_tags = tree.xpath(f'{pl_base_xpath}//img')

                if not all_img_tags:
                    logger.warning("Could not find any img tags inside div#sample-image-block.")
                    return img_urls

                processed_arts = []
                pl_url = None
                specific_poster_url = None # *-1.jpg 등 특정 포스터 후보

                for idx, img_tag in enumerate(all_img_tags):
                    src = img_tag.attrib.get("src", "").strip()
                    if not src: continue
                    full_src = py_urllib_parse.urljoin(cls.site_base_url, src)
                    filename = src.split('/')[-1].lower() # 파일명 (소문자 변환)

                    # 첫 번째 이미지는 기본 pl 후보
                    if idx == 0:
                        pl_url = full_src
                        logger.debug(f"Found potential pl (index 0): {filename}")
                        # 첫번째가 pl.jpg 가 아닐 수도 있음

                    # 파일명이 'pl.jpg' 로 끝나면 pl_url 로 확정 (덮어쓰기 가능)
                    if filename.endswith("pl.jpg"):
                        pl_url = full_src
                        logger.debug(f"Confirmed 'pl' based on filename: {filename}")

                    # 파일명이 숫자로 끝나거나(예: -1.jpg, -01.jpg) 특정 패턴을 가질 때 specific 후보로 지정
                    # 예시: '-'+숫자+'.jpg' 형태의 첫번째 이미지
                    match_specific = re.match(r".*-(\d+)\.jpg$", filename)
                    if specific_poster_url is None and match_specific:
                        specific_poster_url = full_src
                        logger.debug(f"Found potential specific poster candidate: {filename}")

                    # 모든 유효한 이미지는 arts 후보에 추가
                    processed_arts.append(full_src)

                img_urls['pl'] = pl_url if pl_url else ""
                img_urls['specific_poster_candidate'] = specific_poster_url if specific_poster_url else ""

                # arts에서 pl 및 specific_poster 와 중복 제거
                unique_arts = []
                urls_to_exclude = {img_urls['pl'], img_urls['specific_poster_candidate']}
                for art_url in processed_arts:
                    if art_url and art_url not in urls_to_exclude and art_url not in unique_arts:
                        unique_arts.append(art_url)
                img_urls['arts'] = unique_arts
                logger.debug(f"Found {len(img_urls['arts'])} unique arts links.")

            elif content_type == 'dvd':
                # DVD 로직 (기존 v_old XPath 사용)
                logger.debug("Extracting dvd URLs using v_old logic...")
                pl_xpath = '//div[@id="fn-sampleImage-imagebox"]/img/@src'
                pl_tags = tree.xpath(pl_xpath)
                raw_pl = pl_tags[0].strip() if pl_tags else ""
                if raw_pl:
                    img_urls['pl'] = ("https:" + raw_pl) if not raw_pl.startswith("http") else raw_pl
                    logger.debug(f"Found dvd pl using v_old XPath: {img_urls['pl']}")
                else: logger.warning("Could not find dvd pl using v_old XPath: %s", pl_xpath)

                img_urls['ps'] = "" # dvd는 ps가 상세페이지에 없음 (캐시 사용)

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
                    logger.debug(f"Found {len(img_urls['arts'])} arts links for dvd using v_old XPath.")
                else: logger.warning("Could not find dvd arts using v_old XPath: %s", arts_xpath)

            else: logger.error(f"Unknown content type '{content_type}' in __img_urls")

        except Exception as e:
            logger.exception(f"Error extracting image URLs: {e}")
            # 실패 시 기본 구조 반환
            img_urls = {'ps': "", 'pl': "", 'arts': [], 'specific_poster_candidate': None}

        # 최종 추출된 URL 정보 로깅
        logger.debug(f"Extracted img_urls: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} specific_poster={bool(img_urls.get('specific_poster_candidate'))} arts={len(img_urls.get('arts',[]))}")
        return img_urls

    @classmethod
    def __info( cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, ps_to_poster=False, crop_mode=None):
        logger.info(f"Getting detail info for {code}")
        cached_data = cls._ps_url_cache.pop(code, {})
        ps_url_from_cache = cached_data.get('ps')
        # --- 타입 결정: VR은 'vr', 블루레이는 'dvd'로 통일 ---
        original_content_type = cached_data.get('type', 'unknown')
        if original_content_type == 'bluray':
            content_type = 'dvd' # 블루레이는 DVD 로직 사용
            logger.debug("Treating 'bluray' as 'dvd' for info processing.")
        # elif original_content_type == 'vr': # VR 타입 유지 가능 (선택)
        #      content_type = 'vr'
        else:
            content_type = original_content_type # videoa, dvd, unknown 등

        if ps_url_from_cache: logger.debug(f"Using cached ps_url for {code}: {ps_url_from_cache}")
        else: logger.warning(f"ps_url for {code} not found in cache.")

        if not cls._ensure_age_verified(proxy_url=proxy_url): raise Exception(f"Age verification failed for info ({code}).")

        cid_part = code[2:]
        detail_url = None
        is_vr_content = False # VR 플래그

        # --- 상세 페이지 URL 생성 ---
        if content_type == 'videoa' or content_type == 'vr': # VR 포함 videoa 경로 사용
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
        elif content_type == 'dvd': # 블루레이도 dvd 로직 사용하므로 이 경로
            detail_url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={cid_part}/"
        else: # 타입 불명 시 videoa 시도
            logger.warning(f"Unknown type '{content_type}'. Trying 'videoa' path for {code}.")
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
            content_type = 'videoa' # 임시로 videoa 지정

        logger.info(f"Accessing DMM detail page (Processing as: {content_type}): {detail_url}")
        referer_url = cls.fanza_av_url if content_type in ['videoa', 'vr'] else (cls.site_base_url + "/mono/dvd/")
        info_headers = cls._get_request_headers(referer=referer_url)
        tree = None
        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=info_headers)
            if tree is None: raise Exception(f"SiteUtil.get_tree returned None for {detail_url}.")
        except Exception as e: logger.exception(f"Failed get/process detail tree: {e}"); raise

        entity = EntityMovie(cls.site_name, code); entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"

        # --- 제목 파싱 및 VR 플래그 설정 ---
        raw_title_text = ""
        try:
            title_node = tree.xpath('//h1[@id="title"]')
            if title_node:
                raw_title_text = title_node[0].text_content().strip()
                if raw_title_text.startswith("【VR】"):
                    is_vr_content = True
                    logger.info(f"VR content detected for {code}.")
                entity.tagline = raw_title_text # 원본 제목 (VR 접두사 포함) -> 이후 번역
            else: logger.warning("Could not find h1#title.")
        except Exception as e_title_parse: logger.warning(f"Error parsing title: {e_title_parse}")

        # ================================================
        # === 디지털 비디오 / VR 처리 (videoa, vr) ===
        # ================================================
        if content_type == 'videoa' or content_type == 'vr':
            logger.debug(f"Processing as VIDEOA/VR type (is_vr={is_vr_content})...")
            try:
                # --- 이미지 처리 (직접 비교 로직) ---
                raw_img_urls = cls.__img_urls(tree, content_type='videoa') # VR도 videoa XPath 사용
                specific_poster_candidate = raw_img_urls.get('specific_poster_candidate')
                original_pl_url = raw_img_urls.get('pl')
                original_arts = raw_img_urls.get('arts', [])
                current_ps_url = ps_url_from_cache if ps_url_from_cache else ""

                ps_valid = bool(current_ps_url)
                pl_valid = bool(original_pl_url)
                specific_poster_valid = bool(specific_poster_candidate)

                final_poster_url = None; final_poster_crop = None; poster_source_log = "Unknown"

                # 1순위: ps vs pl (crop)
                if ps_valid and pl_valid:
                    crop_pos = SiteUtil.has_hq_poster(current_ps_url, original_pl_url, proxy_url=proxy_url)
                    if crop_pos:
                        final_poster_url = original_pl_url; final_poster_crop = crop_pos
                        poster_source_log = f"pl (cropped '{crop_pos}')"; logger.info("Priority 1 Met: Using 'pl' with crop.")
                    else: logger.debug("Priority 1 Check Failed: ps not similar crop of pl.")
                else: logger.debug("Priority 1 Check Skipped: ps or pl invalid.")

                # 2순위: ps vs specific_poster
                if final_poster_url is None and ps_valid and specific_poster_valid:
                    if SiteUtil.is_hq_poster(current_ps_url, specific_poster_candidate, proxy_url=proxy_url):
                        final_poster_url = specific_poster_candidate; final_poster_crop = None
                        poster_source_log = "specific_poster_candidate"; logger.info("Priority 2 Met: Using specific poster.")
                    else: logger.debug("Priority 2 Check Failed: ps not similar to specific.")
                elif final_poster_url is None: logger.debug(f"Priority 2 Check Skipped: Poster found or URLs invalid (ps={ps_valid}, specific={specific_poster_valid}).")

                # 3순위: ps fallback
                if final_poster_url is None:
                    if ps_valid:
                        final_poster_url = current_ps_url; final_poster_crop = None
                        poster_source_log = "ps (Fallback)"; logger.info("Fallback: Using 'ps'.")
                    elif pl_valid:
                        final_poster_url = original_pl_url; poster_source_log = "pl (Last Resort Fallback)"
                        logger.warning("Fallback: 'ps' missing, using 'pl'.")
                    else: final_poster_url = ""; poster_source_log = "None"; logger.error("No valid poster image found.")
                logger.info(f"Final Poster Decision: URL='{final_poster_url}', Crop='{final_poster_crop}', Source='{poster_source_log}'")

                # --- 썸네일 및 팬아트 생성 ---
                entity.thumb = []
                final_landscape_url = None
                if pl_valid: # Landscape는 pl이 가로일때만
                    try:
                        im_pl_check = SiteUtil.imopen(original_pl_url, proxy_url=proxy_url)
                        if im_pl_check and im_pl_check.size[0] > im_pl_check.size[1]: final_landscape_url = original_pl_url
                    except Exception as e_lcheck: logger.warning(f"Could not check pl orientation: {e_lcheck}")
                if final_landscape_url and final_landscape_url != final_poster_url:
                    try:
                        processed_landscape = SiteUtil.process_image_mode(image_mode, final_landscape_url, proxy_url=proxy_url)
                        if processed_landscape: entity.thumb.append(EntityThumb(aspect="landscape", value=processed_landscape))
                    except Exception as e_proc_land: logger.error(f"Error processing landscape: {e_proc_land}")
                if final_poster_url:
                    try:
                        processed_poster = SiteUtil.process_image_mode(image_mode, final_poster_url, proxy_url=proxy_url, crop_mode=final_poster_crop)
                        if processed_poster: entity.thumb.append(EntityThumb(aspect="poster", value=processed_poster))
                    except Exception as e_proc_post: logger.error(f"Error processing poster: {e_proc_post}")
                # 팬아트 처리
                entity.fanart = []
                processed_fanart_count = 0
                urls_to_exclude_from_arts = {final_poster_url, final_landscape_url}
                for art_url in original_arts:
                    if processed_fanart_count >= max_arts: break
                    if art_url and art_url not in urls_to_exclude_from_arts:
                        try:
                            processed_art = SiteUtil.process_image_mode(image_mode, art_url, proxy_url=proxy_url)
                            if processed_art: entity.fanart.append(processed_art); processed_fanart_count += 1
                        except Exception as e_fanart: logger.error(f"Error processing fanart {art_url}: {e_fanart}")
                logger.debug(f"Final Thumb: {entity.thumb}, Fanart Count: {len(entity.fanart)}")
                # --- 이미지 처리 끝 ---

                # --- 메타데이터 파싱 (videoa XPath 사용) ---
                if entity.tagline: entity.tagline = SiteUtil.trans(entity.tagline, do_trans=do_trans) # 번역
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
                    # 파싱 로직 (이전 답변 내용 참고 - videoa 부분)
                    if "配信開始日" in key: premiered_haishin = value_text_all.replace("/", "-")
                    elif "商品発売日" in key: premiered_shouhin = value_text_all.replace("/", "-")
                    elif "収録時間" in key: m=re.search(r"(\d+)",value_text_all); entity.runtime = int(m.group(1)) if m else None
                    elif "出演者" in key: # ... 배우 ...
                        actors = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                        if actors: entity.actor = [EntityActor(name) for name in actors]
                        elif value_text_all != '----': entity.actor = [EntityActor(n.strip()) for n in value_text_all.split('/') if n.strip()]
                    elif "監督" in key: # ... 감독 ...
                        directors = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                        entity.director = directors[0] if directors else (value_text_all if value_text_all != '----' else None)
                    elif "シリーズ" in key: # ... 시리즈 ...
                        if entity.tag is None: entity.tag = []
                        series = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                        s_name = series[0] if series else (value_text_all if value_text_all != '----' else None)
                        if s_name and SiteUtil.trans(s_name, do_trans=do_trans) not in entity.tag: entity.tag.append(SiteUtil.trans(s_name, do_trans=do_trans))
                    elif "メーカー" in key: # ... 제작사 ...
                        if entity.studio is None:
                            makers = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                            m_name = makers[0] if makers else (value_text_all if value_text_all != '----' else None)
                            if m_name: entity.studio = SiteUtil.trans(m_name, do_trans=do_trans)
                    elif "レーベル" in key: # ... 레이블 ...
                        labels = [a.strip() for a in value_node.xpath('.//a/text()') if a.strip()]
                        l_name = labels[0] if labels else (value_text_all if value_text_all != '----' else None)
                        if l_name:
                            if do_trans: entity.studio = SiteUtil.av_studio.get(l_name, SiteUtil.trans(l_name))
                            else: entity.studio = l_name
                    elif "ジャンル" in key: # ... 장르 ...
                        entity.genre = []
                        for genre_ja in value_node.xpath('.//a/text()'):
                            genre_ja = genre_ja.strip(); # ... (장르 처리) ...
                            if not genre_ja or "％OFF" in genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                    elif "品番" in key: # ... 품번 ...
                        # 품번 처리 로직 (제목 설정은 나중에)
                        value = value_text_all; match_id = cls.PTN_ID.search(value); id_before = None
                        if match_id: id_before = match_id.group(0); value = value.lower().replace(id_before, "zzid")
                        match_real = cls.PTN_SEARCH_REAL_NO.match(value); formatted_code = value_text_all.upper()
                        if match_real:
                            label = match_real.group("real").upper()
                            if id_before is not None: label = label.replace("ZZID", id_before.upper())
                            formatted_code = label + "-" + str(int(match_real.group("no"))).zfill(3)
                            if entity.tag is None: entity.tag = []
                            if label not in entity.tag: entity.tag.append(label)
                        entity.title = entity.originaltitle = entity.sorttitle = formatted_code # 임시 설정
                    elif "平均評価" in key: # ... 평점 ...
                        rating_img = value_node.xpath('.//img/@src')# ... (평점 처리) ...
                        if rating_img:
                            match_rate = cls.PTN_RATING.search(rating_img[0])
                            if match_rate:
                                rate_str = match_rate.group("rating").replace("_",".")
                                try:
                                    rate_val = float(rate_str) / 10.0
                                    if 0 <= rate_val <= 5:
                                        img_url = "https:" + rating_img[0] if rating_img[0].startswith("//") else rating_img[0]
                                        if not entity.ratings: entity.ratings = [EntityRatings(rate_val, max=5, name="dmm", image_url=img_url)]
                                        else: entity.ratings[0].value = rate_val; entity.ratings[0].image_url = img_url
                                except ValueError: logger.warning(f"Rating conversion error: {rate_str}")

                final_premiered = premiered_shouhin or premiered_haishin
                if final_premiered: entity.premiered = final_premiered; entity.year = int(final_premiered[:4]) if final_premiered and len(final_premiered) >= 4 else None
                else: logger.warning("Premiered date not found."); entity.premiered = None; entity.year = None

                # 줄거리 파싱 (videoa XPath 사용)
                plot_xpath = '//div[@class="mg-b20 lh4"]/text()'
                plot_nodes = tree.xpath(plot_xpath)
                if plot_nodes:
                    plot_text = "\n".join([p.strip() for p in plot_nodes if p.strip()]).split("※")[0].strip()
                    if plot_text: entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                else: logger.warning("Plot not found using videoa XPath.")
                # --- 메타데이터 파싱 끝 ---

                # --- 예고편 처리 (VR과 Videoa 분기) ---
                entity.extras = []
                if use_extras:
                    trailer_title = entity.tagline if entity.tagline else raw_title_text if raw_title_text else code
                    try:
                        trailer_url = None

                        if is_vr_content: # ★★★ VR 처리 ★★★
                            logger.debug("Using VR trailer logic (AJAX to vr-sample-player).")
                            vr_player_ajax_url = f"{cls.site_base_url}/digital/-/vr-sample-player/=/cid={cid_part}/"
                            logger.info(f"Requesting VR player AJAX: {vr_player_ajax_url}")
                            # VR 플레이어 페이지는 AJAX로 호출해야 할 수 있음 (헤더 설정)
                            ajax_headers = cls._get_request_headers(referer=detail_url)
                            ajax_headers['Accept'] = 'text/html, */*; q=0.01' # AJAX 요청임을 명시
                            ajax_headers['X-Requested-With'] = 'XMLHttpRequest'

                            # get_response 사용 (content를 파싱하기 위해)
                            vr_player_response = SiteUtil.get_response(vr_player_ajax_url, proxy_url=proxy_url, headers=ajax_headers)

                            if vr_player_response and vr_player_response.status_code == 200:
                                # 응답 content를 직접 파싱 (인코딩 문제 방지)
                                vr_player_content = vr_player_response.content
                                if vr_player_content:
                                    vr_tree = html.fromstring(vr_player_content) # lxml.html 사용
                                    video_tags = vr_tree.xpath("//video/@src")
                                    if video_tags:
                                        trailer_src = video_tags[0]
                                        trailer_url = "https:" + trailer_src if trailer_src.startswith("//") else trailer_src
                                        logger.debug(f"Extracted VR trailer URL from <video> tag: {trailer_url}")
                                    else: logger.error("VR player AJAX response: <video> tag or src not found.")
                                else: logger.error("VR player AJAX response content is empty.")
                            else: logger.error(f"Failed VR player AJAX request. Status: {vr_player_response.status_code if vr_player_response else 'No Resp'}")

                        else: # ★★★ 일반 Videoa 처리 (기존 AJAX 로직) ★★★
                            logger.debug("Using Videoa AJAX trailer logic (ajax-movie).")
                            ajax_url = py_urllib_parse.urljoin(cls.site_base_url, f"/digital/videoa/-/detail/ajax-movie/=/cid={cid_part}/")
                            ajax_headers = cls._get_request_headers(referer=detail_url)
                            ajax_headers['Accept'] = 'text/html, */*; q=0.01'
                            ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                            logger.debug(f"Requesting trailer AJAX: {ajax_url}")
                            ajax_response = SiteUtil.get_response(ajax_url, proxy_url=proxy_url, headers=ajax_headers)

                            if ajax_response and ajax_response.status_code == 200:
                                ajax_html_text = ajax_response.text
                                iframe_tree = html.fromstring(ajax_html_text)
                                iframe_srcs = iframe_tree.xpath("//iframe/@src")
                                if iframe_srcs:
                                    iframe_url = py_urllib_parse.urljoin(ajax_url, iframe_srcs[0])
                                    logger.debug(f"Found iframe, accessing player: {iframe_url}")
                                    iframe_headers = cls._get_request_headers(referer=ajax_url)
                                    iframe_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=iframe_headers)
                                    if iframe_text:
                                        pos = iframe_text.find("const args = {")
                                        if pos != -1:
                                            # ... (const args JSON 파싱 로직 동일) ...
                                            json_start = iframe_text.find("{", pos); json_end = iframe_text.find("};", json_start)
                                            if json_start != -1 and json_end != -1:
                                                data_str = iframe_text[json_start : json_end+1]
                                                try:
                                                    data = json.loads(data_str)
                                                    bitrates = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                                    if bitrates and bitrates[0].get("src"):
                                                        trailer_src = bitrates[0]["src"]
                                                        trailer_url = "https:" + trailer_src if trailer_src.startswith("//") else trailer_src
                                                        logger.debug(f"Extracted trailer URL from const args: {trailer_url}")
                                                    else: logger.warning("'bitrates' data missing/empty.")
                                                    if data.get("title") and data["title"].strip(): trailer_title = data["title"].strip()
                                                except json.JSONDecodeError as je: logger.warning(f"Failed JSON decode: {je}")
                                            else: logger.warning("Could not find JSON ends.")
                                        else: logger.warning("'const args' not found.")
                                    else: logger.warning("Failed to get iframe content.")
                                else: logger.warning("Iframe not found in AJAX response.")
                            else: logger.warning(f"Videoa AJAX request failed. Status: {ajax_response.status_code if ajax_response else 'No Resp'}")

                        # 최종 예고편 추가
                        if trailer_url:
                            final_trailer_title = SiteUtil.trans(trailer_title, do_trans=do_trans) if do_trans else trailer_title
                            entity.extras.append(EntityExtra("trailer", final_trailer_title, "mp4", trailer_url))
                            logger.info(f"Trailer added successfully. Title: {final_trailer_title}")
                        else: logger.warning("Failed to extract final trailer URL.")
                    except Exception as extra_e:
                        logger.exception(f"Error processing trailer: {extra_e}")
                # --- 예고편 처리 끝 ---
            except Exception as e_parse_videoa_vr:
                logger.exception(f"Error parsing VIDEOA/VR metadata: {e_parse_videoa_vr}")

        elif content_type == 'dvd': # 블루레이는 여기서 처리 (content_type 'dvd'로 통일됨)
            logger.debug(f"Processing as DVD/BLURAY type...")
            try:
                # --- 이미지 처리 (기존 resolve_jav_imgs 사용) ---
                # 블루레이도 DVD와 동일한 이미지 구조를 가진다고 가정
                img_urls = cls.__img_urls(tree, content_type='dvd') # dvd XPath 사용
                if ps_url_from_cache: img_urls['ps'] = ps_url_from_cache
                elif not img_urls.get('ps') and img_urls.get('pl'): img_urls['ps'] = img_urls.get('pl')
                elif not img_urls.get('ps') and not img_urls.get('pl'): logger.error("DVD/Blu-ray ps and pl URLs are missing.")

                # DVD/Blu-ray는 resolve_jav_imgs 사용 유지 (필요시 위 로직처럼 변경 가능)
                SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=None, proxy_url=proxy_url)
                entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
                # 팬아트 처리
                entity.fanart = []
                resolved_arts = img_urls.get("arts", [])
                processed_fanart_count = 0

                # --- 메타데이터 파싱 (dvd XPath 사용) ---
                if entity.tagline: entity.tagline = SiteUtil.trans(entity.tagline, do_trans=do_trans) # 번역
                info_table_xpath = '//div[@class="wrapper-product"]//table//tr'
                tags = tree.xpath(info_table_xpath)
                premiered_shouhin = None; premiered_hatsubai = None; premiered_haishin = None
                for tag in tags:
                    td_tags = tag.xpath(".//td")
                    if len(td_tags) != 2: continue
                    # 평점 행 처리 (dvd 방식)
                    if "평균평가" in td_tags[0].text_content():
                        rating_img_tags = td_tags[1].xpath('.//img/@src') # ... (dvd 평점 처리) ...
                        if rating_img_tags:
                            match_rating = cls.PTN_RATING.search(rating_img_tags[0])
                            if match_rating:
                                rating_value_str = match_rating.group("rating").replace("_", ".")
                                try:
                                    rating_value = float(rating_value_str) / 10.0
                                    if 0 <= rating_value <= 5:
                                        rating_img_url = "https:" + rating_img_tags[0] if not rating_img_tags[0].startswith("http") else rating_img_tags[0]
                                        if not entity.ratings: entity.ratings = [EntityRatings(rating_value, max=5, name="dmm", image_url=rating_img_url)]
                                        else: entity.ratings[0].value = rating_value; entity.ratings[0].image_url = rating_img_url
                                except ValueError: logger.warning(f"Could not convert rating (dvd): {rating_value_str}")
                        continue # 평점 행 다음으로

                    key = td_tags[0].text_content().strip().replace("：", "")
                    value_node = td_tags[1]; value_text_all = value_node.text_content().strip()
                    if value_text_all == "----" or not value_text_all: continue
                    # 파싱 로직 (dvd 부분 참고)
                    if "商品発売日" in key: premiered_shouhin = value_text_all.replace("/", "-")
                    elif "発売日" in key: premiered_hatsubai = value_text_all.replace("/", "-")
                    elif "配信開始日" in key: premiered_haishin = value_text_all.replace("/", "-")
                    elif "収録時間" in key: # ... 시간 ...
                        match_runtime = re.search(r"(\d+)", value_text_all)
                        if match_runtime: entity.runtime = int(match_runtime.group(1))
                    elif "出演者" in key: # ... 배우 ...
                        entity.actor = []
                        for a_tag in value_node.xpath(".//a"):
                            actor_name = a_tag.text_content().strip()
                            if actor_name and actor_name != "▼すべて表示する": entity.actor.append(EntityActor(actor_name))
                    elif "監督" in key: # ... 감독 ...
                        a_tags = value_node.xpath(".//a"); entity.director = a_tags[0].text_content().strip() if a_tags else value_text_all
                    elif "シリーズ" in key: # ... 시리즈 ...
                        if entity.tag is None: entity.tag = []
                        a_tags = value_node.xpath(".//a"); series_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                        if series_name and SiteUtil.trans(series_name, do_trans=do_trans) not in entity.tag: entity.tag.append(SiteUtil.trans(series_name, do_trans=do_trans))
                    elif "メーカー" in key: # ... 제작사 ...
                        if entity.studio is None:
                            a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                            entity.studio = SiteUtil.trans(studio_name, do_trans=do_trans)
                    elif "レーベル" in key: # ... 레이블 ...
                        a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                        if do_trans: entity.studio = SiteUtil.av_studio.get(studio_name, SiteUtil.trans(studio_name))
                        else: entity.studio = studio_name
                    elif "ジャンル" in key: # ... 장르 ...
                        entity.genre = []
                        for tag_a in value_node.xpath(".//a"):
                            genre_ja = tag_a.text_content().strip(); # ... (dvd 장르 처리) ...
                            if "％OFF" in genre_ja or not genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                    elif "品番" in key: # ... 품번 ...
                        # 품번 처리 로직 (dvd 방식)
                        value = value_text_all; match_id = cls.PTN_ID.search(value); id_before = None
                        if match_id: id_before = match_id.group(0); value = value.lower().replace(id_before, "zzid")
                        match_real = cls.PTN_SEARCH_REAL_NO.match(value); formatted_code = value_text_all.upper()
                        if match_real:
                            label = match_real.group("real").upper()
                            if id_before is not None: label = label.replace("ZZID", id_before.upper())
                            formatted_code = label + "-" + str(int(match_real.group("no"))).zfill(3)
                            if entity.tag is None: entity.tag = []
                            if label not in entity.tag: entity.tag.append(label)
                        entity.title = entity.originaltitle = entity.sorttitle = formatted_code # 임시 설정

                final_premiered = premiered_shouhin or premiered_hatsubai or premiered_haishin
                if final_premiered: entity.premiered = final_premiered; entity.year = int(final_premiered[:4]) if final_premiered and len(final_premiered) >= 4 else None
                else: logger.warning("DVD/BR Premiered date not found."); entity.premiered = None; entity.year = None

                # 줄거리 파싱 (dvd XPath 사용)
                plot_xpath = '//div[@class="mg-b20 lh4"]/p[@class="mg-b20"]/text()'
                plot_tags = tree.xpath(plot_xpath)
                if plot_tags:
                    plot_text = "\n".join([p.strip() for p in plot_tags if p.strip()]).split("※")[0].strip()
                    entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                else: logger.warning("DVD/BR Plot not found.")
                # --- 메타데이터 파싱 끝 ---

                # --- DVD/블루레이 예고편 처리 ---
                entity.extras = []
                if use_extras:
                    logger.debug("Attempting DVD/Blu-ray trailer...")
                    try:
                        trailer_url = None; trailer_title = entity.tagline if entity.tagline else raw_title_text if raw_title_text else code
                        # AJAX 시도
                        ajax_url_xpath = '//a[@id="sample-video1"]/@data-video-url'
                        ajax_url_tags = tree.xpath(ajax_url_xpath)
                        if ajax_url_tags:
                            ajax_relative_url = ajax_url_tags[0]; ajax_full_url = py_urllib_parse.urljoin(detail_url, ajax_relative_url)
                            try:
                                ajax_headers = cls._get_request_headers(referer=detail_url); ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                                ajax_response_text = SiteUtil.get_text(ajax_full_url, proxy_url=proxy_url, headers=ajax_headers)
                                if ajax_response_text:
                                    try: from lxml import html
                                    except ImportError: logger.error("lxml required."); raise
                                    ajax_tree = html.fromstring(ajax_response_text); iframe_srcs = ajax_tree.xpath("//iframe/@src")
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
                                                    try:
                                                        data = json.loads(data_str)
                                                        bitrates = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                                        if bitrates and bitrates[0].get("src"): trailer_url = "https:" + bitrates[0]["src"] if not bitrates[0]["src"].startswith("http") else bitrates[0]["src"]
                                                        if data.get("title") and data["title"].strip(): trailer_title = data["title"].strip()
                                                    except json.JSONDecodeError as je: logger.warning(f"Failed JSON decode (dvd ajax): {je}")
                                else: logger.warning("AJAX iframe content missing or invalid.")
                            except Exception as ajax_e: logger.exception(f"Error during DVD/BR trailer AJAX: {ajax_e}")

                        # Onclick Fallback
                        if not trailer_url:
                            onclick_xpath = '//a[@id="sample-video1"]/@onclick'
                            onclick_tags = tree.xpath(onclick_xpath)
                            if onclick_tags:
                                onclick_text = onclick_tags[0]; match_json = re.search(r"gaEventVideoStart\('(\{.*?\})','(\{.*?\})'\)", onclick_text)
                                if match_json:
                                    video_data_str = match_json.group(1)
                                    try:
                                        video_data = json.loads(video_data_str.replace('\\"', '"'))
                                        if video_data.get("video_url"): trailer_url = video_data["video_url"]
                                    except Exception as json_e: logger.warning(f"Failed JSON parse (dvd onclick): {json_e}")

                        if trailer_url:
                            final_trailer_title = SiteUtil.trans(trailer_title, do_trans=do_trans) if do_trans else trailer_title
                            entity.extras.append(EntityExtra("trailer", final_trailer_title, "mp4", trailer_url))
                            logger.info(f"DVD/BR Trailer added. Title: {final_trailer_title}")
                        else: logger.warning("DVD/BR Trailer URL not found.")
                    except Exception as extra_e: logger.exception(f"Error processing DVD/BR trailer: {extra_e}")
                # --- 예고편 처리 끝 ---
            except Exception as e_parse_dvd_br:
                logger.exception(f"Error parsing DVD/Blu-ray metadata: {e_parse_dvd_br}")

        else:
            logger.error(f"Cannot parse info: Final content type '{content_type}' is unknown for {code}")

        # --- 공통 후처리 ---
        # Tagline/Title 정리 (VR 접두사 유지됨)
        if not entity.tagline and entity.title: entity.tagline = entity.title
        if not entity.title: # 제목 없으면 품번으로
            match_real_no = cls.PTN_SEARCH_REAL_NO.search(code[2:])
            final_ui_code = code[2:].upper()
            if match_real_no:
                real = match_real_no.group("real").upper(); no = match_real_no.group("no")
                try: final_ui_code = f"{real}-{str(int(no)).zfill(3)}"
                except ValueError: final_ui_code = f"{real}-{no}"
            entity.title = entity.originaltitle = entity.sorttitle = final_ui_code
            if not entity.tagline: entity.tagline = entity.title

        logger.debug(f"Final Parsed Entity: Title='{entity.title}', Tagline='{entity.tagline}', Thumb={len(entity.thumb) if entity.thumb else 0}, Fanart={len(entity.fanart)}, Actors={len(entity.actor) if entity.actor else 0}")
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
