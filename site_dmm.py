# -*- coding: utf-8 -*-
import json
import re
import requests # SiteUtil 내부 의존성
import urllib.parse as py_urllib_parse

# lib_metadata 패키지 내 다른 모듈 import
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

class SiteDmm:
    site_name = "dmm"
    site_base_url = "https://www.dmm.co.jp"
    module_char = "C"
    site_char = "D"

    # --- DMM 전용 기본 헤더 ---
    dmm_base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    # --- 정규 표현식 ---
    PTN_SEARCH_CID = re.compile(r"\/cid=(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^(h_)?\d*(?P<real>[a-zA-Z]+)(?P<no>\d+)([a-zA-Z]+)?$")
    PTN_ID = re.compile(r"\d{2}id", re.I)
    PTN_RATING = re.compile(r"(?P<rating>[\d|_]+)\.gif") # 예: 45.gif -> 4.5, 40.gif -> 4.0

    # --- 상태 관리 변수 ---
    age_verified = False
    last_proxy_used = None
    _ps_url_cache = {} # code: ps_url 딕셔너리

    @classmethod
    def _get_request_headers(cls, referer=None):
        """요청에 사용할 헤더를 생성합니다."""
        headers = cls.dmm_base_headers.copy()
        if referer:
            headers['Referer'] = referer
        return headers

    @classmethod
    def _ensure_age_verified(cls, proxy_url=None):
        """SiteUtil.session에 DMM 연령 확인 쿠키가 있는지 확인하고, 없으면 설정합니다."""
        if not cls.age_verified or cls.last_proxy_used != proxy_url:
            logger.debug("Checking/Performing DMM age verification...")
            cls.last_proxy_used = proxy_url

            session_cookies = SiteUtil.session.cookies
            if 'age_check_done' in session_cookies and session_cookies.get('age_check_done') == '1':
                logger.debug("Age verification cookie already present in SiteUtil.session.")
                cls.age_verified = True
                return True

            logger.debug("Attempting DMM age verification process by directly sending confirmation GET.")
            try:
                target_rurl = f"{cls.site_base_url}/digital/videoa/-/list/" # rurl 값은 크게 중요하지 않을 수 있음
                confirm_path = f"/age_check/=/declared=yes/?rurl={py_urllib_parse.quote(target_rurl)}"
                age_check_confirm_url = py_urllib_parse.urljoin(cls.site_base_url, confirm_path)
                logger.debug(f"Constructed age confirmation URL: {age_check_confirm_url}")

                confirm_headers = cls._get_request_headers(referer=cls.site_base_url + "/")

                confirm_response = SiteUtil.get_response(
                    age_check_confirm_url,
                    method='GET',
                    proxy_url=proxy_url,
                    headers=confirm_headers,
                    allow_redirects=False
                )
                logger.debug(f"Confirmation GET response status code: {confirm_response.status_code}")
                logger.debug(f"Confirmation GET response headers: {confirm_response.headers}")
                logger.debug(f"Cookies *after* confirmation GET in SiteUtil.session: {SiteUtil.session.cookies.items()}")

                if confirm_response.status_code == 302 and 'Location' in confirm_response.headers:
                    set_cookie_header = confirm_response.headers.get('Set-Cookie', '')
                    if 'age_check_done=1' in set_cookie_header:
                        logger.debug("Age confirmation successful. 'age_check_done=1' found in Set-Cookie header.")
                        final_cookies = SiteUtil.session.cookies # 업데이트된 쿠키 확인
                        if 'age_check_done' in final_cookies and final_cookies.get('age_check_done') == '1':
                            logger.debug("age_check_done=1 cookie confirmed in SiteUtil.session.")
                            cls.age_verified = True
                            return True
                        else:
                            logger.warning("Set-Cookie header received, but age_check_done cookie not updated correctly in SiteUtil.session.")
                            cls.age_verified = False; return False
                    else:
                        logger.warning("Age confirmation redirected, but 'age_check_done=1' not found in Set-Cookie header.")
                        cls.age_verified = False; return False
                else:
                    logger.warning(f"Age confirmation GET request did not return expected 302 redirect. Status: {confirm_response.status_code}")
                    cls.age_verified = False; return False
            except Exception as e:
                logger.exception(f"Failed during DMM age verification process: {e}")
                cls.age_verified = False; return False
        else:
            return True # Already verified

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        if not cls._ensure_age_verified(proxy_url=proxy_url):
            return [] # 빈 리스트 반환

        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd": keyword = keyword[:-3]
        keyword = keyword.replace("-", " ")
        keyword_tmps = keyword.split(" ")
        if len(keyword_tmps) == 2:
            dmm_keyword = keyword_tmps[0] + keyword_tmps[1].zfill(5)
        else:
            dmm_keyword = keyword
        logger.debug("keyword [%s] -> [%s]", keyword, dmm_keyword)

        url = f"{cls.site_base_url}/mono/-/search/=/searchstr={dmm_keyword}/"
        logger.debug(f"Using search URL: {url}")

        search_headers = cls._get_request_headers()
        try:
            tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=search_headers)
        except Exception as e:
            logger.exception(f"Failed to get tree for search URL: {url}")
            return []
        if tree is None:
            logger.warning(f"Failed to get tree for URL: {url}")
            return []

        lists = tree.xpath('//ul[@id="list"]/li')
        logger.debug("dmm search len lists2 :%s", len(lists))

        ret = [] # 최종 반환될 딕셔너리 리스트
        score = 60
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)

                link_tag_xpath = './/p[@class="tmb"]/a'
                link_tags = node.xpath(link_tag_xpath)
                if not link_tags: continue
                link_tag = link_tags[0]
                href = link_tag.attrib["href"].lower()

                match = cls.PTN_SEARCH_CID.search(href)
                if not match:
                    logger.warning(f"Could not extract CID from href: {href}")
                    continue
                item.code = cls.module_char + cls.site_char + match.group("code")

                # 중복 제거 (딕셔너리 리스트에서 code 키 확인)
                already_exist = any(exist_item.get("code") == item.code for exist_item in ret)
                if already_exist: continue

                img_tag_xpath = './/p[@class="tmb"]/a/span[@class="img"]/img'
                img_tags = node.xpath(img_tag_xpath)
                original_ps_url = None
                if img_tags:
                    img_tag = img_tags[0]
                    item.title = item.title_ko = img_tag.attrib.get("alt", "").strip()
                    original_ps_url = img_tag.attrib.get("src")
                    if original_ps_url and not original_ps_url.startswith("http"):
                        original_ps_url = "https:" + original_ps_url

                    if manual:
                        _image_mode = "1" if image_mode != "0" else image_mode
                        item.image_url = SiteUtil.process_image_mode(_image_mode, original_ps_url, proxy_url=proxy_url)
                    else:
                        item.image_url = original_ps_url
                else:
                    item.title = item.title_ko = "제목 정보 없음"
                    item.image_url = None

                if item.code and original_ps_url:
                    cls._ps_url_cache[item.code] = original_ps_url
                    logger.debug(f"Stored ps_url for {item.code} in cache.")

                # 번역 처리 (manual 아닐 때만)
                if not manual:
                    if do_trans and item.title:
                        item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)
                    else:
                        item.title_ko = item.title
                else:
                    item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title


                match_real_no = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                if match_real_no:
                    item.ui_code = match_real_no.group("real") + match_real_no.group("no")
                else:
                    item.ui_code = item.code[2:]

                # 점수 계산
                if len(keyword_tmps) == 2:
                    if item.ui_code == dmm_keyword: item.score = 100
                    elif item.ui_code.replace("0", "") == dmm_keyword.replace("0", ""): item.score = 100
                    elif dmm_keyword in item.ui_code: item.score = score; score -= 5
                    elif keyword_tmps[0] in item.code and keyword_tmps[1] in item.code: item.score = score; score -= 5
                    elif keyword_tmps[0] in item.code or keyword_tmps[1] in item.code: item.score = 60
                    else: item.score = 20
                else:
                    if item.ui_code == dmm_keyword: item.score = 100
                    elif dmm_keyword in item.ui_code: item.score = score; score -= 5
                    else: item.score = 20

                # UI 코드 포맷팅
                if match_real_no:
                    item.ui_code = match_real_no.group("real").upper() + "-" + str(int(match_real_no.group("no"))).zfill(3)
                else:
                    if "0000" in item.ui_code: item.ui_code = item.ui_code.replace("0000", "-00").upper()
                    else: item.ui_code = item.ui_code.replace("00", "-").upper()
                    if item.ui_code.endswith("-"): item.ui_code = item.ui_code[:-1] + "00"

                logger.debug(f"Item found - Score: {item.score}, Code: {item.code}, UI Code: {item.ui_code}, Title: {item.title}")
                ret.append(item.as_dict()) # 딕셔너리로 변환하여 추가

            except Exception as e:
                logger.exception(f"Error processing individual search result item: {e}")

        if not ret and len(keyword_tmps) == 2 and len(keyword_tmps[1]) == 5:
            new_title = keyword_tmps[0] + keyword_tmps[1].zfill(6)
            logger.debug(f"No results found for {dmm_keyword}, retrying with {new_title}")
            return cls.__search(new_title, do_trans=do_trans, proxy_url=proxy_url, image_mode=image_mode, manual=manual)

        return sorted(ret, key=lambda k: k["score"], reverse=True) # 딕셔너리 리스트 정렬

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            data_list = cls.__search(keyword, **kwargs)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data_list else "no_match"
            ret["data"] = data_list
        return ret

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
    ):
        # 캐시에서 ps_url 조회 및 제거
        ps_url = cls._ps_url_cache.pop(code, None)
        if ps_url:
            logger.debug(f"Retrieved ps_url for {code} from cache.")
        else:
            logger.warning(f"ps_url for {code} not found in cache. Image comparison might be inaccurate.")

        # __info 호출 시에도 연령 확인 (혹시 모를 세션 만료 대비)
        if not cls._ensure_age_verified(proxy_url=proxy_url):
            raise Exception("DMM age verification failed for info.")

        url = cls.site_base_url + f"/mono/dvd/-/detail/=/cid={code[2:]}/"
        logger.debug(f"Using info URL: {url}")

        info_headers = cls._get_request_headers(referer=cls.site_base_url + "/mono/dvd/") # 적절한 Referer 설정
        try:
            tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=info_headers)
        except Exception as e:
            logger.exception(f"Failed to get tree for info URL: {url}")
            raise Exception(f"Failed to get tree for info URL: {url}")
        if tree is None:
            logger.warning(f"Failed to get tree for URL: {url}")
            raise Exception(f"Failed to get tree for URL: {url}")

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]
        entity.mpaa = "청소년 관람불가"

        # --- 이미지 처리 시작 ---
        img_urls = {}
        # pl 추출
        pl_xpath = '//div[@id="fn-sampleImage-imagebox"]/img/@src'
        pl_tags = tree.xpath(pl_xpath)
        img_urls['pl'] = ("https:" + pl_tags[0]) if pl_tags and not pl_tags[0].startswith("http") else (pl_tags[0] if pl_tags else "")
        if not img_urls['pl']: logger.warning("고화질 메인 이미지(pl) URL을 얻을 수 없음.")

        # ps 설정 (캐시 값 또는 fallback)
        img_urls['ps'] = ps_url if ps_url else ""
        if not img_urls['ps']:
            logger.warning("저화질 썸네일 이미지(ps) URL이 없거나 유효하지 않음. Fallback 시도.")
            if img_urls.get('pl'): img_urls['ps'] = img_urls['pl']
            else: logger.error("Both pl and ps URLs are missing.")

        # arts 추출
        arts_xpath = '//li[contains(@class, "fn-sampleImage__zoom") and not(@data-slick-index="0")]//img'
        arts_tags = tree.xpath(arts_xpath)
        img_urls['arts'] = []
        for tag in arts_tags:
            src = tag.attrib.get("src") or tag.attrib.get("data-lazy")
            if src:
                if not src.startswith("http"): src = "https:" + src
                img_urls['arts'].append(src)
        logger.debug(f"Found {len(img_urls['arts'])} arts images.")

        # SiteUtil 이미지 처리 호출
        try:
            SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)
            entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)
            entity.fanart = []
            resolved_arts = img_urls.get("arts", [])
            for href in resolved_arts[:max_arts]:
                if href != img_urls.get("landscape"): # landscape는 팬아트에서 제외 (선택적)
                    entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))
        except Exception as img_proc_e:
            logger.exception(f"Error during SiteUtil image processing: {img_proc_e}")
            # 이미지 처리 실패 시에도 나머지 정보는 파싱하도록 진행
            entity.thumb = None
            entity.fanart = []
        # --- 이미지 처리 끝 ---


        # --- Tagline / Title 처리 ---
        title_xpath = '//h1[@id="title"]'
        title_tags = tree.xpath(title_xpath)
        title_text = None
        if title_tags:
            h1_full_text = title_tags[0].text_content().strip()
            span_text_nodes = title_tags[0].xpath('./span[contains(@class, "txt_before-sale")]/text()')
            span_text = "".join(span_text_nodes).strip()
            title_text = h1_full_text.replace(span_text, "").strip() if span_text else h1_full_text
            if title_text:
                entity.tagline = SiteUtil.trans(title_text, do_trans=do_trans).replace("[배달 전용]", "").replace("[특가]", "").strip()
                logger.debug(f"Tagline set from h1 title: {entity.tagline}")
            else:
                logger.warning("Could not extract text from h1#title.")
                entity.tagline = None
        else:
            logger.warning("h1#title tag not found.")
            entity.tagline = None

        # --- 정보 테이블 파싱 ---
        info_table_xpath = '//div[@class="wrapper-product"]//table//tr'
        tags = tree.xpath(info_table_xpath)
        premiered_shouhin = None; premiered_hatsubai = None; premiered_haishin = None

        for tag in tags:
            td_tags = tag.xpath(".//td")
            # 평점 행 처리
            if td_tags and len(td_tags)==2 and "平均評価：" in td_tags[0].text_content():
                rating_img_xpath = './/img/@src'
                rating_img_tags = td_tags[1].xpath(rating_img_xpath)
                if rating_img_tags:
                    match_rating = cls.PTN_RATING.search(rating_img_tags[0])
                    if match_rating:
                        rating_value_str = match_rating.group("rating").replace("_", ".")
                        try:
                            # --- 평점 값 10으로 나누기 ---
                            rating_value = float(rating_value_str) / 10.0
                            rating_img_url = "https:" + rating_img_tags[0] if not rating_img_tags[0].startswith("http") else rating_img_tags[0]
                            # 평점 범위 확인 (선택적)
                            if 0 <= rating_value <= 5:
                                entity.ratings = [EntityRatings(rating_value, max=5, name="dmm", image_url=rating_img_url)]
                                logger.debug(f"Rating found from table (raw: {rating_value_str}, adjusted: {rating_value})")
                            else:
                                logger.warning(f"Parsed rating value {rating_value} is out of range (0-5).")
                        except ValueError:
                            logger.warning(f"Could not convert rating value to float: {rating_value_str}")
                    else:
                        logger.warning(f"Could not parse rating from image src: {rating_img_tags[0]}")
                continue # 다음 행으로

            # 일반 정보 행 처리
            if len(td_tags) != 2: continue
            key = td_tags[0].text_content().strip()
            value_node = td_tags[1]
            value_text_all = value_node.text_content().strip()
            if value_text_all == "----" or not value_text_all: continue

            # 날짜 임시 저장
            if key == "商品発売日：": premiered_shouhin = value_text_all.replace("/", "-"); logger.debug(f"Found 商品発売日: {premiered_shouhin}")
            elif key == "発売日：": premiered_hatsubai = value_text_all.replace("/", "-"); logger.debug(f"Found 発売日: {premiered_hatsubai}")
            elif key == "配信開始日：": premiered_haishin = value_text_all.replace("/", "-"); logger.debug(f"Found 配信開始日: {premiered_haishin}")
            # 다른 정보 처리
            elif key == "収録時間：":
                match_runtime = re.search(r"(\d+)", value_text_all)
                if match_runtime: entity.runtime = int(match_runtime.group(1)); logger.debug(f"Runtime: {entity.runtime}")
            elif key == "出演者：":
                entity.actor = []
                a_tags = value_node.xpath(".//a")
                for a_tag in a_tags:
                    actor_name = a_tag.text_content().strip()
                    if actor_name and actor_name != "▼すべて表示する": entity.actor.append(EntityActor(actor_name))
                logger.debug(f"Actors: {[a.originalname for a in entity.actor]}")
            elif key == "監督：":
                a_tags = value_node.xpath(".//a"); entity.director = a_tags[0].text_content().strip() if a_tags else value_text_all; logger.debug(f"Director: {entity.director}")
            elif key == "シリーズ：":
                if entity.tag is None: entity.tag = []
                a_tags = value_node.xpath(".//a"); series_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                if series_name: entity.tag.append(SiteUtil.trans(series_name, do_trans=do_trans)); logger.debug(f"Series tags: {entity.tag}")
            elif key == "メーカー：":
                if entity.studio is None:
                    a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                    entity.studio = SiteUtil.trans(studio_name, do_trans=do_trans); logger.debug(f"Studio (from Maker): {entity.studio}")
            elif key == "レーベル：":
                a_tags = value_node.xpath(".//a"); studio_name = a_tags[0].text_content().strip() if a_tags else value_text_all
                if do_trans: entity.studio = SiteUtil.av_studio.get(studio_name, SiteUtil.trans(studio_name))
                else: entity.studio = studio_name
                logger.debug(f"Studio (from Label): {entity.studio}")
            elif key == "ジャンル：":
                entity.genre = []
                a_tags = value_node.xpath(".//a")
                for tag_a in a_tags:
                    genre_ja = tag_a.text_content().strip()
                    if "％OFF" in genre_ja or not genre_ja or genre_ja in SiteUtil.av_genre_ignore_ja: continue
                    if genre_ja in SiteUtil.av_genre: entity.genre.append(SiteUtil.av_genre[genre_ja])
                    else:
                        genre_ko = SiteUtil.trans(genre_ja, do_trans=do_trans).replace(" ", "")
                        if genre_ko not in SiteUtil.av_genre_ignore_ko: entity.genre.append(genre_ko)
                logger.debug(f"Genres: {entity.genre}")
            elif key == "品番：":
                value = value_text_all
                match_id = cls.PTN_ID.search(value); id_before = None
                if match_id: id_before = match_id.group(0); value = value.lower().replace(id_before, "zzid")
                match_real = cls.PTN_SEARCH_REAL_NO.match(value); formatted_title = value_text_all.upper()
                if match_real:
                    label = match_real.group("real").upper()
                    if id_before is not None: label = label.replace("ZZID", id_before.upper())
                    formatted_title = label + "-" + str(int(match_real.group("no"))).zfill(3)
                    if entity.tag is None: entity.tag = []
                    entity.tag.append(label)
                entity.title = entity.originaltitle = entity.sorttitle = formatted_title
                logger.debug(f"Title (from 品番): {entity.title}")
                if entity.tagline is None: entity.tagline = entity.title # Tagline fallback

        # 최종 날짜 설정
        final_premiered = None
        if premiered_shouhin: final_premiered = premiered_shouhin; logger.debug("Using 商品発売日 for premiered date.")
        elif premiered_hatsubai: final_premiered = premiered_hatsubai; logger.debug("Using 発売日 for premiered date.")
        elif premiered_haishin: final_premiered = premiered_haishin; logger.debug("Using 配信開始日 for premiered date.")
        else: logger.warning("No premiered date found.")
        if final_premiered:
            entity.premiered = final_premiered
            try: entity.year = int(final_premiered[:4])
            except ValueError: logger.warning(f"Could not parse year: {final_premiered}"); entity.year = None
        else: entity.premiered = None; entity.year = None

        # --- 줄거리 파싱 ---
        plot_xpath = '//div[@class="mg-b20 lh4"]/p[@class="mg-b20"]/text()'
        plot_tags = tree.xpath(plot_xpath)
        if plot_tags:
            plot_text = "\n".join([p.strip() for p in plot_tags if p.strip()]).split("※")[0].strip()
            entity.plot = SiteUtil.trans(plot_text, do_trans=do_trans)
            logger.debug(f"Plot found: {entity.plot[:50]}...")
        else: logger.warning("Plot not found.")

        # --- 리뷰 섹션 평점 업데이트 ---
        review_section_xpath = '//div[@id="review_anchor"]'
        review_sections = tree.xpath(review_section_xpath)
        if review_sections:
            review_section = review_sections[0]
            try:
                avg_rating_xpath = './/div[@class="dcd-review__points"]/p[@class="dcd-review__average"]/strong/text()'
                avg_rating_tags = review_section.xpath(avg_rating_xpath)
                if avg_rating_tags:
                    avg_rating_str = avg_rating_tags[0].strip(); avg_rating_value = float(avg_rating_str)
                    if entity.ratings: entity.ratings[0].value = avg_rating_value
                    else: entity.ratings = [EntityRatings(avg_rating_value, max=5, name="dmm")]
                    logger.debug(f"Updated rating value: {avg_rating_value}")

                votes_xpath = './/div[@class="dcd-review__points"]/p[@class="dcd-review__evaluates"]/strong/text()'
                votes_tags = review_section.xpath(votes_xpath)
                if votes_tags:
                    votes_str = votes_tags[0].strip(); match_votes = re.search(r"(\d+)", votes_str)
                    if match_votes:
                        votes_value = int(match_votes.group(1))
                        if entity.ratings: entity.ratings[0].votes = votes_value; logger.debug(f"Updated rating votes: {votes_value}")
            except Exception as rating_update_e: logger.exception(f"Error updating rating details: {rating_update_e}")
        else: logger.warning("Review section not found.")

        # --- 예고편 처리 ---
        entity.extras = []
        if use_extras:
            try:
                trailer_url = None
                trailer_title_from_data = None # DMM 제공 제목 저장 변수

                # --- 우선 AJAX 요청 및 iframe 파싱 시도 ---
                ajax_url_xpath = '//a[@id="sample-video1"]/@data-video-url'
                ajax_url_tags = tree.xpath(ajax_url_xpath)
                if ajax_url_tags:
                    ajax_relative_url = ajax_url_tags[0]
                    ajax_full_url = py_urllib_parse.urljoin(url, ajax_relative_url)
                    logger.debug(f"Attempting trailer AJAX request: {ajax_full_url}")
                    try:
                        ajax_headers = cls._get_request_headers(referer=url); ajax_headers['X-Requested-With'] = 'XMLHttpRequest'
                        ajax_response_text = SiteUtil.get_text(ajax_full_url, proxy_url=proxy_url, headers=ajax_headers)
                        try: from lxml import html except ImportError: logger.error("lxml required"); raise
                        ajax_tree = html.fromstring(ajax_response_text)
                        iframe_srcs = ajax_tree.xpath("//iframe/@src")
                        if iframe_srcs:
                            iframe_url = py_urllib_parse.urljoin(ajax_full_url, iframe_srcs[0])
                            iframe_headers = cls._get_request_headers(referer=ajax_full_url)
                            iframe_text = SiteUtil.get_text(iframe_url, proxy_url=proxy_url, headers=iframe_headers)
                            pos = iframe_text.find("const args = {")
                            if pos != -1:
                                json_start = iframe_text.find("{", pos); json_end = iframe_text.find("};", json_start)
                                if json_start != -1 and json_end != -1:
                                    data_str = iframe_text[json_start : json_end+1]
                                    try:
                                        data = json.loads(data_str)
                                        data["bitrates"] = sorted(data.get("bitrates",[]), key=lambda k: k.get("bitrate", 0), reverse=True)
                                        if data.get("bitrates"):
                                            trailer_src = data["bitrates"][0].get("src")
                                            if trailer_src:
                                                # 트레일러 URL 저장
                                                trailer_url = "https:" + trailer_src if not trailer_src.startswith("http") else trailer_src
                                                # DMM 제공 제목 저장
                                                trailer_title_from_data = data.get("title")
                                                logger.debug(f"Trailer URL found from AJAX iframe: {trailer_url}")
                                                if trailer_title_from_data:
                                                    logger.debug(f"Trailer title found from DMM data: {trailer_title_from_data}")
                                    except json.JSONDecodeError as je: logger.warning(f"Failed to decode JSON from iframe: {data_str} - Error: {je}")
                            else: logger.warning("Could not find 'const args = {' in iframe content.")
                        else: logger.warning("Could not find iframe src in AJAX response.")
                    except Exception as ajax_e: logger.exception(f"Error during trailer AJAX request: {ajax_e}")
                else:
                    logger.warning("data-video-url attribute not found for trailer AJAX.")

                # --- 만약 AJAX로 URL 못찾았으면 onclick 파싱 시도 ---
                if not trailer_url:
                    onclick_xpath = '//a[@id="sample-video1"]/@onclick'
                    onclick_tags = tree.xpath(onclick_xpath)
                    if onclick_tags:
                        onclick_text = onclick_tags[0]
                        match_json = re.search(r"gaEventVideoStart\('(\{.*?\})','(\{.*?\})'\)", onclick_text)
                        if match_json:
                            video_data_str = match_json.group(1)
                            try:
                                video_data = json.loads(video_data_str.replace('\\"', '"'))
                                if video_data.get("video_url"):
                                    # 트레일러 URL 저장
                                    trailer_url = video_data["video_url"]
                                    # onclick에는 제목 정보가 없으므로 trailer_title_from_data는 None 유지
                                    logger.debug(f"Trailer URL found from onclick (no title info): {trailer_url}")
                            except Exception as json_e: logger.warning(f"Failed to parse JSON from onclick: {json_e}")

                # --- 최종적으로 URL을 찾았으면 EntityExtra 추가 ---
                if trailer_url:
                    # 제목 결정: DMM 제공 제목 우선, 없으면 entity.title, 그것도 없으면 "Trailer"
                    if trailer_title_from_data and trailer_title_from_data.strip():
                        trailer_title_to_use = trailer_title_from_data.strip()
                    elif entity.title:
                        trailer_title_to_use = entity.title
                    else:
                        trailer_title_to_use = "Trailer" # 최후의 기본값

                    entity.extras.append(EntityExtra("trailer", SiteUtil.trans(trailer_title_to_use, do_trans=do_trans), "mp4", trailer_url))
                    logger.debug(f"Added trailer with title: '{trailer_title_to_use}' and URL: {trailer_url}")
                else:
                    logger.warning("No trailer URL found using either AJAX or onclick method.")

            except Exception as extra_e:
                logger.exception(f"미리보기 처리 중 예외: {extra_e}")

        return entity

    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
            ret["ret"] = "success"
            ret["data"] = entity.as_dict()
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        return ret
