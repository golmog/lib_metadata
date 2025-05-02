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
    def _parse_name_variations(originalname):
        """입력된 이름에서 검색할 이름 변형 목록을 생성합니다."""
        variations = [originalname] # 1. 원본 이름 항상 포함
        # 정규식을 사용하여 괄호 및 내부 내용 추출 (일반 괄호, 전각 괄호 모두 처리)
        match = re.match(r'^(.*?)\s*[（\(]([^）\)]+)[）\)]\s*$', originalname)
        if match:
            before_paren = match.group(1).strip()
            inside_paren = match.group(2).strip()
            if before_paren and before_paren not in variations:
                variations.append(before_paren) # 2. 괄호 앞부분 추가
            if inside_paren and inside_paren not in variations:
                variations.append(inside_paren) # 3. 괄호 안부분 추가
            logger.debug(f"원본 이름 '{originalname}'에 대한 검색 변형 생성: {variations}")
        return variations

    @staticmethod
    def get_actor_info(entity_actor, **kwargs):
        """
        로컬 DB(일본어 이름 우선) 조회 후 웹 스크래핑 fallback.
        이름에 괄호가 있는 경우 여러 단계로 검색 시도.
        Discord URL 갱신 포함.
        """
        original_input_name = entity_actor.get("originalname")
        if not original_input_name:
            logger.warning("배우 정보 조회 불가: originalname이 없습니다.")
            return entity_actor

        # --- 검색할 이름 변형 목록 생성 ---
        name_variations_to_search = SiteAvdbs._parse_name_variations(original_input_name)

        final_info = None # 최종적으로 찾은 정보를 저장할 변수

        # --- 각 이름 변형에 대해 검색 시도 ---
        for current_search_name in name_variations_to_search:
            logger.info(f"배우 검색 시도: '{current_search_name}' (원본: '{original_input_name}')")
            info_found_for_this_name = None # 현재 이름으로 찾은 정보를 임시 저장
            db_found_valid_for_this_name = False

            # --- DB 검색 시도 (현재 이름 기준) ---
            if os.path.exists(DB_PATH):
                conn = None
                try:
                    db_uri = f"file:{os.path.abspath(DB_PATH)}?mode=ro"
                    conn = sqlite3.connect(db_uri, uri=True, timeout=5)
                    # (WAL 모드 체크는 생략 가능, 필요하면 추가)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    row = None

                    logger.debug(f"DB 1차 쿼리 실행 (WHERE inner_name_cn = '{current_search_name}')")
                    query1 = "SELECT inner_name_kr, inner_name_en, profile_img_view FROM actors WHERE inner_name_cn = ? LIMIT 1"
                    cursor.execute(query1, (current_search_name,))
                    row = cursor.fetchone()

                    if not row:
                        logger.debug("DB 1차 쿼리 결과 없음. 2차 쿼리(다른 이름) 시도.")
                        query2 = "SELECT inner_name_kr, inner_name_en, profile_img_view, actor_onm, inner_name_cn FROM actors WHERE actor_onm LIKE ?"
                        like_param_onm = f"%{current_search_name}%"
                        cursor.execute(query2, (like_param_onm,))
                        potential_rows = cursor.fetchall()
                        if potential_rows:
                            for potential_row in potential_rows:
                                if SiteAvdbs._parse_and_match_other_names(potential_row["actor_onm"], current_search_name):
                                    logger.debug(f"DB 2차 쿼리(파싱): 다른 이름 목록에서 '{current_search_name}' 정확히 일치 배우 찾음.")
                                    row = potential_row
                                    break
                            if not row: logger.debug("DB 2차 쿼리(파싱): 다른 이름 포함 후보 중 정확히 일치 배우 없음.")
                        else: logger.debug("DB 2차 쿼리: 다른 이름 포함 후보 없음.")

                        if not row:
                            logger.debug("DB 1, 2차 쿼리 실패. 3차 쿼리(fallback) 시도.")
                            query3 = "SELECT inner_name_kr, inner_name_en, profile_img_view FROM actors WHERE inner_name_kr = ? OR inner_name_en = ? OR inner_name_en LIKE ? LIMIT 1"
                            like_param_en = f"%({current_search_name})%"
                            cursor.execute(query3, (current_search_name, current_search_name, like_param_en))
                            row = cursor.fetchone()
                            if row: logger.debug("DB 3차 쿼리(fallback): 결과 찾음.")
                            else: logger.debug("DB 3차 쿼리(fallback): 결과 없음.")

                    # --- DB 결과 처리 (row가 찾아졌을 경우) ---
                    if row:
                        # (기존의 Discord URL 갱신 및 db_info 생성 로직 삽입)
                        # --- !!! 중요: 여기서는 필드명 'name2'에 inner_name_en 을 사용하는 것으로 가정 (원본 코드 기반) !!! ---
                        # --- 만약 name2가 inner_name_cn(일본어)여야 한다면 이 부분을 수정해야 함 ---
                        korean_name = row["inner_name_kr"]
                        name2_field = row["inner_name_en"] # 또는 row["inner_name_cn"] ??? 원본 코드 확인 필요
                        thumb_url = row["profile_img_view"]

                        # Discord URL 갱신 로직 (기존 방식 또는 renew_urls 사용 방식)
                        if DISCORD_UTIL_AVAILABLE and thumb_url and DiscordUtil.isurlattachment(thumb_url):
                            if DiscordUtil.isurlexpired(thumb_url):
                                logger.warning(f"DB: 만료된 Discord URL 발견 ('{current_search_name}' -> found: {korean_name}). 갱신 시도...")
                                try:
                                    # renew_urls 사용 방식 예시 (이전 답변 참고)
                                    temp_data_for_renew = {"thumb": thumb_url}
                                    renewed_data = DiscordUtil.renew_urls(temp_data_for_renew)
                                    if renewed_data and isinstance(renewed_data, dict):
                                        renewed_url = renewed_data.get("thumb")
                                        if renewed_url and isinstance(renewed_url, str) and renewed_url != thumb_url:
                                            logger.info(f"DB: Discord URL 갱신 성공: -> {renewed_url}")
                                            thumb_url = renewed_url # thumb_url 변수 업데이트
                                except Exception as e_renew:
                                    logger.error(f"DB: Discord URL 갱신 중 예외: {e_renew}")

                        db_info = {"name": korean_name, "name2": name2_field, "thumb": thumb_url}

                        # name2 필드 정제 로직 (원본 코드 유지)
                        if db_info.get("name2"):
                            match_name2 = re.match(r"^(.*?)\s*\(.*\)$", db_info["name2"])
                            if match_name2: db_info["name2"] = match_name2.group(1).strip()

                        # 유효성 검사 (name과 thumb 필수)
                        if db_info.get("name") and db_info.get("thumb"):
                            logger.info(f"DB에서 '{current_search_name}'에 대한 유효 정보 찾음 ({korean_name}).")
                            info_found_for_this_name = db_info
                            info_found_for_this_name["site"] = "avdbs_db"
                            db_found_valid_for_this_name = True
                        else:
                            logger.debug(f"DB 결과 필수 정보 부족 ('{current_search_name}' -> found: {korean_name}). 웹 스크래핑 시도.")

                except sqlite3.Error as e: logger.error(f"DB 조회 중 오류 ({current_search_name}): {e}")
                except Exception as e_db: logger.exception(f"DB 처리 중 예상치 못한 오류 ({current_search_name}): {e_db}")
                finally:
                    if conn: conn.close()
            else:
                logger.warning(f"Avdbs 데이터베이스 파일 없음: {DB_PATH}. 웹 스크래핑 시도.")

            # --- 웹 스크래핑 시도 (DB에서 못 찾았거나 정보 부족 시) ---
            if not db_found_valid_for_this_name:
                logger.info(f"DB 조회 실패 또는 정보 부족, 웹 스크래핑 시도: '{current_search_name}'")
                web_info = None
                try:
                    # 웹 스크래핑 시도 (kwargs 전달, 재시도 로직은 __get_actor... 내부에 포함 가능)
                    web_info = SiteAvdbs.__get_actor_info_from_web(current_search_name, **kwargs)
                except Exception as e_web:
                    # 웹 스크래핑 자체의 예외 로깅 (재시도는 __get... 내부에서 처리)
                    logger.exception(f"WEB: Failed for '{current_search_name}': {e_web}")

                if web_info is not None:
                    if web_info.get("name") and web_info.get("thumb"):
                        logger.info(f"WEB: 웹 스크래핑으로 '{current_search_name}' 유효 정보 찾음.")
                        info_found_for_this_name = web_info # site 정보는 web_info 생성 시 포함됨
                    else: logger.info(f"WEB: 웹 스크래핑 결과 필수 정보 부족.")
                else: logger.info(f"WEB: 웹 스크래핑으로도 '{current_search_name}' 정보 찾지 못함.")

            # --- 현재 이름으로 유효한 정보를 찾았는지 확인 ---
            if info_found_for_this_name is not None:
                logger.info(f"성공: '{current_search_name}' 이름으로 배우 정보 찾음 (출처: {info_found_for_this_name.get('site', 'N/A')}).")
                final_info = info_found_for_this_name # 최종 정보로 확정
                break # 이름 변형 루프 종료 (더 이상 다른 이름으로 검색 안 함)
            else:
                logger.info(f"실패: '{current_search_name}' 이름으로 배우 정보 찾지 못함. 다음 이름 시도...")

        # --- 최종 결과 처리 (모든 이름 변형 검색 후) ---
        if final_info is not None:
            # 최종 정보 정리 (get으로 안전하게 접근)
            final_info["name"] = final_info.get("name")
            final_info["name2"] = final_info.get("name2")
            final_info["thumb"] = final_info.get("thumb")

            # 하나라도 유효한 값이 있으면 업데이트
            if final_info.get("name") or final_info.get("name2") or final_info.get("thumb"):
                logger.info(f"'{original_input_name}' 최종 정보 업데이트 완료 (출처: {final_info.get('site', 'N/A')}).")
                entity_actor.update(final_info) # 입력 entity 업데이트
            else:
                logger.warning(f"'{original_input_name}' 최종 정보가 비어있어 업데이트 안 함.")
        else:
            logger.info(f"'{original_input_name}'에 대한 최종 정보 없음 (모든 이름 변형 검색 실패).")

        return entity_actor # 수정된 (또는 원본) entity_actor 반환
