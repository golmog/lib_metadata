# -*- coding: utf-8 -*-
import requests # 현재 직접 사용 안 함 (SiteUtil 사용)
import re
import json # 현재 직접 사용 안 함
import traceback
# from dateutil.parser import parse # 사용 안 함

from lxml import html

from framework import SystemModelSetting # 현재 직접 사용 안 함
from framework.util import Util # 현재 직접 사용 안 함
from system import SystemLogicTrans # 번역에 사용

# lib_metadata
from ..entity_av import EntityAVSearch
from ..entity_base import EntityMovie, EntityThumb, EntityActor, EntityRatings, EntityExtra
from ..site_util import SiteUtil

#########################################################
from ..plugin import P # lib_metadata의 plugin.P
logger = P.logger
# ModelSetting = P.ModelSetting # lib_metadata 내에서는 직접적인 SJVA ModelSetting 사용 지양 (필요시 인자로 받아야 함)

class SiteFc2ppvdb(object):
    site_name = 'fc2ppvdb'
    site_base_url = 'https://fc2ppvdb.com'
    module_char = 'L'
    site_char = 'P'

    @classmethod
    def search(cls, keyword_num_part, do_trans=False, proxy_url=None, image_mode='0', manual=False, **kwargs):
        """
        FC2 품번의 숫자 부분을 사용하여 fc2ppvdb.com에서 검색합니다. (JavDB 방식)
        search 단계에서는 원본 이미지 URL을 사용하고, 번역을 수행하지 않습니다.
        do_trans, image_mode, manual 인자는 이 함수 내에서는 직접 사용되지 않으나,
        호출하는 쪽(logic_jav_fc2.py)과의 인터페이스 일관성을 위해 유지합니다.
        proxy_url은 SiteUtil.get_tree 호출 시 사용됩니다.
        """
        logger.debug(f"[{cls.site_name} Search (JavDB Style)] Keyword(num_part): {keyword_num_part}, manual: {manual}, proxy: {'Yes' if proxy_url else 'No'}")
        
        ret = {'ret': 'failed', 'data': []}

        try:
            search_url = f'{cls.site_base_url}/articles/{keyword_num_part}/'
            # logger.debug(f"[{cls.site_name} Search] Requesting URL: {search_url}") # 이전 로그에서 확인되므로 주석 처리 가능
            
            tree = SiteUtil.get_tree(search_url, proxy_url=proxy_url)
            if tree is None:
                logger.warning(f"[{cls.site_name} Search] Failed to get HTML tree for URL: {search_url}")
                ret['data'] = 'failed to get tree'
                return ret

            # 페이지를 찾을 수 없는 경우
            not_found_title_elements = tree.xpath('/html/head/title/text()')
            not_found_h1_elements = tree.xpath('/html/body/div/div/div/main/div/div/h1/text()')
            is_page_not_found = False
            if not_found_title_elements and 'お探しの商品が見つかりません' in not_found_title_elements[0]:
                is_page_not_found = True
            elif not_found_h1_elements and "404 Not Found" in not_found_h1_elements[0]:
                is_page_not_found = True
            
            if is_page_not_found:
                logger.debug(f"[{cls.site_name} Search] Page not found on site for keyword_num_part: {keyword_num_part}")
                ret['data'] = 'not found on site'
                return ret

            item = EntityAVSearch(cls.site_name)
            item.code = cls.module_char + cls.site_char + keyword_num_part
            
            info_block_xpath_base = '/html/body/div[1]/div/div/main/div/section/div[1]/div[1]'

            # 제목 (번역 안 함)
            title_elements = tree.xpath(f'{info_block_xpath_base}/div[2]/h2/a/text()')
            if title_elements:
                item.title = title_elements[0].strip()
                # logger.debug(f"[{cls.site_name} Search] Parsed title: {item.title}")
            else:
                item.title = f"FC2-{keyword_num_part}" 
                logger.warning(f"[{cls.site_name} Search] Title not found. Using fallback: {item.title}")
            item.title_ko = item.title # title_ko에도 원본 제목 할당 (또는 None)

            # 출시년도
            year_text_elements = tree.xpath(f"{info_block_xpath_base}/div[2]/div[starts-with(normalize-space(.), '販売日：')]/span/text()")
            if year_text_elements:
                date_str = year_text_elements[0].strip()
                if re.match(r'\d{4}-\d{2}-\d{2}', date_str):
                    item.year = int(date_str.split('-')[0])
                    # logger.debug(f"[{cls.site_name} Search] Parsed year: {item.year}")
            # else: item.year = 0 # 또는 EntityAVSearch 기본값 사용

            # 이미지 URL (파싱된 원본 URL 그대로 사용)
            img_elements = tree.xpath(f'{info_block_xpath_base}/div[1]/a/img/@src')
            if img_elements:
                image_url_temp = img_elements[0]
                if image_url_temp.startswith('/'):
                    item.image_url = cls.site_base_url + image_url_temp
                else:
                    item.image_url = image_url_temp
                # logger.debug(f"[{cls.site_name} Search] Parsed image URL for search result: {item.image_url}")
            # else: item.image_url = None # 또는 EntityAVSearch 기본값 사용

            item.ui_code = f'FC2-{keyword_num_part}'
            item.score = 100 

            # logger.debug(f"[{cls.site_name} Search] Final item for keyword_num_part '{keyword_num_part}': score={item.score}, ui_code='{item.ui_code}', title='{item.title}', year={item.year if hasattr(item, 'year') else 'N/A'}, image_url='{item.image_url}'")
            
            ret['data'].append(item.as_dict())
            ret['ret'] = 'success'

        except Exception as exception: 
            logger.error(f'[{cls.site_name} Search] Exception for keyword_num_part {keyword_num_part}: {exception}')
            logger.error(traceback.format_exc())
            ret['ret'] = 'exception'
            ret['data'] = str(exception)
        
        return ret


    @classmethod
    def info(cls, code_module_site_id, do_trans=True, proxy_url=None, image_mode='0', 
             use_image_server=False, image_server_url=None, image_server_local_path=None, 
             url_prefix_segment=None, max_arts=0, use_extras=True, **kwargs):

        keyword_num_part = code_module_site_id[len(cls.module_char) + len(cls.site_char):]
        ui_code_for_images = f'FC2-{keyword_num_part}'

        logger.debug(f"[{cls.site_name} Info] Code: {code_module_site_id} (NumPart: {keyword_num_part}), UI Code for Images: {ui_code_for_images}, image_mode: {image_mode}, use_image_server: {use_image_server}")
        if use_image_server:
            logger.debug(f"[{cls.site_name} Info] ImgServ URL: {image_server_url}, LocalPath: {image_server_local_path}, PrefixSeg: {url_prefix_segment}")

        ret = {'ret': 'failed', 'data': None}

        try:
            info_url = f'{cls.site_base_url}/articles/{keyword_num_part}/'
            logger.debug(f"[{cls.site_name} Info] Requesting URL: {info_url}")
            
            tree = SiteUtil.get_tree(info_url, proxy_url=proxy_url)
            if tree is None:
                logger.warning(f"[{cls.site_name} Info] Failed to get HTML tree for URL: {info_url}")
                ret['data'] = 'failed to get tree'
                return ret

            not_found_title_elements = tree.xpath('/html/head/title/text()')
            not_found_h1_elements = tree.xpath('/html/body/div/div/div/main/div/div/h1/text()')
            is_page_not_found = False
            if not_found_title_elements and 'お探しの商品が見つかりません' in not_found_title_elements[0]:
                is_page_not_found = True
            elif not_found_h1_elements and "404 Not Found" in not_found_h1_elements[0]:
                is_page_not_found = True

            if is_page_not_found:
                logger.info(f'[{cls.site_name} Info] Page not found on site for code: {code_module_site_id}')
                ret['data'] = 'not found on site'
                return ret

            entity = EntityMovie(cls.site_name, code_module_site_id)
            entity.country = ['일본']
            entity.mpaa = '청소년 관람불가'
            
            info_base_xpath = '/html/body/div[1]/div/div/main/div/section/div[1]/div[1]/div[2]'
            info_base_elements = tree.xpath(info_base_xpath)
            if not info_base_elements:
                logger.error(f"[{cls.site_name} Info] Main info block not found for {code_module_site_id}")
                ret['data'] = 'Main info block not found on page'
                return ret
            info_element = info_base_elements[0]

            entity.thumb = []
            poster_xpath = '/html/body/div[1]/div/div/main/div/section/div[1]/div[1]/div[1]/a/img/@src'
            poster_img_elements = tree.xpath(poster_xpath)
            if poster_img_elements:
                poster_url_temp = poster_img_elements[0]
                if poster_url_temp.startswith('/'):
                    poster_url_original = cls.site_base_url + poster_url_temp
                else:
                    poster_url_original = poster_url_temp
                logger.debug(f"[{cls.site_name} Info] Original poster URL: {poster_url_original}")

                if use_image_server and image_mode == '4' and image_server_local_path and image_server_url and url_prefix_segment:
                    logger.debug(f"[{cls.site_name} Info] Using image server for poster. UI Code for filename: {ui_code_for_images}")
                    saved_poster_path = SiteUtil.save_image_to_server_path(
                        poster_url_original, 
                        'p',
                        image_server_local_path, 
                        url_prefix_segment, 
                        ui_code_for_images,
                        proxy_url=proxy_url
                    )
                    if saved_poster_path:
                        entity.thumb.append(EntityThumb(aspect='poster', value=f"{image_server_url}/{saved_poster_path}"))
                        logger.debug(f"[{cls.site_name} Info] Poster saved to image server: {image_server_url}/{saved_poster_path}")
                    else: 
                        logger.warning(f"[{cls.site_name} Info] Failed to save poster to image server. Falling back to process_image_mode.")
                        processed_poster_url = SiteUtil.process_image_mode(image_mode, poster_url_original, proxy_url=proxy_url)
                        if processed_poster_url:
                            entity.thumb.append(EntityThumb(aspect='poster', value=processed_poster_url))
                else: 
                    processed_poster_url = SiteUtil.process_image_mode(image_mode, poster_url_original, proxy_url=proxy_url)
                    if processed_poster_url:
                        entity.thumb.append(EntityThumb(aspect='poster', value=processed_poster_url))
                    else:
                        logger.warning(f"[{cls.site_name} Info] Failed to get processed poster URL for: {poster_url_original}")
            else:
                logger.debug(f'[{cls.site_name} Info] 포스터 이미지를 찾을 수 없음: {code_module_site_id}')

            entity.fanart = []
            entity.extras = []

            title_xpath = './h2/a/text()'
            title_elements = info_element.xpath(title_xpath)
            if title_elements:
                raw_title = title_elements[0].strip()
                logger.debug(f"[{cls.site_name} Info] Raw title for tagline/plot: {raw_title}")
                entity.tagline = SiteUtil.trans(raw_title, do_trans=do_trans, source='ja', target='ko')
                entity.plot = entity.tagline 
                # logger.debug(f"[{cls.site_name} Info] Processed tagline/plot: {entity.tagline}")
            else:
                logger.debug(f"[{cls.site_name} Info] Tagline/Plot (Title) not found for {code_module_site_id}")

            date_xpath = "./div[starts-with(normalize-space(.), '販売日：')]/span/text()"
            date_elements = info_element.xpath(date_xpath)
            if date_elements:
                date_str = date_elements[0].strip()
                if re.match(r'\d{4}-\d{2}-\d{2}', date_str):
                    entity.premiered = date_str
                    entity.year = int(date_str.split('-')[0])
                    # logger.debug(f"[{cls.site_name} Info] Parsed premiered: {entity.premiered}, year: {entity.year}")
                else:
                    logger.debug(f"[{cls.site_name} Info] Date format mismatch: {date_str} in {code_module_site_id}")
            else:
                logger.debug(f"[{cls.site_name} Info] Premiered date not found for {code_module_site_id}")

            studio_xpath = "./div[starts-with(normalize-space(.), '販売者：')]/span/a/text()"
            studio_elements = info_element.xpath(studio_xpath)
            if studio_elements:
                entity.studio = studio_elements[0].strip()
                # logger.debug(f"[{cls.site_name} Info] Parsed studio (from link): {entity.studio}")
            else: 
                studio_text_xpath = "./div[starts-with(normalize-space(.), '販売者：')]/span/text()"
                studio_text_elements = info_element.xpath(studio_text_xpath)
                if studio_text_elements and studio_text_elements[0].strip():
                    entity.studio = studio_text_elements[0].strip()
                    # logger.debug(f"[{cls.site_name} Info] Parsed studio (from text): {entity.studio}")
                else:
                    logger.debug(f"[{cls.site_name} Info] Studio (販売者) not found for {code_module_site_id}")
            
            actor_xpath = "./div[starts-with(normalize-space(.), '女優：')]/span//a/text() | ./div[starts-with(normalize-space(.), '女優：')]/span/text()[normalize-space()]"
            actor_name_elements = info_element.xpath(actor_xpath)
            if actor_name_elements:
                entity.actor = []
                processed_actors = set()
                for actor_name_part in actor_name_elements:
                    individual_names = [name.strip() for name in re.split(r'[,/\s]+', actor_name_part.strip()) if name.strip()]
                    for name_ja in individual_names:
                        if name_ja and name_ja not in processed_actors:
                            actor_obj = EntityActor(SiteUtil.trans(name_ja, do_trans=do_trans, source='ja', target='ko'))
                            actor_obj.originalname = name_ja
                            entity.actor.append(actor_obj)
                            processed_actors.add(name_ja)
                            # logger.debug(f"[{cls.site_name} Info] Added actor: {name_ja} (KO: {actor_obj.name})")
            if not hasattr(entity, 'actor') or not entity.actor:
                logger.debug(f"[{cls.site_name} Info] Actors (女優) not found or empty for {code_module_site_id}")

            entity.tag = ['FC2']
            logger.debug(f"[{cls.site_name} Info] Default tag set: {entity.tag}")

            entity.genre = []
            genre_xpath = "./div[starts-with(normalize-space(.), 'タグ：')]/span//a/text() | ./div[starts-with(normalize-space(.), 'タグ：')]/span/text()[normalize-space()]"
            genre_elements = info_element.xpath(genre_xpath)
            if genre_elements:
                raw_genres_from_site = []
                for gen_text_part in genre_elements:
                    individual_tags = [tag.strip() for tag in re.split(r'[,/\s]+', gen_text_part.strip()) if tag.strip()]
                    raw_genres_from_site.extend(individual_tags)
                
                processed_genres = set()
                for item_genre_ja in raw_genres_from_site:
                    if item_genre_ja not in processed_genres:
                        translated_genre = SiteUtil.trans(item_genre_ja, do_trans=do_trans, source='ja', target='ko')
                        entity.genre.append(translated_genre) 
                        processed_genres.add(item_genre_ja)
                        # logger.debug(f"[{cls.site_name} Info] Added genre: {item_genre_ja} (KO: {translated_genre})")
            if not entity.genre:
                logger.debug(f"[{cls.site_name} Info] Genres (タグ) not found or empty for {code_module_site_id}")

            runtime_xpath = "./div[starts-with(normalize-space(.), '収録時間：')]/span/text()"
            runtime_elements = info_element.xpath(runtime_xpath)
            if runtime_elements:
                time_str = runtime_elements[0].strip()
                parts = time_str.split(':')
                try:
                    if len(parts) == 3:
                        h, m, s = map(int, parts)
                        entity.runtime = h * 60 + m
                    elif len(parts) == 2:
                        m, s = map(int, parts)
                        entity.runtime = m
                    else:
                        logger.debug(f"[{cls.site_name} Info] Unexpected runtime format: {time_str} for {code_module_site_id}")
                    if hasattr(entity, 'runtime') and entity.runtime is not None:
                        logger.debug(f"[{cls.site_name} Info] Parsed runtime (minutes): {entity.runtime}")
                except ValueError:
                    logger.debug(f"[{cls.site_name} Info] Failed to parse runtime string: {time_str} for {code_module_site_id}")
            else:
                logger.debug(f"[{cls.site_name} Info] Runtime (収録時間) not found for {code_module_site_id}")

            entity.title = f'FC2-{keyword_num_part}'
            entity.originaltitle = f'FC2-{keyword_num_part}'
            entity.sorttitle = f'FC2-{keyword_num_part}'
            logger.debug(f"[{cls.site_name} Info] Set fixed title/originaltitle/sorttitle: {entity.title}")
            
            logger.info(f"[{cls.site_name} Info] Successfully processed info for code: {code_module_site_id}")
            ret['ret'] = 'success'
            ret['data'] = entity.as_dict()

        except Exception as exception: 
            logger.error(f'[{cls.site_name} Info] Exception for code {code_module_site_id}: {exception}')
            logger.error(traceback.format_exc())
            ret['ret'] = 'exception'
            ret['data'] = str(exception)
        
        return ret
