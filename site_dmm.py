# -*- coding: utf-8 -*-
import json
import re
import requests
import urllib.parse as py_urllib_parse
from lxml import html, etree # etree 추가
import os
import sqlite3

# lib_metadata 패키지 내 다른 모듈 import
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

class SiteDmm:
    site_name = "dmm"
    site_base_url = "https://www.dmm.co.jp"
    fanza_av_url = "https://video.dmm.co.jp/av/"
    age_check_confirm_url_template = "https://www.dmm.co.jp/age_check/set?r={redirect_url}"
    module_char = "C"; site_char = "D"

    dmm_base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": site_base_url + "/",
        "Sec-Ch-Ua": '"Chromium";v="110", "Not A(Brand";v="24", "Google Chrome";v="110"',
        "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1", "DNT": "1", "Cache-Control": "max-age=0", "Connection": "keep-alive",
    }

    PTN_SEARCH_CID = re.compile(r"\/cid=(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^(h_)?\d*(?P<real>[a-zA-Z]+)(?P<no>\d+)([a-zA-Z]+)?$")
    PTN_ID = re.compile(r"\d{2}id", re.I)
    PTN_RATING = re.compile(r"/(?P<rating>\d{1,2})\.gif")
    age_verified = False; last_proxy_used = None; _ps_url_cache = {}

    @classmethod
    def _get_request_headers(cls, referer=None):
        headers = cls.dmm_base_headers.copy()
        if referer: headers['Referer'] = referer
        return headers

    @classmethod
    def _ensure_age_verified(cls, proxy_url=None):
        # (이전 원본 코드 내용 유지 - SiteUtil.session 사용)
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
                confirm_response = SiteUtil.get_response(
                    age_check_confirm_url, method='GET', proxy_url=proxy_url,
                    headers=confirm_headers, allow_redirects=False
                )
                logger.debug(f"Confirmation GET status: {confirm_response.status_code}")
                logger.debug(f"Session Cookies after confirm GET: {[(c.name, c.value, c.domain) for c in SiteUtil.session.cookies]}")
                if confirm_response.status_code == 302 and 'age_check_done=1' in confirm_response.headers.get('Set-Cookie', ''):
                    logger.debug("Age confirmation successful via Set-Cookie.")
                    # 최종 확인
                    final_cookies = SiteUtil.session.cookies
                    if any('age_check_done' in final_cookies.get_dict(domain=d) and final_cookies.get_dict(domain=d)['age_check_done'] == '1' for d in domain_checks):
                        logger.debug("age_check_done=1 confirmed in session.")
                        cls.age_verified = True; return True
                    else:
                        logger.warning("Set-Cookie received, but not updated in session. Trying manual set...")
                        SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.co.jp", path="/"); SiteUtil.session.cookies.set("age_check_done", "1", domain=".dmm.com", path="/")
                        logger.info("Manually set age_check_done cookie."); cls.age_verified = True; return True
                else: logger.warning(f"Age check failed (Status: {confirm_response.status_code} or cookie missing).")
            except Exception as e: logger.exception(f"Age verification exception: {e}")
            cls.age_verified = False; return False
        else:
            logger.debug("Age verification already done."); return True

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        if not cls._ensure_age_verified(proxy_url=proxy_url):
            logger.error("Age verification failed, cannot perform search.")
            return []

        # 키워드 처리
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd": keyword = keyword[:-3]
        keyword = keyword.replace("-", " ")
        keyword_tmps = keyword.split(" ")
        if len(keyword_tmps) == 2: dmm_keyword = keyword_tmps[0] + keyword_tmps[1].zfill(5)
        else: dmm_keyword = keyword
        logger.debug("keyword [%s] -> [%s]", keyword, dmm_keyword)

        # --- 검색 URL: /search/?... 경로 사용 ---
        search_params = {
            'redirect': '1',
            'enc': 'UTF-8',
            'category': '', # 카테고리 지정 안 함
            'searchstr': dmm_keyword,
            # 'commit.x': '23', # 불필요
            # 'commit.y': '22'
        }
        search_url = f"{cls.site_base_url}/search/?{py_urllib_parse.urlencode(search_params)}"
        logger.info(f"Using search URL (will follow redirects): {search_url}")

        # 헤더 준비 (Referer는 FANZA 홈 등)
        search_headers = cls._get_request_headers(referer=cls.fanza_av_url)
        tree = None
        received_html_content = None

        try:
            # SiteUtil.get_tree 사용 (allow_redirects=True 가 기본이거나 내부 처리 가정)
            logger.debug(f"Calling SiteUtil.get_tree for {search_url} (expecting redirect handling)")
            tree = SiteUtil.get_tree(search_url, proxy_url=proxy_url, headers=search_headers, allow_redirects=True) # 명시적으로 True 추가

            if tree is not None:
                # --- 최종 도착 페이지의 HTML 로깅 ---
                try:
                    received_html_content = etree.tostring(tree, pretty_print=True, encoding='unicode', method='html')
                    logger.debug(">>>>>> Received FINAL HTML after redirects Start >>>>>>")
                    log_chunk_size = 1500
                    for i in range(0, len(received_html_content), log_chunk_size):
                        logger.debug(received_html_content[i:i+log_chunk_size])
                    logger.debug("<<<<<< Received FINAL HTML after redirects End <<<<<<")

                    # 연령 확인 페이지 체크
                    title_tags_check = tree.xpath('//title/text()')
                    if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]:
                        logger.error("Age verification page received unexpectedly after redirects.")
                        return []
                except Exception as e_log_html:
                    logger.error(f"Error converting or logging final HTML: {e_log_html}")
            else:
                logger.warning("SiteUtil.get_tree returned None after potential redirects.")
                return []
        except Exception as e:
            logger.exception(f"Failed to get tree for initial search URL {search_url}: {e}")
            return []

        # --- XPath: 데스크톱 grid 구조만 사용 ---
        list_xpath = '//div[contains(@class, "grid-cols-4")]//div[contains(@class, "border-r") and contains(@class, "border-b")]'
        lists = []
        logger.debug(f"Attempting XPath (Desktop Grid): {list_xpath}")
        try:
            lists = tree.xpath(list_xpath)
        except Exception as e_xpath:
            logger.error(f"XPath error ({list_xpath}): {e_xpath}")

        logger.debug(f"Found {len(lists)} items using Desktop Grid XPath.")
        if not lists:
            logger.warning(f"No items found using Desktop Grid XPath.")
            return []

        # --- 개별 결과 처리 루프 (데스크톱 파싱 로직만 사용) ---
        ret = []; score = 60
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                href = None; item.image_url = None; item.title = "Not Found"; original_ps_url = None
                match_real_no = None

                # --- 데스크톱 구조 파싱 ---
                link_tag_img = node.xpath('.//a[contains(@class, "flex justify-center")]')
                if not link_tag_img: continue
                img_link_href = link_tag_img[0].attrib.get("href", "").lower()
                img_tag = link_tag_img[0].xpath('./img/@src')
                if not img_tag: continue
                original_ps_url = img_tag[0]
                title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]')
                if not title_link_tag: continue
                title_link_with_p = node.xpath('.//a[contains(@href, "/detail/=/cid=") and ./p[contains(@class, "hover:text-linkHover")]]')
                if title_link_with_p: title_link_tag = title_link_with_p[0]
                else: title_link_tag = title_link_tag[0]
                title_link_href = title_link_tag.attrib.get("href", "").lower()
                href = title_link_href if title_link_href else img_link_href
                title_p_tag = title_link_tag.xpath('./p[contains(@class, "hover:text-linkHover")]')
                if title_p_tag: item.title = title_p_tag[0].text_content().strip()

                # 공통 처리
                if not original_ps_url: continue
                if original_ps_url.startswith("//"): original_ps_url = "https:" + original_ps_url
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try: item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"Error processing image: {e_img}"); item.image_url = original_ps_url
                else: item.image_url = original_ps_url

                if not href: continue
                match_cid = cls.PTN_SEARCH_CID.search(href)
                if match_cid: item.code = cls.module_char + cls.site_char + match_cid.group("code")
                else: logger.warning(f"CID not found in href: {href}"); continue
                if any(exist_item.get("code") == item.code for exist_item in ret): continue
                if not item.title or item.title == "Not Found": logger.warning(f"Title not found for {item.code}, using code."); item.title = item.code

                if item.code and original_ps_url: cls._ps_url_cache[item.code] = original_ps_url; logger.debug(f"Stored ps_url for {item.code} in cache.")

                if manual: item.title_ko = f"(현재 인터페이스에서는 번역을 제공하지 않습니다) {item.title}"
                else:
                    if do_trans and item.title:
                        try: item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)
                        except Exception as e_trans: logger.error(f"Error translating title: {e_trans}"); item.title_ko = item.title
                    else: item.title_ko = item.title

                match_real_no = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                if match_real_no: item_ui_code_base = match_real_no.group("real") + match_real_no.group("no")
                else: item_ui_code_base = item.code[2:]
                current_score = 0
                if len(keyword_tmps) == 2:
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif item_ui_code_base.replace("0", "") == dmm_keyword.replace("0", ""): current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    elif keyword_tmps[0] in item.code and keyword_tmps[1] in item.code: current_score = score
                    elif keyword_tmps[0] in item.code or keyword_tmps[1] in item.code: current_score = 60
                    else: current_score = 20
                else:
                    if item_ui_code_base == dmm_keyword: current_score = 100
                    elif dmm_keyword in item_ui_code_base: current_score = score
                    else: current_score = 20
                item.score = current_score
                if current_score < 100 and score > 20: score -= 5

                if match_real_no:
                    real_part = match_real_no.group("real").upper()
                    try: no_part_str = str(int(match_real_no.group("no"))).zfill(3); item.ui_code = f"{real_part}-{no_part_str}"
                    except ValueError: item.ui_code = f"{real_part}-{match_real_no.group('no')}"
                else:
                    ui_code_temp = item.code[2:].upper();
                    if ui_code_temp.startswith("H_"): ui_code_temp = ui_code_temp[2:]
                    m = re.match(r"([a-zA-Z]+)(\d+.*)", ui_code_temp)
                    if m:
                        real_part = m.group(1); num_part_match = re.match(r"(\d+)", m.group(2))
                        if num_part_match: item.ui_code = f"{real_part}-{str(int(num_part_match.group(1))).zfill(3)}"
                        else: item.ui_code = f"{real_part}-{m.group(2)}"
                    else: item.ui_code = ui_code_temp

                logger.debug(f"Item found - Score: {item.score}, Code: {item.code}, UI Code: {item.ui_code}, Title: {item.title_ko}")
                ret.append(item.as_dict())

            except Exception as e_inner: logger.exception(f"Error processing item node: {e_inner}")

        sorted_ret = sorted(ret, key=lambda k: k.get("score", 0), reverse=True)

        if not sorted_ret and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6)
            logger.debug(f"Retrying with {new_title}")
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)

        return sorted_ret


    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            data_list = cls.__search(keyword, **kwargs)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data_list else "no_match"; ret["data"] = data_list
        return ret


    # --- __info 메소드: 상세 페이지 구조 분석 후 수정 필요 ---
    @classmethod
    def __img_urls(cls, tree):
        # 상세 페이지 구조 분석 후 수정 필요
        logger.warning("__img_urls: XPath needs update based on actual detail page HTML.")
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        try:
            # 예전 XPath (수정 필요 가능성 높음)
            pl_xpath = '//div[@id="sample-video"]//img/@src' # 메인 이미지
            pl_tags = tree.xpath(pl_xpath)
            if pl_tags:
                img_urls['pl'] = pl_tags[0]
                if img_urls['pl'].startswith("//"): img_urls['pl'] = "https:" + img_urls['pl']
            else: # 다른 경로 시도 (예: fn-sampleImage-imagebox)
                pl_xpath_alt = '//div[@id="fn-sampleImage-imagebox"]/img/@src'
                pl_tags_alt = tree.xpath(pl_xpath_alt)
                if pl_tags_alt:
                    img_urls['pl'] = pl_tags_alt[0]
                    if img_urls['pl'].startswith("//"): img_urls['pl'] = "https:" + img_urls['pl']

            arts_xpath = '//a[@name="sample-image"]/@href' # 샘플 이미지 링크들
            arts_tags = tree.xpath(arts_xpath)
            if arts_tags:
                all_arts = []
                for href in arts_tags:
                    if href and href.strip():
                        full_href = href if href.startswith("http") else py_urllib_parse.urljoin(cls.site_base_url, href)
                        # 첫번째 샘플 이미지가 pl과 같으면 제외 (선택적)
                        if idx == 0 and full_href == img_urls.get('pl'): continue
                        all_arts.append(full_href)
                img_urls['arts'] = sorted(list(set(all_arts)), key=all_arts.index) # 중복 제거

            # ps 는 캐시 또는 pl fallback 사용 (아래 info 에서 처리)

        except Exception as e: logger.exception(f"Error extracting image URLs: {e}")
        logger.debug(f"Extracted img_urls: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} arts={len(img_urls.get('arts',[]))}")
        return img_urls

    @classmethod
    def __info( cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, ps_to_poster=False, crop_mode=None):
        logger.info(f"Getting detail info for {code}")
        ps_url_from_cache = cls._ps_url_cache.pop(code, None)
        if ps_url_from_cache: logger.debug(f"Using cached ps_url: {ps_url_from_cache}")
        else: logger.warning(f"ps_url for {code} not found in cache.")

        if not cls._ensure_age_verified(proxy_url=proxy_url):
            raise Exception(f"DMM age verification failed for info ({code}).")

        # 상세 페이지 URL (videoa 경로 사용)
        detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={code[2:]}/"
        logger.info(f"Accessing DMM detail page: {detail_url}")
        info_headers = cls._get_request_headers(referer=cls.fanza_av_url) # Referer 설정
        tree = None
        received_html_content = None

        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=info_headers)
            if tree is not None:
                # --- 상세 페이지 Raw HTML 로깅 ---
                try:
                    received_html_content = etree.tostring(tree, pretty_print=True, encoding='unicode', method='html')
                    logger.debug(f">>>>>> Received Detail HTML for {code} Start >>>>>>")
                    log_chunk_size = 1500
                    for i in range(0, len(received_html_content), log_chunk_size): logger.debug(received_html_content[i:i+log_chunk_size])
                    logger.debug(f"<<<<<< Received Detail HTML for {code} End <<<<<<")

                    title_tags_check = tree.xpath('//title/text()')
                    if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]:
                        raise Exception("Received age verification page instead of detail.")
                except Exception as e_log_html: logger.error(f"Error logging detail HTML: {e_log_html}")
            else: raise Exception("SiteUtil.get_tree returned None for detail page.")
        except Exception as e: logger.exception(f"Failed get/process detail tree: {e}"); raise

        # --- 파싱 시작 ---
        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"

        # --- 이미지 처리 (원본 코드의 __img_urls 호출 및 후처리) ---
        try:
            img_urls = cls.__img_urls(tree) # XPath 수정 필요할 수 있음
            img_urls['ps'] = ps_url_from_cache if ps_url_from_cache else img_urls.get('ps', "")
            # ps가 여전히 없으면 pl에서 가져오기 시도 (최후 fallback)
            if not img_urls['ps'] and img_urls.get('pl'):
                logger.warning("PS URL missing, using PL as fallback for PS.")
                img_urls['ps'] = img_urls['pl']
            if not img_urls['ps']: logger.error("Crucial PS URL is missing.")

            logger.debug(f"Image URLs for SiteUtil: {img_urls}")
            SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)
            entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
            entity.fanart = []
            resolved_arts = img_urls.get("arts", [])
            landscape_url = img_urls.get("landscape")
            for href in resolved_arts[:max_arts]:
                if href != landscape_url: entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))
            logger.debug(f"Final Thumb: {entity.thumb}, Fanart Count: {len(entity.fanart)}")
        except Exception as e: logger.exception(f"Error processing images: {e}")

        # --- 제목/Tagline (원본 코드 로직) ---
        try:
            # 원본 코드는 h1#title 사용 - 새 구조에서는 다를 수 있음!
            title_xpath = '//h1[@id="title"]'
            title_tags = tree.xpath(title_xpath)
            if title_tags:
                h1_full_text = title_tags[0].text_content().strip()
                # span_text_nodes = title_tags[0].xpath('./span[contains(@class, "txt_before-sale")]/text()') # 이전 span 클래스
                span_text_nodes = title_tags[0].xpath('./span[@class="red"]/text()') # 새 구조의 span 클래스
                span_text = "".join(span_text_nodes).strip()
                title_text = h1_full_text.replace(span_text, "").strip() if span_text else h1_full_text
                if title_text:
                    entity.tagline = SiteUtil.trans(title_text, do_trans=do_trans).replace("[배달 전용]", "").replace("[특가]", "").strip()
                    logger.debug(f"Tagline parsed: {entity.tagline[:50]}...")
                else: logger.warning("Could not extract text from h1#title.")
            else: logger.warning("Tagline (h1#title) not found.")
        except Exception as e: logger.error(f"Error parsing tagline: {e}")

        # --- 정보 테이블 파싱 (원본 코드 로직) ---
        # 이 XPath는 새 구조에서 작동 안 할 가능성 높음!
        # info_table_xpath = '//*[@id="mu"]/div/table//tr/td[1]/table//tr' # 원본 XPath
        info_table_xpath = '//table[contains(@class, "mg-b20")]//tr' # 이전 로그 기반 XPath
        tags = tree.xpath(info_table_xpath)
        logger.debug(f"Found {len(tags)} rows in info table (using: {info_table_xpath}).")
        premiered_shouhin = None; premiered_haishin = None; premiered_hatsubai = None; # 원본 변수명 유지

        for tag in tags:
            try:
                key_node = tag.xpath('./td[@class="nw"]/text()')
                value_node = tag.xpath('./td[not(@class="nw")]')
                if not key_node or not value_node: continue
                key = key_node[0].strip().replace("：", "")
                value_td = value_node[0]
                value_text_all = value_td.text_content().strip()
                if value_text_all == "----" or not value_text_all: continue

                logger.debug(f"Processing table row: Key='{key}', Value='{value_text_all[:50]}...'")

                if key == "配信開始日": premiered_haishin = value_text_all.replace("/", "-")
                elif key == "商品発売日": premiered_shouhin = value_text_all.replace("/", "-")
                # 원본 코드에는 '発売日' 처리도 있었음 (필요시 추가)
                elif key == "発売日：": premiered_hatsubai = value_text_all.replace("/", "-")
                elif key == "収録時間":
                    m = re.search(r"(\d+)", value_text_all); entity.runtime = int(m.group(1)) if m else None
                elif key == "出演者":
                    actors_found = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                    if actors_found: entity.actor = [EntityActor(name) for name in actors_found]
                    elif value_text_all != '----': entity.actor = [EntityActor(name.strip()) for name in value_text_all.split() if name.strip()]
                    else: entity.actor = []
                elif key == "監督":
                    directors = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                    entity.director = directors[0] if directors else (value_text_all if value_text_all != '----' else None)
                elif key == "シリーズ":
                    if entity.tag is None: entity.tag = []
                    series = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                    s_name = series[0] if series else (value_text_all if value_text_all != '----' else None)
                    if s_name: entity.tag.append(SiteUtil.trans(s_name, do_trans=do_trans))
                elif key == "メーカー":
                    if entity.studio is None:
                        makers = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        m_name = makers[0] if makers else (value_text_all if value_text_all != '----' else None)
                        if m_name: entity.studio = SiteUtil.trans(m_name, do_trans=do_trans)
                elif key == "レーベル":
                    labels = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                    l_name = labels[0] if labels else (value_text_all if value_text_all != '----' else None)
                    if l_name:
                        if do_trans: entity.studio = SiteUtil.av_studio.get(l_name, SiteUtil.trans(l_name))
                        else: entity.studio = l_name
                elif key == "ジャンル":
                    entity.genre = []
                    for genre_ja in value_td.xpath('.//a/text()'):
                        genre_ja = genre_ja.strip()
                        if not genre_ja or "％OFF" in genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                        if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                        else:
                            genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                            if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                elif key == "品番":
                    match_real = cls.PTN_SEARCH_REAL_NO.match(value_text_all)
                    if match_real:
                        label = match_real.group("real").upper()
                        if entity.tag is None: entity.tag = []
                        if label not in entity.tag: entity.tag.append(label)
                elif key == "平均評価":
                    rating_img = value_td.xpath('.//img/@src')
                    if rating_img:
                        match_rate = cls.PTN_RATING.search(rating_img[0])
                        if match_rate:
                            rate_str = match_rate.group("rating")
                            try:
                                rate_val = float(rate_str)
                                if rate_val > 5: rate_val /= 10.0 # 5점 만점 변환
                                if 0 <= rate_val <= 5:
                                    img_url = "https:" + rating_img[0] if rating_img[0].startswith("//") else rating_img[0]
                                    entity.ratings = [EntityRatings(rate_val, max=5, name="dmm", image_url=img_url)]
                                    logger.debug(f"Rating parsed: {rate_val}")
                            except ValueError: logger.warning(f"Rating conv err: {rate_str}")

            except Exception as e_row: logger.exception(f"Error parsing info row (key:{key}): {e_row}")

        # 최종 날짜 설정
        final_premiered = premiered_shouhin or premiered_haishin or premiered_hatsubai # 상품 출시일 우선
        if final_premiered:
            entity.premiered = final_premiered
            try: entity.year = int(final_premiered[:4])
            except: entity.year = None
        else: logger.warning("No premiered date found."); entity.premiered = None; entity.year = None

        # --- 줄거리 파싱 (원본 코드 로직) ---
        # 이 XPath도 새 구조에서 작동 안 할 가능성 높음!
        plot_xpath = '//div[@class="mg-b20 lh4"]/p[@class="mg-b20"]/text()' # 원본 XPath
        # 또는 plot_xpath = '//div[@class="mg-b20 lh4"]/text()' # 이전 로그 기반
        try:
            plot_nodes = tree.xpath(plot_xpath)
            if plot_nodes:
                plot_text = "\n".join([p.strip() for p in plot_nodes if p.strip()]).split("※")[0].strip()
                entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                logger.debug(f"Plot parsed: {entity.plot[:50]}...")
            else: logger.warning(f"Plot not found using XPath: {plot_xpath}")
        except Exception as e: logger.exception(f"Error parsing plot: {e}")


        # --- 예고편 처리 (원본 코드 로직 - 수정 필요) ---
        entity.extras = []
        if use_extras:
            logger.warning("Trailer parsing logic needs update based on new structure.")
            # 원본의 onclick 또는 AJAX/iframe 방식 분석 필요
            # try:
            #     # 예: ajax_url = tree.xpath('//a[@id="sample-video1"]/@data-video-url')
            #     # ... ajax/iframe 파싱 ...
            #     # 또는 onclick_text = tree.xpath('//a[@id="sample-video1"]/@onclick')
            #     # ... onclick 파싱 ...
            #     # if trailer_url: entity.extras.append(...)
            # except Exception as extra_e: logger.exception(f"Trailer parsing error: {extra_e}")


        # --- 원본 제목/정렬 제목 설정 (UI 코드 형식화 적용) ---
        match_real_no = cls.PTN_SEARCH_REAL_NO.search(code[2:])
        final_ui_code = code[2:].upper() # 기본값
        if match_real_no:
            real_part = match_real_no.group("real").upper()
            try: no_part_str = str(int(match_real_no.group("no"))).zfill(3); final_ui_code = f"{real_part}-{no_part_str}"
            except ValueError: final_ui_code = f"{real_part}-{match_real_no.group('no')}"
        else:
            ui_code_temp = code[2:].upper()
            if ui_code_temp.startswith("H_"): ui_code_temp = ui_code_temp[2:]
            m = re.match(r"([a-zA-Z]+)(\d+.*)", ui_code_temp)
            if m:
                real = m.group(1); rest = m.group(2); num_m = re.match(r"(\d+)", rest)
                final_ui_code = f"{real}-{str(int(num_m.group(1))).zfill(3)}" if num_m else f"{real}-{rest}"
            else: final_ui_code = ui_code_temp
        entity.title = entity.originaltitle = entity.sorttitle = final_ui_code
        logger.debug(f"Final title/originaltitle/sorttitle set to: {entity.title}")
        if not entity.tagline: entity.tagline = entity.title # Tagline fallback

        return entity

    @classmethod
    def info(cls, code, **kwargs): # 원본 유지
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            if entity: ret["ret"] = "success"; ret["data"] = entity.as_dict()
            else: ret["ret"] = "error"; ret["data"] = f"Failed to get info for {code}"
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        return ret
