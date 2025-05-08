import json
import os
import re
import time
from datetime import timedelta
from io import BytesIO

import requests
from framework import SystemModelSetting  # pylint: disable=import-error
from framework import path_data, py_urllib  # pylint: disable=import-error
from framework.util import Util  # pylint: disable=import-error
from lxml import html
from PIL import Image

from .cache_util import CacheUtil
from .constants import (AV_GENRE, AV_GENRE_IGNORE_JA, AV_GENRE_IGNORE_KO,
                        AV_STUDIO, COUNTRY_CODE_TRANSLATE, GENRE_MAP)
from .discord import DiscordUtil
from .entity_base import EntityActor, EntityThumb
from .plugin import P
from .trans_util import TransUtil

logger = P.logger


class SiteUtil:
    try:
        from requests_cache import CachedSession

        session = CachedSession(
            "lib_metadata",
            use_temp=True,
            expire_after=timedelta(hours=6),
        )
    except Exception as e:
        logger.warning("requests cache 사용 안함: %s", e)
        session = requests.Session()

    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.82 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        # 'Cookie' : 'over18=1;age_check_done=1;',
    }

    av_genre = AV_GENRE
    av_genre_ignore_ja = AV_GENRE_IGNORE_JA
    av_genre_ignore_ko = AV_GENRE_IGNORE_KO
    av_studio = AV_STUDIO
    country_code_translate = COUNTRY_CODE_TRANSLATE
    genre_map = GENRE_MAP

    PTN_SPECIAL_CHAR = re.compile(r"[-=+,#/\?:^$.@*\"※~&%ㆍ!』\\‘|\(\)\[\]\<\>`'…》]")
    PTN_HANGUL_CHAR = re.compile(r"[ㄱ-ㅣ가-힣]+")

    @classmethod
    def get_tree(cls, url, **kwargs):
        text = cls.get_text(url, **kwargs)
        # logger.debug(text)
        if text is None:
            return text
        return html.fromstring(text)

    @classmethod
    def get_text(cls, url, **kwargs):
        res = cls.get_response(url, **kwargs)
        # logger.debug('url: %s, %s', res.status_code, url)
        # if res.status_code != 200:
        #    return None
        return res.text

    @classmethod
    def get_response(cls, url, **kwargs):
        proxy_url = kwargs.pop("proxy_url", None)
        if proxy_url:
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

        kwargs.setdefault("headers", cls.default_headers)

        method = kwargs.pop("method", "GET")
        post_data = kwargs.pop("post_data", None)
        if post_data:
            method = "POST"
            kwargs["data"] = post_data

        # temporary fix to bypass blocked image url
        if "javbus.com" in url:
            kwargs.setdefault("headers", {})
            kwargs["headers"]["referer"] = "https://www.javbus.com/"

        res = cls.session.request(method, url, **kwargs)
        # logger.debug(res.headers)
        # logger.debug(res.text)
        return res


    @classmethod
    def get_mgs_half_pl_poster_info_local(cls, ps_url: str, pl_url: str, proxy_url: str = None):
        """
        MGStage용으로 pl 이미지를 특별 처리합니다. (로컬 임시 파일 사용, Fallback 강화)
        pl 이미지를 가로로 반으로 자르고 (오른쪽 우선), 각 절반의 중앙 부분을 ps와 비교합니다.
        is_hq_poster 검사 성공 시 해당 결과를 사용하고,
        검사 실패 시에는 성공적으로 크롭된 첫 번째 후보(오른쪽 우선)를 사용합니다.
        """
        try:
            logger.debug(f"MGS Special Local: Trying get_mgs_half_pl_poster_info_local for ps='{ps_url}', pl='{pl_url}'")
            if not ps_url or not pl_url: return None, None, None

            ps_image = cls.imopen(ps_url, proxy_url=proxy_url)
            pl_image_original = cls.imopen(pl_url, proxy_url=proxy_url)

            if ps_image is None or pl_image_original is None:
                logger.debug("MGS Special Local: Failed to open ps_image or pl_image_original.")
                return None, None, None

            pl_width, pl_height = pl_image_original.size
            if pl_width < pl_height * 1.1:
                logger.debug(f"MGS Special Local: pl_image_original not wide enough ({pl_width}x{pl_height}). Skipping.")
                return None, None, None

            # 최종 결과를 저장할 변수들
            result_filepath = None
            fallback_candidate_obj = None # is_hq_poster 실패 시 사용할 첫 번째 성공 크롭 객체

            # 처리 순서 정의: 오른쪽 먼저
            candidate_sources = []
            # 오른쪽 절반
            right_half_box = (pl_width / 2, 0, pl_width, pl_height)
            right_half_img_obj = pl_image_original.crop(right_half_box)
            if right_half_img_obj: candidate_sources.append( (right_half_img_obj, f"{pl_url} (right_half)") )
            # 왼쪽 절반
            left_half_box = (0, 0, pl_width / 2, pl_height)
            left_half_img_obj = pl_image_original.crop(left_half_box)
            if left_half_img_obj: candidate_sources.append( (left_half_img_obj, f"{pl_url} (left_half)") )

            for img_obj_to_crop, obj_name in candidate_sources:
                logger.debug(f"MGS Special Local: Processing candidate source: {obj_name}")
                # 중앙 크롭 시도
                center_cropped_candidate_obj = cls.imcrop(img_obj_to_crop, position='c') 

                if center_cropped_candidate_obj:
                    logger.debug(f"MGS Special Local: Successfully cropped center from {obj_name}.")
                    
                    # is_hq_poster 유사도 검사 시도
                    logger.debug(f"MGS Special Local: Comparing ps_image with cropped candidate from {obj_name}")
                    is_similar = cls.is_hq_poster(ps_image, center_cropped_candidate_obj)

                    if is_similar:
                        logger.info(f"MGS Special Local: Similarity check PASSED for {obj_name}. This is the best match.")
                        # 성공! 이 객체를 저장하고 반환
                        try:
                            # 임시 파일 저장 로직 (이전과 동일)
                            img_format = center_cropped_candidate_obj.format if center_cropped_candidate_obj.format else "JPEG"
                            ext = img_format.lower().replace("jpeg", "jpg")
                            if ext not in ['jpg', 'png', 'webp']: ext = 'jpg'
                            temp_filename = f"mgs_temp_poster_{int(time.time())}_{os.urandom(4).hex()}.{ext}"
                            temp_filepath = os.path.join(path_data, "tmp", temp_filename)
                            os.makedirs(os.path.join(path_data, "tmp"), exist_ok=True)
                            save_params = {}
                            if ext in ['jpg', 'webp']: save_params['quality'] = 95
                            elif ext == 'png': save_params['optimize'] = True
                            center_cropped_candidate_obj.save(temp_filepath, **save_params)
                            logger.info(f"MGS Special Local: Saved similarity match to temp file: {temp_filepath}")
                            return temp_filepath, None, pl_url # 성공 반환 (파일경로, crop=None, 원본pl)
                        except Exception as e_save_hq:
                            logger.exception(f"MGS Special Local: Failed to save HQ similarity match from {obj_name}: {e_save_hq}")
                            # 저장 실패 시 루프 계속 진행 (fallback 가능성 고려)
                    
                    else: # is_hq_poster 검사 실패
                        logger.info(f"MGS Special Local: Similarity check FAILED for {obj_name}.")
                        # fallback 후보로 저장 (아직 fallback 후보가 없다면)
                        if fallback_candidate_obj is None:
                            logger.debug(f"MGS Special Local: Storing cropped candidate from {obj_name} as fallback.")
                            fallback_candidate_obj = center_cropped_candidate_obj
                        # 루프 계속 (왼쪽 절반에서 더 좋은 결과(is_similar=True)가 나올 수 있으므로)
                
                else: # 크롭 자체 실패
                    logger.warning(f"MGS Special Local: Failed to crop center from {obj_name}.")
            
            # 루프 종료 후: is_hq_poster 매칭이 없었는지 확인
            if result_filepath is None: # is_hq_poster 성공 케이스가 없었음
                if fallback_candidate_obj: # fallback 후보가 있다면 사용
                    logger.info("MGS Special Local: No similarity match found, using the first successfully cropped candidate as fallback.")
                    try:
                        # Fallback 후보 저장
                        img_format = fallback_candidate_obj.format if fallback_candidate_obj.format else "JPEG"
                        ext = img_format.lower().replace("jpeg", "jpg")
                        if ext not in ['jpg', 'png', 'webp']: ext = 'jpg'
                        temp_filename = f"mgs_temp_poster_fallback_{int(time.time())}_{os.urandom(4).hex()}.{ext}"
                        temp_filepath = os.path.join(path_data, "tmp", temp_filename)
                        os.makedirs(os.path.join(path_data, "tmp"), exist_ok=True)
                        save_params = {}
                        if ext in ['jpg', 'webp']: save_params['quality'] = 95
                        elif ext == 'png': save_params['optimize'] = True
                        fallback_candidate_obj.save(temp_filepath, **save_params)
                        logger.info(f"MGS Special Local: Saved fallback candidate to temp file: {temp_filepath}")
                        return temp_filepath, None, pl_url # Fallback 성공 반환
                    except Exception as e_save_fb:
                        logger.exception(f"MGS Special Local: Failed to save fallback candidate: {e_save_fb}")
                        # Fallback 저장 실패 시 최종 실패
                else: # Fallback 후보조차 없음 (양쪽 다 크롭 실패)
                    logger.warning("MGS Special Local: Cropping failed for both halves. Cannot provide fallback.")
            
            # 최종적으로 아무것도 반환되지 못했다면 실패
            logger.warning("MGS Special Local: Failed to find or create a suitable poster.")
            return None, None, None

        except Exception as e:
            logger.exception(f"MGS Special Local: Error in get_mgs_half_pl_poster_info_local: {e}")
            return None, None, None


    # 도메인별로 마지막 세션 쿠키 획득 시간 추적 (간단 캐싱)
    _last_cookie_fetch_time = {}
    _cookie_fetch_interval = 60 * 5 # 예: 5분마다 쿠키 갱신 시도

    @classmethod
    def imopen(cls, img_src, proxy_url=None, referer=None):
        if isinstance(img_src, Image.Image):
            return img_src
        try:
            # 로컬 파일 처리
            return Image.open(img_src)
        except (FileNotFoundError, OSError):
            # 원격 URL 처리
            try:
                # --- 쿠키 획득 시도 (Jav321 등 특정 사이트 대상) ---
                needs_cookie_check = False
                target_domain = None
                if isinstance(img_src, str):
                    if 'jav321.com' in img_src: 
                        needs_cookie_check = True; target_domain = 'jav321.com'; target_base_url = 'https://www.jav321.com/'
                    # 필요시 다른 사이트 추가 (예: javbus.com)
                    elif 'javbus.com' in img_src:
                        needs_cookie_check = True; target_domain = 'javbus.com'; target_base_url = 'https://www.javbus.com/'
                
                if needs_cookie_check and target_domain:
                    current_time = time.time()
                    last_fetch = cls._last_cookie_fetch_time.get(target_domain, 0)
                    # 일정 시간이 지났으면 쿠키 갱신 시도
                    if current_time - last_fetch > cls._cookie_fetch_interval:
                        logger.debug(f"Attempting to fetch/refresh cookies for {target_domain}...")
                        try:
                            # 메인 페이지 방문하여 쿠키 획득 시도
                            cookie_headers = cls.default_headers.copy()
                            cookie_headers['Referer'] = target_base_url # 메인 페이지 Referer
                            cls.get_response(target_base_url, method='GET', proxy_url=proxy_url, headers=cookie_headers, timeout=10)
                            cls._last_cookie_fetch_time[target_domain] = current_time # 마지막 획득 시간 기록
                            logger.debug(f"Cookie fetch attempt done for {target_domain}.")
                        except Exception as e_cookie:
                            logger.warning(f"Failed to fetch cookies for {target_domain}: {e_cookie}")
                            # 실패해도 일단 진행 (기존 쿠키가 유효할 수 있음)
                # --- 쿠키 획득 시도 끝 ---

                # --- 이미지 요청 헤더 설정 ---
                req_headers = cls.default_headers.copy()
                if referer: 
                    req_headers['Referer'] = referer
                elif target_base_url: # 위에서 설정된 target_base_url 사용 (더 구체적인 Referer)
                    req_headers['Referer'] = target_base_url
                # --- 헤더 설정 끝 ---

                # 이미지 요청 (세션은 SiteUtil.session 사용)
                res = cls.get_response(img_src, proxy_url=proxy_url, headers=req_headers)

                if res.status_code != 200:
                    logger.warning(f"imopen: Received status code {res.status_code} for {img_src}")
                    # return None
                content_type = res.headers.get('Content-Type', '').lower()
                if not content_type.startswith('image/'):
                    logger.warning(f"imopen: Received non-image Content-Type '{content_type}' for {img_src}")
                    # return None

                return Image.open(BytesIO(res.content))
            except Exception as e_remote: 
                logger.exception(f"이미지 URL 열기 중 예외 ({img_src}): {e_remote}")
                return None


    @classmethod
    def imcrop(cls, im, position=None, box_only=False):
        """원본 이미지에서 잘라내 세로로 긴 포스터를 만드는 함수"""

        if not isinstance(im, Image.Image):
            return im
        width, height = im.size
        new_w = height / 1.4225
        if position == "l":
            left = 0
        elif position == "c":
            left = (width - new_w) / 2
        else:
            # default: from right
            left = width - new_w
        box = (left, 0, left + new_w, height)
        if box_only:
            return box
        return im.crop(box)

    @classmethod
    def resolve_jav_imgs(cls, img_urls: dict, ps_to_poster: bool = False, proxy_url: str = None, crop_mode: str = None):
        ps = img_urls["ps"]  # poster small
        pl = img_urls["pl"]  # poster large
        arts = img_urls["arts"]  # arts

        # poster 기본값
        poster = ps if ps_to_poster else ""
        poster_crop = None

        if not poster and arts:
            if cls.is_hq_poster(ps, arts[0], proxy_url=proxy_url):
                # first art to poster
                poster = arts[0]
            elif len(arts) > 1 and cls.is_hq_poster(ps, arts[-1], proxy_url=proxy_url):
                # last art to poster
                poster = arts[-1]
        if not poster and pl:
            if cls.is_hq_poster(ps, pl, proxy_url=proxy_url):
                # pl이 세로로 큰 이미지
                poster = pl
            elif crop_mode is not None:
                # 사용자 설정에 따름
                poster, poster_crop = pl, crop_mode
            else:
                loc = cls.has_hq_poster(ps, pl, proxy_url=proxy_url)
                if loc:
                    # pl의 일부를 crop해서 포스터로...
                    poster, poster_crop = pl, loc
        if not poster:
            # 그래도 없으면...
            poster = ps

        # # first art to landscape
        # if arts and not pl:
        #     pl = arts.pop(0)

        img_urls.update(
            {
                "poster": poster,
                "poster_crop": poster_crop,
                "landscape": pl,
            }
        )

    @classmethod
    def process_jav_imgs(cls, image_mode: str, img_urls: dict, proxy_url: str = None):
        thumbs = []

        landscape = img_urls["landscape"]
        if landscape:
            _url = cls.process_image_mode(image_mode, landscape, proxy_url=proxy_url)
            thumbs.append(EntityThumb(aspect="landscape", value=_url))

        poster, poster_crop = img_urls["poster"], img_urls["poster_crop"]
        if poster:
            _url = cls.process_image_mode(image_mode, poster, proxy_url=proxy_url, crop_mode=poster_crop)
            thumbs.append(EntityThumb(aspect="poster", value=_url))

        return thumbs


    @classmethod
    def process_image_mode(cls, image_mode, image_source, proxy_url=None, crop_mode=None):
        # logger.debug('process_image_mode : %s %s', image_mode, image_url)
        if image_source is None:
            return

        source_is_url = isinstance(image_source, str) and not os.path.exists(image_source) # 로컬 파일 경로가 아닌 URL
        source_is_local_file = isinstance(image_source, str) and os.path.exists(image_source) # 로컬 파일 경로

        # 로깅 및 파일명 생성용 기본 이름
        log_name = image_source
        if source_is_local_file:
            log_name = f"localfile:{os.path.basename(image_source)}"
        
        logger.debug(f"process_image_mode: mode='{image_mode}', source='{log_name}', crop='{crop_mode}'")

        if image_mode == "0": # 원본 사용
            if source_is_url or source_is_local_file: # URL 또는 파일 경로면 그대로 반환
                return image_source 
            else: # PIL 객체 등 기타 타입 (현재 이 함수에서는 URL/파일경로만 가정)
                logger.warning("process_image_mode: Mode 0 called with non-URL/filepath. Returning None.")
                return None

        # image_mode '1', '2' (SJVA URL 프록시)는 image_source가 외부 접근 가능한 URL이어야 함.
        # 로컬 파일은 이 프록시를 직접 사용할 수 없음.
        if image_mode in ["1", "2"]:
            if not source_is_url:
                logger.warning(f"Image mode {image_mode} (SJVA URL Proxy) called with non-URL source '{log_name}'. This mode requires a public URL. Returning original source or None.")
                # 로컬 파일이면 원래 경로 반환 (프록시 안됨), 객체면 None
                return image_source if source_is_local_file else None 
            
            # 기존 URL 기반 프록시 로직
            api_path = "image_proxy" if image_mode == "1" else "discord_proxy"
            tmp = f"{{ddns}}/metadata/api/{api_path}?url=" + py_urllib.quote_plus(image_source) # image_source는 URL
            if proxy_url: tmp += "&proxy_url=" + py_urllib.quote_plus(proxy_url)
            if crop_mode: tmp += "&crop_mode=" + py_urllib.quote_plus(crop_mode)
            return Util.make_apikey(tmp)

        # image_mode '3' (직접 디스코드 업로드 - 로컬 파일도 가능하게 수정)
        # image_mode '5' (로컬 임시파일 생성 후 디스코드 업로드)
        # 이 모드들은 최종적으로 PIL Image 객체를 DiscordUtil.proxy_image 등에 전달해야 함.
        if image_mode in ["3", "5"]:
            im_opened = None
            if source_is_url:
                im_opened = cls.imopen(image_source, proxy_url=proxy_url)
            elif source_is_local_file:
                im_opened = cls.imopen(image_source) # 로컬 파일은 프록시 불필요
            
            if im_opened is None:
                logger.warning(f"process_image_mode: Mode {image_mode} failed to open image from '{log_name}'.")
                return image_source # 실패 시 원본 반환 (URL 또는 파일 경로)

            final_im_for_upload = im_opened
            # crop_mode 적용 (URL에서 왔거나, 로컬파일인데 crop_mode가 지정된 경우)
            # get_mgs_half_pl_poster_info_local에서 온 파일은 crop_mode=None으로 전달됨.
            if crop_mode: 
                logger.debug(f"process_image_mode: Mode {image_mode} applying crop_mode '{crop_mode}' to image from '{log_name}'.")
                cropped = cls.imcrop(im_opened, position=crop_mode)
                if cropped: final_im_for_upload = cropped
                else: logger.warning(f"process_image_mode: Mode {image_mode} cropping failed for '{log_name}'. Using uncropped.")
            
            # Mode 5는 항상 로컬 파일을 거침. Mode 3은 직접 PIL 객체를 discord_proxy_image에 전달.
            if image_mode == "5":
                temp_filename_mode5 = f"proxy_mode5_{time.time()}.jpg"
                temp_filepath_mode5 = os.path.join(path_data, "tmp", temp_filename_mode5)
                try:
                    final_im_for_upload.save(temp_filepath_mode5, quality=95)
                    # discord_proxy_image_localfile은 파일 경로를 받아 Discord에 올림
                    return cls.discord_proxy_image_localfile(temp_filepath_mode5) 
                except Exception as e_save5:
                    logger.exception(f"process_image_mode: Mode 5 failed to save/proxy image from '{log_name}': {e_save5}")
                    return image_source # 실패 시 원본
            
            elif image_mode == "3":
                # discord_proxy_image가 PIL 객체를 받을 수 있도록 수정했거나,
                # 아니면 여기서 객체를 임시 저장하고 그 경로를 전달.
                # 여기서는 discord_proxy_image가 객체를 처리한다고 가정.
                # obj_info_str은 디버깅/캐시 등에 사용될 수 있는 문자열.
                return cls.discord_proxy_image(final_im_for_upload, obj_info_str=log_name)

        # 모든 조건에 맞지 않거나 처리 실패 시 원본 반환
        logger.debug(f"process_image_mode: No specific action for mode '{image_mode}' or processing failed. Returning original source: {image_source}")
        return image_source


    @classmethod
    def save_image_to_server_path(cls, image_source, image_type: str, base_path: str, path_segment: str, ui_code: str, art_index: int = None, proxy_url: str = None, crop_mode: str = None):
        """
        이미지를 다운로드하거나 로컬 파일로부터 지정된 로컬 경로에 저장하고, 웹 서버 접근용 상대 경로를 반환합니다.
        기존 파일이 존재하면 덮어씁니다.
        image_source는 URL 문자열 또는 로컬 파일 경로여야 합니다.
        """
        # 1. 필수 인자 유효성 검사
        if not all([image_source, image_type, base_path, path_segment, ui_code]):
            logger.warning("save_image_to_server_path: 필수 인자 누락.")
            return None
        if image_type not in ['ps', 'pl', 'p', 'art']:
            logger.warning(f"save_image_to_server_path: 유효하지 않은 image_type: {image_type}")
            return None
        if image_type == 'art' and art_index is None:
            logger.warning("save_image_to_server_path: image_type='art'일 때 art_index 필요.")
            return None

        # 2. 입력 소스 타입 판별 및 로깅 정보 설정
        source_is_local_file = isinstance(image_source, str) and os.path.exists(image_source)
        source_is_url = not source_is_local_file and isinstance(image_source, str)

        im = None
        log_source_info = ""
        if source_is_url:
            log_source_info = image_source
        elif source_is_local_file:
            log_source_info = f"localfile:{os.path.basename(image_source)}"
        else: # URL이나 로컬 파일 경로가 아닌 경우
            logger.warning(f"save_image_to_server_path: 지원하지 않는 image_source 타입: {type(image_source)}. URL 또는 로컬 파일 경로여야 합니다.")
            return None

        try:
            # 3. 이미지 열기 (URL 또는 로컬 파일)
            if source_is_url:
                im = cls.imopen(image_source, proxy_url=proxy_url)
            elif source_is_local_file:
                im = cls.imopen(image_source) # 로컬 파일

            if im is None:
                logger.warning(f"save_image_to_server_path: 이미지 열기 실패: {log_source_info}")
                return None

            # 4. 확장자 결정 및 지원 포맷 확인/변환
            original_format = im.format
            if not original_format: # format 정보 없을 시 추론
                if source_is_url:
                    ext_match = re.search(r'\.(jpg|jpeg|png|webp|gif)(\?|$)', image_source.lower())
                    if ext_match: original_format = ext_match.group(1).upper()
                elif source_is_local_file:
                    _, file_ext = os.path.splitext(image_source)
                    if file_ext: original_format = file_ext[1:].upper()
                if not original_format: original_format = "JPEG" # 최후 기본값
                logger.debug(f"PIL format missing for '{log_source_info}', deduced/defaulted to: {original_format}")

            ext = original_format.lower().replace("jpeg", "jpg")
            allowed_exts = ['jpg', 'png', 'webp']

            if ext not in allowed_exts:
                logger.warning(f"save_image_to_server_path: 지원하지 않는 이미지 포맷 '{ext}' from {log_source_info}. JPG로 변환 시도.")
                try:
                    # 변환 로직 (RGBA, P 모드 등 고려)
                    if im.mode == 'P':
                        im = im.convert('RGBA' if 'transparency' in im.info else 'RGB')
                    if im.mode == 'RGBA' and ext != 'png': # PNG 외에는 알파 채널 제거
                        im = im.convert('RGB')
                    elif im.mode not in ('RGB', 'L', 'RGBA'): # L(흑백)도 일단 통과
                        im = im.convert('RGB')

                    ext = 'jpg' # JPG로 강제 변환 시 확장자 변경
                    logger.info(f"save_image_to_server_path: 이미지 변환 성공 (to RGB/JPG) for {log_source_info}.")
                except Exception as e_convert:
                    logger.error(f"save_image_to_server_path: 이미지 변환 실패 for {log_source_info}: {e_convert}")
                    return None # 변환 실패 시 저장 불가

            # 5. 저장 경로 및 파일명 결정
            ui_code_parts = ui_code.split('-')
            label_part = ui_code_parts[0].upper() if ui_code_parts else "UNKNOWN"
            first_char = label_part[0] if label_part and label_part[0].isalpha() else '09'
            save_dir = os.path.join(base_path, path_segment, first_char, label_part)

            if image_type == 'art':
                filename = f"{ui_code.lower()}_art_{art_index}.{ext}"
            else: # ps, pl, p
                filename = f"{ui_code.lower()}_{image_type}.{ext}"
            save_filepath = os.path.join(save_dir, filename)

            # 6. 디렉토리 생성 (덮어쓰므로 파일 존재 검사 불필요)
            os.makedirs(save_dir, exist_ok=True)

            # 7. 이미지 크롭 (필요 시)
            if image_type == 'p' and crop_mode:
                logger.debug(f"save_image_to_server_path: Applying crop_mode '{crop_mode}' to image for {log_source_info}")
                cropped_im = cls.imcrop(im, position=crop_mode)
                if cropped_im is None:
                    logger.error(f"save_image_to_server_path: 크롭 실패 (crop_mode: {crop_mode}) for {log_source_info}")
                    return None # 크롭 실패 시 저장 불가
                im = cropped_im # 크롭된 이미지로 대체

            # 8. 이미지 저장 (덮어쓰기)
            logger.debug(f"Saving image to {save_filepath} (will overwrite if exists).")
            save_options = {}
            if ext == 'jpg': save_options['quality'] = 95
            elif ext == 'webp': save_options.update({'quality': 95, 'lossless': False})
            elif ext == 'png': save_options['optimize'] = True

            try:
                im.save(save_filepath, **save_options)
            except OSError as e_os_save:
                # OSError 발생 시 RGB 변환 후 JPG로 저장 재시도
                logger.warning(f"save_image_to_server_path: OSError on save ({save_filepath}): {str(e_os_save)}. Retrying as RGB/JPG.")
                try:
                    if im.mode != 'RGB':
                        logger.debug(f"Converting image mode from {im.mode} to RGB for saving.")
                        im_rgb = im.convert("RGB")
                    else:
                        im_rgb = im # 이미 RGB였다면 그대로 사용

                    save_filepath_jpg = f"{os.path.splitext(save_filepath)[0]}.jpg" # 파일명 확장자 .jpg로 변경
                    im_rgb.save(save_filepath_jpg, quality=95) # JPG로 저장
                    logger.info(f"save_image_to_server_path: Saved as JPG after retry: {save_filepath_jpg}")
                    filename = os.path.basename(save_filepath_jpg) # 최종 파일명 업데이트!
                except Exception as e_retry_save:
                    logger.exception(f"save_image_to_server_path: RGB conversion or save retry failed: {e_retry_save}")
                    return None # 재시도 실패
            except Exception as e_main_save: # 기타 저장 예외
                logger.exception(f"save_image_to_server_path: Main image save failed for {save_filepath}: {e_main_save}")
                return None # 최종 실패

            # 9. 성공 시 상대 경로 반환
            relative_path = os.path.join(path_segment, first_char, label_part, filename).replace("\\", "/") # 최종 filename 사용
            logger.info(f"save_image_to_server_path: 저장 성공: {relative_path}")
            return relative_path

        except Exception as e: # 함수 전체를 감싸는 예외 처리
            logger.exception(f"save_image_to_server_path: 처리 중 예외 발생 ({log_source_info}): {e}")
            return None


    @classmethod
    def __shiroutoname_info(cls, keyword):
        url = "https://shiroutoname.com/"
        tree = cls.get_tree(url, params={"s": keyword}, timeout=30)

        results = []
        for article in tree.xpath("//section//article"):
            title = article.xpath("./h2")[0].text_content()
            title = title[title.find("【") + 1 : title.rfind("】")]

            link = article.xpath(".//a/@href")[0]
            thumb_url = article.xpath(".//a/img/@data-src")[0]
            title_alt = article.xpath(".//a/img/@alt")[0]
            assert title == title_alt  # 다르면?

            result = {"title": title, "link": link, "thumb_url": thumb_url}

            for div in article.xpath("./div/div"):
                kv = div.xpath("./div")
                if len(kv) != 2:
                    continue
                key, value = [x.text_content().strip() for x in kv]
                if not key.endswith("："):
                    continue

                if key.startswith("品番"):
                    result["code"] = value
                    another_link = kv[1].xpath("./a/@href")[0]
                    assert link == another_link  # 다르면?
                elif key.startswith("素人名"):
                    result["name"] = value
                elif key.startswith("配信日"):
                    result["premiered"] = value
                    # format - YYYY/MM/DD
                elif key.startswith("シリーズ"):
                    result["series"] = value
                else:
                    logger.warning("UNKNOWN: %s=%s", key, value)

            a_class = "mlink" if "mgstage.com" in link else "flink"
            actors = []
            for a_tag in article.xpath(f'./div/div/a[@class="{a_class}"]'):
                actors.append(
                    {
                        "name": a_tag.text_content().strip(),
                        "href": a_tag.xpath("./@href")[0],
                    }
                )
            result["actors"] = actors
            results.append(result)
        return results

    @classmethod
    def shiroutoname_info(cls, entity):
        """upgrade entity(meta info) by shiroutoname"""
        data = None
        for d in cls.__shiroutoname_info(entity.originaltitle):
            if entity.originaltitle.lower() in d["code"].lower():
                data = d
                break
        if data is None:
            return entity
        if data.get("premiered", None):
            value = data["premiered"].replace("/", "-")
            entity.premiered = value
            entity.year = int(value[:4])
        if data.get("actors", []):
            entity.actor = [EntityActor(a["name"]) for a in data["actors"]]
        return entity

    @classmethod
    def trans(cls, text, do_trans=True, source="ja", target="ko"):
        text = text.strip()
        if do_trans and text:
            return TransUtil.trans(text, source=source, target=target).strip()
        return text

    @classmethod
    def discord_proxy_image(cls, image_url: str, **kwargs) -> str:
        if not image_url:
            return image_url

        cache = CacheUtil.get_cache()
        cached = cache.get(image_url, {})

        crop_mode = kwargs.pop("crop_mode", None)
        mode = f"crop{crop_mode}" if crop_mode is not None else "original"
        if cached_url := cached.get(mode):
            if DiscordUtil.isurlattachment(cached_url):
                if not DiscordUtil.isurlexpired(cached_url):
                    return cached_url

        proxy_url = kwargs.pop("proxy_url", None)
        if (im := cls.imopen(image_url, proxy_url=proxy_url)) is None:
            return image_url

        try:
            if crop_mode is not None:
                imformat = im.format  # retain original image's format like "JPEG", "PNG"
                im = cls.imcrop(im, position=crop_mode)
                im.format = imformat
            # 파일 이름이 이상한 값이면 첨부가 안될 수 있음
            filename = f"{mode}.{im.format.lower().replace('jpeg', 'jpg')}"
            fields = [{"name": "mode", "value": mode}]
            cached[mode] = DiscordUtil.proxy_image(im, filename, title=image_url, fields=fields)
            cache[image_url] = cached
            return cached[mode]
        except Exception:
            logger.exception("이미지 프록시 중 예외:")
            return image_url

    @classmethod
    def discord_proxy_image_localfile(cls, filepath: str) -> str:
        if not filepath:
            return filepath
        try:
            im = Image.open(filepath)
            # 파일 이름이 이상한 값이면 첨부가 안될 수 있음
            filename = f"localfile.{im.format.lower().replace('jpeg', 'jpg')}"
            return DiscordUtil.proxy_image(im, filename, title=filepath)
        except Exception:
            logger.exception("이미지 프록시 중 예외:")
            return filepath

    @classmethod
    def discord_renew_urls(cls, data):
        return DiscordUtil.renew_urls(data)

    @classmethod
    def get_image_url(cls, image_url, image_mode, proxy_url=None, with_poster=False):
        try:
            # logger.debug('get_image_url')
            # logger.debug(image_url)
            # logger.debug(image_mode)
            ret = {}
            # tmp = cls.discord_proxy_get_target(image_url)

            # logger.debug('tmp : %s', tmp)
            # if tmp is None:
            ret["image_url"] = cls.process_image_mode(image_mode, image_url, proxy_url=proxy_url)
            # else:
            #    ret['image_url'] = tmp

            if with_poster:
                logger.debug(ret["image_url"])
                # ret['poster_image_url'] = cls.discord_proxy_get_target_poster(image_url)
                # if ret['poster_image_url'] is None:
                ret["poster_image_url"] = cls.process_image_mode("5", ret["image_url"])  # 포스터이미지 url 본인 sjva
                # if image_mode == '3': # 디스코드 url 모드일때만 포스터도 디스코드로
                # ret['poster_image_url'] = cls.process_image_mode('3', tmp) #디스코드 url / 본인 sjva가 소스이므로 공용으로 등록
                # cls.discord_proxy_set_target_poster(image_url, ret['poster_image_url'])

        except Exception:
            logger.exception("Image URL 생성 중 예외:")
        # logger.debug('get_image_url')
        # logger.debug(ret)
        return ret

    @classmethod
    def is_hq_poster(cls, im_sm_source, im_lg_source, proxy_url=None):
        logger.debug(f"--- is_hq_poster called ---")
        log_sm_info = f"URL: {im_sm_source}" if isinstance(im_sm_source, str) else f"Type: {type(im_sm_source)}"
        log_lg_info = f"URL: {im_lg_source}" if isinstance(im_lg_source, str) else f"Type: {type(im_lg_source)}"
        logger.debug(f"  Small Image Source: {log_sm_info}")
        logger.debug(f"  Large Image Source: {log_lg_info}")
        
        try:
            if im_sm_source is None or im_lg_source is None:
                logger.debug("  Result: False (Source is None)")
                return False

            im_sm_obj = cls.imopen(im_sm_source, proxy_url=proxy_url)
            im_lg_obj = cls.imopen(im_lg_source, proxy_url=proxy_url)

            if im_sm_obj is None or im_lg_obj is None:
                logger.debug("  Result: False (Failed to open one or both images from source)")
                return False
            logger.debug("  Images acquired/opened successfully.")

            try:
                from imagehash import dhash as hfun
                from imagehash import phash 

                ws, hs = im_sm_obj.size; wl, hl = im_lg_obj.size
                logger.debug(f"  Sizes: Small=({ws}x{hs}), Large=({wl}x{hl})")

                ratio_sm = ws / hs if hs != 0 else 0
                ratio_lg = wl / hl if hl != 0 else 0
                ratio_diff = abs(ratio_sm - ratio_lg)
                logger.debug(f"  Aspect Ratios: Small={ratio_sm:.3f}, Large={ratio_lg:.3f}, Diff={ratio_diff:.3f}")

                if ratio_diff > 0.1: 
                    logger.debug("  Result: False (Aspect ratio difference > 0.1)")
                    return False

                # dhash 비교
                dhash_sm = hfun(im_sm_obj); dhash_lg = hfun(im_lg_obj)
                hdis_d = dhash_sm - dhash_lg
                logger.debug(f"  dhash distance: {hdis_d}")
                if hdis_d >= 14:
                    logger.debug("  Result: False (dhash distance >= 14)")
                    return False

                if hdis_d <= 6:
                    logger.debug("  Result: True (dhash distance <= 6)")
                    return True

                phash_sm = phash(im_sm_obj); phash_lg = phash(im_lg_obj)
                hdis_p = phash_sm - phash_lg
                hdis_sum = hdis_d + hdis_p # 합산 거리
                logger.debug(f"  phash distance: {hdis_p}, Combined distance (d+p): {hdis_sum}")
                result = hdis_sum < 20 # 합산 거리가 20 미만이면 유사하다고 판단
                logger.debug(f"  Result: {result} (Combined distance < 20)")
                return result

            except ImportError:
                logger.warning("  ImageHash library not found. Cannot perform hash comparison.")
                return False
            except Exception as hash_e:
                logger.exception(f"  Error during image hash comparison: {hash_e}")
                return False
        except Exception as e:
            logger.exception(f"  Error in is_hq_poster: {e}")
            return False
        finally:
            logger.debug(f"--- is_hq_poster finished ---")

    @classmethod
    def has_hq_poster(cls, im_sm, im_lg, proxy_url=None):
        try:
            # --- URL 유효성 검사 ---
            if not im_sm or not isinstance(im_sm, str) or not im_lg or not isinstance(im_lg, str):
                logger.debug("has_hq_poster: Invalid or empty URL provided.")
                return None

            im_sm_obj = cls.imopen(im_sm, proxy_url=proxy_url)
            im_lg_obj = cls.imopen(im_lg, proxy_url=proxy_url)

            # --- 이미지 열기 확인 ---
            if im_sm_obj is None or im_lg_obj is None:
                logger.debug("has_hq_poster: Failed to open one or both images.")
                return None

            try:
                # --- imagehash 함수 임포트 ---
                try:
                    # average_hash 와 phash 를 함께 임포트
                    from imagehash import average_hash, phash
                except ImportError:
                    logger.warning("ImageHash library not found, cannot perform similarity checks.")
                    return None # 라이브러리 없으면 비교 불가

                ws, hs = im_sm_obj.size
                wl, hl = im_lg_obj.size
                # 작은 이미지가 큰 이미지보다 클 수 없음
                if ws > wl or hs > hl:
                    logger.debug("has_hq_poster: Small image dimensions exceed large image.")
                    return None

                found_pos = None # 최종 찾은 위치 저장 변수
                positions = ["r", "l", "c"] # 비교할 위치

                # --- 1차 시도: average_hash ---
                logger.debug("has_hq_poster: Performing primary check using average_hash.")
                ahash_threshold = 10 # average_hash 임계값
                for pos in positions:
                    try:
                        cropped_im = cls.imcrop(im_lg_obj, position=pos)
                        if cropped_im is None: continue
                        # average_hash 비교
                        val = average_hash(im_sm_obj) - average_hash(cropped_im)
                        logger.debug(f"  ahash comparison for pos '{pos}': distance = {val}")
                        if val <= ahash_threshold:
                            logger.debug(f"has_hq_poster: Found similar region (ahash <= {ahash_threshold}) at position '{pos}'.")
                            found_pos = pos
                            break # 찾으면 루프 종료
                    except Exception as crop_comp_e:
                        logger.warning(f"Error comparing cropped image (ahash) at pos '{pos}': {crop_comp_e}")
                        continue

                # --- 2차 시도: phash (1차 실패 시) ---
                if found_pos is None:
                    logger.debug("has_hq_poster: Primary check (ahash) failed. Performing secondary check using phash.")
                    phash_threshold = 10 # phash 임계값 (ahash와 동일하게 시작, 조정 가능)
                    for pos in positions:
                        try:
                            cropped_im = cls.imcrop(im_lg_obj, position=pos)
                            if cropped_im is None: continue
                            # phash 비교
                            val = phash(im_sm_obj) - phash(cropped_im)
                            logger.debug(f"  phash comparison for pos '{pos}': distance = {val}")
                            if val <= phash_threshold:
                                logger.debug(f"has_hq_poster: Found similar region (phash <= {phash_threshold}) at position '{pos}'.")
                                found_pos = pos
                                break # 찾으면 루프 종료
                        except Exception as crop_comp_e:
                            logger.warning(f"Error comparing cropped image (phash) at pos '{pos}': {crop_comp_e}")
                            continue

                # --- 최종 결과 반환 ---
                if found_pos:
                    return found_pos # 찾은 위치 반환 ('r', 'l', 'c')
                else:
                    logger.debug("has_hq_poster: No similar region found using ahash or phash.")
                    return None # 최종적으로 못 찾으면 None 반환

            except Exception as hash_e: # 해시 계산 자체의 오류 처리
                logger.exception(f"Error during image hash comparison in has_hq_poster: {hash_e}")
                return None
        except Exception as e: # 이미지 열기 등 외부 오류 처리
            logger.exception(f"Error in has_hq_poster function: {e}")
            return None

    @classmethod
    def change_html(cls, text):
        if not text:
            return text
        return (
            text.replace("&nbsp;", " ")
            .replace("&nbsp", " ")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&amp;", "&")
            .replace("&quot;", '"')
            .replace("&#35;", "#")
            .replace("&#39;", "‘")
        )

    @classmethod
    def remove_special_char(cls, text):
        return cls.PTN_SPECIAL_CHAR.sub("", text)

    @classmethod
    def compare(cls, a, b):
        return (
            cls.remove_special_char(a).replace(" ", "").lower() == cls.remove_special_char(b).replace(" ", "").lower()
        )

    @classmethod
    def get_show_compare_text(cls, title):
        title = title.replace("일일연속극", "").strip()
        title = title.replace("특별기획드라마", "").strip()
        title = re.sub(r"\[.*?\]", "", title).strip()
        title = re.sub(r"\(.*?\)", "", title).strip()
        title = re.sub(r"^.{2,3}드라마", "", title).strip()
        title = re.sub(r"^.{1,3}특집", "", title).strip()
        return title

    @classmethod
    def compare_show_title(cls, title1, title2):
        title1 = cls.get_show_compare_text(title1)
        title2 = cls.get_show_compare_text(title2)
        return cls.compare(title1, title2)

    @classmethod
    def info_to_kodi(cls, data):
        data["info"] = {}
        data["info"]["title"] = data["title"]
        data["info"]["studio"] = data["studio"]
        data["info"]["premiered"] = data["premiered"]
        # if data['info']['premiered'] == '':
        #    data['info']['premiered'] = data['year'] + '-01-01'
        data["info"]["year"] = data["year"]
        data["info"]["genre"] = data["genre"]
        data["info"]["plot"] = data["plot"]
        data["info"]["tagline"] = data["tagline"]
        data["info"]["mpaa"] = data["mpaa"]
        if "director" in data and len(data["director"]) > 0:
            if isinstance(data["director"][0], dict):
                tmp_list = []
                for tmp in data["director"]:
                    tmp_list.append(tmp["name"])
                data["info"]["director"] = ", ".join(tmp_list).strip()
            else:
                data["info"]["director"] = data["director"]
        if "credits" in data and len(data["credits"]) > 0:
            data["info"]["writer"] = []
            if isinstance(data["credits"][0], dict):
                for tmp in data["credits"]:
                    data["info"]["writer"].append(tmp["name"])
            else:
                data["info"]["writer"] = data["credits"]

        if "extras" in data and data["extras"] is not None and len(data["extras"]) > 0:
            if data["extras"][0]["mode"] in ["naver", "youtube"]:
                url = "{ddns}/metadata/api/video?site={site}&param={param}&apikey={apikey}".format(
                    ddns=SystemModelSetting.get("ddns"),
                    site=data["extras"][0]["mode"],
                    param=data["extras"][0]["content_url"],
                    apikey=SystemModelSetting.get("auth_apikey"),
                )
                data["info"]["trailer"] = url
            elif data["extras"][0]["mode"] == "mp4":
                data["info"]["trailer"] = data["extras"][0]["content_url"]

        data["cast"] = []

        if "actor" in data and data["actor"] is not None:
            for item in data["actor"]:
                entity = {}
                entity["type"] = "actor"
                entity["role"] = item["role"]
                entity["name"] = item["name"]
                entity["thumbnail"] = item["thumb"]
                data["cast"].append(entity)

        if "art" in data and data["art"] is not None:
            for item in data["art"]:
                if item["aspect"] == "landscape":
                    item["aspect"] = "fanart"
        elif "thumb" in data and data["thumb"] is not None:
            for item in data["thumb"]:
                if item["aspect"] == "landscape":
                    item["aspect"] = "fanart"
            data["art"] = data["thumb"]
        if "art" in data:
            data["art"] = sorted(data["art"], key=lambda k: k["score"], reverse=True)
        return data

    @classmethod
    def is_hangul(cls, text):
        hanCount = len(cls.PTN_HANGUL_CHAR.findall(text))
        return hanCount > 0

    @classmethod
    def is_include_hangul(cls, text):
        try:
            return cls.is_hangul(text)
        except Exception:
            return False

    # 의미상으로 여기 있으면 안되나 예전 코드에서 많이 사용하기 때문에 잠깐만 나둔다.
    @classmethod
    def get_tree_daum(cls, url, post_data=None):
        from system.logic_site import \
            SystemLogicSite  # pylint: disable=import-error

        from .site_daum import SiteDaum

        return cls.get_tree(
            url,
            proxy_url=SystemModelSetting.get("site_daum_proxy"),
            headers=SiteDaum.default_headers,
            post_data=post_data,
            cookies=SystemLogicSite.get_daum_cookies(),
        )

    @classmethod
    def get_text_daum(cls, url, post_data=None):
        from system.logic_site import \
            SystemLogicSite  # pylint: disable=import-error

        from .site_daum import SiteDaum

        return cls.get_text(
            url,
            proxy_url=SystemModelSetting.get("site_daum_proxy"),
            headers=SiteDaum.default_headers,
            post_data=post_data,
            cookies=SystemLogicSite.get_daum_cookies(),
        )

    @classmethod
    def get_response_daum(cls, url, post_data=None):
        from system.logic_site import \
            SystemLogicSite  # pylint: disable=import-error

        from .site_daum import SiteDaum

        return cls.get_response(
            url,
            proxy_url=SystemModelSetting.get("site_daum_proxy"),
            headers=SiteDaum.default_headers,
            post_data=post_data,
            cookies=SystemLogicSite.get_daum_cookies(),
        )

    @classmethod
    def process_image_book(cls, url):
        im = cls.imopen(url)
        width, _ = im.size
        filename = f"proxy_{time.time()}.jpg"
        filepath = os.path.join(path_data, "tmp", filename)
        left = 0
        top = 0
        right = width
        bottom = width
        poster = im.crop((left, top, right, bottom))
        try:
            poster.save(filepath, quality=95)
        except Exception:
            poster = poster.convert("RGB")
            poster.save(filepath, quality=95)
        ret = cls.discord_proxy_image_localfile(filepath)
        return ret

    @classmethod
    def get_treefromcontent(cls, url, **kwargs):
        text = cls.get_response(url, **kwargs).content
        # logger.debug(text)
        if text is None:
            return
        return html.fromstring(text)

    @classmethod
    def get_translated_tag(cls, tag_type, tag):
        tags_json = os.path.join(os.path.dirname(__file__), "tags.json")
        with open(tags_json, "r", encoding="utf8") as f:
            tags = json.load(f)

        if tag_type not in tags:
            return tag

        if tag in tags[tag_type]:
            return tags[tag_type][tag]

        trans_text = cls.trans(tag, source="ja", target="ko")
        # logger.debug(f'태그 번역: {tag} - {trans_text}')
        if cls.is_include_hangul(trans_text) or trans_text.replace(" ", "").isalnum():
            tags[tag_type][tag] = trans_text

            with open(tags_json, "w", encoding="utf8") as f:
                json.dump(tags, f, indent=4, ensure_ascii=False)

            res = tags[tag_type][tag]
        else:
            res = tag

        return res
