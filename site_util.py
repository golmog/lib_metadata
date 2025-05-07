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
    def imopen(cls, img_src, proxy_url=None):
        if isinstance(img_src, Image.Image):
            return img_src
        try:
            # local file
            return Image.open(img_src)
        except (FileNotFoundError, OSError):
            # remote url
            try:
                res = cls.get_response(img_src, proxy_url=proxy_url)
                return Image.open(BytesIO(res.content))
            except Exception:
                logger.exception("이미지 여는 중 예외:")
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
    def process_image_mode(cls, image_mode, image_url, proxy_url=None, crop_mode=None):
        # logger.debug('process_image_mode : %s %s', image_mode, image_url)
        if image_url is None:
            return
        ret = image_url
        if image_mode == "1":
            tmp = "{ddns}/metadata/api/image_proxy?url=" + py_urllib.quote_plus(image_url)
            if proxy_url is not None:
                tmp += "&proxy_url=" + py_urllib.quote_plus(proxy_url)
            if crop_mode is not None:
                tmp += "&crop_mode=" + py_urllib.quote_plus(crop_mode)
            ret = Util.make_apikey(tmp)
        elif image_mode == "2":
            tmp = "{ddns}/metadata/api/discord_proxy?url=" + py_urllib.quote_plus(image_url)
            if proxy_url is not None:
                tmp += "&proxy_url=" + py_urllib.quote_plus(proxy_url)
            if crop_mode is not None:
                tmp += "&crop_mode=" + py_urllib.quote_plus(crop_mode)
            ret = Util.make_apikey(tmp)
        elif image_mode == "3":  # 고정 디스코드 URL.
            ret = cls.discord_proxy_image(image_url, proxy_url=proxy_url, crop_mode=crop_mode)
        # elif image_mode == "4":  # landscape to poster (사용 중지됨)
        #     # logger.debug(image_url)
        #     # ret = "{ddns}/metadata/normal/image_process.jpg?mode=landscape_to_poster&url=" + py_urllib.quote_plus(
        #     #     image_url
        #     # )
        #     # ret = ret.format(ddns=SystemModelSetting.get("ddns"))
        #     # ret = Util.make_apikey(tmp)
        #     logger.warning("Image Mode 4 (landscape_to_poster) is deprecated and ignored.")
        #     # 이미지 서버 모드(4)는 이 함수에서 처리하지 않음. 호출하는 쪽에서 분기.
        elif image_mode == "5":  # 로컬에 포스터를 만들고
            # image_url : 디스코드에 올라간 표지 url 임.
            im = cls.imopen(image_url, proxy_url=proxy_url)
            width, height = im.size
            filename = f"proxy_{time.time()}.jpg"
            filepath = os.path.join(path_data, "tmp", filename)
            if width > height:
                im = cls.imcrop(im)
            im.save(filepath, quality=95)
            # poster_url = '{ddns}/file/data/tmp/%s' % filename
            # poster_url = Util.make_apikey(poster_url)
            # logger.debug('poster_url : %s', poster_url)
            ret = cls.discord_proxy_image_localfile(filepath)
        return ret

    @classmethod
    def save_image_to_server_path(cls, image_url: str, image_type: str, base_path: str, path_segment: str, ui_code: str, art_index: int = None, proxy_url: str = None, crop_mode: str = None):
        """
        이미지를 다운로드하여 지정된 로컬 경로에 저장하고, 웹 서버 접근용 상대 경로를 반환합니다.

        :param image_url: 다운로드할 원본 이미지 URL
        :param image_type: 이미지 종류 ('ps', 'pl', 'p', 'art')
        :param base_path: 로컬 저장 기본 경로 (설정값: jav_censored_image_server_local_path)
        :param path_segment: 하위 경로 세그먼트 (예: 'jav/cen')
        :param ui_code: 파일명 생성에 사용될 코드 (예: 'ABP-123')
        :param art_index: 이미지 타입이 'art'일 경우 파일명에 사용될 인덱스
        :param proxy_url: 이미지 다운로드 시 사용할 프록시 URL
        :param crop_mode: 이미지 타입이 'p'일 경우 적용할 크롭 모드 ('l', 'r', 'c')
        :return: 저장 성공 시 상대 경로 (예: 'jav/cen/A/ABP/abp-123_p.jpg'), 실패 시 None
        """
        if not all([image_url, image_type, base_path, path_segment, ui_code]):
            logger.warning("save_image_to_server_path: 필수 인자 누락.")
            return None
        if image_type not in ['ps', 'pl', 'p', 'art']:
            logger.warning(f"save_image_to_server_path: 유효하지 않은 image_type: {image_type}")
            return None
        if image_type == 'art' and art_index is None:
            logger.warning("save_image_to_server_path: image_type='art'일 때 art_index 필요.")
            return None

        try:
            # 1. 경로 및 파일명 생성 준비
            ui_code_parts = ui_code.split('-')
            if not ui_code_parts:
                logger.error(f"save_image_to_server_path: 유효하지 않은 ui_code 형식: {ui_code}")
                return None
            label = ui_code_parts[0].upper()
            first_char = label[0] if label[0].isalpha() else '09'

            # 2. 이미지 다운로드 및 정보 확인
            im = cls.imopen(image_url, proxy_url=proxy_url)
            if im is None:
                logger.warning(f"save_image_to_server_path: 이미지 열기 실패: {image_url}")
                return None

            # 3. 확장자 결정 및 허용 여부 확인
            original_format = im.format
            if not original_format: # PIL에서 format을 못 읽는 경우 대비 (거의 없음)
                ext_match = re.search(r'\.(jpg|jpeg|png|webp|gif)(\?|$)', image_url.lower())
                if ext_match: original_format = ext_match.group(1).upper()
                else: original_format = "JPEG" # 기본값 JPEG
                logger.debug(f"PIL format missing, deduced format: {original_format} from URL: {image_url}")

            ext = original_format.lower()
            if ext == 'jpeg': ext = 'jpg' # jpeg는 jpg로 통일

            allowed_exts = ['jpg', 'png', 'webp']
            if ext not in allowed_exts:
                logger.warning(f"save_image_to_server_path: 지원하지 않는 이미지 포맷: {ext} ({image_url})")
                return None # gif 등 처리 안 함

            # 4. 최종 저장 경로 및 파일명 결정
            save_dir = os.path.join(base_path, path_segment, first_char, label)
            if image_type == 'art':
                filename = f"{ui_code.lower()}_art_{art_index}.{ext}"
            else:
                filename = f"{ui_code.lower()}_{image_type}.{ext}"
            save_filepath = os.path.join(save_dir, filename)

            # 5. 파일 존재 여부 확인 (존재 시 패스)
            if os.path.exists(save_filepath):
                logger.debug(f"save_image_to_server_path: 파일 이미 존재함: {save_filepath}")
                # 이미 존재해도 상대 경로는 반환해야 함
                relative_path = os.path.join(path_segment, first_char, label, filename).replace("\\", "/")
                return relative_path

            # 6. 디렉토리 생성
            os.makedirs(save_dir, exist_ok=True)

            # 7. 이미지 크롭 (필요 시)
            if image_type == 'p' and crop_mode in ['l', 'r', 'c']:
                logger.debug(f"save_image_to_server_path: 이미지 크롭 적용 (type='p', mode='{crop_mode}')")
                im = cls.imcrop(im, position=crop_mode)
                if im is None: # 크롭 실패 시
                    logger.error(f"save_image_to_server_path: 크롭 실패: {image_url}")
                    return None

            # 8. 이미지 저장 (PIL.Image.save()는 기본적으로 덮어씀)
            logger.debug(f"save_image_to_server_path: 저장 시도 (덮어쓰기 가능): {save_filepath}")
            save_kwargs = {'quality': 95} if ext == 'jpg' or ext == 'webp' else {}
            try:
                im.save(save_filepath, **save_kwargs)
            except OSError as e:
                logger.warning(f"save_image_to_server_path: 저장 중 OS 오류 발생 (포맷 변환 시도): {e}")
                try:
                    im = im.convert("RGB")
                    ext_new = 'jpg'
                    filename_new = f"{os.path.splitext(filename)[0]}.{ext_new}"
                    save_filepath_new = os.path.join(save_dir, filename_new)
                    
                    im.save(save_filepath_new, quality=95)
                    logger.info(f"save_image_to_server_path: 원본 포맷 저장 실패, JPEG 변환 저장 성공: {save_filepath_new}")
                    filename = filename_new
                except Exception as e_save_retry:
                    logger.exception(f"save_image_to_server_path: JPEG 변환 저장 재시도 실패: {e_save_retry}")
                    return None
            except Exception as e_save:
                logger.exception(f"save_image_to_server_path: 이미지 저장 실패: {e_save}")
                return None

            # 9. 성공 시 상대 경로 반환
            relative_path = os.path.join(path_segment, first_char, label, filename).replace("\\", "/")
            logger.info(f"save_image_to_server_path: 저장 성공: {relative_path}")
            return relative_path

        except Exception as e:
            logger.exception(f"save_image_to_server_path: 처리 중 예외 발생: {e}")
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
    def is_hq_poster(cls, im_sm, im_lg, proxy_url=None):
        logger.debug(f"--- is_hq_poster called ---")
        logger.debug(f"  Small Image URL: {im_sm}")
        logger.debug(f"  Large Image URL: {im_lg}")
        try:
            if not im_sm or not isinstance(im_sm, str) or not im_lg or not isinstance(im_lg, str):
                logger.debug("  Result: False (Invalid or empty URL)")
                return False

            im_sm_obj = cls.imopen(im_sm, proxy_url=proxy_url)
            im_lg_obj = cls.imopen(im_lg, proxy_url=proxy_url)

            if im_sm_obj is None or im_lg_obj is None:
                logger.debug("  Result: False (Failed to open one or both images)")
                return False
            logger.debug("  Images opened successfully.")

            try:
                from imagehash import dhash as hfun
                from imagehash import phash # phash도 미리 임포트

                ws, hs = im_sm_obj.size; wl, hl = im_lg_obj.size
                logger.debug(f"  Sizes: Small=({ws}x{hs}), Large=({wl}x{hl})")
                if ws > wl or hs > hl:
                    logger.debug("  Result: False (Small image larger than large image)")
                    return False

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

                # phash 추가 비교
                phash_sm = phash(im_sm_obj); phash_lg = phash(im_lg_obj)
                hdis_p = phash_sm - phash_lg
                hdis_sum = hdis_d + hdis_p # 합산 거리
                logger.debug(f"  phash distance: {hdis_p}, Combined distance (d+p): {hdis_sum}")
                result = hdis_sum < 20
                logger.debug(f"  Result: {result} (Combined distance < 20)")
                return result

            except ImportError:
                logger.warning("  Result: False (ImageHash library not found)")
                return False
            except Exception as hash_e:
                logger.exception(f"  Result: False (Error during image hash comparison: {hash_e})")
                return False
        except Exception as e:
            logger.exception(f"  Result: False (Error in is_hq_poster: {e})")
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
