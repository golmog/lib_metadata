# -*- coding: utf-8 -*-
import requests
import re
import json
import traceback
import time

from lxml import html

from framework import SystemModelSetting
from framework.util import Util
from system import SystemLogicTrans

# lib_metadata
from ..entity_av import EntityAVSearch
from ..entity_base import EntityMovie, EntityThumb, EntityActor, EntityRatings, EntityExtra, EntityReview
from ..site_util import SiteUtil

#########################################################
from ..plugin import P
logger = P.logger

class SiteFc2ppvdb(object):
    site_name = 'fc2ppvdb'
    site_base_url = 'https://fc2ppvdb.com'
    module_char = 'L'
    site_char = 'P'

    @classmethod
    def search(cls, keyword_num_part, do_trans=False, proxy_url=None, image_mode='0', manual=False, 
                 not_found_delay_seconds=0, **kwargs):
        logger.debug(f"[{cls.site_name} Search Keyword(num_part): {keyword_num_part}, manual: {manual}, proxy: {'Yes' if proxy_url else 'No'}, delay: {not_found_delay_seconds}s")

        ret = {'ret': 'failed', 'data': []}

        try:
            search_url = f'{cls.site_base_url}/articles/{keyword_num_part}/'
            # logger.debug(f"[{cls.site_name} Search] Requesting URL: {search_url}")

            tree = SiteUtil.get_tree(search_url, proxy_url=proxy_url)
            if tree is None:
                logger.warning(f"[{cls.site_name} Search] Failed to get HTML tree for URL: {search_url}")
                ret['data'] = 'failed to get tree'
                if not_found_delay_seconds > 0:
                    logger.info(f"[{cls.site_name} Search] 'failed to get tree', delaying for {not_found_delay_seconds} seconds.")
                    time.sleep(not_found_delay_seconds)
                return ret

            # 페이지를 찾을 수 없는 경우
            not_found_title_elements = tree.xpath('/html/head/title/text()')
            not_found_h1_elements = tree.xpath('/html/body/div/div/div/main/div/div/h1/text()')
            is_page_not_found = False
            if not_found_title_elements and 'お探しの商品が見つかりません' in not_found_title_elements[0]:
                is_page_not_found = True
            elif not_found_title_elements and 'not found' in not_found_title_elements[0].lower():
                logger.debug(f"[{cls.site_name} Search] Page Not Found {keyword_num_part} (429 Too many requests)")
                is_page_not_found = True
            elif not_found_h1_elements and "404 Not Found" in not_found_h1_elements[0]:
                is_page_not_found = True

            # 페이지 삭제
            # XPath: //div[contains(@class, 'absolute') and contains(@class, 'inset-0')]/h1[contains(text(), 'このページは削除されました')]
            # 더 간단하게: //h1[contains(text(), 'このページは削除されました')]
            deleted_page_elements = tree.xpath("//h1[contains(text(), 'このページは削除されました')]")
            if deleted_page_elements:
                logger.debug(f"[{cls.site_name} Search] Page deleted on site for keyword_num_part: {keyword_num_part} (문구: {deleted_page_elements[0].text.strip()})")
                is_page_not_found = True

            if is_page_not_found:
                logger.debug(f"[{cls.site_name} Search] Page not found or deleted on site for keyword_num_part: {keyword_num_part}")
                ret['data'] = 'not found on site'
                if not_found_delay_seconds > 0:
                    logger.debug(f"[{cls.site_name} Search] 'not found on site', delaying for {not_found_delay_seconds} seconds.")
                    time.sleep(not_found_delay_seconds)
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
             url_prefix_segment=None, max_arts=0, use_extras=True, 
             use_review=False, **kwargs):

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

            seller_name_raw = None
            
            seller_xpath_link = "./div[starts-with(normalize-space(.), '販売者：')]/span/a/text()"
            seller_elements_link = info_element.xpath(seller_xpath_link)

            if seller_elements_link:
                seller_name_raw = seller_elements_link[0].strip()
                logger.debug(f"[{cls.site_name} Info] Parsed Seller (for Director/Studio) from link: {seller_name_raw}")
            else: 
                seller_xpath_text = "./div[starts-with(normalize-space(.), '販売者：')]/span/text()"
                seller_elements_text = info_element.xpath(seller_xpath_text)
                if seller_elements_text and seller_elements_text[0].strip():
                    seller_name_raw = seller_elements_text[0].strip()
                    logger.debug(f"[{cls.site_name} Info] Parsed Seller (for Director/Studio) from text: {seller_name_raw}")
                else:
                    logger.debug(f"[{cls.site_name} Info] Seller (for Director/Studio) not found for {code_module_site_id}")

            if seller_name_raw:
                entity.director = entity.studio = seller_name_raw
            else:
                entity.director = entity.studio = None

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

            # 리뷰 정보 파싱
            entity.review = []
            if use_review:
                logger.debug(f"[{cls.site_name} Info] Parsing reviews for {code_module_site_id}")
                comments_section = tree.xpath("//div[@id='comments']")
                if comments_section:
                    comment_elements = comments_section[0].xpath("./div[starts-with(@id, 'comment-')]")
                    logger.debug(f"[{cls.site_name} Info] Found {len(comment_elements)} comment elements.")

                    for comment_el in comment_elements:
                        try:
                            review_obj = EntityReview(cls.site_name)

                            author_el = comment_el.xpath("./div[contains(@class, 'flex-auto')]/div[1]/div[1]/p/text()")
                            author = author_el[0].strip() if author_el and author_el[0].strip() else 'Anonymous'

                            up_votes_el = comment_el.xpath(".//span[starts-with(@id, 'up-counter-')]/text()")
                            up_votes = up_votes_el[0].strip() if up_votes_el else '0'

                            down_votes_el = comment_el.xpath(".//span[starts-with(@id, 'down-counter-')]/text()")
                            down_votes = down_votes_el[0].strip() if down_votes_el else '0'

                            date_id_text_el = comment_el.xpath("./div[contains(@class, 'flex-auto')]/div[1]/div[2]/p/text()")
                            review_date_str = ''
                            comment_id_str = ''
                            if date_id_text_el:
                                full_date_id_str = date_id_text_el[0].strip()
                                match_date = re.search(r'(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})', full_date_id_str)
                                if match_date:
                                    review_date_str = match_date.group(1)

                                match_id = re.search(r'ID:(\S+)', full_date_id_str)
                                if match_id:
                                    comment_id_str = match_id.group(1)

                            review_obj.author = author
                            if hasattr(review_obj, 'date') and review_date_str:
                                review_obj.date = review_date_str

                            comment_p_elements = comment_el.xpath("./div[contains(@class, 'flex-auto')]/p[contains(@class, 'text-gray-500')]")
                            comment_text_raw = ''
                            if comment_p_elements:
                                p_element = comment_p_elements[0]
                                parts = []
                                for node in p_element.xpath('./node()'):
                                    if isinstance(node, str):
                                        parts.append(node)
                                    elif hasattr(node, 'tag'):
                                        if node.tag == 'br':
                                            parts.append('\n')
                                        else:
                                            parts.append(html.tostring(node, encoding='unicode', method='html'))

                                inner_html_content_with_newlines = ''.join(parts)
                                temp_element = html.fromstring(f"<div>{inner_html_content_with_newlines}</div>")
                                comment_text_raw = temp_element.text_content().strip()

                            if not comment_text_raw:
                                logger.debug(f"[{cls.site_name} Info] Skipping comment (ID: {comment_id_str or 'N/A'}) due to empty content.")
                                continue

                            if hasattr(review_obj, 'source'):
                                review_obj.source = comment_text_raw

                            comment_text_for_display = SiteUtil.trans(comment_text_raw, do_trans=do_trans, source='ja', target='ko')

                            review_header_parts = [f"좋아요: {up_votes}", f"싫어요: {down_votes}"]
                            if review_date_str and not hasattr(review_obj, 'date'): # date 속성이 없을 경우 text에 포함
                                review_header_parts.append(f"작성일: {review_date_str}")

                            review_header = "[" + " / ".join(review_header_parts) + "]"
                            review_obj.text = f"{review_header} {comment_text_for_display}"

                            if comment_id_str:
                                review_obj.link = f"{info_url}#comment-{comment_id_str}"
                            else:
                                review_obj.link = info_url

                            entity.review.append(review_obj)
                            logger.debug(f"[{cls.site_name} Info] Added review by '{author}': Up={up_votes}, Down={down_votes}, Date='{review_date_str}', ID='{comment_id_str}'")

                        except Exception as e_review:
                            logger.error(f"[{cls.site_name} Info] Exception parsing a review for {code_module_site_id}: {e_review}")
                            logger.error(traceback.format_exc())
                else:
                    logger.debug(f"[{cls.site_name} Info] No comments section found for {code_module_site_id}")
            else:
                logger.debug(f"[{cls.site_name} Info] Skipping review parsing as 'use_review' is False for {code_module_site_id}")

            logger.info(f"[{cls.site_name} Info] Successfully processed info for code: {code_module_site_id}")
            ret['ret'] = 'success'
            ret['data'] = entity.as_dict()

        except Exception as exception: 
            logger.error(f'[{cls.site_name} Info] Exception for code {code_module_site_id}: {exception}')
            logger.error(traceback.format_exc())
            ret['ret'] = 'exception'
            ret['data'] = str(exception)

        return ret
