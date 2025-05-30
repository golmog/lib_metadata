# -*- coding: utf-8 -*-
import requests
import re
import json
import traceback
from urllib.parse import urljoin
import os 
from framework import path_data

from lxml import html
from lxml import etree

from system import SystemLogicTrans

# lib_metadata
from ..entity_av import EntityAVSearch
from ..entity_base import EntityMovie, EntityThumb, EntityActor, EntityRatings, EntityExtra
from ..site_util import SiteUtil

#########################################################
from ..plugin import P
logger = P.logger

class SiteFc2Com(object):
    site_name = 'fc2com'
    site_base_url = 'https://adult.contents.fc2.com'
    module_char = 'L'
    site_char = 'F'

    fc2_cookies = {
        'wei6H': '1', # 성인 인증 플래그로 추정
        'language': 'ja', # 또는 'en'
        'GDPRCHECK': 'true',
        'contents_mode': 'digital',
        'contents_func_mode': 'buy',
        #'CONTENTS_FC2_PHPSESSID': '',
    }

    @staticmethod
    def _extract_fc2com_title(h3_element):
        if h3_element is None: return ""
        title_parts = []
        if h3_element.text: title_parts.append(h3_element.text.strip())
        for child in h3_element:
            is_target_span_to_remove = False
            if child.tag == 'span':
                if child.get('class') == 'items_article_saleTag': is_target_span_to_remove = True
                style_attr = child.get('style', '')
                if 'zoom:0.01' in style_attr or 'width:1px' in style_attr or 'height:1px' in style_attr or 'display:none' in style_attr:
                    is_target_span_to_remove = True
            if not is_target_span_to_remove:
                child_text = child.text_content()
                if child_text: title_parts.append(child_text.strip())
            if child.tail: title_parts.append(child.tail.strip())
        full_title_text = " ".join(filter(None, title_parts))
        return ' '.join(full_title_text.split()).strip()

    @classmethod
    def _get_fc2_page_content(cls, url, proxy_url=None, use_cloudscraper=True):
        headers = SiteUtil.default_headers.copy()
        headers['Referer'] = cls.site_base_url + "/" # adult.contents.fc2.com
        
        logger.debug(f"[{cls.site_name}] Requesting URL: {url} with Referer: {headers['Referer']}, use_cloudscraper: {use_cloudscraper}")

        res = None
        if use_cloudscraper:
            res = SiteUtil.get_response_cs(url, proxy_url=proxy_url, headers=headers, cookies=cls.fc2_cookies, timeout=20)
        
        if res is None and not use_cloudscraper:
            res = SiteUtil.get_response(url, proxy_url=proxy_url, headers=headers, cookies=cls.fc2_cookies, timeout=20)
        elif res is None and use_cloudscraper:
            logger.warning(f"[{cls.site_name}] Cloudscraper failed for {url}. Falling back to standard requests.")
            res = SiteUtil.get_response(url, proxy_url=proxy_url, headers=headers, cookies=cls.fc2_cookies, timeout=20)

        if res and res.status_code == 200:
            page_text = res.text
            
            is_login_page = False
            if "fc2.com" in res.url and "login.php" in res.url:
                is_login_page = True
                logger.warning(f"[{cls.site_name}] Detected redirection to FC2 main login page. Final URL: {res.url}")
            elif '<meta http-equiv="Refresh"' in page_text and ('login.php' in page_text or 'adult.contents.fc2.com/login/' in res.url or 'adult.contents.fc2.com/accountscenter/' in res.url):
                is_login_page = True
                logger.warning(f"[{cls.site_name}] Detected meta refresh or content indicating login/account page. URL: {url}, Final URL: {res.url}")
            
            if is_login_page:
                logger.warning(f"[{cls.site_name}] Treated as login page for URL: {url}. Cookies used: {cls.fc2_cookies}")
                return None, page_text

            if "お探しのページは見つかりませんでした。" in page_text:
                logger.info(f"[{cls.site_name}] Page explicitly states 'not found' for URL: {url}")
                return None, page_text 

            try:
                return html.fromstring(page_text), page_text
            except Exception as e_parse:
                logger.error(f"[{cls.site_name}] Failed to parse HTML: {e_parse}. URL: {url}")
                return None, page_text
        elif res:
            logger.warning(f"[{cls.site_name}] Failed to get page. Status: {res.status_code}. URL: {url}")
            return None, res.text if hasattr(res, 'text') else None
        else:
            logger.warning(f"[{cls.site_name}] Failed to get page (response is None). URL: {url}")
            return None, None


    @classmethod
    def search(cls, keyword_num_part, do_trans=False, proxy_url=None, image_mode='0', manual=False, **kwargs):
        logger.debug(f"[{cls.site_name} Search] Keyword(num_part): {keyword_num_part}, manual: {manual}, proxy: {'Yes' if proxy_url else 'No'}")
        # 기본 반환 상태: 'no_match', data는 빈 리스트
        ret = {'ret': 'no_match', 'data': []} 
        tree = None
        response_html_text = None

        try:
            search_url = f'{cls.site_base_url}/article/{keyword_num_part}/'
            tree, response_html_text = cls._get_fc2_page_content(search_url, proxy_url=proxy_url, use_cloudscraper=True)

            # HTML 저장 로직 (디버깅용)
            #if response_html_text:
            #    temp_filename = f"{cls.site_name}_search_{keyword_num_part}_{P.package_name}.html"
            #    temp_filepath = os.path.join(path_data, "tmp", temp_filename)
            #    try:
            #        os.makedirs(os.path.join(path_data, "tmp"), exist_ok=True)
            #        with open(temp_filepath, 'w', encoding='utf-8') as f:
            #            f.write(response_html_text)
            #        logger.debug(f"[{cls.site_name} Search] HTML content saved to: {temp_filepath}")
            #    except Exception as e_save:
            #        logger.error(f"[{cls.site_name} Search] Failed to save HTML to {temp_filepath}: {e_save}")

            if tree is None:
                failure_reason = "Failed to get or parse page content."
                if response_html_text and "お探しのページは見つかりませんでした。" in response_html_text:
                    failure_reason = "Page not found (お探しの商品が見つかりません)"
                    # ret['ret'] is already 'no_match'
                    ret['data'] = failure_reason
                elif response_html_text and ("fc2.com" in response_html_text and "login.php" in response_html_text or \
                                            '<meta http-equiv="Refresh"' in response_html_text and ('login.php' in response_html_text or 'adult.contents.fc2.com/login/' in response_html_text)):
                    failure_reason = "Redirected to login page"
                    ret['ret'] = 'error_site_login_required'
                    ret['data'] = failure_reason
                else:
                    ret['ret'] = 'error_site_page_load'
                    ret['data'] = failure_reason
                
                logger.warning(f"[{cls.site_name} Search] URL: {search_url}. Reason: {failure_reason}.")
                return ret

            # tree가 존재하면 파싱 시도
            item = EntityAVSearch(cls.site_name)
            
            # 필수 정보 파싱 (예: 제목)
            h3_title_element = tree.xpath('//div[contains(@class, "items_article_headerInfo")]/h3')
            if not h3_title_element:
                logger.warning(f"[{cls.site_name} Search] Essential title element not found for {search_url}. Assuming no valid item.")
                ret['data'] = f"Essential title element not found for {keyword_num_part}"
                return ret

            # --- 실제 아이템 정보 파싱 ---
            item.code = cls.module_char + cls.site_char + keyword_num_part
            
            extracted_title = cls._extract_fc2com_title(h3_title_element[0])
            item.title = extracted_title if extracted_title else f"FC2-{keyword_num_part}"
            item.title_ko = item.title

            # 연도 파싱
            year_text_elements = tree.xpath('//*[@id="top"]/div[1]/section[1]/div/section/div[2]/div[2]/p/text()')
            if year_text_elements and year_text_elements[0]:
                year_match = re.search(r'(\d{4})/\d{2}/\d{2}', year_text_elements[0])
                if year_match: item.year = int(year_match.group(1))

            # 이미지 URL 파싱
            img_elements = tree.xpath('//div[contains(@class, "items_article_MainitemThumb")]/span/img/@src')
            if not img_elements:
                img_elements = tree.xpath('//*[@id="top"]/div[1]/section[1]/div/section/div[1]/span/img/@src')

            if img_elements:
                img_src = img_elements[0]
                if img_src.startswith('//'): item.image_url = 'https:' + img_src
                elif img_src.startswith('/'): item.image_url = urljoin(f'{cls.site_base_url}/article/', img_src)
                else: item.image_url = img_src

            item.ui_code = f'FC2-{keyword_num_part}'
            item.score = 100
            
            ret['data'] = [item.as_dict()]
            ret['ret'] = 'success'

        except Exception as exception: 
            logger.error(f'[{cls.site_name} Search] Exception for keyword_num_part {keyword_num_part}: {exception}')
            logger.error(traceback.format_exc())
            ret['ret'] = 'error_site_exception'
            ret['data'] = str(exception)

        return ret


    @classmethod
    def _modify_fc2_image_url_width(cls, url, target_width=600):
        if not url or not isinstance(url, str):
            return url
        
        processed_url = url
        # 1. 프로토콜 보장 (https:)
        if processed_url.startswith('//'):
            processed_url = 'https:' + processed_url
        elif not processed_url.startswith(('http:', 'https:')):
            pass

        # 2. 너비 수정
        # 예: //contents-thumbnail2.fc2.com/w1280/storage... -> //contents-thumbnail2.fc2.com/w600/storage...
        # 또는 https://contents-thumbnail2.fc2.com/w1280/storage... -> https://contents-thumbnail2.fc2.com/w600/storage...
        final_url = re.sub(r'/w\d+/', f'/w{target_width}/', processed_url)
        
        # if final_url != processed_url :
        #    logger.debug(f"[{cls.site_name}] Image URL width modified: '{url}' -> '{final_url}' (target: w{target_width})")
        # else:
            # logger.debug(f"[{cls.site_name}] Image URL width modification: No pattern '/w<number>/' found or already target width in '{url}'. Returning: {final_url}")
        return final_url


    @classmethod
    def info(cls, code_module_site_id, do_trans=True, proxy_url=None, image_mode='0', 
             use_image_server=False, image_server_url=None, image_server_local_path=None, 
             url_prefix_segment=None, max_arts=0, use_extras=False, **kwargs):
        keyword_num_part = code_module_site_id[len(cls.module_char) + len(cls.site_char):]
        ui_code_for_images = f'FC2-{keyword_num_part}'

        logger.debug(f"[{cls.site_name} Info] Code: {code_module_site_id}, MaxArts: {max_arts}, UseExtras: {use_extras}")
        ret = {'ret': 'failed', 'data': None}
        tree_info = None
        response_html_text_info = None # HTML 저장용

        try:
            info_url = f'{cls.site_base_url}/article/{keyword_num_part}/'
            tree_info, response_html_text_info = cls._get_fc2_page_content(info_url, proxy_url=proxy_url, use_cloudscraper=True)

            if tree_info is None:
                logger.warning(f"[{cls.site_name} Info] Failed to get valid HTML tree for URL: {info_url}.")
                ret['data'] = 'failed to get tree or redirection page'
                return ret

            title_text_nodes_info = tree_info.xpath('/html/head/title/text()')
            if not title_text_nodes_info or (title_text_nodes_info and title_text_nodes_info[0] == 'お探しの商品が見つかりません'):
                logger.info(f'[{cls.site_name} Info] Page not found or invalid title for {code_module_site_id}')
                ret['data'] = 'not found on site or invalid title'
                return ret

            entity = EntityMovie(cls.site_name, code_module_site_id)
            entity.country = ['일본']
            entity.mpaa = '청소년 관람불가'

            # --- 상세 정보 파싱 시작 ---

            # 타이틀, Tagline, Plot (헬퍼 함수 사용)
            h3_title_element_info = tree_info.xpath('//div[contains(@class, "items_article_headerInfo")]/h3')
            if h3_title_element_info:
                raw_title_info = cls._extract_fc2com_title(h3_title_element_info[0])
                if raw_title_info:
                    entity.tagline = SiteUtil.trans(raw_title_info, do_trans=do_trans, source='ja', target='ko')
                    entity.plot = entity.tagline # Plot도 동일하게
                else:
                    logger.warning(f"[{cls.site_name} Info] Tagline/Plot (Title) extraction returned empty for {code_module_site_id}")
            else:
                logger.warning(f"[{cls.site_name} Info] Tagline/Plot (Title) h3 element not found for {code_module_site_id}")

            # 판매일 (Premiered, Year)
            date_text_elements = tree_info.xpath('//div[contains(@class, "items_article_softDevice")]/p[contains(text(), "Sale Day")]/text()')
            if not date_text_elements: # 기존 XPath도 시도
                date_text_elements = tree_info.xpath('//*[@id="top"]/div[1]/section[1]/div/section/div[2]/div[2]/p/text()')

            if date_text_elements and date_text_elements[0]:
                date_str_match = re.search(r'(\d{4})[/\-](\d{2})[/\-](\d{2})', date_text_elements[0]) # "Sale Day : " 부분은 무시
                if date_str_match:
                    year_str, month_str, day_str = date_str_match.groups()
                    entity.premiered = f"{year_str}-{month_str}-{day_str}"
                    entity.year = int(year_str)
                else:
                    logger.warning(f"[{cls.site_name} Info] 날짜 형식(YYYY/MM/DD)을 찾을 수 없음: {date_text_elements[0]}")
            else:
                logger.warning(f"[{cls.site_name} Info] 출시일 정보 XPath 결과 없음 또는 비어있음.")

            # 판매자 (Director, Studio)
            director_elements = tree_info.xpath('//div[contains(@class, "items_article_headerInfo")]/ul/li/a[contains(@href, "/users/")]/text()')
            if not director_elements :
                director_elements = tree_info.xpath('//*[@id="top"]/div[1]/section[1]/div/section/div[2]/ul/li/a/text()')

            if director_elements:
                seller_name_raw = director_elements[0].strip()
                processed_seller_name = SiteUtil.trans(seller_name_raw, do_trans=do_trans, source='ja', target='ko')
                entity.director = entity.studio = seller_name_raw
            else:
                logger.debug(f"[{cls.site_name} Info] 판매자(Director/Studio) 정보 XPath 결과 없음.")

            # 배우 (FC2Com은 명시적 배우 정보 드묾)
            entity.actor = []

            # 태그 (기본 "FC2" 및 판매자 이름)
            entity.tag = ['FC2']
            if entity.director and entity.director not in entity.tag:
                entity.tag.append(entity.director)

            # 장르 (Product tag)
            entity.genre = []
            genre_elements = tree_info.xpath('//section[contains(@class, "items_article_TagArea")]//a[contains(@class, "tagTag")]/text()')
            if genre_elements:
                for genre_text_ja in genre_elements:
                    genre_text_ja_cleaned = genre_text_ja.strip()
                    if genre_text_ja_cleaned:
                        translated_genre = SiteUtil.trans(genre_text_ja_cleaned, do_trans=do_trans, source='ja', target='ko')
                        entity.genre.append(translated_genre)

            # 재생 시간 (Runtime) - FC2Com에는 명시적 정보 드묾
            entity.runtime = None

            # 품번 기반 타이틀 (OriginalTitle, SortTitle, 기본 Title)
            entity.title = f'FC2-{keyword_num_part}' # 최종 포맷팅 전 기본값
            entity.originaltitle = f'FC2-{keyword_num_part}'
            entity.sorttitle = f'FC2-{keyword_num_part}'

            # --- 이미지 처리 ---
            entity.thumb = [] 
            entity.fanart = []

            # 1. 포스터 (p)
            try:
                poster_img_src_list = tree_info.xpath('//div[contains(@class, "items_article_MainitemThumb")]/span/img/@src')
                if not poster_img_src_list:
                    poster_img_src_list = tree_info.xpath('//*[@id="top"]/div[1]/section[1]/div/section/div[1]/span/img/@src')

                if poster_img_src_list:
                    poster_img_src = poster_img_src_list[0]
                    modified_poster_src = cls._modify_fc2_image_url_width(poster_img_src)
                    poster_url_original = 'https:' + modified_poster_src if modified_poster_src.startswith('//') \
                        else (urljoin(info_url, modified_poster_src) if modified_poster_src.startswith('/') else modified_poster_src)

                    if use_image_server and image_mode == '4':
                        saved_path = SiteUtil.save_image_to_server_path(poster_url_original, 'p', image_server_local_path, url_prefix_segment, ui_code_for_images, proxy_url=proxy_url)
                        if saved_path: entity.thumb.append(EntityThumb(aspect='poster', value=f"{image_server_url}/{saved_path}"))
                    else:
                        processed_url = SiteUtil.process_image_mode(image_mode, poster_url_original, proxy_url=proxy_url)
                        if processed_url: entity.thumb.append(EntityThumb(aspect='poster', value=processed_url))
                else: 
                    logger.debug(f'[{cls.site_name} Info] 포스터 이미지 XPath 결과 없음: {code_module_site_id}')
            except Exception as e_poster: 
                logger.error(f'[{cls.site_name} Info] 포스터 처리 중 예외: {e_poster}')

            # 2. 팬아트(art)
            sample_image_links_xpath = '//section[contains(@class, "items_article_SampleImages")]//ul[contains(@class, "items_article_SampleImagesArea")]/li/a/@href'
            sample_image_links = tree_info.xpath(sample_image_links_xpath)

            if sample_image_links:
                logger.debug(f"[{cls.site_name} Info] Found {len(sample_image_links)} sample images for arts.")
                if max_arts > 0:
                    for idx, art_href_raw in enumerate(sample_image_links):
                        if len(entity.fanart) >= max_arts: break

                        art_url_original = cls._modify_fc2_image_url_width(art_href_raw)
                        if art_url_original.startswith('/'): # 만약을 위한 상대경로 처리
                            art_url_original = urljoin(info_url, art_url_original)

                        if use_image_server and image_mode == '4':
                            # art_index는 0부터 시작하도록 수정 (또는 1부터 시작하도록 통일)
                            # SiteUtil.save_image_to_server_path의 art_index는 1부터 시작한다고 가정되어 있음
                            saved_art_path = SiteUtil.save_image_to_server_path(art_url_original, 'art', image_server_local_path, url_prefix_segment, ui_code_for_images, art_index=idx + 1, proxy_url=proxy_url)
                            if saved_art_path: entity.fanart.append(f"{image_server_url}/{saved_art_path}")
                        else:
                            processed_art_url = SiteUtil.process_image_mode(image_mode, art_url_original, proxy_url=proxy_url)
                            if processed_art_url: entity.fanart.append(processed_art_url)
                else:
                    logger.debug(f"[{cls.site_name} Info] max_arts is 0, skipping fanart processing.")
            else:
                logger.debug(f"[{cls.site_name} Info] 샘플 이미지(팬아트용) XPath 결과 없음.")

            # --- 엑스트라 (Sample Video) 파싱 시작 ---
            entity.extras = []
            if use_extras: 
                logger.debug(f"[{cls.site_name} Info] Extras (Sample Video) 파싱 시작.")
                video_tag_list = tree_info.xpath('//section[contains(@class, "items_article_SmapleVideo")]//div[contains(@class, "fc2-video-container")]/video')
                
                if video_tag_list:
                    video_tag = video_tag_list[0]
                    video_src_raw = video_tag.get('src')

                    if video_src_raw:
                        video_url_final = video_src_raw
                        if video_src_raw.startswith("//"):
                            video_url_final = "https:" + video_src_raw
                        elif not video_src_raw.startswith(("http:", "https:")) and video_src_raw.startswith("/"):
                            video_url_final = urljoin(info_url, video_src_raw)
                        elif not video_src_raw.startswith(("http:", "https:")):
                            logger.warning(f"[{cls.site_name} Info] Sample Video URL might be incomplete: {video_src_raw}. Attempting to prefix with https:")
                            video_url_final = "https:" + video_src_raw
                        
                        base_title_for_extra = entity.tagline
                        extra_title = f"{base_title_for_extra} - Sample Video"
                        
                        entity.extras.append(EntityExtra(
                            mode='trailer', 
                            title=extra_title,
                            type='video/mp4', 
                            content_url=video_url_final,
                            thumb="" # 썸네일은 에이전트가 pl 이미지 사용
                        ))
                        logger.debug(f"[{cls.site_name} Info] Sample Video 추가됨: {video_url_final}")
                    else:
                        logger.debug(f"[{cls.site_name} Info] Sample Video src 속성 없음.")
                else:
                    logger.debug(f"[{cls.site_name} Info] Sample Video <video> 태그 없음 또는 XPath 실패.")
            else:
                logger.debug(f"[{cls.site_name} Info] Extras 가져오기 비활성화 (use_extras: {use_extras}).")

            ret['ret'] = 'success'
            ret['data'] = entity.as_dict()

        except IndexError as e_idx_info:
            logger.error(f'[{cls.site_name} Info] IndexError for code {code_module_site_id}: {e_idx_info}')
            logger.error(traceback.format_exc())
            ret['ret'] = 'exception'
            ret['data'] = f"IndexError: {e_idx_info}"
        except Exception as exception: 
            logger.error(f'[{cls.site_name} Info] Exception for code {code_module_site_id}: {exception}')
            logger.error(traceback.format_exc())
            ret['ret'] = 'exception'
            ret['data'] = str(exception)

        return ret
