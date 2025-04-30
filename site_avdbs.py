# site_avdbs.py (DB 우선 조회, 웹 스크래핑 fallback 방식)

import time
import requests
from lxml import html
import json # JSONDecodeError 를 명시적으로 처리하기 위해 import
import os
import sqlite3
import re

# lib_metadata 공용 요소들
from .plugin import P
from .site_util import SiteUtil # 웹 스크래핑 fallback 시 필요할 수 있음

logger = P.logger

# SJVA 내 DB 파일 경로
DB_PATH = '/app/data/db/avdbs.db'

class SiteAvdbs:
    site_char = "A"
    site_name = "avdbs" # 원래 사이트 이름 유지 (fallback 시 사용)
    base_url = "https://www.avdbs.com"

    # --- 원본 웹 스크래핑 로직 (내부 메소드로 유지) ---
    @staticmethod
    def __get_actor_info_from_web(originalname, proxy_url=None, image_mode="0") -> dict:
        """Avdbs.com 웹사이트에서 배우 정보를 가져오는 내부 메소드 (원본 로직)"""
        # 이 함수는 원본 코드의 __get_actor_info 내용과 동일하게 유지
        # 헤더 등 필요한 설정 추가
        with requests.Session() as s:
            # 헤더 설정 (이전 논의에서 보강된 헤더 사용 또는 SiteUtil.default_headers 사용)
            enhanced_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": SiteAvdbs.base_url + "/",
                "X-Requested-With": "XMLHttpRequest",
                "Connection": "keep-alive",
                # 필요시 다른 헤더 추가
            }
            s.headers.update(enhanced_headers) # SiteUtil.default_headers 대신 보강된 헤더 사용

            if proxy_url:
                s.proxies.update({"http": proxy_url, "https": proxy_url})

            # 단계 1: 초기 접속
            try:
                logger.debug(f"WEB: Initial request to {SiteAvdbs.base_url}")
                init_resp = s.get(SiteAvdbs.base_url, timeout=10)
                # 403 오류 발생해도 일단 진행 (API가 다를 수 있음)
                if init_resp.status_code == 403:
                    logger.warning(f"WEB: Initial request received 403 Forbidden.")
                else:
                    init_resp.raise_for_status()
                logger.debug(f"WEB: Initial request done. Cookies: {s.cookies.get_dict()}")
            except requests.exceptions.RequestException as e_init:
                logger.warning(f"WEB: Initial request to {SiteAvdbs.base_url} failed, but continuing: {e_init}")
            except Exception as e_other_init:
                logger.warning(f"WEB: Unexpected error during initial request: {e_other_init}")


            # 단계 2: 검색 로그 API 호출
            seq = None
            log_api_url = SiteAvdbs.base_url + "/w2017/api/iux_kwd_srch_log2.php"
            log_api_params = {"op": "srch", "kwd": originalname}
            response_text = "" # 응답 로깅 위해 초기화
            try:
                logger.debug(f"WEB: Requesting log API: {log_api_url} with params: {log_api_params}")
                # API 요청 헤더 조정
                api_req_headers = enhanced_headers.copy()
                api_req_headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
                api_req_headers['Referer'] = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php"

                response_log_api = s.get(log_api_url, params=log_api_params, headers=api_req_headers, timeout=10)
                response_text = response_log_api.text # 로깅 및 오류 분석 위해 저장
                response_log_api.raise_for_status()

                logger.debug(f"WEB: Response text from log API ({log_api_url}) for '{originalname}':\n{response_text}")

                json_data = response_log_api.json()
                seq = json_data.get("seq")
                if seq is None:
                    logger.error(f"WEB: Key 'seq' not found in JSON response. Full JSON: {json_data}")
                    return None # seq 없으면 진행 불가
                logger.debug(f"WEB: Successfully obtained seq: {seq}")

            except requests.exceptions.JSONDecodeError as e_json:
                # 403 등의 이유로 HTML이 반환되면 이 오류 발생
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

            # 단계 3: 실제 배우 검색 페이지 요청
            search_page_url = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php"
            search_page_params = {"kwd": originalname, "seq": seq}
            try:
                logger.debug(f"WEB: Requesting search page: {search_page_url} with params: {search_page_params}")
                page_req_headers = enhanced_headers.copy()
                page_req_headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
                page_req_headers['Referer'] = log_api_url

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

            # 단계 4: 검색 결과 파싱 및 처리 (원본 코드 로직)
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
                            # 이미지 처리 (SiteUtil 사용)
                            processed_thumb = SiteUtil.process_image_mode(image_mode, img_url, proxy_url=proxy_url)

                            return {
                                "name": k_name_text,
                                "name2": name_en,
                                "site": "avdbs", # 출처를 웹사이트로 명시
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

    # --- DB 조회 후 웹 fallback을 제어하는 공개 메소드 ---
    @staticmethod
    def get_actor_info(entity_actor, **kwargs):
        """
        배우 정보를 로컬 DB(한자 이름 우선)에서 조회하고, 없으면 웹 스크래핑을 시도합니다.
        """
        originalname = entity_actor.get("originalname")
        if not originalname:
            logger.warning("배우 정보 조회 불가: originalname이 없습니다.")
            return entity_actor

        info = None # 최종 정보
        db_found_valid = False # DB에서 유효한 정보를 찾았는지

        # --- 단계 1: 로컬 DB 조회 (한자 이름 우선) ---
        logger.debug(f"DB 조회 시도: originalname(cn)='{originalname}'")
        if os.path.exists(DB_PATH):
            conn = None
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                row = None # 결과 행 초기화

                # --- 1차 쿼리: 한자 이름(inner_name_cn) 정확히 일치 검색 ---
                logger.debug("DB 1차 쿼리 실행 (WHERE inner_name_cn = ?)")
                query1 = "SELECT inner_name_kr, inner_name_en, profile_img_view FROM actors WHERE inner_name_cn = ? LIMIT 1"
                cursor.execute(query1, (originalname,))
                row = cursor.fetchone()

                if row:
                    logger.debug("DB 1차 쿼리: 한자 이름 일치 항목 찾음.")
                else:
                    logger.debug("DB 1차 쿼리: 한자 이름 일치 항목 없음. 2차 쿼리(fallback) 시도.")
                    # --- 2차 쿼리 (Fallback): 다른 이름 필드 검색 ---
                    query2 = """
                    SELECT inner_name_kr, inner_name_en, profile_img_view
                    FROM actors
                    WHERE inner_name_kr = ? OR inner_name_en = ? OR inner_name_en LIKE ? OR actor_onm LIKE ?
                    LIMIT 1
                    """
                    like_param_en = f"%({originalname})%" # 혹시 originalname이 괄호안 일본어일 경우 대비
                    like_param_onm = f"%{originalname}%"
                    cursor.execute(query2, (originalname, originalname, like_param_en, like_param_onm))
                    row = cursor.fetchone()
                    if row:
                        logger.debug("DB 2차 쿼리(fallback): 다른 이름 필드에서 일치 항목 찾음.")
                    else:
                        logger.debug("DB 2차 쿼리(fallback): 다른 이름 필드에서도 찾을 수 없음.")

                # --- 찾은 결과(row) 처리 ---
                if row:
                    db_info = {
                        "name": row["inner_name_kr"],
                        "name2": row["inner_name_en"],
                        "thumb": row["profile_img_view"]
                    }
                    # name2 필드 정리
                    if db_info["name2"]:
                        match = re.match(r"^(.*?)\s*\(.*\)$", db_info["name2"])
                        if match: db_info["name2"] = match.group(1).strip()

                    # 필수 정보 (한국어 이름, 썸네일) 유효성 검사
                    if db_info.get("name") and db_info.get("thumb"):
                        logger.info(f"DB에서 '{originalname}' 유효 정보 찾음 (이름, 썸네일 존재).")
                        info = db_info
                        info["site"] = "avdbs_db"
                        db_found_valid = True # 유효 정보 찾음 플래그 설정
                    else:
                        logger.debug(f"DB에서 '{originalname}' 행은 찾았으나, 필수 정보(한국어 이름, 썸네일) 부족.")
                # else: # row가 없는 경우는 이미 위에서 로깅됨

            except sqlite3.Error as e:
                logger.error(f"DB 조회 중 오류 발생 (originalname='{originalname}'): {e}")
            except Exception as e_db:
                logger.exception(f"DB 처리 중 예상치 못한 오류 (originalname='{originalname}'): {e_db}")
            finally:
                if conn: conn.close()
        else:
            logger.warning(f"Avdbs 데이터베이스 파일 없음: {DB_PATH}. 웹 스크래핑 시도.")

        # --- 단계 2: DB에서 유효 정보를 못 찾았으면 웹 스크래핑 시도 ---
        if not db_found_valid:
            logger.info(f"DB 조회 실패 또는 정보 부족, 웹 스크래핑 시도 (fallback): '{originalname}'")
            retry = kwargs.pop("retry", True)
            web_info = None
            try:
                # 웹 스크래핑 내부 메소드 호출 (__get_actor_info_from_web은 이전 답변 내용 그대로 사용)
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
                    logger.info(f"WEB: 웹 스크래핑으로 '{originalname}' 정보 찾음.")
                    info = web_info # 웹 정보를 최종 정보로 사용
                else:
                    logger.info(f"WEB: 웹 스크래핑으로도 '{originalname}' 정보 찾지 못함.")

        # --- 최종 결과 처리 ---
        if info is not None:
            # 비어있는 값 None 처리
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
