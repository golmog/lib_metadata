import os

from framework import app

try:
    import xmltodict
except ImportError:
    try:
        os.system(f"{app.config['config']['pip']} install xmltodict cloudscraper")
    except Exception:
        pass

try:
    import cloudscraper
except ImportError:
    try:
        os.system(f"{app.config['config']['pip']} install cloudscraper")
    except Exception:
        pass

from .plugin import P

blueprint = P.blueprint
menu = P.menu
plugin_load = P.plugin_load
plugin_unload = P.plugin_unload
plugin_info = P.plugin_info

from .server_util import MetadataServerUtil
from .site_util import SiteUtil
from .util_nfo import UtilNfo
from .site_daum import SiteDaumTv
from .site_daum_movie import SiteDaumMovie
from .site_tmdb import SiteTmdbTv, SiteTmdbMovie, SiteTmdbFtv
from .site_tving import SiteTvingTv, SiteTvingMovie
from .site_wavve import SiteWavveTv, SiteWavveMovie
from .site_naver import SiteNaverMovie
from .site_naver_book import SiteNaverBook
from .site_watcha import SiteWatchaMovie, SiteWatchaTv
from .site_tvdb import SiteTvdbTv

from .site_hentaku import SiteHentaku
from .site_avdbs import SiteAvdbs
from .site_dmm import SiteDmm
from .site_javbus import SiteJavbus
from .site_jav321 import SiteJav321
from .site_mgstage import SiteMgstageDvd
from .site_javdb import SiteJavdb

from .site_vibe import SiteVibe
from .site_melon import SiteMelon
from .site_lastfm import SiteLastfm

from .site_uncensored.site_1pondotv import Site1PondoTv
from .site_uncensored.site_10musume import Site10Musume
from .site_uncensored.site_heyzo import SiteHeyzo
from .site_uncensored.site_carib import SiteCarib

from .site_fc2.site_fc2ppvdb import SiteFc2ppvdb
