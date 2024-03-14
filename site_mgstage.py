import re

from .constants import MGS_CODE_LEN, MGS_LABEL_MAP
from .entity_av import EntityAVSearch
from .entity_base import EntityActor, EntityExtra, EntityMovie, EntityRatings
from .plugin import P
from .site_util import SiteUtil

logger = P.logger


class SiteMgstage:
    site_name = "mgs"
    site_char = "M"
    site_base_url = "https://www.mgstage.com"
    module_char = None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/71.0.3578.98 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cookie": "coc=1;mgs_agef=1;",
    }

    PTN_SEARCH_PID = re.compile(r"\/product_detail\/(?P<code>.*?)\/")
    PTN_SEARCH_REAL_NO = re.compile(r"^\d*(?P<real>[a-zA-Z]+)\-(?P<no>\d+)$")
    PTN_TEXT_SUB = [
        re.compile(r"【(?<=【)(?:MGSだけのおまけ映像付き|期間限定).*(?=】)】(:?\s?\+\d+分\s?)?"),
        re.compile(r"※通常版\+\d+分の特典映像付のスペシャルバージョン！"),
    ]
    PTN_RATING = re.compile(r"\s(?P<rating>[\d\.]+)点\s.+\s(?P<vote>\d+)\s件")

    @classmethod
    def __search(cls, keyword, do_trans=True, proxy_url=None, image_mode="0", manual=False):
        keyword = keyword.strip().lower()
        if keyword[-3:-1] == "cd":
            keyword = keyword[:-3]
        keyword = keyword.replace(" ", "-")

        if cls.module_char == "C":
            module_query = "&is_dvd_product=1&type=dvd"
        elif cls.module_char == "D":
            module_query = "&is_dvd_product=0&type=haishin"
        else:
            raise ValueError(f"Class variable for 'module_char' should be either 'C' or 'D': {cls.module_char}")

        url = f"{cls.site_base_url}/search/cSearch.php?search_word={keyword}&x=0&y=0{module_query}"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)
        lists = tree.xpath('//div[@class="search_list"]/div/ul/li')
        logger.debug("mgs search kwd=%s len=%d", keyword, len(lists))

        ret = []
        for node in lists[:10]:
            try:
                item = EntityAVSearch(cls.site_name)
                tag = node.xpath(".//a")[0]
                href = tag.attrib["href"].lower()
                match = cls.PTN_SEARCH_PID.search(href)
                if match:
                    item.code = cls.module_char + cls.site_char + match.group("code").upper()
                already_exist = False
                for exist_item in ret:
                    if exist_item["code"] == item.code:
                        already_exist = True
                        break
                if already_exist:
                    continue

                tag = node.xpath(".//img")[0]
                item.image_url = tag.attrib["src"]

                tag = node.xpath('.//p[@class="title lineclamp"]')[0]
                title = tag.text_content()
                for ptn in cls.PTN_TEXT_SUB:
                    title = ptn.sub("", title)
                item.title = item.title_ko = title.strip()

                # tmp = SiteUtil.discord_proxy_get_target(item.image_url)
                # 2021-03-22 서치에는 discord 고정 url을 사용하지 않는다. 3번
                # manual == False  때는 아예 이미치 처리를 할 필요가 없다.
                # 일치항목 찾기 때는 화면에 보여줄 필요가 있는데 3번은 하면 하지 않는다.
                if manual:
                    _image_mode = "1" if image_mode != "0" else image_mode
                    item.image_url = SiteUtil.process_image_mode(_image_mode, item.image_url, proxy_url=proxy_url)
                    if do_trans:
                        item.title_ko = "(현재 인터페이스에서는 번역을 제공하지 않습니다) " + item.title
                else:
                    item.title_ko = SiteUtil.trans(item.title, do_trans=do_trans)

                match = cls.PTN_SEARCH_REAL_NO.search(item.code[2:])
                if match:
                    item.ui_code = match.group("real") + "-" + match.group("no")
                else:
                    item.ui_code = item.code[2:]

                if item.ui_code == keyword.upper():
                    item.score = 100
                elif keyword.upper().replace(item.ui_code, "").isnumeric():
                    item.score = 100
                else:
                    item.score = 60 - (len(ret) * 10)
                if item.score < 0:
                    item.socre = 0
                ret.append(item.as_dict())
            except Exception:
                logger.exception("개별 검색 결과 처리 중 예외:")
        return sorted(ret, key=lambda k: k["score"], reverse=True)

    @classmethod
    def search(cls, keyword, **kwargs):
        ret = {}
        try:
            data = []
            tmps = keyword.upper().replace("-", " ").split()
            if len(tmps) == 2:
                label, code = tmps
                numlabels = MGS_LABEL_MAP.get(label) or []
                if numlabels:
                    if label not in numlabels:
                        numlabels.append(label)
                    for idx, lab in enumerate(numlabels):
                        if codelen := MGS_CODE_LEN.get(lab):
                            try:
                                code = str(int(code)).zfill(codelen)
                            except ValueError:
                                pass
                        _d = cls.__search(f"{lab}-{code}", **kwargs)
                        if _d:
                            data += _d
                            if idx > 0:
                                # last hit to first by keeping mutability
                                numlabels.remove(lab)
                                numlabels.insert(0, lab)
                            break
            if not data:
                data = cls.__search(keyword, **kwargs)
        except Exception as exception:
            logger.exception("검색 결과 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success" if data else "no_match"
            ret["data"] = data
        return ret


class SiteMgstageAma(SiteMgstage):
    module_char = "D"

    @classmethod
    def __img_urls(cls, tree):
        """collect raw image urls from html page"""

        # poster large
        pl = tree.xpath('//*[@id="package"]/a/@href')
        pl = pl[0] if pl else ""
        if not pl:
            logger.warning("이미지 URL을 얻을 수 없음: poster large")

        # poster small
        # 세로 이미지 / 저화질 썸네일
        # 없는 경우 있음: SIRO GANA
        ps = tree.xpath('//div[@class="detail_photo"]//img/@src')
        ps = ps[0] if ps else ""
        if not ps and "pb_e_" in pl:
            ps = pl.replace("pb_e_", "pf_o1_")

        # fanart
        arts = tree.xpath('//*[@id="sample-photo"]//ul/li/a/@href')
        arts.insert(0, pl.replace("pb_e_", "pf_e_"))  # 세로 고화질 이미지

        return {"ps": ps, "pl": pl, "arts": arts}

    @classmethod
    def __info(
        cls,
        code,
        do_trans=True,
        proxy_url=None,
        image_mode="0",
        max_arts=10,
        use_extras=True,
        ps_to_poster=False,
        crop_mode=None,
    ):
        url = cls.site_base_url + f"/product/product_detail/{code[2:]}/"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]
        entity.mpaa = "청소년 관람불가"

        #
        # 이미지 관련 시작
        #
        img_urls = cls.__img_urls(tree)
        SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)

        entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)

        entity.fanart = []
        for href in img_urls["arts"][:max_arts]:
            entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))
        #
        # 이미지 관련 끝
        #

        h1 = tree.xpath('//h1[@class="tag"]/text()')[0]
        for ptn in cls.PTN_TEXT_SUB:
            h1 = ptn.sub("", h1)
        entity.tagline = SiteUtil.trans(h1, do_trans=do_trans)

        basetag = '//div[@class="detail_data"]'

        tags = tree.xpath(f"{basetag}//tr")
        tmp_premiered = None
        for tag in tags:
            key = tag.xpath("./th")
            if not key:
                continue
            key = key[0].text_content().strip()
            value = tag.xpath("./td")[0].text_content().strip()
            if key == "商品発売日：":
                try:
                    entity.year = int(value[:4])
                    entity.premiered = value.replace("/", "-")
                except Exception:
                    pass
            elif key == "配信開始日：":
                tmp_premiered = value.replace("/", "-")
            elif key == "収録時間：":
                entity.runtime = int(value.replace("min", ""))
            elif key == "出演：":
                entity.actor = [EntityActor(x.strip()) for x in tag.xpath("./td/a/text()")]
            elif key == "監督：":
                entity.director = value
            elif key == "シリーズ：":
                # series
                if entity.tag is None:
                    entity.tag = []
                entity.tag.append(SiteUtil.trans(value, do_trans=do_trans))
            elif key == "レーベル：":
                # label
                entity.studio = value
                if do_trans:
                    if value in SiteUtil.av_studio:
                        entity.studio = SiteUtil.av_studio[value]
                    else:
                        entity.studio = SiteUtil.change_html(SiteUtil.trans(value))
            elif key == "ジャンル：":
                # genre
                a_tags = tag.xpath("./td/a")
                entity.genre = []
                for tag in a_tags:
                    tmp = tag.text_content().strip()
                    if "MGSだけのおまけ映像付き" in tmp:
                        continue
                    if tmp in SiteUtil.av_genre:
                        entity.genre.append(SiteUtil.av_genre[tmp])
                    elif tmp in SiteUtil.av_genre_ignore_ja:
                        continue
                    else:
                        genre_tmp = SiteUtil.trans(tmp, do_trans=do_trans).replace(" ", "")
                        if genre_tmp not in SiteUtil.av_genre_ignore_ko:
                            entity.genre.append(genre_tmp)
            elif key == "品番：":
                match = cls.PTN_SEARCH_REAL_NO.match(value)
                if match:
                    label = match.group("real").upper()
                    value = label + "-" + str(int(match.group("no"))).zfill(3)
                    if entity.tag is None:
                        entity.tag = []
                    entity.tag.append(label)
                entity.title = entity.originaltitle = entity.sorttitle = value

        if entity.premiered is None and tmp_premiered is not None:
            entity.premiered = tmp_premiered
            entity.year = int(tmp_premiered[:4])

        for br in tree.xpath('//*[@id="introduction"]//p//br'):
            br.tail = "\n" + br.tail if br.tail else "\n"
        tmp = tree.xpath('//*[@id="introduction"]//p[1]')[0].text_content()
        if not tmp:
            tmp = tree.xpath('//*[@id="introduction"]//p[2]')[0].text_content()
        for ptn in cls.PTN_TEXT_SUB:
            tmp = ptn.sub("", tmp)
        entity.plot = SiteUtil.trans(tmp, do_trans=do_trans)  # NOTE: 번역을 거치면서 newline이 모두 사라진다.

        try:
            tag = tree.xpath('//div[@class="user_review_head"]/p[@class="detail"]/text()')
            if tag:
                match = cls.PTN_RATING.search(tag[0])
                if match:
                    tmp = float(match.group("rating"))
                    entity.ratings = [EntityRatings(tmp, max=5, name="mgs", votes=int(match.group("vote")))]
        except Exception:
            logger.exception("평점 정보 처리 중 예외:")

        entity.extras = []
        if use_extras:
            try:
                tag = tree.xpath('//*[@class="sample_movie_btn"]/a/@href')
                if tag:
                    pid = tag[0].split("/")[-1]
                    url = f"https://www.mgstage.com/sampleplayer/sampleRespons.php?pid={pid}"
                    res = SiteUtil.get_response(url, proxy_url=proxy_url, headers=cls.headers).json()["url"]
                    entity.extras = [EntityExtra("trailer", entity.tagline, "mp4", res.split(".ism")[0] + ".mp4")]
            except Exception:
                logger.exception("미리보기 처리 중 예외:")

        try:
            return SiteUtil.shiroutoname_info(entity)
        except Exception:
            logger.exception("shiroutoname.com을 이용해 메타 보정 중 예외:")
            return entity

    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success"
            ret["data"] = entity.as_dict()
        return ret


