import random
import time
from base64 import b64decode
from datetime import datetime, timedelta
from io import BytesIO
from itertools import islice, zip_longest
from pathlib import Path
from typing import Dict, List
from urllib.parse import parse_qs, urlparse

from discord_webhook import DiscordEmbed, DiscordWebhook
from framework import path_data  # pylint: disable=import-error
from PIL import Image

from .plugin import P

logger = P.logger

try:
    webhook_file = Path(path_data).joinpath("db/lib_metadata.webhook")
    with open(webhook_file, encoding="utf-8") as fp:
        webhook_list = list(filter(str, fp.read().splitlines()))
    assert webhook_list, f"웹훅을 찾을 수 없음: {webhook_file}"
    logger.debug("나의 웹훅 사용: %d", len(webhook_list))
except Exception as e:
    logger.debug("내장 웹훅 사용: %s", e)
    webhook_list = [
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyODM4MzE4MzI4MjIyNy9ORXpNWFBmT05vbUU3bl8xck1iT0ZWQUI4ZmlXN21vRFlGYnJHRk03UlJSWF90ZGMyS0lxY2hWcXV6VF8wVm5ZUEJRVQ==",  # 1
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyODc5MzExMzU4MzY3Ni94dEZlWnRWbkhEUGc4aFBYZkZDMkFidUtDSmlwNjQ0d1RQMDFJalVncTR5ODB6XzZCRi1kTFctemlEdGNlWF84RXVtRw==",  # 2
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyODkyMTA4MTgxMDk1NS9xOUVYZndHYll6bHdwM1MtMnpxUmxZcnJYWS1nTUttTTRlTUd0YW8zNTF1d1c2N0U2ckNFUW0zWDJhbDJURnFXMHR4cw==",  # 3
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyOTA4MzA0NDg1MTc3My9jVU1YRkVERHQ2emtWOW90Mmd5dlpYeVlOZV9VcGtmcmZhTzg5aHZoLVdod0c0Z24zOGJhT19DVkg2Z0N4clFraVZRcA==",  # 4
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyOTE4MzAwMzQ5MjM5Mi9CVXl1U3lKTHc1cktHdFRKOWhqRDk3SklKWW9HSTZ6SnJ4MzdLX0s4TkVKU3ZTYlZ4aC0tMVFRMEFZbTJFa0tzaEJRcQ==",  # 5
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyOTI4OTQ4ODQ5NDY0My9XLWhZck95QTBza2M1dUdVTkpyenU4ZHFSMVF0QmMtOVAzMW45RHhQWkhVLXptdEZ3MWVLWTE0dlZubkRUV25EU2ZRTw==",  # 6
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyOTQwOTExODQzNzQ2OS95NDRjVTkwM1hLS2NyaWFidERHMzRuMzZfRkZsMF9TV2p4b0lWMlBZY0dxNWxyU1dxVWt5ZklkZlcwM0FFVDJObThMaQ==",  # 7
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyOTQ5NTQyNDYxODQ5Ni9PSnFlVHRhZ1FtVGFrU2VNQkRucVhZRTJieWRuX2cweHV2VTA0WmdKS3NjWEQydkVHbHBWYzdhQWZMQ0ZYSXNTbTN5OQ==",  # 8
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyOTU4NzE2NjY0MjM2OC9PeG9NRllUT1dOWmcwS3pfQ3k2ZXowX2laQnpqbm02QVkyTjJfMWhaVHhYdWxPRm5oZ0lsOGxxcUVYNVVWRWhyMHJNcw==",  # 9
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyOTY4OTM3NzYzNjQ3Mi9iblgyYTNsWjI1R2NZZ1g4Sy1JeXZVcS1TMV9zYmhkbEtoSTI4eWp6SWdHOHRyekFWMXBKUkgxYkdrdmhzMVNUNS1uMg==",  # 10
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyOTgwMTg0MzY5MTY4Mi96OUFocjZjNmxaS1VyWV9TRmhfODVQeEVlSjJuQW8wMXlHS3RCUWpNNnJmR3JGVXdvQ1oyQ3NJYmlTMHQ1NDZwU3NUUg==",  # 11
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIyOTk0NjQyMTM2Njg0NC9BYnFVRlJCN0dzb3ktUkdfMzBLNXZkNm9XUWRnbkpDZ1ctTlpBbkFRN0N2TzdsNjRfeXRkY0ZHUkhLV2RldE1jQzhTSw==",  # 12
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIzMDExNjAyNjQyMTM0OC82dG1NOTA2eV9QTHJ3WGFxcGNZS25OMEJIQjlDTkxJT1dJeTdpc3Exbm9VMHJxU2V0NzI2R1Y4Zk9Ua2pCbDZacXMxVA==",  # 13
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIzMDIwMTExMjA3MjM2My9DYkdkcVdvd3hCcTV3ck1hck0taGZqajVIbFJ2VFFWa0tuZUVaVl9yMlc1UkxHZFZpWW15VzZZcl9PbEJCZG5KWk1wNw==",  # 14
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIzMDI5MTUwNzcyNDQwOC9UanJFc08zSTJyT3l0d0ZvVFhUSlNTOXphaDJpbG9CcVk3TzhHMHZWbDROTmI0aGpaaDNjVGo0cGNla2lxa3RqaGRPTg==",  # 15
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIzMDM3NDQ4NzgzNDc3NC85ZFpodjZfajRuT0hpbGtMaFVVc1B6OVFKa2dqQ3BJZ19PWE55YnZtV1BiQlNVdmRZWC1IVW5UM3RneDlKdnZlYjVMZw==",  # 16
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIzMDU2ODkzMTU2OTc3NC82MTFBVTg0ZUZBcXllWlktQ2lPbnozbm4zSHg3ZldwQUNCbjlMTFNENUFJdHRkYjVHSm9pV3B1dHpxdEVHZ3l0RHlXYg==",  # 17
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIzMDY3MzkxMDgwMDM4Ni9jbEZvZHEwREhGNUlvYUtVRXVRcXNGbnB3OXZoZUx1RU1qbVJFNjQyRUZGa21wYXBwMzhYWDNPMmZKWUVSdjMzY0tORg==",  # 18
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIzMDc0OTMyMDE5NjE5OC8weE1vZ2o5UXRCM1NGZE5KYk04STk1LU9XQzI2Zm1WTWpjelpSX2REY2hnblZoUk1QelVzRHFlYTc0QUdISFRFVWFVZQ==",  # 19
        "aHR0cHM6Ly9kaXNjb3JkLmNvbS9hcGkvd2ViaG9va3MvMTE2MjIzMDg0NTMxNTIyMzU2Mi9HWVItZUo2WFltdy1VMHRkWWY5QXdnU2JVZFpSb1k4aWx2MlVxaVJBSnlBdDBsYWV5ck1tSzJmRGp5T1YyenBJUUR2bA==",  # 20
    ]
    webhook_list = [b64decode(x).decode() for x in webhook_list]


