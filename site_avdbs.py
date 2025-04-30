# site_avdbs.py

import time
import requests
from lxml import html
import json # JSONDecodeError 를 명시적으로 처리하기 위해 import

# lib_metadata 공용 요소들 (실제 경로에 맞게 조정 필요)
from .plugin import P
from .site_util import SiteUtil

logger = P.logger

# 사이트 차단 주석은 여전히 유효할 수 있음

class SiteAvdbs:
    site_char = "A"
    site_name = "avdbs"
    base_url = "https://www.avdbs.com" # 클래스 변수로 정의

    @staticmethod
    def __get_actor_info(originalname, proxy_url=None, image_mode="0") -> dict:
        """Avdbs에서 배우 정보를 가져오는 내부 메소드"""

        # 사용할 세션 객체 생성
        with requests.Session() as s:
            # --- 헤더 설정: 브라우저와 유사하게 설정 시도 ---
            browser_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36", # 최신 UA 예시
                "Accept": "application/json, text/javascript, */*; q=0.01", # API 요청 시 일반적인 Accept
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": SiteAvdbs.base_url + "/", # 기본 Referer
                "X-Requested-With": "XMLHttpRequest", # AJAX 요청임을 명시
                # 필요시 브라우저 개발자 도구에서 복사한 다른 헤더 추가 가능
            }
            s.headers.update(browser_headers)

            # 프록시 설정
            if proxy_url:
                s.proxies.update({"http": proxy_url, "https": proxy_url})

            # --- 단계 1: 초기 접속 (쿠키 획득 목적) ---
            try:
                logger.debug(f"Making initial request to {SiteAvdbs.base_url}")
                init_resp = s.get(SiteAvdbs.base_url, timeout=10)
                init_resp.raise_for_status()
                logger.debug(f"Initial request successful. Cookies: {s.cookies.get_dict()}")
            except requests.exceptions.RequestException as e_init:
                logger.warning(f"Initial request to {SiteAvdbs.base_url} failed, but continuing: {e_init}")
            except Exception as e_other_init:
                logger.warning(f"Unexpected error during initial request: {e_other_init}")

            # --- 단계 2: 검색 로그 API 호출 및 응답 확인 ---
            seq = None
            log_api_url = SiteAvdbs.base_url + "/w2017/api/iux_kwd_srch_log2.php"
            log_api_params = {"op": "srch", "kwd": originalname}
            try:
                logger.debug(f"Requesting log API: {log_api_url} with params: {log_api_params}")
                # API 요청 시 Referer는 검색 페이지 또는 메인 페이지일 수 있음
                s.headers['Referer'] = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php" # 검색 페이지 Referer 시도
                response_log_api = s.get(log_api_url, params=log_api_params, timeout=10)
                response_log_api.raise_for_status() # HTTP 오류 확인

                # --- 실제 응답 로깅 ---
                response_text = response_log_api.text
                logger.info(f"Response text from log API ({log_api_url}) for '{originalname}':\n{response_text}") # INFO 레벨로 변경하여 확인 용이

                # JSON 파싱 시도
                json_data = response_log_api.json()
                seq = json_data.get("seq") # .get() 사용하여 키 오류 방지
                if seq is None:
                    logger.error(f"Key 'seq' not found in JSON response. Full JSON: {json_data}")
                    return None # seq 없으면 진행 불가

                logger.debug(f"Successfully obtained seq: {seq}")

            except requests.exceptions.JSONDecodeError as e_json:
                logger.error(f"Failed to decode JSON from log API. Response text was logged above.")
                logger.error(f"JSONDecodeError details: {e_json}")
                return None # JSON 파싱 실패 시 종료
            except requests.exceptions.RequestException as e_req:
                logger.error(f"Request failed for log API {log_api_url}: {e_req}")
                return None # 요청 실패 시 종료
            except Exception as e_other:
                logger.exception(f"Unexpected error processing log API response: {e_other}")
                return None # 기타 예외 발생 시 종료

            # --- 단계 3: 실제 배우 검색 페이지 요청 ---
            search_page_url = SiteAvdbs.base_url + "/w2017/page/search/search_actor.php"
            search_page_params = {"kwd": originalname, "seq": seq} # 얻은 seq 사용
            try:
                logger.debug(f"Requesting search page: {search_page_url} with params: {search_page_params}")
                # 검색 페이지 요청 시 Referer는 이전 API 또는 메인 페이지일 수 있음
                s.headers['Referer'] = log_api_url # 로그 API를 Referer로 설정 시도
                s.headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8' # HTML 페이지 용 Accept
                response_search_page = s.get(search_page_url, params=search_page_params, timeout=15)
                response_search_page.raise_for_status()

                # 검색 결과 페이지 HTML 로깅 (디버깅용, 필요시 활성화)
                search_page_html = response_search_page.text
                # logger.debug(f"Search page HTML for '{originalname}':\n{search_page_html[:2000]}...")

                # HTML 파싱
                tree = html.fromstring(search_page_html)

            except requests.exceptions.RequestException as e_req:
                logger.error(f"Request failed for search page {search_page_url}: {e_req}")
                return None
            except Exception as e_parse:
                logger.error(f"Failed to parse search page HTML: {e_parse}")
                return None

        # --- 단계 4: 검색 결과 파싱 및 처리 ---
        try:
            img_src = tree.xpath(".//img/@src")
            if not img_src:
                logger.debug("No actor images found on search results page for: %s", originalname)
                return None

            e_names = tree.xpath('//p[starts-with(@class, "e_name")]/a')
            k_names = tree.xpath('//p[starts-with(@class, "k_name")]/a')

            if len(img_src) != len(e_names) or len(img_src) != len(k_names):
                logger.warning(f"Mismatch in result counts: img({len(img_src)}), e_name({len(e_names)}), k_name({len(k_names)}) for '{originalname}'")
                # 개수가 안 맞아도 zip은 짧은 쪽 기준으로 동작하므로 일단 진행

            for idx, (e_name_tag, k_name_tag, img_url) in enumerate(zip(e_names, k_names, img_src)):
                try:
                    e_name_text = e_name_tag.text_content().strip()
                    k_name_text = k_name_tag.text_content().strip()

                    # 영어 이름에서 일본어 이름 추출
                    names = [x.strip().strip("()").strip("（）") for x in e_name_text.split("(")] # 전각 괄호도 제거
                    if len(names) < 1: # 이름 형식이 예상과 다를 수 있음
                        logger.debug(f"Cannot parse Japanese name from e_name: '{e_name_text}'")
                        continue

                    # 마지막 부분이 일본어 이름이라고 가정 (더 확실한 방법이 필요할 수 있음)
                    name_ja = names[-1]
                    name_en = names[0] if len(names) > 1 else "" # 영어 이름 (없을 수도 있음)

                    # 입력된 이름과 비교
                    if name_ja == originalname or f"（{originalname}）" in e_name_text: # 괄호 포함 비교도 추가
                        logger.debug(f"Match found for '{originalname}' at index {idx}: ja='{name_ja}', ko='{k_name_text}', en='{name_en}'")

                        # 이미지 URL 처리 (SiteUtil 사용)
                        try:
                            processed_thumb = SiteUtil.process_image_mode(image_mode, img_url, proxy_url=proxy_url)
                        except NameError:
                            logger.error("SiteUtil not available for image processing")
                            processed_thumb = img_url # 원본 사용
                        except Exception as e_img:
                            logger.error(f"Error processing image {img_url}: {e_img}")
                            processed_thumb = img_url # 오류 시 원본 사용

                        return {
                            "name": k_name_text,
                            "name2": name_en, # 영어 이름 필드 (없으면 빈 문자열)
                            "site": "avdbs",
                            "thumb": processed_thumb,
                        }
                    else:
                        logger.debug(f"No match at index {idx}: '{name_ja}' != '{originalname}'")

                except Exception as e_item:
                    logger.exception(f"Error processing item at index {idx}: {e_item}")
                    continue # 개별 아이템 오류 시 다음 아이템으로

            logger.debug("No matching actor found in search results for: %s", originalname)
            return None # 루프 종료 후에도 못 찾음

        except Exception as e_parse_results:
            logger.exception(f"Error parsing search results: {e_parse_results}")
            return None


    @staticmethod
    def get_actor_info(entity_actor, **kwargs):
        """배우 정보를 가져오는 공개 메소드 (재시도 로직 포함)"""
        retry = kwargs.pop("retry", True) # 재시도 옵션 가져오기 (기본 True)
        originalname = entity_actor.get("originalname")
        if not originalname:
            logger.warning("Cannot search actor info: originalname is missing.")
            return entity_actor

        info = None
        try:
            # 내부 메소드 호출
            info = SiteAvdbs.__get_actor_info(originalname, **kwargs)

        except Exception as e:
            # 재시도 로직
            if retry:
                logger.warning(f"Exception occurred for '{originalname}', retrying after 2 seconds... Error: {e}")
                time.sleep(2)
                # 재시도 시에는 retry=False 전달
                return SiteAvdbs.get_actor_info(entity_actor, retry=False, **kwargs)
            else:
                # 최종 실패 시 로깅
                logger.exception(f"Failed to update actor info for '{originalname}' after retry. Error: {e}")
        else:
            # 성공 시 정보 업데이트
            if info is not None:
                logger.info(f"Successfully updated actor info for '{originalname}' from avdbs.")
                entity_actor.update(info)
            else:
                logger.info(f"No actor info found on avdbs for '{originalname}'.")

        return entity_actor # 업데이트된 (또는 그대로인) entity_actor 반환
