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
                logger.debug(f"Confirmation GET status: {confirm_response.status_code}")
                logger.debug(f"Session Cookies after confirm GET: {[(c.name, c.value, c.domain) for c in SiteUtil.session.cookies]}")
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
            if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]: logger.error("Age page received."); return []
        except Exception as e: logger.exception(f"Failed to get tree for search: {e}"); return []

        list_xpath = '//div[contains(@class, "grid-cols-4")]//div[contains(@class, "border-r") and contains(@class, "border-b")]'
        lists = []; logger.debug(f"Attempting XPath (Desktop Grid): {list_xpath}")
        try: lists = tree.xpath(list_xpath)
        except Exception as e_xpath: logger.error(f"XPath error: {e_xpath}")
        logger.debug(f"Found {len(lists)} items using Desktop Grid XPath.")
        if not lists: logger.warning(f"No items found using Desktop Grid XPath."); return []

        ret = []; score = 60
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                href = None; item.image_url = None; item.title = "Not Found"; original_ps_url = None; match_real_no = None; content_type = "unknown"
                link_tag_img = node.xpath('.//a[contains(@class, "flex justify-center")]');
                if not link_tag_img: continue
                img_link_href = link_tag_img[0].attrib.get("href", "").lower()
                img_tag = link_tag_img[0].xpath('./img/@src')
                if not img_tag: continue
                original_ps_url = img_tag[0]
                title_link_tag = node.xpath('.//a[contains(@href, "/detail/=/cid=")]')
                if not title_link_tag: continue
                title_link_with_p = node.xpath('.//a[contains(@href, "/detail/=/cid=") and ./p[contains(@class, "hover:text-linkHover")]]')
                title_link_tag = title_link_with_p[0] if title_link_with_p else title_link_tag[0]
                title_link_href = title_link_tag.attrib.get("href", "").lower()
                href = title_link_href if title_link_href else img_link_href
                if href:
                    if "/digital/videoa/" in href: content_type = "videoa"
                    elif "/mono/dvd/" in href: content_type = "dvd"
                item.content_type = content_type
                title_p_tag = title_link_tag.xpath('./p[contains(@class, "hover:text-linkHover")]')
                if title_p_tag: item.title = title_p_tag[0].text_content().strip()

                if not original_ps_url: continue
                if original_ps_url.startswith("//"): original_ps_url = "https:" + original_ps_url
                item.image_url = original_ps_url
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    try: item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    except Exception as e_img: logger.error(f"ImgProcErr:{e_img}")

                if not href: continue
                match_cid = cls.PTN_SEARCH_CID.search(href)
                if match_cid: item.code = cls.module_char + cls.site_char + match_cid.group("code")
                else: continue
                if any(i.get("code") == item.code for i in ret): continue
                if not item.title or item.title == "Not Found": item.title = item.code
                if item.code and original_ps_url: cls._ps_url_cache[item.code] = {'ps': original_ps_url, 'type': content_type}; logger.debug(f"Stored ps&type:{item.code}")

                if manual: item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                else: item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans) if do_trans and item.title else item.title

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
                    real = match_real_no.group("real").upper(); no = match_real_no.group("no")
                    try: item.ui_code = f"{real}-{str(int(no)).zfill(3)}"
                    except ValueError: item.ui_code = f"{real}-{no}"
                else:
                    tmp = item.code[2:].upper();
                    if tmp.startswith("H_"): tmp = tmp[2:]
                    m = re.match(r"([a-zA-Z]+)(\d+.*)", tmp)
                    if m:
                        real = m.group(1); rest = m.group(2); num_m = re.match(r"(\d+)", rest)
                        item.ui_code = f"{real}-{str(int(num_m.group(1))).zfill(3)}" if num_m else f"{real}-{rest}"
                    else: item.ui_code = tmp
                logger.debug(f"Item found ({content_type}) - Score: {item.score}, Code: {item.code}, UI Code: {item.ui_code}, Title: {item.title_ko}")
                ret.append(item.as_dict())
            except Exception as e_inner: logger.exception(f"ItemProcErr:{e_inner}")

        sorted_ret = sorted(ret, key=lambda k: k.get("score", 0), reverse=True)
        if not sorted_ret and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6)
            logger.debug(f"Retrying with {new_title}")
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)
        return sorted_ret

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try: data_list = cls.__search(keyword, **kwargs)
        except Exception as exception: logger.exception("SearchErr:"); ret["ret"] = "exception"; ret["data"] = str(exception)
        else: ret["ret"] = "success" if data_list else "no_match"; ret["data"] = data_list
        return ret

    # --- __img_urls 수정: videoa 타입 처리 변경 ---
    @classmethod
    def __img_urls(cls, tree, content_type='unknown'):
        logger.debug(f"Extracting image URLs for type: {content_type}")
        img_urls = {'ps': "", 'pl': "", 'arts': []}
        try:
            if content_type == 'videoa':
                # videoa: 샘플 이미지 블록(arts)을 먼저 찾고, pl은 arts[0] 사용
                # XPath 수정 필요: 실제 샘플 이미지 블록 식별자 확인
                arts_xpath = '//div[@id="sample-image-block"]//a/@href' # 예전 구조 XPath
                arts_tags = tree.xpath(arts_xpath)
                if not arts_tags:
                    # 대체 XPath 시도 (최신 구조에서 발견된 패턴?)
                    arts_xpath_alt = '//a[contains(@id, "sample-image")]/@href'
                    logger.debug(f"Trying alternative arts XPath: {arts_xpath_alt}")
                    arts_tags = tree.xpath(arts_xpath_alt)

                if arts_tags:
                    logger.debug(f"Found {len(arts_tags)} potential arts links for videoa.")
                    all_arts = []
                    for href in arts_tags:
                        if href and href.strip():
                            # href 자체가 이미지 URL일 수 있음 (고화질 링크)
                            full_href = href if href.startswith("http") else py_urllib_parse.urljoin(cls.site_base_url, href)
                            all_arts.append(full_href)
                    # 중복 제거 및 순서 유지
                    unique_arts = sorted(list(set(all_arts)), key=all_arts.index)
                    img_urls['arts'] = unique_arts
                    # pl은 arts의 첫 번째 이미지로 설정
                    if img_urls['arts']:
                        img_urls['pl'] = img_urls['arts'][0]
                        logger.debug(f"PL for videoa set from first art: {img_urls['pl']}")
                    else:
                        logger.warning("Arts found for videoa, but list is empty after processing.")
                else:
                    logger.warning("Arts block not found for videoa using known XPaths.")
                    # Fallback: 메인 플레이어 이미지를 pl로 시도
                    pl_xpath_fallback = '//div[@id="sample-video"]//img/@src'
                    pl_tags_fallback = tree.xpath(pl_xpath_fallback)
                    if pl_tags_fallback:
                        img_urls['pl'] = pl_tags_fallback[0]
                        if img_urls['pl'].startswith("//"): img_urls['pl'] = "https:" + img_urls['pl']
                        logger.debug(f"PL for videoa set from fallback sample-video: {img_urls['pl']}")
                    else:
                        logger.error("PL could not be found for videoa using any method.")

            elif content_type == 'dvd':

                img_urls = cls.__img_urls(tree)
                SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)

                entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)

                entity.fanart = []
                for href in img_urls["arts"][:max_arts]:
                    entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))

            else:
                logger.error(f"Unknown content type '{content_type}' in __img_urls")

        except Exception as e:
            logger.exception(f"Error extracting image URLs: {e}")

        logger.debug(f"Extracted img_urls: ps={bool(img_urls.get('ps'))} pl={bool(img_urls.get('pl'))} arts={len(img_urls.get('arts',[]))}")
        return img_urls


    @classmethod
    def __info( cls, code, do_trans=True, proxy_url=None, image_mode="0", max_arts=10, use_extras=True, ps_to_poster=False, crop_mode=None):
        logger.info(f"Getting detail info for {code}")
        cached_data = cls._ps_url_cache.pop(code, {})
        ps_url_from_cache = cached_data.get('ps')
        content_type = cached_data.get('type', 'unknown')
        if ps_url_from_cache: logger.debug(f"Using cached ps_url for {code}.")
        else: logger.warning(f"ps_url for {code} not found in cache.")
        logger.debug(f"Determined content type: {content_type}")

        if not cls._ensure_age_verified(proxy_url=proxy_url): raise Exception(f"Age verification failed for info ({code}).")

        # 타입에 따라 상세 페이지 URL 결정
        cid_part = code[2:]
        detail_url = None
        if content_type == 'videoa': detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
        elif content_type == 'dvd': detail_url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={cid_part}/"
        else:
            logger.warning(f"Unknown type '{content_type}'. Assuming 'videoa' path for {code}.")
            detail_url = cls.site_base_url + f"/digital/videoa/-/detail/=/cid={cid_part}/"
            content_type = 'videoa' # 임시 가정

        logger.info(f"Accessing DMM detail page ({content_type}): {detail_url}")
        info_headers = cls._get_request_headers(referer=cls.fanza_av_url)
        tree = None; received_html_content = None
        try:
            tree = SiteUtil.get_tree(detail_url, proxy_url=proxy_url, headers=info_headers)
            if tree is None: raise Exception("SiteUtil.get_tree returned None.")
            # --- 상세 페이지 Raw HTML 로깅 ---
            try:
                received_html_content = etree.tostring(tree, pretty_print=True, encoding='unicode', method='html')
                logger.debug(f">>>>>> Received Detail HTML for {code} Start >>>>>>")
                log_chunk_size = 1500
                for i in range(0, len(received_html_content), log_chunk_size): logger.debug(received_html_content[i:i+log_chunk_size])
                logger.debug(f"<<<<<< Received Detail HTML for {code} End <<<<<<")
                title_tags_check = tree.xpath('//title/text()')
                if title_tags_check and "年齢認証 - FANZA" in title_tags_check[0]: raise Exception("Age page received.")
            except Exception as e_log_html: logger.error(f"Error logging detail HTML: {e_log_html}")
        except Exception as e: logger.exception(f"Failed get/process detail tree: {e}"); raise

        entity = EntityMovie(cls.site_name, code); entity.country = ["일본"]; entity.mpaa = "청소년 관람불가"

        # --- 파싱 로직 분기 ---
        if content_type == 'videoa':
            logger.debug("Parsing as 'videoa' type...")
            # --- videoa 파싱 로직 (이전 로그 기반 XPath + 원본 로직 참고) ---
            try:
                # 제목/Tagline
                title_node = tree.xpath('//h1[@id="title"]')
                if title_node:
                    h1_text = title_node[0].text_content().strip()
                    prefix_tags = title_node[0].xpath('./span[@class="red"]/text()')
                    title_cleaned = h1_text.replace(prefix_tags[0].strip(), "").strip() if prefix_tags else h1_text
                    entity.tagline = SiteUtil.trans(title_cleaned, do_trans=do_trans)
                else: logger.warning("Tagline (h1#title) not found for videoa.")

                # 이미지 처리
                img_urls = cls.__img_urls(tree, content_type='videoa')
                img_urls['ps'] = ps_url_from_cache if ps_url_from_cache else img_urls.get('ps', "")
                if not img_urls['ps'] and img_urls.get('pl'): img_urls['ps'] = img_urls['pl']
                if not img_urls['ps']: logger.error("Crucial PS URL missing for videoa.")
                SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)
                entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
                entity.fanart = []
                resolved_arts = img_urls.get("arts", []); landscape_url = img_urls.get("landscape")
                for href in resolved_arts[:max_arts]:
                    if href != landscape_url: entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))

                # 정보 테이블 파싱 (videoa 용 - XPath 수정 필요)
                info_table_xpath = '//table[contains(@class, "mg-b20")]//tr' # 예전 구조 XPath, 수정 필요
                tags = tree.xpath(info_table_xpath)
                premiered_shouhin = None; premiered_haishin = None
                for tag in tags:
                    key_node = tag.xpath('./td[@class="nw"]/text()')
                    value_node = tag.xpath('./td[not(@class="nw")]')
                    if not key_node or not value_node: continue
                    key = key_node[0].strip().replace("：", "")
                    value_td = value_node[0]; value_text_all = value_td.text_content().strip()
                    if value_text_all == "----" or not value_text_all: continue
                    # ... (원본 테이블 파싱 로직 적용하되 videoa 구조에 맞게 수정) ...
                    if key == "配信開始日": premiered_haishin = value_text_all.replace("/", "-")
                    elif key == "商品発売日": premiered_shouhin = value_text_all.replace("/", "-")
                    elif key == "収録時間": m=re.search(r"(\d+)",value_text_all); entity.runtime = int(m.group(1)) if m else None
                    elif key == "出演者": # videoa 는 액터 정보가 다를 수 있음
                        actors = [a.strip() for a in value_td.xpath('.//a/text()') if a.strip()]
                        if actors: entity.actor = [EntityActor(name) for name in actors]
                        elif value_text_all != '----': entity.actor = [EntityActor(n.strip()) for n in value_text_all.split('/') if n.strip()] # / 구분자 사용 가능성
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

                final_premiered = premiered_shouhin or premiered_haishin
                if final_premiered: entity.premiered = final_premiered; entity.year = int(final_premiered[:4]) if final_premiered else None
                else: logger.warning("Premiered date not found for videoa."); entity.premiered = None; entity.year = None

                # 줄거리 파싱 (videoa 용 - XPath 수정 필요)
                plot_xpath = '//div[@class="mg-b20 lh4"]/text()' # 예전 구조 XPath
                plot_nodes = tree.xpath(plot_xpath)
                if plot_nodes:
                    plot_text = "\n".join([p.strip() for p in plot_nodes if p.strip()]).split("※")[0].strip()
                    if plot_text: entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
                else: logger.warning(f"Plot not found using XPath: {plot_xpath}")

                # 예고편 처리 (AJAX 방식)
                entity.extras = []
                if use_extras:
                    logger.debug(f"Attempting to extract trailer for {code} via AJAX/Iframe")
                    try:
                        trailer_url = None
                        trailer_title = entity.title if entity.title and entity.title != code[2:].upper() else code

                        # 1. AJAX 요청 URL 생성 및 실행
                        ajax_url = py_urllib_parse.urljoin(cls.site_base_url, f"/digital/videoa/-/detail/ajax-movie/=/cid={code[2:]}/")
                        logger.debug(f"Trailer Step 1: Requesting AJAX URL: {ajax_url}")
                        ajax_headers = cls._get_request_headers(referer=detail_url)
                        ajax_headers['Accept'] = 'text/html, */*; q=0.01'
                        ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                        ajax_response = SiteUtil.get_response(ajax_url, proxy_url=proxy_url, headers=ajax_headers)

                        if not (ajax_response and ajax_response.status_code == 200):
                            logger.warning(f"Trailer Step 1 Failed: AJAX request failed (Status: {ajax_response.status_code if ajax_response else 'No Resp'})")
                            raise Exception("AJAX request failed")

                        ajax_html_text = ajax_response.text
                        logger.debug("Trailer Step 1 Success: AJAX response received.")

                        # 2. Iframe URL 추출
                        iframe_tree = html.fromstring(ajax_html_text)
                        iframe_srcs = iframe_tree.xpath("//iframe/@src")
                        if not iframe_srcs:
                            logger.warning("Trailer Step 2 Failed: No iframe found in AJAX response.")
                            raise Exception("Iframe not found")

                        iframe_url = py_urllib_parse.urljoin(ajax_url, iframe_srcs[0])
                        logger.debug(f"Trailer Step 2 Success: Found iframe URL: {iframe_url}")

                        # 3. 플레이어 페이지 요청
                        logger.debug(f"Trailer Step 3: Requesting Player Page URL: {iframe_url}")
                        player_headers = cls._get_request_headers(referer=ajax_url)
                        player_response_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=player_headers)

                        if not player_response_text:
                            logger.warning(f"Trailer Step 3 Failed: Empty content from Player Page: {iframe_url}")
                            raise Exception("Failed to get player page content")

                        logger.debug(f"Trailer Step 3 Success: Player page content received.")

                        # 4. 플레이어 페이지 HTML 분석 및 비디오 URL 추출 (const args JSON)
                        logger.debug("Trailer Step 4: Parsing 'const args' JSON...")
                        pos = player_response_text.find("const args = {")
                        if pos != -1:
                            json_start = player_response_text.find("{", pos)
                            json_end = player_response_text.find("};", json_start) # 세미콜론 포함
                            if json_start != -1 and json_end != -1:
                                data_str = player_response_text[json_start : json_end+1]
                                try:
                                    data = json.loads(data_str)
                                    bitrates = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                    if bitrates:
                                        trailer_src = bitrates[0].get("src")
                                        if trailer_src:
                                            trailer_url = "https:" + trailer_src if trailer_src.startswith("//") else trailer_src # https: 추가
                                            if data.get("title"): trailer_title = data.get("title").strip()
                                            logger.info(f"Trailer URL found via JSON: {trailer_url}")
                                        else: logger.warning("Highest bitrate found, but 'src' key is missing.")
                                    else: logger.warning("'bitrates' array found in JSON, but it's empty.")
                                except json.JSONDecodeError as je: logger.warning(f"Failed to decode 'const args' JSON: {je}")
                            else: logger.warning("Could not find end '};' for 'const args' JSON.")
                        else:
                            logger.warning("'const args' pattern not found in player page HTML.")
                            # 여기서 Regex 등 다른 방법 시도 가능

                        # 5. 최종 EntityExtra 추가
                        if trailer_url:
                            entity.extras.append(EntityExtra("trailer", SiteUtil.trans(trailer_title, do_trans=do_trans), "mp4", trailer_url))
                            logger.info(f"Trailer added successfully for {code}")
                        else:
                            logger.error(f"Failed to extract trailer URL for {code}.")

                    except Exception as extra_e:
                        logger.exception(f"Error processing trailer for {code}: {extra_e}")

            except Exception as e_parse: logger.exception(f"Error parsing videoa info: {e_parse}")


        elif content_type == 'dvd':
            logger.debug("Parsing as 'dvd' type...")
            # --- dvd 파싱 로직 ---
            try:

                img_urls = cls.__img_urls(tree)
                SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)

                entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)

                entity.fanart = []
                for href in img_urls["arts"][:max_arts]:
                    entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))


                alt = tree.xpath('//div[@id="sample-video"]//img/@alt')[0].strip()
                entity.tagline = SiteUtil.trans(alt, do_trans=do_trans).replace("[배달 전용]", "").replace("[특가]", "").strip()

                basetag = '//*[@id="mu"]/div/table//tr/td[1]'

                tags = tree.xpath(f"{basetag}/table//tr")
                tmp_premiered = None
                for tag in tags:
                    td_tag = tag.xpath(".//td")
                    if len(td_tag) != 2:
                        continue
                    key = td_tag[0].text_content().strip()
                    value = td_tag[1].text_content().strip()
                    if value == "----":
                        continue
                    if key == "商品発売日：":
                        entity.premiered = value.replace("/", "-")
                        entity.year = int(value[:4])
                    elif key == "配信開始日：":
                        tmp_premiered = value.replace("/", "-")
                    elif key == "収録時間：":
                        entity.runtime = int(value.replace("分", ""))
                    elif key == "出演者：":
                        entity.actor = []
                        a_tags = tag.xpath(".//a")
                        for a_tag in a_tags:
                            tmp = a_tag.text_content().strip()
                            if tmp == "▼すべて表示する":
                                break
                            entity.actor.append(EntityActor(tmp))
                        # for v in value.split(' '):
                        #    entity.actor.append(EntityActor(v.strip()))
                    elif key == "監督：":
                        entity.director = value
                    elif key == "シリーズ：":
                        if entity.tag is None:
                            entity.tag = []
                        entity.tag.append(SiteUtil.trans(value, do_trans=do_trans))
                    elif key == "レーベル：":
                        entity.studio = value
                        if do_trans:
                            if value in SiteUtil.av_studio:
                                entity.studio = SiteUtil.av_studio[value]
                            else:
                                entity.studio = SiteUtil.change_html(SiteUtil.trans(value))
                    elif key == "ジャンル：":
                        a_tags = td_tag[1].xpath(".//a")
                        entity.genre = []
                        for tag in a_tags:
                            tmp = tag.text_content().strip()
                            if "％OFF" in tmp:
                                continue
                            if tmp in SiteUtil.av_genre:
                                entity.genre.append(SiteUtil.av_genre[tmp])
                            elif tmp in SiteUtil.av_genre_ignore_ja:
                                continue
                            else:
                                genre_tmp = SiteUtil.trans(tmp, do_trans=do_trans).replace(" ", "")
                                if genre_tmp not in SiteUtil.av_genre_ignore_ko:
                                    entity.genre.append(genre_tmp)
                    elif key == "品番：":
                        # 24id
                        match = cls.PTN_ID.search(value)
                        id_before = None
                        if match:
                            id_before = match.group(0)
                            value = value.lower().replace(id_before, "zzid")

                        match = cls.PTN_SEARCH_REAL_NO.match(value)
                        if match:
                            label = match.group("real").upper()
                            if id_before is not None:
                                label = label.replace("ZZID", id_before.upper())

                            value = label + "-" + str(int(match.group("no"))).zfill(3)
                            if entity.tag is None:
                                entity.tag = []
                            entity.tag.append(label)
                        entity.title = entity.originaltitle = entity.sorttitle = value

                if entity.premiered is None and tmp_premiered is not None:
                    entity.premiered = tmp_premiered
                    entity.year = int(tmp_premiered[:4])

                try:
                    tag = tree.xpath(f"{basetag}/table//tr[13]/td[2]/img")
                    if tag:
                        match = cls.PTN_RATING.search(tag[0].attrib["src"])
                        if match:
                            tmp = match.group("rating").replace("_", ".")
                            entity.ratings = [EntityRatings(float(tmp), max=5, name="dmm", image_url=tag[0].attrib["src"])]
                except Exception:
                    logger.exception("평점 정보 처리 중 예외:")

                tmp = tree.xpath(f"{basetag}/div[4]/text()")[0]
                tmp = tmp.split("※")[0].strip()
                entity.plot = SiteUtil.trans(tmp, do_trans=do_trans)

                try:
                    tmp = tree.xpath('//div[@class="d-review__points"]/p/strong')
                    if len(tmp) == 2 and entity.ratings:
                        point = float(tmp[0].text_content().replace("点", "").strip())
                        votes = int(tmp[1].text_content().strip())
                        entity.ratings[0].value = point
                        entity.ratings[0].votes = votes
                except Exception:
                    logger.exception("평점 정보 업데이트 중 예외:")

                entity.extras = []
                if use_extras:
                    try:
                        for tmp in tree.xpath('//*[@id="detail-sample-movie"]/div/a/@onclick'):
                            url = cls.site_base_url + tmp.split("'")[1]
                            url = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.dmm_headers).xpath("//iframe/@src")[0]
                            text = SiteUtil.get_text(url, proxy_url=proxy_url, headers=cls.dmm_headers)
                            pos = text.find("const args = {")
                            data = json.loads(text[text.find("{", pos) : text.find(";", pos)])
                            # logger.debug(json.dumps(data, indent=4))
                            data["bitrates"] = sorted(data["bitrates"], key=lambda k: k["bitrate"], reverse=True)
                            entity.extras = [
                                EntityExtra(
                                    "trailer",
                                    SiteUtil.trans(data["title"], do_trans=do_trans),
                                    "mp4",
                                    "https:" + data["bitrates"][0]["src"],
                                )
                            ]
                    except Exception:
                        logger.exception("미리보기 처리 중 예외:")

            except Exception as e_parse: logger.exception(f"Error parsing dvd info: {e_parse}")

        else: # 타입 불명
            logger.error(f"Cannot parse info: Unknown content type '{content_type}' for {code}")
            return None

        # --- 공통 후처리: 최종 제목 설정 ---
        match_real_no = cls.PTN_SEARCH_REAL_NO.search(code[2:])
        final_ui_code = code[2:].upper()
        if match_real_no:
            real = match_real_no.group("real").upper(); no = match_real_no.group("no")
            try: final_ui_code = f"{real}-{str(int(no)).zfill(3)}"
            except ValueError: final_ui_code = f"{real}-{no}"
        else:
            tmp = code[2:].upper();
            if tmp.startswith("H_"): tmp = tmp[2:]
            m = re.match(r"([a-zA-Z]+)(\d+.*)", tmp)
            if m:
                real = m.group(1); rest = m.group(2); num_m = re.match(r"(\d+)", rest)
                final_ui_code = f"{real}-{str(int(num_m.group(1))).zfill(3)}" if num_m else f"{real}-{rest}"
            else: final_ui_code = tmp
        # 파싱된 제목이 있으면 유지, 없으면 UI 코드로 설정
        if not entity.title or entity.title == "Not Found": entity.title = final_ui_code
        entity.originaltitle = entity.sorttitle = final_ui_code # 정렬/원본 제목은 UI 코드
        if not entity.tagline: entity.tagline = entity.title # 태그라인 fallback

        logger.debug(f"Final Parsed Entity: Title='{entity.title}', Tagline='{entity.tagline}', Thumb='{entity.thumb}', Actors={len(entity.actor) if entity.actor else 0}")
        return entity

    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            if entity: ret["ret"] = "success"; ret["data"] = entity.as_dict()
            else: ret["ret"] = "error"; ret["data"] = f"Failed to get info for {code}"
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"; ret["data"] = str(exception)
        return ret
