# -*- coding: utf-8 -*-
import json
import re
import urllib.parse as py_urllib_parse
from lxml import html, etree

# lib_metadata 패키지 내 다른 모듈 import
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings
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
        # (이전 통합 버전의 URL 추출 로직 그대로 사용 - 각 타입별 XPath 적용됨)
        logger.debug(f"Extracting raw image URLs for type: {content_type}")
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        try:
            if content_type == 'videoa':
                logger.debug("Extracting videoa URLs...")
                pl_url = None
                pl_base_xpath = '//div[@id="sample-image-block"]' # 기준점 XPath

                # 1. 첫 번째 img 태그의 src를 pl로 사용
                first_img_xpath = f'{pl_base_xpath}//img[1]/@src' # 첫 번째 img 태그의 src
                first_img_tags = tree.xpath(first_img_xpath)
                if first_img_tags:
                    first_img_src = first_img_tags[0].strip()
                    if first_img_src:
                        pl_url = py_urllib_parse.urljoin(cls.site_base_url, first_img_src)
                        img_urls['pl'] = pl_url
                        logger.debug(f"Found videoa pl from first img/@src: {pl_url}")
                    else:
                        logger.warning("First img/@src is empty.")
                else:
                    logger.warning("Could not find the first img tag inside div#sample-image-block.")
                    # 필요시 다른 fallback 로직 추가 가능 (예: sample-video div 확인)

                # 2. 모든 img 태그의 src를 arts로 사용
                all_imgs_xpath = f'{pl_base_xpath}//img/@src' # 모든 img 태그의 src
                all_img_tags = tree.xpath(all_imgs_xpath)
                if all_img_tags:
                    processed_arts = []
                    pl_url_to_exclude = img_urls.get('pl') # pl로 확정된 URL

                    for src in all_img_tags:
                        current_src = src.strip()
                        if not current_src: continue # 빈 src는 제외

                        full_current_src = py_urllib_parse.urljoin(cls.site_base_url, current_src)

                        # pl과 동일한 이미지는 arts에서 제외
                        if pl_url_to_exclude and full_current_src == pl_url_to_exclude:
                            logger.debug(f"Skipping art identical to pl: {current_src}")
                            continue

                        processed_arts.append(full_current_src)

                    unique_arts = []; [unique_arts.append(x) for x in processed_arts if x not in unique_arts]
                    img_urls['arts'] = unique_arts
                    logger.debug(f"Found {len(img_urls['arts'])} potential arts links (from img src) for videoa (excluding pl if identical).")
                else:
                    logger.warning("Could not find any img tags inside div#sample-image-block for arts.")

                arts_xpath_main = '//div[@id="sample-image-block"]//a/@href'
                arts_xpath_alt = '//a[contains(@id, "sample-image")]/@href'
                arts_tags = tree.xpath(arts_xpath_main) or tree.xpath(arts_xpath_alt)
                if arts_tags:
                    processed_arts = []
                    for href in arts_tags:
                        if href and href.strip():
                            full_href = py_urllib_parse.urljoin(cls.site_base_url, href)
                            processed_arts.append(full_href)
                    unique_arts = []; [unique_arts.append(x) for x in processed_arts if x not in unique_arts]
                    img_urls['arts'] = unique_arts
                    # videoa는 pl을 위에서 찾았으므로, 여기서 arts[0]으로 덮어쓰지 않음
                else: logger.warning("Arts block not found for videoa using known XPaths.")

            elif content_type == 'dvd':
                logger.debug("Extracting dvd URLs using v_old logic...")
                pl_xpath = '//div[@id="fn-sampleImage-imagebox"]/img/@src'
                pl_tags = tree.xpath(pl_xpath)
                raw_pl = pl_tags[0] if pl_tags else ""
                if raw_pl:
                    img_urls['pl'] = ("https:" + raw_pl) if not raw_pl.startswith("http") else raw_pl
                    logger.debug(f"Found dvd pl using v_old XPath: {img_urls['pl']}")
                else: logger.warning("Could not find dvd pl using v_old XPath: %s", pl_xpath)
                img_urls['ps'] = "" # ps는 캐시에서 처리
                arts_xpath = '//li[contains(@class, "fn-sampleImage__zoom") and not(@data-slick-index="0")]//img'
                arts_tags = tree.xpath(arts_xpath)
                if arts_tags:
                    processed_arts = []
                    for tag in arts_tags:
                        src = tag.attrib.get("src") or tag.attrib.get("data-lazy")
                        if src:
                            if not src.startswith("http"): src = "https:" + src
                            processed_arts.append(src)
                    unique_arts = []; [unique_arts.append(x) for x in processed_arts if x not in unique_arts]
                    img_urls['arts'] = unique_arts
                    logger.debug(f"Found {len(img_urls['arts'])} arts links for dvd using v_old XPath.")
                else: logger.warning("Could not find dvd arts using v_old XPath: %s", arts_xpath)

            else: logger.error(f"Unknown content type '{content_type}' in __img_urls")
        except Exception as e:
            logger.exception(f"Error extracting image URLs: {e}")
            img_urls = {'ps': "", 'pl': "", 'arts': []}
        logger.debug(f"Extracted img_urls: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} arts={len(img_urls.get('arts',[]))}")
        return img_urls

    @classmethod
    def __info( cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, ps_to_poster=False, crop_mode=None):
        logger.info(f"Getting detail info for {code}")
        cached_data = cls._ps_url_cache.pop(code, {})
        ps_url_from_cache = cached_data.get('ps')
        content_type = cached_data.get('type', 'unknown')
        if ps_url_from_cache: logger.debug(f"Using cached ps_url for {code}: {ps_url_from_cache}")
        else: logger.warning(f"ps_url for {code} not found in cache.")
        logger.debug(f"Determined content type: {content_type}")

        if not cls._ensure_age_verified(proxy_url=proxy_url): raise Exception(f"Age verification failed for info ({code}).")

        cid_part = code[2:]
        detail_url = None
        if content_type == 'videoa': detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
        elif content_type == 'dvd': detail_url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={cid_part}/"
        else:
            logger.warning(f"Unknown type '{content_type}'. Trying 'videoa' path for {code}.")
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
            content_type = 'videoa'

        logger.info(f"Accessing DMM detail page ({content_type}): {detail_url}")
        referer_url = cls.fanza_av_url if content_type == 'videoa' else (cls.site_base_url + "/mono/dvd/")
        info_headers = cls._get_request_headers(referer=referer_url)
        tree = None
        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=info_headers)
            if tree is None: raise Exception(f"SiteUtil.get_tree returned None for {detail_url}.")
        except Exception as e: logger.exception(f"Failed get/process detail tree: {e}"); raise

        entity = EntityMovie(cls.site_name, code); entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"

        # --- 타입별 분기 시작 ---
        if content_type == 'videoa':
            logger.debug("Processing type 'videoa'...")
            try:
                # --- videoa 원시 이미지 URL 추출 ---
                raw_img_urls = cls.__img_urls(tree, content_type='videoa')
                original_pl_url = raw_img_urls.get('pl')

                # --- 원본 pl 이미지 방향 미리 확인 ---
                is_pl_vertical = False
                is_pl_landscape = False # 가로 여부 변수 추가
                pl_width = 0; pl_height = 0 # 크기 저장 변수
                if original_pl_url:
                    try:
                        logger.debug(f"Checking orientation for videoa pl: {original_pl_url}")
                        im = SiteUtil.imopen(original_pl_url, proxy_url=proxy_url)
                        if im:
                            pl_width, pl_height = im.size
                            if pl_width < pl_height: is_pl_vertical = True
                            elif pl_width > pl_height: is_pl_landscape = True # 가로 조건 추가
                            logger.debug(f"Original videoa 'pl' image ({pl_width}x{pl_height}) - Vertical: {is_pl_vertical}, Landscape: {is_pl_landscape}")
                        else: logger.warning("Could not open videoa pl image to check orientation.")
                    except Exception as e_imopen: logger.warning(f"Could not determine videoa pl orientation: {e_imopen}")
                # --- 방향 확인 끝 ---

                # --- videoa 이미지 처리 시작 ---
                img_urls = raw_img_urls.copy()
                if ps_url_from_cache: img_urls['ps'] = ps_url_from_cache
                elif not img_urls['ps'] and img_urls['pl']: img_urls['ps'] = img_urls['pl']
                elif not img_urls['ps'] and not img_urls['pl']: logger.error("Videoa ps and pl URLs are missing.")

                current_ps_url = img_urls.get('ps') # 비교용 ps URL

                logger.debug(f"[Videoa] Image URLs before resolve: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} arts={len(img_urls.get('arts',[]))}")
                SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)
                logger.debug(f"[Videoa] Image URLs *after* resolve: poster={bool(img_urls.get('poster'))} crop={img_urls.get('poster_crop')} landscape={bool(img_urls.get('landscape'))}")

                # --- !!! 후처리 로직 강화 !!! ---
                resolved_poster_url = img_urls.get('poster')
                resolved_crop_mode = img_urls.get('poster_crop') # crop 모드 확인

                override_applied = False # 후처리 적용 여부 플래그

                # 조건 1: SiteUtil이 ps로 fallback했는데, 원본 pl이 세로였다면 원본 pl 사용
                if not override_applied and is_pl_vertical and current_ps_url and resolved_poster_url == current_ps_url and original_pl_url:
                    logger.info("Override 1: SiteUtil fallback to 'ps', but original 'pl' was vertical. Using original 'pl' as poster.")
                    img_urls['poster'] = original_pl_url
                    img_urls['poster_crop'] = None
                    if img_urls.get('landscape') == original_pl_url: img_urls['landscape'] = "" # 세로 pl은 landscape 아님
                    override_applied = True

                # 조건 2: SiteUtil이 원본 pl을 포스터로 선택했지만, crop 안 했고 & 원본 pl이 가로였다면 -> ps로 강제 fallback
                if not override_applied and is_pl_landscape and resolved_poster_url == original_pl_url and resolved_crop_mode is None and current_ps_url:
                    # ps가 있어야 fallback 가능
                    logger.info("Override 2: SiteUtil chose landscape 'pl' as poster without cropping. Falling back to 'ps'.")
                    img_urls['poster'] = current_ps_url
                    img_urls['poster_crop'] = None # ps는 crop 없음
                    # landscape는 원래 pl 유지 (SiteUtil 결정 존중 또는 원본 pl 강제 지정 가능)
                    # img_urls['landscape'] = original_pl_url # 명시적으로 설정
                    override_applied = True

                if override_applied:
                     logger.debug(f"[Videoa] Image URLs *after* override: poster={bool(img_urls.get('poster'))} crop={img_urls.get('poster_crop')} landscape={bool(img_urls.get('landscape'))}")
                # --- 후처리 끝 ---

                # --- 최종 이미지 처리 및 할당 ---
                entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
                entity.fanart = []
                resolved_arts = img_urls.get("arts", [])
                processed_fanart_count = 0
                for href in resolved_arts:
                    if processed_fanart_count >= max_arts: break
                    try:
                        fanart_url = SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url)
                        if fanart_url: entity.fanart.append(fanart_url); processed_fanart_count += 1
                    except Exception as e_fanart: logger.error(f"Error processing videoa fanart image {href}: {e_fanart}")
                logger.debug(f"[Videoa] Final Thumb: {entity.thumb}, Fanart Count: {len(entity.fanart)}")

                # --- videoa 메타데이터 파싱 (v_new 로직) ---
                # 제목/Tagline
                title_node = tree.xpath('//h1[@id="title"]')
                if title_node:
                    h1_text = title_node[0].text_content().strip()
                    prefix_tags = title_node[0].xpath('./span[@class="red"]/text()')
                    title_cleaned = h1_text.replace(prefix_tags[0].strip(), "").strip() if prefix_tags else h1_text
                    entity.tagline = SiteUtil.trans(title_cleaned, do_trans=do_trans)
                else: logger.warning("[Videoa] Tagline (h1#title) not found.")
                # 정보 테이블 파싱 (XPath 검증 필요)
                info_table_xpath = '//table[contains(@class, "mg-b20")]//tr'
                tags = tree.xpath(info_table_xpath)
                premiered_shouhin = None; premiered_haishin = None
                for tag in tags: # (v_new videoa 파싱 로직)
                    key_node = tag.xpath('./td[@class="nw"]/text()'); value_node = tag.xpath('./td[not(@class="nw")]')
                    if not key_node or not value_node: continue
                    key = key_node[0].strip().replace("：", ""); value_td = value_node[0]; value_text_all = value_td.text_content().strip()
                    if value_text_all == "----" or not value_text_all: continue
                    if key == "配信開始日": premiered_haishin = value_text_all.replace("/", "-")
                    elif key == "商品発売日": premiered_shouhin = value_text_all.replace("/", "-")
                    elif key == "収録時間": m=re.search(r"(\d+)",value_text_all); entity.runtime = int(m.group(1)) if m else None
                    elif key == "出演者": # (배우 처리)
                        actors = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        if actors: entity.actor = [EntityActor(name) for name in actors]
                        elif value_text_all != '----': entity.actor = [EntityActor(n.strip()) for n in value_text_all.split('/') if n.strip()]
                        else: entity.actor = []
                    elif key == "監督": # (감독 처리)
                        directors = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        entity.director = directors[0] if directors else (value_text_all if value_text_all != '----' else None)
                    elif key == "シリーズ": # (시리즈 처리)
                        if entity.tag is None: entity.tag = []
                        series = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        s_name = series[0] if series else (value_text_all if value_text_all != '----' else None)
                        if s_name: entity.tag.append(SiteUtil.trans(s_name, do_trans=do_trans))
                    elif key == "メーカー": # (제작사 처리)
                        if entity.studio is None:
                            makers = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                            m_name = makers[0] if makers else (value_text_all if value_text_all != '----' else None)
                            if m_name: entity.studio = SiteUtil.trans(m_name, do_trans=do_trans)
                    elif key == "レーベル": # (레이블 -> 스튜디오 처리)
                        labels = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        l_name = labels[0] if labels else (value_text_all if value_text_all != '----' else None)
                        if l_name:
                            if do_trans: entity.studio = SiteUtil.av_studio.get(l_name, SiteUtil.trans(l_name))
                            else: entity.studio = l_name
                    elif key == "ジャンル": # (장르 처리)
                        entity.genre = []
                        for genre_ja in value_td.xpath('.//a/text()'):
                            genre_ja = genre_ja.strip()
                            if not genre_ja or "％OFF" in genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                    elif key == "品番": # (품번 -> 태그 처리)
                        match_real = cls.PTN_SEARCH_REAL_NO.match(value_text_all)
                        if match_real:
                            label = match_real.group("real").upper()
                            if entity.tag is None: entity.tag = []
                            if label not in entity.tag: entity.tag.append(label)
                    elif key == "平均評価": # (평점 처리 - v_new 방식 사용)
                        rating_img = value_td.xpath('.//img/@src')
                        if rating_img:
                            match_rate = cls.PTN_RATING.search(rating_img[0]) # v_old 패턴 시도
                            if match_rate:
                                rate_str = match_rate.group("rating").replace("_",".")
                                try:
                                    rate_val = float(rate_str); rate_val /= 10.0 # 5점 만점 변환
                                    if 0 <= rate_val <= 5:
                                        img_url = "https:" + rating_img[0] if rating_img[0].startswith("//") else rating_img[0]
                                        entity.ratings = [EntityRatings(rate_val, max=5, name="dmm", image_url=img_url)]
                                except ValueError: logger.warning(f"Rating conv err (videoa): {rate_str}")
                final_premiered = premiered_shouhin or premiered_haishin
                if final_premiered: entity.premiered = final_premiered; entity.year = int(final_premiered[:4]) if final_premiered else None
                else: logger.warning("[Videoa] Premiered date not found."); entity.premiered = None; entity.year = None
                # 줄거리 파싱 (XPath 검증 필요)
                plot_xpath = '//div[@class="mg-b20 lh4"]/text()'
                plot_nodes = tree.xpath(plot_xpath)
                if plot_nodes:
                    plot_text = "\n".join([p.strip() for p in plot_nodes if p.strip()]).split("※")[0].strip()
                    if plot_text: entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                else: logger.warning(f"[Videoa] Plot not found using XPath: {plot_xpath}")
                # 예고편 처리 (v_new AJAX 방식)
                entity.extras = []
                if use_extras:
                    logger.debug(f"[Videoa] Attempting trailer AJAX/Iframe for {code}")
                    try:
                        trailer_url = None; trailer_title = entity.title if entity.title and entity.title != code[2:].upper() else code
                        ajax_url = py_urllib_parse.urljoin(cls.site_base_url, f"/digital/videoa/-/detail/ajax-movie/=/cid={code[2:]}/")
                        ajax_headers = cls._get_request_headers(referer=detail_url); ajax_headers['Accept'] = 'text/html, */*; q=0.01'; ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                        ajax_response = SiteUtil.get_response(ajax_url, proxy_url=proxy_url, headers=ajax_headers)
                        if not (ajax_response and ajax_response.status_code == 200): raise Exception(f"AJAX request failed (Status: {ajax_response.status_code if ajax_response else 'No Resp'})")

                        ajax_html_text = ajax_response.text

                        # --- lxml.html 임포트 확인 및 사용 ---
                        try:
                            from lxml import html # 사용 직전에 명시적 임포트
                        except ImportError:
                            logger.error("lxml library is required for AJAX trailer parsing but not installed.")
                            raise # 상위 except에서 잡도록 함

                        iframe_tree = html.fromstring(ajax_html_text) # 이제 html은 확실히 lxml.html 모듈
                        # --- 임포트 확인 끝 ---

                        iframe_srcs = iframe_tree.xpath("//iframe/@src")
                        if not iframe_srcs: raise Exception("Iframe not found")

                        iframe_url = py_urllib_parse.urljoin(ajax_url, iframe_srcs[0]); player_headers = cls._get_request_headers(referer=ajax_url)
                        player_response_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=player_headers)
                        if not player_response_text: raise Exception("Failed to get player page content")

                        pos = player_response_text.find("const args = {");
                        if pos != -1:
                            json_start = player_response_text.find("{", pos); json_end = player_response_text.find("};", json_start)
                            if json_start != -1 and json_end != -1:
                                data_str = player_response_text[json_start : json_end+1]
                                try:
                                    data = json.loads(data_str)
                                    bitrates = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                    if bitrates and bitrates[0].get("src"):
                                        trailer_src = bitrates[0]["src"]
                                        trailer_url = "https:" + trailer_src if trailer_src.startswith("//") else trailer_src
                                        if data.get("title"): trailer_title = data.get("title").strip()
                                except json.JSONDecodeError as je: logger.warning(f"Failed to decode 'const args' JSON: {je}")
                        if trailer_url:
                            entity.extras.append(EntityExtra("trailer", SiteUtil.trans(trailer_title, do_trans=do_trans), "mp4", trailer_url))
                            logger.info(f"[Videoa] Trailer added successfully for {code}")
                        else: logger.error(f"[Videoa] Failed to extract trailer URL for {code}.")
                    except Exception as extra_e: # ImportError 포함 모든 예외 처리
                        logger.exception(f"Error processing trailer for videoa {code}: {extra_e}")

            except Exception as e_parse_videoa:
                logger.exception(f"Error parsing videoa metadata: {e_parse_videoa}")


        elif content_type == 'dvd':
            logger.debug("Processing type 'dvd'...")
            try:
                # --- dvd 이미지 처리 ---
                img_urls = cls.__img_urls(tree, content_type='dvd') # dvd용 URL 추출
                if ps_url_from_cache: img_urls['ps'] = ps_url_from_cache
                elif not img_urls['ps'] and img_urls['pl']: img_urls['ps'] = img_urls['pl'] # ps fallback
                elif not img_urls['ps'] and not img_urls['pl']: logger.error("DVD ps and pl URLs are missing.")

                logger.debug(f"[DVD] Image URLs before resolve: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} arts={len(img_urls.get('arts',[]))}")
                SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)
                entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
                entity.fanart = []
                resolved_arts = img_urls.get("arts", [])
                processed_fanart_count = 0
                for href in resolved_arts:
                    if processed_fanart_count >= max_arts: break
                    try:
                        fanart_url = SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url)
                        if fanart_url: entity.fanart.append(fanart_url); processed_fanart_count += 1
                    except Exception as e_fanart: logger.error(f"Error processing dvd fanart image {href}: {e_fanart}")
                logger.debug(f"[DVD] Final Thumb: {entity.thumb}, Fanart Count: {len(entity.fanart)}")

                # --- dvd 메타데이터 파싱 (v_old 로직) ---
                # Tagline / Title
                title_xpath = '//h1[@id="title"]'
                title_tags = tree.xpath(title_xpath)
                if title_tags:
                    h1_full_text = title_tags[0].text_content().strip()
                    span_text_nodes = title_tags[0].xpath('./span[contains(@class, "txt_before-sale")]/text()')
                    span_text = "".join(span_text_nodes).strip()
                    title_text = h1_full_text.replace(span_text, "").strip() if span_text else h1_full_text
                    if title_text: entity.tagline = SiteUtil.trans(title_text, do_trans=do_trans).replace("[배달 전용]", "").replace("[특가]", "").strip()
                else: logger.warning("[DVD] h1#title tag not found (v_old).")
                # 정보 테이블 파싱
                info_table_xpath = '//div[@class="wrapper-product"]//table//tr'
                tags = tree.xpath(info_table_xpath)
                premiered_shouhin = None; premiered_hatsubai = None; premiered_haishin = None
                for tag in tags: # (v_old DVD 파싱 로직)
                    td_tags = tag.xpath(".//td")
                    if td_tags and len(td_tags)==2 and "平均評価：" in td_tags[0].text_content(): # 평점 처리
                        rating_img_tags = td_tags[1].xpath('.//img/@src')
                        if rating_img_tags:
                            match_rating = cls.PTN_RATING.search(rating_img_tags[0])
                            if match_rating:
                                rating_value_str = match_rating.group("rating").replace("_", ".")
                                try:
                                    rating_value = float(rating_value_str) / 10.0 # 10 나누기
                                    if 0 <= rating_value <= 5:
                                        rating_img_url = "https:" + rating_img_tags[0] if not rating_img_tags[0].startswith("http") else rating_img_tags[0]
                                        if not entity.ratings: entity.ratings = [EntityRatings(rating_value, max=5, name="dmm", image_url=rating_img_url)]
                                        else: entity.ratings[0].value = rating_value; entity.ratings[0].image_url = rating_img_url
                                except ValueError: logger.warning(f"Could not convert rating value: {rating_value_str} (v_old)")
                        continue
                    if len(td_tags) != 2: continue # 일반 행
                    key = td_tags[0].text_content().strip(); value_node = td_tags[1]; value_text_all = value_node.text_content().strip()
                    if value_text_all == "----" or not value_text_all: continue
                    if key == "商品発売日：": premiered_shouhin = value_text_all.replace("/", "-")
                    elif key == "発売日：": premiered_hatsubai = value_text_all.replace("/", "-")
                    elif key == "配信開始日：": premiered_haishin = value_text_all.replace("/", "-")
                    elif key == "収録時間：": # (시간)
                        match_runtime = re.search(r"(\d+)", value_text_all)
                        if match_runtime: entity.runtime = int(match_runtime.group(1))
                    elif key == "出演者：": # (배우)
                        entity.actor = []
                        for a_tag in value_node.xpath(".//a"):
                            actor_name = a_tag.text_content().strip()
                            if actor_name and actor_name != "▼すべて表示する": entity.actor.append(EntityActor(actor_name))
                    elif key == "監督：": # (감독)
                        a_tags = value_node.xpath(".//a"); entity.director = a_tags[0].text_content().strip() if a_tags else value_text_all
                    elif key == "シリーズ：": # (시리즈)
                        if entity.tag is None: entity.tag = []
                        a_tags = value_node.xpath(".//a"); series_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                        if series_name: entity.tag.append(SiteUtil.trans(series_name, do_trans=do_trans))
                    elif key == "メーカー：": # (제작사)
                        if entity.studio is None:
                            a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                            entity.studio = SiteUtil.trans(studio_name, do_trans=do_trans)
                    elif key == "レーベル：": # (레이블)
                        a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                        if do_trans: entity.studio = SiteUtil.av_studio.get(studio_name, SiteUtil.trans(studio_name))
                        else: entity.studio = studio_name
                    elif key == "ジャンル：": # (장르)
                        entity.genre = []
                        for tag_a in value_node.xpath(".//a"):
                            genre_ja = tag_a.text_content().strip()
                            if "％OFF" in genre_ja or not genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                            if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                            else:
                                genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                                if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                    elif key == "品番：": # (품번 -> 제목)
                        value = value_text_all; match_id = cls.PTN_ID.search(value); id_before = None
                        if match_id: id_before = match_id.group(0); value = value.lower().replace(id_before, "zzid")
                        match_real = cls.PTN_SEARCH_REAL_NO.match(value); formatted_title = value_text_all.upper()
                        if match_real:
                            label = match_real.group("real").upper()
                            if id_before is not None: label = label.replace("ZZID", id_before.upper())
                            formatted_title = label + "-" + str(int(match_real.group("no"))).zfill(3)
                            if entity.tag is None: entity.tag = []
                            if label not in entity.tag: entity.tag.append(label)
                        entity.title = entity.originaltitle = entity.sorttitle = formatted_title
                        if entity.tagline is None: entity.tagline = entity.title # Tagline fallback
                # 최종 날짜
                final_premiered = premiered_shouhin or premiered_hatsubai or premiered_haishin
                if final_premiered:
                    entity.premiered = final_premiered
                    try: entity.year = int(final_premiered[:4])
                    except ValueError: logger.warning(f"[DVD] Could not parse year: {final_premiered}"); entity.year = None
                else: logger.warning("[DVD] No premiered date found."); entity.premiered = None; entity.year = None
                # 줄거리
                plot_xpath = '//div[@class="mg-b20 lh4"]/p[@class="mg-b20"]/text()'
                plot_tags = tree.xpath(plot_xpath)
                if plot_tags:
                    plot_text = "\n".join([p.strip() for p in plot_tags if p.strip()]).split("※")[0].strip()
                    entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                else: logger.warning("[DVD] Plot not found.")
                # 리뷰 섹션 평점/투표수
                review_section_xpath = '//div[@id="review_anchor"]'
                review_sections = tree.xpath(review_section_xpath)
                if review_sections:
                    review_section = review_sections[0]
                    try:
                        avg_rating_tags = review_section.xpath('.//div[@class="dcd-review__points"]/p[@class="dcd-review__average"]/strong/text()')
                        if avg_rating_tags:
                            avg_rating_str = avg_rating_tags[0].strip(); avg_rating_value = float(avg_rating_str)
                            if 0 <= avg_rating_value <= 5:
                                if entity.ratings: entity.ratings[0].value = avg_rating_value
                                else: entity.ratings = [EntityRatings(avg_rating_value, max=5, name="dmm")]
                                logger.debug(f"[DVD] Updated rating value from review: {avg_rating_value}")
                        votes_tags = review_section.xpath('.//div[@class="dcd-review__points"]/p[@class="dcd-review__evaluates"]/strong/text()')
                        if votes_tags:
                            votes_str = votes_tags[0].strip(); match_votes = re.search(r"(\d+)", votes_str)
                            if match_votes:
                                votes_value = int(match_votes.group(1))
                                if entity.ratings: entity.ratings[0].votes = votes_value; logger.debug(f"[DVD] Updated rating votes from review: {votes_value}")
                    except Exception as rating_update_e: logger.exception(f"Error updating rating details from review (v_old): {rating_update_e}")
                else: logger.warning("[DVD] Review section not found.")
                # 예고편 (v_old 로직)
                entity.extras = []
                if use_extras: # (AJAX / onclick 로직)
                    logger.debug(f"[DVD] Attempting trailer for {code}")
                    try: # (예고편 처리 로직 v_old 것 그대로 삽입)
                        trailer_url = None; trailer_title_from_data = None
                        ajax_url_xpath = '//a[@id="sample-video1"]/@data-video-url'
                        ajax_url_tags = tree.xpath(ajax_url_xpath)
                        if ajax_url_tags:
                            ajax_relative_url = ajax_url_tags[0]; ajax_full_url = py_urllib_parse.urljoin(detail_url, ajax_relative_url)
                            try:
                                ajax_headers = cls._get_request_headers(referer=detail_url); ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                                ajax_response_text = SiteUtil.get_text(ajax_full_url, proxy_url=proxy_url, headers=ajax_headers)
                                try: from lxml import html
                                except ImportError: logger.error("lxml library required."); raise
                                ajax_tree = html.fromstring(ajax_response_text); iframe_srcs = ajax_tree.xpath("//iframe/@src")
                                if iframe_srcs:
                                    iframe_url = py_urllib_parse.urljoin(ajax_full_url, iframe_srcs[0]); iframe_headers = cls._get_request_headers(referer=ajax_full_url)
                                    iframe_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=iframe_headers)
                                    pos = iframe_text.find("const args = {")
                                    if pos != -1:
                                        json_start = iframe_text.find("{", pos); json_end = iframe_text.find("};", json_start)
                                        if json_start != -1 and json_end != -1:
                                            data_str = iframe_text[json_start : json_end+1]
                                            try:
                                                data = json.loads(data_str)
                                                data["bitrates"] = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                                if data.get("bitrates") and data["bitrates"][0].get("src"):
                                                    trailer_src = data["bitrates"][0]["src"]
                                                    trailer_url = "https:" + trailer_src if not trailer_src.startswith("http") else trailer_src
                                                    trailer_title_from_data = data.get("title")
                                            except json.JSONDecodeError as je: logger.warning(f"Failed to decode JSON from iframe (v_old): {je}")
                            except Exception as ajax_e: logger.exception(f"Error during trailer AJAX request (v_old): {ajax_e}")
                        if not trailer_url: # onclick fallback
                            onclick_xpath = '//a[@id="sample-video1"]/@onclick'
                            onclick_tags = tree.xpath(onclick_xpath)
                            if onclick_tags:
                                onclick_text = onclick_tags[0]; match_json = re.search(r"gaEventVideoStart\('(\{.*?\})','(\{.*?\})'\)", onclick_text)
                                if match_json:
                                    video_data_str = match_json.group(1)
                                    try:
                                        video_data = json.loads(video_data_str.replace('\\"', '"'))
                                        if video_data.get("video_url"): trailer_url = video_data["video_url"]
                                    except Exception as json_e: logger.warning(f"Failed to parse JSON from onclick (v_old fallback): {json_e}")
                        if trailer_url:
                            if trailer_title_from_data and trailer_title_from_data.strip(): trailer_title_to_use = trailer_title_from_data.strip()
                            elif entity.title: trailer_title_to_use = entity.title
                            else: trailer_title_to_use = "Trailer"
                            entity.extras.append(EntityExtra("trailer", SiteUtil.trans(trailer_title_to_use, do_trans=do_trans), "mp4", trailer_url))
                            logger.info(f"[DVD] Trailer added successfully for {code}")
                        else: logger.warning(f"[DVD] No trailer URL found for {code}.")
                    except Exception as extra_e: logger.exception(f"미리보기 처리 중 예외 (v_old): {extra_e}")

            except Exception as e_parse_dvd:
                logger.exception(f"Error parsing dvd metadata (v_old logic block): {e_parse_dvd}")

        else: # 타입 불명
            logger.error(f"Cannot parse info: Unknown content type '{content_type}' for {code}")
            # 타입 불명 시에도 entity 객체는 반환됨 (기본 정보만 포함)

        # --- 공통 후처리 ---
        # 최종 제목/태그라인 정리 (선택적)
        if not entity.tagline and entity.title: entity.tagline = entity.title # 태그라인 없으면 제목으로
        if not entity.title: # 제목이 아예 없으면 코드로
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
