# site_avdbs.py

import time
import requests
from lxml import html
import json

# lib_metadata 공용 요소들
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

class SiteAvdbs:
    site_char = "A"
    site_name = "avdbs"
    base_url = "https://www.avdbs.com"

    @staticmethod
    def __get_actor_info(originalname, proxy_url=None, image_mode="0") -> dict:
        """Avdbs에서 배우 정보를 가져오는 내부 메소드 (헤더 보강)"""

        with requests.Session() as s:
            # --- 헤더 설정: 브라우저 헤더 최대한 모방 ---
            enhanced_headers = {
                # User-Agent: 일반적인 최신 데스크톱 브라우저 UA
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36", # 특정 버전 명시
                # Accept: 다양한 콘텐츠 타입 수용
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
                # Accept-Language: 한국어 우선, 영어 차선
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                # Accept-Encoding: 서버가 지원하는 압축 방식 명시 (requests가 자동으로 처리해주기도 함)
                "Accept-Encoding": "gzip, deflate, br",
                # Referer: 초기 요청 시에는 보통 없음, 이후 요청 시 설정
                # "Referer": SiteAvdbs.base_url + "/", # 요청 직전에 설정하는 것이 더 적절
                # Connection: Keep-Alive 요청
                "Connection": "keep-alive",
                # Upgrade-Insecure-Requests: HTTPS 선호 알림
                "Upgrade-Insecure-Requests": "1",
                # Sec-Fetch-... 헤더들: 브라우저의 요청 맥락 정보 (일부 서버에서 검사)
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin", # 또는 "none" (초기 요청 시)
                "Sec-Fetch-User": "?1",
                # Sec-CH-UA 헤더들: User-Agent Client Hints (최신 브라우저 특징)
                "Sec-CH-UA": '"Chromium";v="110", "Not A(Brand";v="24", "Google Chrome";v="110"',
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Windows"', # 또는 "macOS", "Linux" 등 환경에 맞게
                # DNT (Do Not Track): 보통 1로 설정
                "DNT": "1",
                # Cache-Control: 캐시 동작 제어
                "Cache-Control": "max-age=0",
            }
            s.headers.update(enhanced_headers)

            if proxy_url:
                s.proxies.update({"http": proxy_url, "https": proxy_url})

            # --- 단계 1: 초기 접속 ---
            try:
                logger.debug(f"Making initial request to {SiteAvdbs.base_url} with enhanced headers.")
                # 초기 접속 시 Referer는 없고, Sec-Fetch-Site는 'none'일 수 있음
                initial_req_headers = enhanced_headers.copy()
                initial_req_headers.pop('Referer', None)
                initial_req_headers['Sec-Fetch-Site'] = 'none'
                init_resp = s.get(SiteAvdbs.base_url, headers=initial_req_headers, timeout=10)
                init_resp.raise_for_status()
                logger.debug(f"Initial request successful. Cookies: {s.cookies.get_dict()}")
            except requests.exceptions.RequestException as e_init:
                logger.warning(f"Initial request to {SiteAvdbs.base_url} failed, but continuing: {e_init}")
                # 403 오류 시에도 다음 단계 진행 (API가 다르게 반응할 수도 있으므로)
            except Exception as e_other_init:
                logger.warning(f"Unexpected error during initial request: {e_other_init}")

            # --- 단계 2: 검색 로그 API 호출 ---
            seq = None
            log_api_url = SiteAvdbs.base_url + "/w2017/api/iux_kwd_srch_log2.php"
            log_api_params = {"op": "srch", "kwd": originalname}
            try:
                logger.debug(f"Requesting log API: {log_api_url} with params: {log_api_params}")
                # API 요청 헤더 수정 (Accept, X-Requested-With 등 AJAX 요청에 맞게)
                api_req_headers = enhanced_headers.copy()
                api_req_headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
                api_req_headers['Referer'] = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php" # 검색 페이지 Referer
                api_req_headers['Sec-Fetch-Dest'] = 'empty'
                api_req_headers['Sec-Fetch-Mode'] = 'cors' # API 요청 시 종종 사용됨
                api_req_headers['Sec-Fetch-Site'] = 'same-origin'
                api_req_headers.pop('Sec-Fetch-User', None) # AJAX 요청에는 보통 없음
                api_req_headers['X-Requested-With'] = 'XMLHttpRequest'

                response_log_api = s.get(log_api_url, params=log_api_params, headers=api_req_headers, timeout=10)
                response_log_api.raise_for_status()
                response_text = response_log_api.text
                logger.info(f"Response text from log API ({log_api_url}) for '{originalname}':\n{response_text}")

                json_data = response_log_api.json()
                seq = json_data.get("seq")
                if seq is None:
                    logger.error(f"Key 'seq' not found in JSON response. Full JSON: {json_data}")
                    return None
                logger.debug(f"Successfully obtained seq: {seq}")

            except requests.exceptions.JSONDecodeError as e_json:
                logger.error(f"Failed to decode JSON from log API. Response text was logged above.")
                logger.error(f"JSONDecodeError details: {e_json}")
                return None
            except requests.exceptions.RequestException as e_req:
                logger.error(f"Request failed for log API {log_api_url}: {e_req}")
                # 403 오류 시 응답 내용 로깅 추가
                if hasattr(e_req, 'response') and e_req.response is not None:
                    logger.error(f"Log API Failed Response Status: {e_req.response.status_code}")
                    logger.error(f"Log API Failed Response Content: {e_req.response.text[:500]}")
                return None
            except Exception as e_other:
                logger.exception(f"Unexpected error processing log API response: {e_other}")
                return None

            # --- 단계 3: 실제 배우 검색 페이지 요청 ---
            search_page_url = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php"
            search_page_params = {"kwd": originalname, "seq": seq}
            try:
                logger.debug(f"Requesting search page: {search_page_url} with params: {search_page_params}")
                # 검색 페이지 요청 헤더 (초기 접속 헤더와 유사하게)
                page_req_headers = enhanced_headers.copy()
                page_req_headers['Referer'] = log_api_url # 로그 API가 Referer
                # page_req_headers['Sec-Fetch-Site'] = 'same-origin' # 이미 설정됨

                response_search_page = s.get(search_page_url, params=search_page_params, headers=page_req_headers, timeout=15)
                response_search_page.raise_for_status()
                search_page_html = response_search_page.text
                # logger.debug(f"Search page HTML for '{originalname}':\n{search_page_html[:2000]}...")
                tree = html.fromstring(search_page_html)

            except requests.exceptions.RequestException as e_req:
                logger.error(f"Request failed for search page {search_page_url}: {e_req}")
                if hasattr(e_req, 'response') and e_req.response is not None:
                    logger.error(f"Search Page Failed Response Status: {e_req.response.status_code}")
                    logger.error(f"Search Page Failed Response Content: {e_req.response.text[:500]}")
                return None
            except Exception as e_parse:
                logger.error(f"Failed to parse search page HTML: {e_parse}")
                return None

            # --- 단계 4: 검색 결과 파싱 및 처리 (이전과 동일) ---
            # ... (try...except 블록 포함하여 이전 코드 내용 복사) ...
            try:
                img_src = tree.xpath(".//img/@src")
                if not img_src:
                    logger.debug("No actor images found on search results page for: %s", originalname)
                    return None

                e_names = tree.xpath('//p[starts-with(@class, "e_name")]/a')
                k_names = tree.xpath('//p[starts-with(@class, "k_name")]/a')

                if len(img_src) != len(e_names) or len(img_src) != len(k_names):
                    logger.warning(f"Mismatch in result counts: img({len(img_src)}), e_name({len(e_names)}), k_name({len(k_names)}) for '{originalname}'")

                for idx, (e_name_tag, k_name_tag, img_url) in enumerate(zip(e_names, k_names, img_src)):
                    try:
                        e_name_text = e_name_tag.text_content().strip()
                        k_name_text = k_name_tag.text_content().strip()
                        names = [x.strip().strip("()").strip("（）") for x in e_name_text.split("(")]
                        if len(names) < 1: continue
                        name_ja = names[-1]
                        name_en = names[0] if len(names) > 1 else ""

                        if name_ja == originalname or f"（{originalname}）" in e_name_text:
                            logger.debug(f"Match found for '{originalname}' at index {idx}: ja='{name_ja}', ko='{k_name_text}', en='{name_en}'")
                            try:
                                processed_thumb = SiteUtil.process_image_mode(image_mode, img_url, proxy_url=proxy_url)
                            except NameError: processed_thumb = img_url
                            except Exception as e_img: logger.error(f"Error processing image {img_url}: {e_img}"); processed_thumb = img_url

                            return {
                                "name": k_name_text, "name2": name_en, "site": "avdbs", "thumb": processed_thumb,
                            }
                        else:
                            logger.debug(f"No match at index {idx}: '{name_ja}' != '{originalname}'")
                    except Exception as e_item:
                        logger.exception(f"Error processing item at index {idx}: {e_item}")
                        continue
                logger.debug("No matching actor found in search results for: %s", originalname)
                return None
            except Exception as e_parse_results:
                logger.exception(f"Error parsing search results: {e_parse_results}")
                return None

    @staticmethod
    def get_actor_info(entity_actor, **kwargs):
        # 이 메소드는 수정 불필요 (내부 __get_actor_info 호출)
        retry = kwargs.pop("retry", True)
        originalname = entity_actor.get("originalname")
        if not originalname:
            logger.warning("Cannot search actor info: originalname is missing.")
            return entity_actor

        info = None
        try:
            info = SiteAvdbs.__get_actor_info(originalname, **kwargs)
        except Exception as e:
            if retry:
                logger.warning(f"Exception occurred for '{originalname}', retrying after 2 seconds... Error: {e}")
                time.sleep(2)
                return SiteAvdbs.get_actor_info(entity_actor, retry=False, **kwargs)
            else:
                logger.exception(f"Failed to update actor info for '{originalname}' after retry. Error: {e}")
        else:
            if info is not None:
                logger.info(f"Successfully updated actor info for '{originalname}' from avdbs.")
                entity_actor.update(info)
            else:
                logger.info(f"No actor info found on avdbs for '{originalname}'.")
        return entity_actor