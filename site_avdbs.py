# -*- coding: utf-8 -*-
import time
import requests
from lxml import html
import json
import os
import sqlite3
import re
from urllib.parse import quote, urlencode

# lib_metadata 공용 요소들
from .plugin import P
from .site_util import SiteUtil
try:
    from .discord import DiscordUtil
    DISCORD_UTIL_AVAILABLE = True
except ImportError:
    P.logger.error("DiscordUtil을 임포트할 수 없습니다. Discord URL 갱신 기능 비활성화.")
    DISCORD_UTIL_AVAILABLE = False
    # DiscordUtil dummy 클래스 정의 (AttributeError 방지용)
    class DiscordUtil:
        @staticmethod
        def isurlattachment(url): return False
        @staticmethod
        def isurlexpired(url): return False
        @staticmethod
        def proxy_image_url(urls, **kwargs): return {}

logger = P.logger
DB_PATH = '/app/data/db/avdbs.db' # SJVA 환경 내 DB 경로

class SiteAvdbs:
    site_char = "A"
    site_name = "avdbs" # 웹 fallback 시 사용될 이름
    base_url = "https://www.avdbs.com"

    @staticmethod
    def __get_actor_info_from_web(originalname, proxy_url=None, image_mode="0") -> dict:
        """Avdbs.com 웹사이트에서 배우 정보를 가져오는 내부 메소드 (Fallback용)"""
        logger.info(f"WEB Fallback: Avdbs.com 에서 '{originalname}' 정보 직접 검색 시작.")
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
            if proxy_url: s.proxies.update({"http": proxy_url, "https": proxy_url})

            # 단계 1: 초기 접속 시도
            try:
                logger.debug(f"WEB: Initial request to {SiteAvdbs.base_url}")
                initial_req_headers = enhanced_headers.copy()
                initial_req_headers.pop('Referer', None); initial_req_headers['Sec-Fetch-Site'] = 'none'
                initial_req_headers['Sec-Fetch-Dest'] = 'document'; initial_req_headers['Sec-Fetch-Mode'] = 'navigate'
                initial_req_headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
                init_resp = s.get(SiteAvdbs.base_url, headers=initial_req_headers, timeout=10)
                if init_resp.status_code == 403: logger.warning(f"WEB: Initial request received 403 Forbidden.")
                else: init_resp.raise_for_status()
                logger.debug(f"WEB: Initial request done. Cookies: {s.cookies.get_dict()}")
            except Exception as e_init: logger.warning(f"WEB: Initial request failed, but continuing: {e_init}")

            # 단계 2: 검색 로그 API 호출
            seq = None; log_api_url = SiteAvdbs.base_url + "/w2017/api/iux_kwd_srch_log2.php"
            log_api_params = {"op": "srch", "kwd": originalname}; response_text = ""
            try:
                logger.debug(f"WEB: Requesting log API: {log_api_url} with params: {log_api_params}")
                api_req_headers = enhanced_headers.copy()
                api_req_headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
                api_req_headers['Referer'] = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php"
                api_req_headers['Sec-Fetch-Dest'] = 'empty'; api_req_headers['Sec-Fetch-Mode'] = 'cors'
                api_req_headers['Sec-Fetch-Site'] = 'same-origin'; api_req_headers.pop('Sec-Fetch-User', None)
                api_req_headers['X-Requested-With'] = 'XMLHttpRequest'
                response_log_api = s.get(log_api_url, params=log_api_params, headers=api_req_headers, timeout=10)
                response_text = response_log_api.text
                response_log_api.raise_for_status()
                logger.debug(f"WEB: Response text from log API for '{originalname}':\n{response_text}")
                json_data = response_log_api.json(); seq = json_data.get("seq")
                if seq is None: logger.error(f"WEB: Key 'seq' not found. JSON: {json_data}"); return None
                logger.debug(f"WEB: Obtained seq: {seq}")
            except requests.exceptions.JSONDecodeError: logger.error(f"WEB: Failed to decode JSON from log API. Response:\n{response_text}"); return None
            except requests.exceptions.RequestException as e_req: logger.error(f"WEB: Request failed for log API: {e_req}"); return None
            except Exception as e_other: logger.exception(f"WEB: Unexpected error processing log API: {e_other}"); return None

            # 단계 3: 배우 검색 페이지 요청
            search_page_url = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php"
            search_page_params = {"kwd": originalname, "seq": seq}; tree = None
            try:
                logger.debug(f"WEB: Requesting search page: {search_page_url} with params: {search_page_params}")
                page_req_headers = enhanced_headers.copy()
                page_req_headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
                page_req_headers['Referer'] = log_api_url
                page_req_headers['Sec-Fetch-Dest'] = 'document'; page_req_headers['Sec-Fetch-Mode'] = 'navigate'; page_req_headers['Sec-Fetch-Site'] = 'same-origin'
                response_search_page = s.get(search_page_url, params=search_page_params, headers=page_req_headers, timeout=15)
                response_search_page.raise_for_status()
                tree = html.fromstring(response_search_page.text)
            except requests.exceptions.RequestException as e_req: logger.error(f"WEB: Request failed for search page: {e_req}"); return None
            except Exception as e_parse: logger.error(f"WEB: Failed to parse search page HTML: {e_parse}"); return None

            # 단계 4: 검색 결과 파싱 및 처리
            if tree is None: return None
            try:
                img_src = tree.xpath(".//img/@src")
                if not img_src: logger.debug("WEB: No actor images found for: %s", originalname); return None
                e_names = tree.xpath('//p[starts-with(@class, "e_name")]/a'); k_names = tree.xpath('//p[starts-with(@class, "k_name")]/a')
                for idx, (e_name_tag, k_name_tag, img_url) in enumerate(zip(e_names, k_names, img_src)):
                    try:
                        e_name_text = e_name_tag.text_content().strip(); k_name_text = k_name_tag.text_content().strip()
                        names = [x.strip().strip("()").strip("（）") for x in e_name_text.split("(")]
                        if len(names) < 1: continue
                        name_ja = names[-1]; name_en = names[0] if len(names) > 1 else ""
                        if name_ja == originalname or f"（{originalname}）" in e_name_text:
                            logger.debug(f"WEB: Match found for '{originalname}' at index {idx}")
                            processed_thumb = SiteUtil.process_image_mode(image_mode, img_url, proxy_url=proxy_url)
                            return {"name": k_name_text, "name2": name_en, "site": "avdbs", "thumb": processed_thumb}
                    except Exception as e_item: logger.exception(f"WEB: Error processing item at index {idx}: {e_item}")
                logger.debug("WEB: No matching actor found in search results for: %s", originalname)
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
                    if jp_name == originalname:
                        logger.debug(f"다른 이름 파싱: 괄호 안 일치 '{jp_name}' == '{originalname}'")
                        return True
            elif '(' not in name_part and ')' not in name_part:
                if name_part == originalname:
                    logger.debug(f"다른 이름 파싱: 괄호 없는 이름 일치 '{name_part}' == '{originalname}'")
                    return True
        return False

    @staticmethod
    def get_actor_info(entity_actor, **kwargs):
        """로컬 DB(일본어 이름 우선) 조회 후 웹 스크래핑 fallback, Discord URL 갱신"""
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
                db_uri = f"file:{os.path.abspath(DB_PATH)}?mode=ro"
                logger.debug(f"Connecting to DB (RO, WAL check): {db_uri}")
                conn = sqlite3.connect(db_uri, uri=True, timeout=5)
                try:
                    journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
                    if journal_mode.lower() != 'wal': logger.warning("DB is not in WAL mode.")
                except sqlite3.Error: pass # WAL 확인 오류는 무시
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                row = None

                logger.debug("DB 1차 쿼리 실행 (WHERE inner_name_cn = ?)")
                query1 = "SELECT inner_name_kr, inner_name_en, profile_img_view FROM actors WHERE inner_name_cn = ? LIMIT 1"
                cursor.execute(query1, (originalname,))
                row = cursor.fetchone()

                if not row:
                    logger.debug("DB 1차 쿼리 결과 없음. 2차 쿼리(다른 이름) 시도.")
                    query2 = "SELECT inner_name_kr, inner_name_en, profile_img_view, actor_onm, inner_name_cn FROM actors WHERE actor_onm LIKE ?"
                    like_param_onm = f"%{originalname}%"
                    logger.debug(f"DB 2차 쿼리 실행 (WHERE actor_onm LIKE '{like_param_onm}')")
                    cursor.execute(query2, (like_param_onm,))
                    potential_rows = cursor.fetchall()
                    if potential_rows:
                        logger.debug(f"DB 2차 쿼리: 다른 이름 포함 가능 후보 {len(potential_rows)}개 발견. 파싱 비교 시작...")
                        for potential_row in potential_rows:
                            if SiteAvdbs._parse_and_match_other_names(potential_row["actor_onm"], originalname):
                                logger.debug(f"DB 2차 쿼리(파싱): 다른 이름 목록에서 '{originalname}' 정확히 일치 배우 찾음 (실제 배우: {potential_row['inner_name_kr']}/{potential_row['inner_name_cn']}).")
                                row = potential_row
                                break
                        if not row: logger.debug("DB 2차 쿼리(파싱): 다른 이름 포함 후보 중 정확히 일치 배우 없음.")
                    else: logger.debug("DB 2차 쿼리: 다른 이름 포함 후보 없음.")

                    if not row:
                        logger.debug("DB 1, 2차 쿼리 실패. 3차 쿼리(fallback) 시도.")
                        query3 = "SELECT inner_name_kr, inner_name_en, profile_img_view FROM actors WHERE inner_name_kr = ? OR inner_name_en = ? OR inner_name_en LIKE ? LIMIT 1"
                        like_param_en = f"%({originalname})%"
                        cursor.execute(query3, (originalname, originalname, like_param_en))
                        row = cursor.fetchone()
                        if row: logger.debug("DB 3차 쿼리(fallback): 결과 찾음.")
                        else: logger.debug("DB 3차 쿼리(fallback): 결과 없음.")

                if row:
                    # DB row 객체에서 직접 값을 읽어와 db_info 딕셔너리 생성
                    db_info = {
                        "name": row["inner_name_kr"],       # 한국어 이름
                        "name2": row["inner_name_cn"],      # 일본어 이름 (DB 컬럼 사용)
                        "thumb": row["profile_img_view"]    # 썸네일 URL
                    }
                    logger.debug(f"DB 조회 결과: name='{db_info['name']}', name2='{db_info['name2']}', thumb='{db_info['thumb'][:60]}...'")

                    # DiscordUtil 사용 가능하고, 썸네일이 Discord 첨부파일 URL 형식인지 확인
                    if DISCORD_UTIL_AVAILABLE and db_info.get("thumb") and DiscordUtil.isurlattachment(db_info["thumb"]):
                        logger.debug(f"DB: Discord 첨부파일 URL 발견 ('{originalname}' -> {db_info.get('name')}). 갱신 시도 (renew_urls)...")
                        original_thumb_before_renew = db_info["thumb"] # 비교용 원본 URL 저장
                        try:
                            # renew_urls 호출하여 db_info 내 thumb 값 갱신 시도
                            db_info = DiscordUtil.renew_urls(db_info)

                            # 갱신 후 로그 (URL 변경 여부 확인)
                            if db_info.get("thumb") != original_thumb_before_renew:
                                logger.info(f"DB: Discord URL 갱신 완료. 새 URL 적용됨.")
                            else:
                                logger.debug(f"DB: Discord URL 갱신 처리 완료 (URL 변경 없음 - 만료 전 또는 갱신 실패).")

                        except Exception as e_renew:
                            logger.error(f"DB: Discord URL 갱신 프로세스 중 예외: {e_renew}")
                            # 오류 발생 시 db_info['thumb']는 이전 값을 유지

                    if db_info.get("name") and db_info.get("thumb"):
                        logger.info(f"DB에서 '{originalname}' 정보 처리 완료 (이름: {db_info.get('name')}).")
                        info = db_info # 최종 정보 할당 (갱신된 thumb 포함 가능)
                        info["site"] = "avdbs_db"
                        db_found_valid = True
                    else:
                        missing_fields = []
                        if not db_info.get("name"): missing_fields.append("name (한국어 이름)")
                        if not db_info.get("thumb"): missing_fields.append("thumb (썸네일)")
                        logger.warning(f"DB 처리 후 필수 정보 부족: {', '.join(missing_fields)} ('{originalname}' -> 이름: {db_info.get('name')}, 썸네일: {db_info.get('thumb')})")

            except sqlite3.OperationalError as e_op: logger.error(f"DB OperationalError: {e_op}")
            except sqlite3.Error as e: logger.error(f"DB 조회 중 오류: {e}")
            except Exception as e_db: logger.exception(f"DB 처리 중 예상치 못한 오류: {e_db}")
            finally:
                if conn: conn.close(); logger.debug("DB connection closed.")
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
                    logger.warning(f"WEB: Exception, retrying after 2s: {e_web}")
                    time.sleep(2)
                    return SiteAvdbs.get_actor_info(entity_actor, retry=False, **kwargs)
                else: logger.exception(f"WEB: Failed after retry: {e_web}")
            else:
                if web_info is not None:
                    if web_info.get("name") and web_info.get("thumb"):
                        logger.info(f"WEB: 웹 스크래핑으로 '{originalname}' 유효 정보 찾음.")
                        info = web_info
                    else: logger.info(f"WEB: 웹 스크래핑 결과 필수 정보 부족.")
                else: logger.info(f"WEB: 웹 스크래핑으로도 정보 찾지 못함.")

        if info is not None:
            info["name"] = info.get("name") if info.get("name") else None
            info["name2"] = info.get("name2") if info.get("name2") else None
            info["thumb"] = info.get("thumb") if info.get("thumb") else None
            if info.get("name") or info.get("name2") or info.get("thumb"):
                logger.info(f"'{originalname}' 정보 업데이트 완료 (출처: {info.get('site', 'N/A')}).")
                entity_actor.update(info)
            else: logger.info(f"'{originalname}' 최종 정보 비어있어 업데이트 안 함.")
        else: logger.info(f"'{originalname}' 최종 정보 없음.")

        return entity_actor
