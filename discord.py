import random
import time
from base64 import b64decode
from io import BytesIO
from pathlib import Path
from typing import Dict, List

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

    @classmethod
    def get_webhook_url(cls):
        if not cls._webhook_list:
            cls._webhook_list = random.sample(webhook_list, k=len(webhook_list))
        return cls._webhook_list.pop()

    @classmethod
    def proxy_image(cls, im: Image.Image, filename: str, title: str = None, fields: List[Dict] = None):
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

        num_retries = 2
        sleep_sec = 1
        for retry_num in range(num_retries + 1):
            if retry_num > 0:
                logger.warning("[%d/%d] Sleeping %.2f secs before executing webhook", retry_num, num_retries, sleep_sec)
                webhook.url = cls.get_webhook_url()
                time.sleep(sleep_sec)

            res = webhook.execute()
            if isinstance(res, list):
                res = res[0]
            if res.status_code != 429:
                break

        try:
            return res.json()["embeds"][0]["image"]["url"]
        except AttributeError:
            return res[0].json()["embeds"][0]["image"]["url"]