class DiscordUtil:
    _webhook_list = []
    MARGIN = timedelta(seconds=60)

    @classmethod
    def get_webhook_url(cls):
        if not cls._webhook_list:
            cls._webhook_list = random.sample(webhook_list, k=len(webhook_list))
        return cls._webhook_list.pop()

    @classmethod
    def __execute(cls, webhook: DiscordWebhook, num_retries: int = 2, sleep_sec: int = 1) -> Optional[dict]:
        """
        warps DiscordWebhook.execute() with a retry scheme and enhanced error handling.
        Returns the JSON response as a dict on success, None on failure.
        """
        res = None  # 응답 객체 초기화
        last_exception = None  # 마지막 예외 저장용
        original_url = webhook.url # 초기 웹훅 URL 저장

        for retry_num in range(num_retries + 1):
            current_url = webhook.url # 현재 시도할 웹훅 URL
            if retry_num > 0:
                logger.warning(f"[{retry_num}/{num_retries}] Retrying webhook execution. Sleeping {sleep_sec}s.")
                try:
                    webhook.url = cls.get_webhook_url() # 실패 시 다른 웹훅 URL 가져오기
                    logger.debug(f"Retrying with new webhook URL: ...{webhook.url[-30:]}")
                except Exception as e_get_wh:
                    logger.error(f"Failed to get a new webhook URL for retry: {e_get_wh}")
                    # 웹훅을 더 얻을 수 없으면 재시도 중단
                    last_exception = e_get_wh
                    break
                time.sleep(sleep_sec)

            try:
                logger.debug(f"Executing webhook (Attempt {retry_num+1})... URL: ...{current_url[-30:]}")
                res = webhook.execute() # 웹훅 실행

                # discord_webhook 라이브러리는 응답 객체 또는 리스트를 반환할 수 있음
                response_obj = res[0] if isinstance(res, list) else res

                logger.debug(f"Webhook response status code: {response_obj.status_code}")

                if response_obj.status_code == 429:
                    logger.warning("Rate limited (429). Will retry if possible.")
                    # Rate limit 시에는 재시도 루프를 계속 진행 (다음 루프에서 sleep 및 웹훅 변경)
                    last_exception = requests.exceptions.HTTPError(f"Rate limited (429) on URL: ...{current_url[-30:]}")
                    # 다음 재시도를 위해 continue
                    continue
                elif 200 <= response_obj.status_code < 300:
                    logger.debug("Webhook execution successful.")
                    last_exception = None  # 성공 시 이전 예외 기록 삭제
                    break  # 성공했으므로 재시도 루프 탈출
                else:
                    # 429 외의 다른 HTTP 에러 (예: 404 Not Found, 403 Forbidden, 5xx Server Error)
                    logger.error(f"Webhook execution failed with status code: {response_obj.status_code}. Response: {response_obj.text[:500]}")
                    last_exception = requests.exceptions.HTTPError(f"HTTP Error {response_obj.status_code} on URL: ...{current_url[-30:]}")
                    # 다른 HTTP 에러 발생 시, 웹훅 URL이 유효하지 않거나 문제가 있을 수 있으므로
                    # 다음 재시도에서 다른 웹훅을 사용하도록 루프 계속 진행 (혹은 break 결정 가능)
                    # 여기서는 일단 다음 재시도를 위해 continue

            except requests.exceptions.RequestException as e_req:
                logger.error(f"Webhook request failed (Network/Connection Error): {e_req} on URL: ...{current_url[-30:]}")
                last_exception = e_req
                # 네트워크 오류 시 잠시 후 같은 URL로 재시도할 수도 있고, 다른 웹훅으로 시도할 수도 있음
                # 여기서는 다음 재시도에서 다른 웹훅을 사용하도록 루프 계속 진행
            except Exception as e_inner:  # discord_webhook.execute() 내부 또는 기타 예외
                logger.exception(f"Unexpected error during webhook execution: {e_inner} on URL: ...{current_url[-30:]}")
                last_exception = e_inner
                # 예상치 못한 오류 발생 시 재시도 중단 (혹은 계속 진행 결정 가능)
                break # 여기서는 일단 중단

        # 재시도 루프 종료 후 결과 처리
        if last_exception is not None:
            # 루프가 끝났는데 성공하지 못함 (모든 재시도 실패 또는 중간에 break)
            logger.error(f"Webhook execution ultimately failed after {retry_num + 1} attempts. Last exception: {last_exception}")
            # 실패 시 원래 웹훅 URL로 복원 (필요하다면)
            webhook.url = original_url
            return None

        # 성공적으로 응답을 받은 경우 (last_exception == None)
        if res is not None:
            try:
                # 응답 객체 재확인 (리스트일 수 있음)
                response_obj = res[0] if isinstance(res, list) else res
                json_response = response_obj.json() # JSON 파싱 시도
                # Discord API가 에러 메시지를 JSON으로 반환하는 경우 확인
                if isinstance(json_response, dict) and json_response.get('message'):
                    logger.error(f"Discord API Error received: {json_response.get('message')} (Code: {json_response.get('code')})")
                    return None # API 레벨 에러는 실패로 간주
                logger.debug("Webhook response parsed successfully.")
                return json_response
            except requests.exceptions.JSONDecodeError as e_json:
                logger.error(f"Failed to decode JSON response: {e_json}")
                logger.debug(f"Response text: {response_obj.text[:500]}") # 응답 내용 일부 로깅
                return None
            except Exception as e_parse: # json() 또는 이후 처리 중 예외
                logger.exception(f"Error processing webhook JSON response: {e_parse}")
                return None
        else:
            # 이 경우는 루프가 성공적으로 끝났으나 res가 None인 예외적인 상황 (이론상 발생 어려움)
            logger.error("Webhook execution ended without a valid response object.")
            return None

    @classmethod
    def proxy_image(cls, im: Image.Image, filename: str, title: str = None, fields: List[dict] = None) -> str:
        """proxy image by attachments"""
        webhook = DiscordWebhook(url=cls.get_webhook_url())
        with BytesIO() as buf:
            im.save(buf, format=im.format, quality=95)
            webhook.add_file(buf.getvalue(), filename)
        embed = DiscordEmbed(title=title, color=16164096)
        embed.set_footer(text="lib_metadata")
        embed.set_timestamp()
        for field in fields or []:
            embed.add_embed_field(**field)
        embed.set_image(url=f"attachment://{filename}")
        webhook.add_embed(embed)

        return cls.__execute(webhook)["embeds"][0]["image"]["url"]

    @classmethod
    def isurlattachment(cls, url: str) -> bool:
        if not any(x in url for x in ["cdn.discordapp.com", "media.discordapp.net"]):
            return False
        if "/attachments/" not in url:
            return False
        return True

    @classmethod
    def isurlexpired(cls, url: str) -> bool:
        u = urlparse(url)
        q = parse_qs(u.query, keep_blank_values=True)
        try:
            ex = datetime.utcfromtimestamp(int(q["ex"][0], base=16))
            return ex - cls.MARGIN < datetime.utcnow()
        except KeyError:
            return True

    @classmethod
    def iter_attachment_url(cls, data: dict):
        if isinstance(data, dict):
            for v in data.values():
                yield from cls.iter_attachment_url(v)
        if isinstance(data, list):
            for v in data:
                yield from cls.iter_attachment_url(v)
        if isinstance(data, str) and cls.isurlattachment(data):
            yield data

    @classmethod
    def __proxy_image_url(
        cls,
        urls: List[str],
        titles: Optional[List[str]] = None,
        lfields: Optional[List[List[dict]]] = None,
    ) -> Dict[str, str]:
        """
        Internal method to proxy a batch of image URLs (max 10) using Discord embeds.
        Returns a dictionary mapping original URLs to new proxied URLs.
        Returns an empty dict if the webhook execution fails or no URLs could be proxied.
        """
        if not urls:
            return {}

        # Ensure titles and lfields match the length of urls if provided
        titles = titles or []
        lfields = lfields or []

        if len(urls) > 10:
            logger.warning(f"__proxy_image_url received {len(urls)} URLs, but Discord only supports 10 embeds per message. Truncating.")
            urls = urls[:10]
            titles = titles[:10]
            lfields = lfields[:10]

        webhook = DiscordWebhook(url=cls.get_webhook_url())
        logger.debug(f"Preparing to proxy {len(urls)} URLs via embeds using webhook ...{webhook.url[-30:]}")

        for url, title, fields in zip_longest(urls, titles, lfields):
            if not url: continue # 혹시 모를 빈 URL 스킵

            embed = DiscordEmbed(title=title, color=5814783) # Using a different color for proxying
            embed.set_footer(text="lib_metadata (URL Proxy)")
            embed.set_timestamp()
            for field in fields or []:
                # 필드 값 유효성 검사 (문자열로 변환 시도)
                try:
                    name = str(field.get('name', ''))
                    value = str(field.get('value', ''))
                    inline = bool(field.get('inline', True)) # 기본값 True
                    if name and value: # 이름과 값이 모두 있어야 함
                        embed.add_embed_field(name=name, value=value, inline=inline)
                    else:
                        logger.warning(f"Skipping invalid embed field: {field}")
                except Exception as e_field:
                    logger.error(f"Error adding embed field {field}: {e_field}")

            # 원본 이미지 URL을 Embed에 설정
            embed.set_image(url=url)
            webhook.add_embed(embed)

        # 수정된 __execute 호출
        res = cls.__execute(webhook)

        # __execute 결과 확인 (None이거나 dict가 아니면 실패)
        if res is None or not isinstance(res, dict):
            logger.error(f"Webhook execution failed or returned invalid data for URL proxying. Response type: {type(res)}")
            return {} # 실패 시 빈 딕셔너리 반환

        # 응답에서 embeds 키 확인
        if "embeds" not in res or not isinstance(res["embeds"], list):
            logger.error(f"Webhook response is missing 'embeds' list or it's not a list. Response: {res}")
            return {} # 실패 시 빈 딕셔너리 반환

        result_map = {}
        # 응답받은 embed 수와 요청한 URL 수가 다를 수 있음 (Discord 제한 등)
        num_embeds_received = len(res["embeds"])
        if num_embeds_received != len(urls):
            logger.warning(f"Number of embeds received ({num_embeds_received}) does not match number of URLs sent ({len(urls)}).")

        # 받은 embed 기준으로 처리 (IndexError 방지)
        for n, embed_data in enumerate(res["embeds"]):
            # 원본 URL을 알아내기 위해 원래 순서 사용 (받은 embed 수보다 적을 수 있음)
            if n >= len(urls):
                logger.warning(f"Received more embeds ({num_embeds_received}) than URLs sent ({len(urls)}). Ignoring extra embed {n}.")
                break
            original_url = urls[n]

            try:
                # 각 embed 구조 및 내용 확인 강화
                if isinstance(embed_data, dict) and \
                   "image" in embed_data and isinstance(embed_data["image"], dict) and \
                   "url" in embed_data["image"]:
                    new_url = embed_data["image"]["url"]
                    # 새 URL이 유효한지 (문자열이고 비어있지 않은지) 확인
                    if isinstance(new_url, str) and new_url.strip():
                        # 새 URL이 원본과 다른지 확인 (프록시 성공 여부 간접 확인)
                        if new_url != original_url:
                            logger.debug(f"Successfully proxied URL: {original_url} -> {new_url}")
                            result_map[original_url] = new_url
                        else:
                            # 간혹 Discord가 프록시하지 않고 원본 URL을 그대로 반환할 수 있음
                            logger.warning(f"Proxy URL is the same as original for: {original_url}. Assuming proxy failed.")
                    else:
                        logger.warning(f"Received empty or invalid new URL for original URL: {original_url}. New URL: {new_url}")
                else:
                    logger.warning(f"Unexpected embed structure or missing image/url for original URL: {original_url}. Embed data: {embed_data}")
            except Exception as e:
                # 루프 내에서 발생할 수 있는 다른 예외 처리
                logger.exception(f"Error processing received embed {n} for original URL {original_url}: {e}")

        if not result_map:
            logger.warning(f"Failed to proxy any URLs in this batch. Check webhook validity and Discord status.")

        return result_map # 성공/부분성공/실패 모두 포함된 결과 반환 (실패한 URL은 포함 안 됨)

    @classmethod
    def proxy_image_url(
        cls,
        urls: List[str],
        titles: List[str] = None,
        lfields: List[List[dict]] = None,  # list of fields
    ) -> Dict[str, str]:
        urls = list(set(urls))

        def chunker(it, chunk_size=10):
            it = iter(it)
            while chunk := list(islice(it, chunk_size)):
                yield chunk

        titles = titles or []
        lfields = lfields or []
        urlmaps = {}
        for u, t, lf in zip_longest(*[chunker(x) for x in [urls, titles, lfields]]):
            urlmaps.update(cls.__proxy_image_url(u, t, lf))
        return urlmaps

    @classmethod
    def renew_urls(cls, data):
        """renew and in-place replacement of discord attachments urls in data"""

        def _repl(d, m):
            if isinstance(d, (dict, list)):
                for k, v in d.items() if isinstance(d, dict) else enumerate(d):
                    if isinstance(v, str) and v in m:
                        d[k] = m[v]
                    _repl(v, m)

        if isinstance(data, dict):
            urls = list(filter(cls.isurlexpired, cls.iter_attachment_url(data)))
            titles = [x.split("?")[0] for x in urls]
            lfields = [[{"name": "mode", "value": "renew"}]] * len(urls)
            urlmaps = cls.proxy_image_url(urls, titles=titles, lfields=lfields)
            _repl(data, urlmaps)
            return data
        if isinstance(data, list):
            urls = list(filter(cls.isurlexpired, data))
            titles = [x.split("?")[0] for x in urls]
            lfields = [[{"name": "mode", "value": "renew"}]] * len(urls)
            urlmaps = cls.proxy_image_url(urls, titles=titles, lfields=lfields)
            return [urlmaps.get(x, x) for x in data]
        raise NotImplementedError(f"알 수 없는 데이터 유형: {type(data)}")
