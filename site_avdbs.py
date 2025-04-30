# site_avdbs.py

import time
import requests
from lxml import html
import json
import os
import sqlite3
import re
from urllib.parse import quote, urlencode

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
        def proxy_image_url(urls, **kwargs): return {}

logger = P.logger
DB_PATH = '/app/data/db/avdbs.db'

class SiteAvdbs:
    site_char = "A"
    site_name = "avdbs"
    base_url = "https://www.avdbs.com"

    @staticmethod
    def __get_actor_info_from_web(originalname, proxy_url=None, image_mode="0") -> dict:
        with requests.Session() as s:
            enhanced_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": SiteAvdbs.base_url + "/",
                "X-Requested-With": "XMLHttpRequest",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Sec-CH-UA": '"Chromium";v="110", "Not A(Brand";v="24", "Google Chrome";v="110"',
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Windows"',
                "DNT": "1",
                "Cache-Control": "max-age=0",
            }
            s.headers.update(enhanced_headers)

            if proxy_url:
                s.proxies.update({"http": proxy_url, "https": proxy_url})

            try:
                logger.debug(f"WEB: Initial request to {SiteAvdbs.base_url}")
                initial_req_headers = enhanced_headers.copy()
                initial_req_headers.pop('Referer', None)
                initial_req_headers['Sec-Fetch-Site'] = 'none'
                initial_req_headers['Sec-Fetch-Dest'] = 'document'
                initial_req_headers['Sec-Fetch-Mode'] = 'navigate'
                initial_req_headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
                init_resp = s.get(SiteAvdbs.base_url, headers=initial_req_headers, timeout=10)
                if init_resp.status_code == 403:
                    logger.warning(f"WEB: Initial request received 403 Forbidden.")
                else:
                    init_resp.raise_for_status()
                logger.debug(f"WEB: Initial request done. Cookies: {s.cookies.get_dict()}")
            except requests.exceptions.RequestException as e_init:
                logger.warning(f"WEB: Initial request to {SiteAvdbs.base_url} failed, but continuing: {e_init}")
            except Exception as e_other_init:
                logger.warning(f"WEB: Unexpected error during initial request: {e_other_init}")

            seq = None
            log_api_url = SiteAvdbs.base_url + "/w2017/api/iux_kwd_srch_log2.php"
            log_api_params = {"op": "srch", "kwd": originalname}
            response_text = ""
            try:
                logger.debug(f"WEB: Requesting log API: {log_api_url} with params: {log_api_params}")
                api_req_headers = enhanced_headers.copy()
                api_req_headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
                api_req_headers['Referer'] = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php"
                api_req_headers['Sec-Fetch-Dest'] = 'empty'
                api_req_headers['Sec-Fetch-Mode'] = 'cors'
                api_req_headers['Sec-Fetch-Site'] = 'same-origin'
                api_req_headers.pop('Sec-Fetch-User', None)
                api_req_headers['X-Requested-With'] = 'XMLHttpRequest'

                response_log_api = s.get(log_api_url, params=log_api_params, headers=api_req_headers, timeout=10)
                response_text = response_log_api.text
                response_log_api.raise_for_status()

                logger.debug(f"WEB: Response text from log API ({log_api_url}) for '{originalname}':\n{response_text}")

                json_data = response_log_api.json()
                seq = json_data.get("seq")
                if seq is None:
                    logger.error(f"WEB: Key 'seq' not found in JSON response. Full JSON: {json_data}")
                    return None
                logger.debug(f"WEB: Successfully obtained seq: {seq}")

            except requests.exceptions.JSONDecodeError as e_json:
                logger.error(f"WEB: Failed to decode JSON from log API. Response text was:\n{response_text}")
                logger.error(f"WEB: JSONDecodeError details: {e_json}")
                return None
            except requests.exceptions.RequestException as e_req:
                logger.error(f"WEB: Request failed for log API {log_api_url}: {e_req}")
                if hasattr(e_req, 'response') and e_req.response is not None:
                    logger.error(f"WEB: Log API Failed Response Status: {e_req.response.status_code}")
                    logger.error(f"WEB: Log API Failed Response Content: {e_req.response.text[:500]}")
                return None
            except Exception as e_other:
                logger.exception(f"WEB: Unexpected error processing log API response: {e_other}")
                return None

            search_page_url = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php"
            search_page_params = {"kwd": originalname, "seq": seq}
            try:
                logger.debug(f"WEB: Requesting search page: {search_page_url} with params: {search_page_params}")
                page_req_headers = enhanced_headers.copy()
                page_req_headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
                page_req_headers['Referer'] = log_api_url
                page_req_headers['Sec-Fetch-Dest'] = 'document'
                page_req_headers['Sec-Fetch-Mode'] = 'navigate'
                page_req_headers['Sec-Fetch-Site'] = 'same-origin'

                response_search_page = s.get(search_page_url, params=search_page_params, headers=page_req_headers, timeout=15)
                response_search_page.raise_for_status()
                search_page_html = response_search_page.text
                tree = html.fromstring(search_page_html)

            except requests.exceptions.RequestException as e_req:
                logger.error(f"WEB: Request failed for search page {search_page_url}: {e_req}")
                if hasattr(e_req, 'response') and e_req.response is not None:
                    logger.error(f"WEB: Search Page Failed Response Status: {e_req.response.status_code}")
                    logger.error(f"WEB: Search Page Failed Response Content: {e_req.response.text[:500]}")
                return None
            except Exception as e_parse:
                logger.error(f"WEB: Failed to parse search page HTML: {e_parse}")
                return None

            try:
                img_src = tree.xpath(".//img/@src")
                if not img_src:
                    logger.debug("WEB: No actor images found on search results page for: %s", originalname)
                    return None
                e_names = tree.xpath('//p[starts-with(@class, "e_name")]/a')
                k_names = tree.xpath('//p[starts-with(@class, "k_name")]/a')

                for idx, (e_name_tag, k_name_tag, img_url) in enumerate(zip(e_names, k_names, img_src)):
                    try:
                        e_name_text = e_name_tag.text_content().strip()
                        k_name_text = k_name_tag.text_content().strip()
                        names = [x.strip().strip("()").strip("（）") for x in e_name_text.split("(")]
                        if len(names) < 1: continue
                        name_ja = names[-1]
                        name_en = names[0] if len(names) > 1 else ""

                        if name_ja == originalname or f"（{originalname}）" in e_name_text:
                            logger.debug(f"WEB: Match found for '{originalname}' at index {idx}")
                            processed_thumb = None
                            try:
                                processed_thumb = SiteUtil.process_image_mode(image_mode, img_url, proxy_url=proxy_url)
                            except NameError:
                                logger.error("SiteUtil not defined for image processing")
                                processed_thumb = img_url
                            except Exception as e_img:
                                logger.error(f"Error processing image with SiteUtil: {e_img}")
                                processed_thumb = img_url

                            return {
                                "name": k_name_text,
                                "name2": name_en,
                                "site": "avdbs",
                                "thumb": processed_thumb,
                            }
                    except Exception as e_item:
                        logger.exception(f"WEB: Error processing item at index {idx}: {e_item}")
                        continue

                logger.debug("WEB: No matching actor found in search results for: %s", originalname)
                return None
            except Exception as e_parse_results:
                logger.exception(f"WEB: Error parsing search results: {e_parse_results}")
                return None

    @staticmethod
    def get_actor_info(entity_actor, **kwargs):
        originalname = entity_actor.get("originalname")
        if not originalname:
            logger.warning("배우 정보 조회 불가: originalname이 없습니다.")
            return entity_actor

        info = None
        db_found_valid = False

        logger.debug(f"DB 조회 시도: originalname(일본어)='{originalname}'")
        if os.path.exists(DB_PATH):
            conn = None
            try:
                # --- DB 연결 (함수 호출 시) ---
                db_uri = f"file:{os.path.abspath(DB_PATH)}?mode=ro"
                logger.debug(f"Connecting to DB (RO, WAL check): {db_uri}")
                conn = sqlite3.connect(db_uri, uri=True, timeout=5)

                try:
                    journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
                    logger.debug(f"DB current journal_mode: {journal_mode}")
                    if journal_mode.lower() != 'wal':
                        logger.warning("DB is not in WAL mode.")
                except sqlite3.Error as e_wal_check:
                    logger.warning(f"Failed to check journal mode: {e_wal_check}")

                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                row = None

                # --- 1차 쿼리 ---
                logger.debug("DB 1차 쿼리 실행 (WHERE inner_name_cn = ?)")
                query1 = "SELECT inner_name_kr, inner_name_en, profile_img_view FROM actors WHERE inner_name_cn = ? LIMIT 1"
                cursor.execute(query1, (originalname,))
                row = cursor.fetchone()

                if not row:
                    logger.debug("DB 1차 쿼리 결과 없음. 2차 쿼리(fallback) 시도.")
                    # --- 2차 쿼리 ---
                    query2 = """
                    SELECT inner_name_kr, inner_name_en, profile_img_view
                    FROM actors WHERE inner_name_kr = ? OR inner_name_en = ? OR inner_name_en LIKE ? OR actor_onm LIKE ? LIMIT 1
                    """
                    like_param_en = f"%({originalname})%"
                    like_param_onm = f"%{originalname}%"
                    cursor.execute(query2, (originalname, originalname, like_param_en, like_param_onm))
                    row = cursor.fetchone()
                    if row: logger.debug("DB 2차 쿼리(fallback): 결과 찾음.")
                    else: logger.debug("DB 2차 쿼리(fallback): 결과 없음.")

                # --- 결과 처리 ---
                if row:
                    korean_name = row["inner_name_kr"]
                    eng_orig_name = row["inner_name_en"]
                    thumb_url = row["profile_img_view"]

                    # Discord URL 처리
                    if DISCORD_UTIL_AVAILABLE and thumb_url and DiscordUtil.isurlattachment(thumb_url):
                        if DiscordUtil.isurlexpired(thumb_url):
                            logger.warning(f"DB: 만료된 Discord URL 발견 ('{originalname}'). 갱신 시도...")
                            try:
                                renew_map = DiscordUtil.proxy_image_url([thumb_url])
                                if thumb_url in renew_map and renew_map[thumb_url]:
                                    renewed_url = renew_map[thumb_url]
                                    if renewed_url != thumb_url:
                                        logger.info(f"DB: Discord URL 갱신 성공 ('{originalname}'): -> {renewed_url}")
                                        thumb_url = renewed_url
                            except Exception as e_renew:
                                logger.error(f"DB: Discord URL 갱신 중 예외 ('{originalname}'): {e_renew}")

                    db_info = { "name": korean_name, "name2": eng_orig_name, "thumb": thumb_url }
                    if db_info["name2"]:
                        match = re.match(r"^(.*?)\s*\(.*\)$", db_info["name2"])
                        if match: db_info["name2"] = match.group(1).strip()

                    if db_info.get("name") and db_info.get("thumb"):
                        logger.info(f"DB에서 '{originalname}' 유효 정보 찾음.")
                        info = db_info
                        info["site"] = "avdbs_db"
                        db_found_valid = True
                    else:
                        logger.debug(f"DB 결과 필수 정보 부족 ('{originalname}').")

            except sqlite3.OperationalError as e_op:
                logger.error(f"DB OperationalError (읽기 전용/잠금 등): {e_op}")
            except sqlite3.Error as e:
                logger.error(f"DB 조회 중 오류 (originalname='{originalname}'): {e}")
            except Exception as e_db:
                logger.exception(f"DB 처리 중 예상치 못한 오류 (originalname='{originalname}'): {e_db}")
            finally:
                # --- DB 연결 해제 (함수 종료 전) ---
                if conn:
                    conn.close()
                    logger.debug("DB connection closed.")
        else:
            logger.warning(f"Avdbs 데이터베이스 파일 없음: {DB_PATH}. 웹 스크래핑 시도.")

        if not db_found_valid:
            logger.info(f"DB 조회 실패 또는 정보 부족, 웹 스크래핑 시도 (fallback): '{originalname}'")
            retry = kwargs.pop("retry", True)
            web_info = None
            try:
                web_info = SiteAvdbs.__get_actor_info_from_web(originalname, **kwargs)
            except Exception as e_web:
                if retry:
                    logger.warning(f"WEB: Exception occurred for '{originalname}', retrying after 2 seconds... Error: {e_web}")
                    time.sleep(2)
                    return SiteAvdbs.get_actor_info(entity_actor, retry=False, **kwargs)
                else:
                    logger.exception(f"WEB: Failed to get actor info for '{originalname}' from web after retry. Error: {e_web}")
            else:
                if web_info is not None:
                    if web_info.get("name") and web_info.get("thumb"):
                        logger.info(f"WEB: 웹 스크래핑으로 '{originalname}' 유효 정보 찾음.")
                        info = web_info
                    else:
                        logger.info(f"WEB: 웹 스크래핑 결과 찾았으나 필수 정보(이름, 썸네일) 부족.")
                else:
                    logger.info(f"WEB: 웹 스크래핑으로도 '{originalname}' 정보 찾지 못함.")

        if info is not None:
            info["name"] = info.get("name") if info.get("name") else None
            info["name2"] = info.get("name2") if info.get("name2") else None
            info["thumb"] = info.get("thumb") if info.get("thumb") else None

            if info.get("name") or info.get("name2") or info.get("thumb"):
                logger.info(f"'{originalname}' 정보 업데이트 완료 (출처: {info.get('site', '알 수 없음')}).")
                entity_actor.update(info)
            else:
                logger.info(f"'{originalname}' 최종 정보가 비어있어 업데이트하지 않음.")
        else:
            logger.info(f"'{originalname}' 최종 정보 없음.")

        return entity_actor