class SiteMgstageDvd(SiteMgstage):
    module_char = "C"

    @classmethod
    def __img_urls(cls, tree):
        """collect raw image urls from html page"""

        # poster small
        # 세로 이미지 / 저화질 썸네일
        # 없는 경우가 있나?
        ps = tree.xpath('//div[@class="detail_photo"]//img/@src')
        ps = ps[0] if ps else ""
        if not ps:
            logger.warning("이미지 URL을 얻을 수 없음: poster small")

        # poster large
        pl = tree.xpath('//*[@id="package"]/a/@href')
        pl = pl[0] if pl else ""

        # fanart
        arts = tree.xpath('//*[@id="sample-photo"]//ul/li/a/@href')

        return {"ps": ps, "pl": pl, "arts": arts}

    @classmethod
    def __info(
        cls,
        code,
        do_trans=True,
        proxy_url=None,
        image_mode="0",
        max_arts=10,
        use_extras=True,
        ps_to_poster=False,
        crop_mode=None,
    ):
        url = cls.site_base_url + f"/product/product_detail/{code[2:]}/"
        tree = SiteUtil.get_tree(url, proxy_url=proxy_url, headers=cls.headers)

        entity = EntityMovie(cls.site_name, code)
        entity.country = ["일본"]
        entity.mpaa = "청소년 관람불가"

        #
        # 이미지 관련 시작
        #
        img_urls = cls.__img_urls(tree)
        SiteUtil.resolve_jav_imgs(img_urls, ps_to_poster=ps_to_poster, crop_mode=crop_mode, proxy_url=proxy_url)

        entity.thumb = SiteUtil.process_jav_imgs(image_mode, img_urls, proxy_url=proxy_url)

        entity.fanart = []
        for href in img_urls["arts"][:max_arts]:
            entity.fanart.append(SiteUtil.process_image_mode(image_mode, href, proxy_url=proxy_url))
        #
        # 이미지 관련 끝
        #

        h1 = tree.xpath('//h1[@class="tag"]/text()')[0]
        for ptn in cls.PTN_TEXT_SUB:
            h1 = ptn.sub("", h1)
        entity.tagline = SiteUtil.trans(h1, do_trans=do_trans)

        basetag = '//div[@class="detail_data"]'

        tags = tree.xpath(f"{basetag}//tr")
        tmp_premiered = None
        for tag in tags:
            key = tag.xpath("./th")
            if not key:
                continue
            key = key[0].text_content().strip()
            value = tag.xpath("./td")[0].text_content().strip()
            if key == "商品発売日：":
                entity.premiered = value.replace("/", "-")
                entity.year = int(value[:4])
            elif key == "配信開始日：":
                tmp_premiered = value.replace("/", "-")
            elif key == "収録時間：":
                entity.runtime = int(value.replace("min", ""))
            elif key == "出演：":
                entity.actor = [EntityActor(x.strip()) for x in tag.xpath("./td/a/text()")]
            elif key == "監督：":
                entity.director = value
            elif key == "シリーズ：":
                # series
                if entity.tag is None:
                    entity.tag = []
                entity.tag.append(SiteUtil.trans(value, do_trans=do_trans))
            elif key == "レーベル：":
                # label
                entity.studio = value
                if do_trans:
                    if value in SiteUtil.av_studio:
                        entity.studio = SiteUtil.av_studio[value]
                    else:
                        entity.studio = SiteUtil.change_html(SiteUtil.trans(value))
            elif key == "ジャンル：":
                # genre
                a_tags = tag.xpath("./td/a")
                entity.genre = []
                for tag in a_tags:
                    tmp = tag.text_content().strip()
                    if "MGSだけのおまけ映像付き" in tmp:
                        continue
                    if tmp in SiteUtil.av_genre:
                        entity.genre.append(SiteUtil.av_genre[tmp])
                    elif tmp in SiteUtil.av_genre_ignore_ja:
                        continue
                    else:
                        genre_tmp = SiteUtil.trans(tmp, do_trans=do_trans).replace(" ", "")
                        if genre_tmp not in SiteUtil.av_genre_ignore_ko:
                            entity.genre.append(genre_tmp)
            elif key == "品番：":
                match = cls.PTN_SEARCH_REAL_NO.match(value)
                if match:
                    label = match.group("real").upper()
                    value = label + "-" + str(int(match.group("no"))).zfill(3)
                    if entity.tag is None:
                        entity.tag = []
                    entity.tag.append(label)
                entity.title = entity.originaltitle = entity.sorttitle = value

        if entity.premiered is None and tmp_premiered is not None:
            entity.premiered = tmp_premiered
            entity.year = int(tmp_premiered[:4])

        for br in tree.xpath('//*[@id="introduction"]//p//br'):
            br.tail = "\n" + br.tail if br.tail else "\n"
        tmp = tree.xpath('//*[@id="introduction"]//p[1]')[0].text_content()
        if not tmp:
            tmp = tree.xpath('//*[@id="introduction"]//p[2]')[0].text_content()
        for ptn in cls.PTN_TEXT_SUB:
            tmp = ptn.sub("", tmp)
        entity.plot = SiteUtil.trans(tmp, do_trans=do_trans)  # NOTE: 번역을 거치면서 newline이 모두 사라진다.

        try:
            tag = tree.xpath('//div[@class="user_review_head"]/p[@class="detail"]/text()')
            if tag:
                match = cls.PTN_RATING.search(tag[0])
                if match:
                    tmp = float(match.group("rating"))
                    entity.ratings = [EntityRatings(tmp, max=5, name="mgs", votes=int(match.group("vote")))]
        except Exception:
            logger.exception("평점 정보 처리 중 예외:")

        entity.extras = []
        if use_extras:
            try:
                tag = tree.xpath('//*[@class="sample_movie_btn"]/a/@href')
                if tag:
                    pid = tag[0].split("/")[-1]
                    url = f"https://www.mgstage.com/sampleplayer/sampleRespons.php?pid={pid}"
                    res = SiteUtil.get_response(url, proxy_url=proxy_url, headers=cls.headers).json()["url"]
                    entity.extras = [EntityExtra("trailer", entity.tagline, "mp4", res.split(".ism")[0] + ".mp4")]
            except Exception:
                logger.exception("미리보기 처리 중 예외:")

        return entity

    @classmethod
    def info(cls, code, **kwargs):
        ret = {}
        try:
            entity = cls.__info(code, **kwargs)
        except Exception as exception:
            logger.exception("메타 정보 처리 중 예외:")
            ret["ret"] = "exception"
            ret["data"] = str(exception)
        else:
            ret["ret"] = "success"
            ret["data"] = entity.as_dict()
        return ret
