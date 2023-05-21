import json

# sjva 공용
from framework import SystemModelSetting, app

from .plugin import P

logger = P.logger
from .site_util import SiteUtil

# 패키지


server_plugin_ddns = app.config["DEFINE"]["METADATA_SERVER_URL"]
try:
    if SystemModelSetting.get("ddns") == server_plugin_ddns:
        server_plugin_ddns = "http://127.0.0.1:19999"
except Exception:
    pass


class MetadataServerUtil:
    @classmethod
    def get_metadata(cls, code):
        try:
            url = f"{app.config['DEFINE']['WEB_DIRECT_URL']}/meta/get_meta.php"
            params = {"type": "meta", "code": code}
            logger.info("서버로부터 메타데이터를 가져오는 중: %s", params)
            data = SiteUtil.get_response(url, params=params, timeout=30).json()
            if data["ret"] == "success":
                return data["data"]
        except Exception:
            logger.exception("서버로부터 메타데이터를 가져오는 중 예외:")
        return None

    @classmethod
    def set_metadata(cls, code, data, keyword):
        try:
            url = f"{server_plugin_ddns}/server/normal/metadata/set"
            param = {
                "code": code,
                "data": json.dumps(data),
                "user": SystemModelSetting.get("sjva_me_user_id"),
                "keyword": keyword,
            }
            logger.debug("서버로 메타데이터 보내는 중: %s", param)
            data = SiteUtil.get_response(url, post_data=param, timeout=30).json()
            if data["ret"] == "success":
                logger.info("메타데이터 '%s' 저장 성공. 감사합니다!", code)
        except Exception:
            logger.exception("서버로 메타데이터 보내는 중 예외:")

    @classmethod
    def set_metadata_jav_censored(cls, code, data, keyword):
        try:
            thumbs = data.get("thumb", [])
            if code.startswith("C") and len(thumbs) < 2:
                return
            if code.startswith("D") and len(thumbs) < 1:
                return
            for thumb in thumbs:
                value = thumb.get("value", "")
                if ".discordapp." not in value:
                    return
                if not SiteUtil.get_response(thumb["value"], method="HEAD", timeout=30).ok:
                    return
            if not SiteUtil.is_include_hangul(data.get("plot", "")):
                return
        except Exception:
            logger.exception("보낼 메타데이터 확인 중 예외:")
        else:
            cls.set_metadata(code, data, keyword)

    @classmethod
    def set_metadata_jav_uncensored(cls, code, data, keyword):
        try:
            thumbs = data.get("thumb", [])
            for thumb in thumbs:
                value = thumb.get("value", "")
                if ".discordapp." not in value:
                    return
                if not SiteUtil.get_response(thumb["value"], method="HEAD", timeout=30).ok:
                    return
            if not SiteUtil.is_include_hangul(data.get("tagline", "")):
                return
            if not SiteUtil.is_include_hangul(data.get("plot", "")):
                return
        except Exception:
            logger.exception("보낼 메타데이터 확인 중 예외:")
        else:
            cls.set_metadata(code, data, keyword)

    @classmethod
    def get_meta_extra(cls, code):
        try:
            url = f"{app.config['DEFINE']['WEB_DIRECT_URL']}/meta/get_meta.php"
            params = {"type": "extra", "code": code}
            logger.info("서버로부터 메타데이터를 가져오는 중: %s", params)
            data = SiteUtil.get_response(url, params=params, timeout=30).json()
            if data["ret"] == "success":
                return data["data"]
        except Exception:
            logger.exception("서버로부터 메타데이터를 가져오는 중 예외:")
        return None
