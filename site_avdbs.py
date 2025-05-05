import time
import requests
from lxml import html
import json
import os
import sqlite3
import re
from urllib.parse import urljoin # urljoin 추가

# lib_metadata 공용 요소들
from .plugin import P
from .site_util import SiteUtil
try:
    from .discord import DiscordUtil
    DISCORD_UTIL_AVAILABLE = True
except ImportError:
    P.logger.error("DiscordUtil을 임포트할 수 없습니다. Discord URL 갱신 기능 비활성화.")
    DISCORD_UTIL_AVAILABLE = False
    class DiscordUtil:
        @staticmethod
        def isurlattachment(url): return False
        @staticmethod
        def isurlexpired(url): return False
        @staticmethod
        def renew_urls(data): return data
        @staticmethod
        def proxy_image_url(urls, **kwargs): return {}

logger = P.logger

class SiteAvdbs:
    site_char = "A"
    site_name = "avdbs"
    base_url = "https://www.avdbs.com"

    @staticmethod
    def __get_actor_info_from_web(originalname, **kwargs) -> dict:
        """Avdbs.com 웹사이트에서 배우 정보를 가져오는 내부 메소드 (Fallback용)"""
        logger.info(f"WEB Fallback: Avdbs.com 에서 '{originalname}' 정보 직접 검색 시작.")
        proxy_url = kwargs.get('proxy_url')
        image_mode = kwargs.get('image_mode', '0')
        use_image_transform = kwargs.get('use_image_transform', False)
        image_transform_source = kwargs.get('image_transform_source', '')
        image_transform_target = kwargs.get('image_transform_target', '')

        with requests.Session() as s:
            enhanced_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": SiteAvdbs.base_url + "/",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Sec-CH-UA": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Windows"',
                "DNT": "1",
                "Cache-Control": "max-age=0",
            }
            s.headers.update(enhanced_headers)
            if proxy_url: s.proxies.update({"http": proxy_url, "https": proxy_url})

            search_url = f"{SiteAvdbs.base_url}/w2017/page/search/search_actor.php"
            search_params = {'kwd': originalname}
            search_headers = enhanced_headers.copy()
            tree = None
            try:
                logger.debug(f"WEB: Requesting search page: {search_url} with params: {search_params}")
                response_search_page = s.get(search_url, params=search_params, headers=search_headers, timeout=20)
                response_search_page.raise_for_status()
                tree = html.fromstring(response_search_page.text)
                if tree is None: return None
            except requests.exceptions.RequestException as e_req: logger.error(f"WEB: Request failed for search page: {e_req}"); return None
            except Exception as e_parse: logger.error(f"WEB: Failed to parse search page HTML: {e_parse}"); return None

            try:
                actor_items = tree.xpath('//div[contains(@class, "search-actor-list")]/ul/li')
                if not actor_items: logger.debug("WEB: No actor items found."); return None

                names_to_check = SiteAvdbs._parse_name_variations(originalname)

                for idx, item_li in enumerate(actor_items):
                    try:
                        name_tags = item_li.xpath('.//p[starts-with(@class, "name")]')
                        if len(name_tags) < 3: continue
                        name_ja_raw = name_tags[0].text_content().strip()
                        name_en_raw = name_tags[1].text_content().strip()
                        name_ko_raw = name_tags[2].text_content().strip()
                        name_ja_clean = name_ja_raw.split('(')[0].strip()

                        if name_ja_clean in names_to_check:
                            logger.debug(f"WEB: Match found for '{originalname}' - JA:'{name_ja_clean}'")
                            img_tag = item_li.xpath('.//img/@src')
                            if not img_tag: continue
                            img_url_raw = img_tag[0].strip()
                            if not img_url_raw.startswith('http'): img_url_raw = urljoin(SiteAvdbs.base_url, img_url_raw)

                            final_thumb_url = img_url_raw
                            if use_image_transform and image_transform_source and final_thumb_url.startswith(image_transform_source):
                                final_thumb_url = final_thumb_url.replace(image_transform_source, image_transform_target, 1)
                                logger.debug(f"WEB: Image URL transformed: {final_thumb_url}")

                            processed_thumb = SiteUtil.process_image_mode(image_mode, final_thumb_url, proxy_url=proxy_url)

                            return {"name": name_ko_raw, "name2": name_en_raw, "site": "avdbs_web", "thumb": processed_thumb}
                    except Exception as e_item: logger.exception(f"WEB: Error processing item at index {idx}: {e_item}")

                logger.debug("WEB: No matching actor found in search results.")
                return None
            except Exception as e_parse_results: logger.exception(f"WEB: Error parsing search results: {e_parse_results}"); return None


    @staticmethod
    def _parse_and_match_other_names(other_names_str, originalname):
        """actor_onm 필드를 파싱하여 originalname과 정확히 일치하는지 확인"""
        if not other_names_str or not originalname: return False
        for name_part in other_names_str.split(','):
            name_part = name_part.strip()
            if not name_part: continue
            match_bracket = re.search(r'\(([^)]+)\)', name_part)
            if match_bracket:
                names_in_bracket_str = match_bracket.group(1).strip()
                japanese_names_in_bracket = [name.strip() for name in names_in_bracket_str.split('/') if name.strip()]
                for jp_name in japanese_names_in_bracket:
                    if jp_name == originalname: return True
            name_part_without_bracket = re.sub(r'\s*\(.*\)', '', name_part).strip()
            if name_part_without_bracket == originalname: return True
        return False

    @staticmethod
    def _parse_name_variations(originalname):
        """입력된 이름에서 검색할 이름 변형 목록을 생성합니다."""
        variations = {originalname}
        match = re.match(r'^(.*?)\s*[（\(]([^）\)]+)[）\)]\s*$', originalname)
        if match:
            before_paren = match.group(1).strip(); inside_paren = match.group(2).strip()
            if before_paren: variations.add(before_paren)
            if inside_paren: variations.add(inside_paren)
        logger.debug(f"원본 이름 '{originalname}'에 대한 검색 변형 생성: {list(variations)}")
        return list(variations)

    @staticmethod
    def get_actor_info(entity_actor, **kwargs) -> bool:
        """
        로컬 DB(다단계 이름 검색) 조회 후 웹 스크래핑 fallback.
        이미지 URL 변환 기능 적용. 유니코드 URL 유지.
        Discord URL 갱신 포함 (가능 시).
        """
        original_input_name = entity_actor.get("originalname")
        if not original_input_name:
            logger.warning("배우 정보 조회 불가: originalname이 없습니다.")
            return False

        use_local_db = kwargs.get('use_local_db', False)
        local_db_path = kwargs.get('local_db_path') if use_local_db else None
        proxy_url = kwargs.get('proxy_url')
        image_mode = kwargs.get('image_mode', '0')
        use_image_transform = kwargs.get('use_image_transform', False)
        image_transform_source = kwargs.get('image_transform_source', '') if use_image_transform else ''
        image_transform_target = kwargs.get('image_transform_target', '') if use_image_transform else ''
        db_image_base_url = kwargs.get('db_image_base_url', '')

        logger.info(f"배우 정보 검색 시작: '{original_input_name}' (DB:{use_local_db}, Transform:{use_image_transform})")

        name_variations_to_search = SiteAvdbs._parse_name_variations(original_input_name)
        final_info = None
        db_found_valid = False

        if use_local_db and local_db_path and os.path.exists(local_db_path):
            conn = None
            try:
                db_uri = f"file:{os.path.abspath(local_db_path)}?mode=ro"
                conn = sqlite3.connect(db_uri, uri=True, timeout=10)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                logger.debug(f"로컬 DB 연결 성공: {local_db_path}")

                for current_search_name in name_variations_to_search:
                    logger.debug(f"DB 검색 시도: '{current_search_name}'")
                    row = None
                    cursor.execute("SELECT inner_name_kr, inner_name_en, profile_img_view FROM actors WHERE inner_name_cn = ? LIMIT 1", (current_search_name,))
                    row = cursor.fetchone()
                    if not row:
                        cursor.execute("SELECT inner_name_kr, inner_name_en, profile_img_view, actor_onm FROM actors WHERE actor_onm LIKE ?", (f"%{current_search_name}%",))
                        potential_rows = cursor.fetchall()
                        if potential_rows:
                            for potential_row in potential_rows:
                                if SiteAvdbs._parse_and_match_other_names(potential_row["actor_onm"], current_search_name):
                                    row = potential_row; break
                    if not row:
                        cursor.execute("SELECT inner_name_kr, inner_name_en, profile_img_view FROM actors WHERE inner_name_kr = ? OR inner_name_en = ? OR inner_name_en LIKE ? LIMIT 1", (current_search_name, current_search_name, f"%({current_search_name})%"))
                        row = cursor.fetchone()

                    if row:
                        korean_name = row["inner_name_kr"]
                        name2_field = row["inner_name_en"] if row["inner_name_en"] else ""
                        db_relative_path = row["profile_img_view"]
                        thumb_url = ""

                        if db_relative_path:
                            if db_image_base_url:
                                thumb_url = db_image_base_url.rstrip('/') + '/' + db_relative_path.lstrip('/')
                            else: thumb_url = db_relative_path

                            if use_image_transform and image_transform_source and thumb_url.startswith(image_transform_source):
                                thumb_url = thumb_url.replace(image_transform_source, image_transform_target, 1)
                                logger.debug(f"DB: 이미지 URL 변환 적용됨 -> {thumb_url}")

                            if DISCORD_UTIL_AVAILABLE and thumb_url and DiscordUtil.isurlattachment(thumb_url) and DiscordUtil.isurlexpired(thumb_url):
                                logger.warning(f"DB: 만료된 Discord URL 발견, 갱신 시도...")
                                try:
                                    renewed_data = DiscordUtil.renew_urls({"thumb": thumb_url})
                                    if renewed_data and renewed_data.get("thumb") != thumb_url:
                                        thumb_url = renewed_data.get("thumb"); logger.info(f"DB: Discord URL 갱신 성공 -> {thumb_url}")
                                except Exception as e_renew: logger.error(f"DB: Discord URL 갱신 중 예외: {e_renew}")

                        if name2_field:
                            match_name2 = re.match(r"^(.*?)\s*\(.*\)$", name2_field)
                            if match_name2: name2_field = match_name2.group(1).strip()

                        if korean_name and thumb_url:
                            logger.info(f"DB에서 '{current_search_name}' 유효 정보 찾음 ({korean_name}).")
                            final_info = {"name": korean_name, "name2": name2_field, "thumb": thumb_url, "site": "avdbs_db"}
                            db_found_valid = True
                            break
                        # else: logger.debug(f"DB 결과 필수 정보 부족.")
                    # else: logger.debug(f"DB에서 '{current_search_name}' 정보 찾지 못함.")

            except sqlite3.Error as e: logger.error(f"DB 조회 중 오류: {e}")
            except Exception as e_db: logger.exception(f"DB 처리 중 예상치 못한 오류: {e_db}")
            finally:
                if conn: conn.close()
        elif use_local_db: logger.warning(f"로컬 DB 사용 설정되었으나 경로 문제: {local_db_path}")

        if not db_found_valid:
            logger.info(f"DB 조회 실패 또는 미사용, 웹 스크래핑 시도: '{original_input_name}'")
            web_info = None
            try:
                web_kwargs = {
                    'proxy_url': proxy_url, 'image_mode': image_mode,
                    'use_image_transform': use_image_transform,
                    'image_transform_source': image_transform_source,
                    'image_transform_target': image_transform_target
                }
                web_info = SiteAvdbs.__get_actor_info_from_web(original_input_name, **web_kwargs)
            except Exception as e_web: logger.exception(f"WEB: Fallback 중 예외 발생: {e_web}")

            if web_info and web_info.get("name") and web_info.get("thumb"):
                logger.info(f"WEB: 웹 스크래핑으로 '{original_input_name}' 유효 정보 찾음.")
                final_info = web_info

        if final_info is not None:
            update_count = 0
            if final_info.get("name"): entity_actor["name"] = final_info["name"]; update_count += 1
            if final_info.get("name2"): entity_actor["name2"] = final_info["name2"]; update_count += 1
            if final_info.get("thumb"): entity_actor["thumb"] = final_info["thumb"]; update_count += 1
            entity_actor["site"] = final_info.get("site", "unknown")

            if update_count > 0: logger.info(f"'{original_input_name}' 최종 정보 업데이트 완료 (출처: {entity_actor['site']}).")
            else: logger.warning(f"'{original_input_name}' 최종 정보가 비어있어 업데이트 안 함.")
            return True
        else:
            logger.info(f"'{original_input_name}'에 대한 최종 정보 없음 (DB 및 웹 검색 실패).")
            if not entity_actor.get('name') and entity_actor.get('originalname'):
                entity_actor['name'] = entity_actor.get('originalname')
            return False
