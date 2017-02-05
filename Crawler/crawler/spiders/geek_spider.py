import logging
import six

from scrapy.http import Request
from scrapy.utils.sitemap import Sitemap, sitemap_urls_from_robots
from bs4 import BeautifulSoup
import re

from scrapy.spiders import SitemapSpider
from datetime import datetime
import pymysql
from urllib.parse import *

from crawler.items import BrainedItem, BrainedItemLoader
from scrapy.selector import Selector

logger = logging.getLogger(__name__)

# db = pymysql.connect(host='93.174.131.56', port=3306, user='oldfox', password='StrongPassword111',
#                              db='ratepersons', charset='utf8mb4',
#                              cursorclass=pymysql.cursors.DictCursor)

db = pymysql.connect(host='localhost', port=3306, user='root', password='',
                     db='ratepersons', charset='utf8mb4',
                     cursorclass=pymysql.cursors.DictCursor)
cursor = db.cursor()
site_ids = {}


def get_new_sitemaps():
    sitemaps = []
    explored_sites_ids = set()
    new_sitemaps = []
    cursor.execute('SELECT * FROM Sites')
    for site in cursor:
        print('Site: {}\nSite ID: {}'.format(site['Name'], site['ID']))
        sitemap_url = urlunparse(('https', site['Name'], '/robots.txt', '', '', ''))
        site_ids[site['Name']] = site['ID']
        sitemaps.append(sitemap_url)
        print('Sitemap URL: ' + sitemap_url)

    print(site_ids)
    print(sitemaps)

    cursor.execute('SELECT * FROM Pages')
    for p in cursor:
        explored_sites_ids.add(p['SiteID'])

    print(explored_sites_ids)

    for site_name in site_ids:
        if site_ids[site_name] not in explored_sites_ids:
            robot = urlunparse(('https', site_name, '/robots.txt', '', '', ''))
            query = 'INSERT INTO Pages (Url, SiteID, FoundDateTime, LastScanDate) VALUES (%s, %s, %s, null)'
            cursor.execute(query, (robot, site_ids[site_name], datetime.today()))
            new_sitemaps.append(urlunparse(('https', site_name, '/robots.txt', '', '', '')))
            explored_sites_ids.add(site_ids[site_name])
            db.commit()

    return new_sitemaps


def get_keywords():
    keywords = {}
    cursor.execute('select * from `Persons`')
    personslist = cursor.fetchall()
    for person in personslist:
        query = "select * from `Keywords` where `Keywords`.`PersonID`=%s"
        cursor.execute(query, (person['ID'],))
        keywords[person['ID']] = []

    cursor.execute('select * from `Keywords`')
    keywordslist = cursor.fetchall()
    for keyword in keywordslist:
        if keyword['PersonID'] in keywords:
            keywords[keyword['PersonID']].append(keyword['Name'])

    return keywords


class GeekSitemapSpider(SitemapSpider):
    name = 'geek_sitemap_spider'

    sitemap_urls = get_new_sitemaps()
    old_sitemap_urls = []
    sitemap_follow = ['']

    keywords = get_keywords()

    def parse(self, response):
        print('Parsing... ', response.url)
        selector = Selector(response)
        l = BrainedItemLoader(BrainedItem(), selector)
        l.add_value('url', response.url)

        sql = 'select * from `Pages` where `Pages`.`Url`=%s'
        cursor.execute(sql, (response.url,))
        # sleep(0.1)
        pages = cursor.fetchall()
        try:
            page = pages[0]
        except IndexError:
            url = urlparse(response.url)
            sql = 'INSERT INTO Pages (Url, SiteID, FoundDateTime, LastScanDate) VALUES (%s, %s, %s, %s)'
            site_id = site_ids[url.netloc]
            cursor.execute(sql, (response.url, site_id, datetime.today(), datetime.today()))
            db.commit()
            # sleep(0.5)
            sql = 'select * from `Pages` where `Pages`.`Url`=%s'
            cursor.execute(sql, (url.geturl(),))
            pages = cursor.fetchall()
            page = pages[0]
        sql = 'update `Pages` set `LastScanDate`=%s where `Pages`.`Url`=%s'
        cursor.execute(sql, (datetime.today(), response.url))
        db.commit()

        for person in self.keywords:
            rank = 0
            print('PersonID: ', str(person))
            for word in self.keywords[person]:
                print(response.xpath('.').re(r'\b{}\b'.format(word)))
                # print(len(response.xpath('.').re(r'\b{}\b'.format(word))))
                # print(rank)
                rank += len(response.xpath('.').re(r'\b{}\b'.format(word)))
                # print(rank)
            l.add_value('PersonID', str(person))
            l.add_value('Rank', str(rank))
            sql = 'insert into `personpagerank` (personid, pageid, rank) values (%s, %s, %s)'
            cursor.execute(sql, (person, page['ID'], rank))
            db.commit()
        print('Parsed')
        return l.load_item()

    def start_requests(self):
        for url in self.sitemap_urls:
            yield Request(url, self._parse_sitemap)
        for url in self.old_sitemap_urls:
            yield Request(url, self._parse_oldsitemap)

    def _parse_sitemap(self, response):
        if response.url.endswith('/robots.txt'):
            ur = urlparse(response.url, scheme='https')
            sql = 'UPDATE `Pages` SET `LastScanDate`=%s WHERE `Pages`.`Url` = %s'
            cursor.execute(sql, (datetime.today(), ur.geturl()))
            for url in sitemap_urls_from_robots(response.text, base_url=response.url):
                # print('sitemap_url_from_robots: ' + url)
                u = urlparse(url, scheme='https')
                sql = 'INSERT INTO Pages (Url, SiteID, FoundDateTime, LastScanDate) VALUES (%s, %s, %s, %s)'
                site_id = site_ids[urlparse(url).netloc]
                cursor.execute(sql, (u.geturl(), site_id, datetime.today(), datetime.today()))
                yield Request(url, callback=self._parse_sitemap)
        else:
            body = self._get_sitemap_body(response)
            if body is None:
                logger.warning("Ignoring invalid sitemap: %(response)s",
                               {'response': response}, extra={'spider': self})
                return
            s = Sitemap(body)
            if s.type == 'sitemapindex':
                for loc in iterloc(s, self.sitemap_alternate_links):
                    # print('sitemapindex.loc: ' + loc)
                    u = urlparse(loc, scheme='https')
                    sql = 'INSERT INTO Pages (Url, SiteID, FoundDateTime, LastScanDate) VALUES (%s, %s, %s, %s)'
                    site_id = site_ids[urlparse(loc).netloc]
                    cursor.execute(sql, (u.geturl(), site_id, datetime.today(), datetime.today()))
                    db.commit()
                    if any(x.search(loc) for x in self._follow):
                        yield Request(loc, callback=self._parse_sitemap)
            elif s.type == 'urlset':
                for loc in iterloc(s):
                    u = urlparse(loc, scheme='https')
                    # print('urlset.loc.https: ' + urlunparse(('https', u.netloc, u.path, '', '', '')))
                    sql = 'INSERT INTO Pages (Url, SiteID, FoundDateTime, LastScanDate) VALUES (%s, %s, %s, null)'
                    site_id = site_ids[urlparse(loc).netloc]
                    if u.path:
                        cursor.execute(sql, (urlunparse(('https', u.netloc, u.path, '', '', '')), site_id,
                                             datetime.today()))
                    else:
                        cursor.execute(sql, (urlunparse(('https', u.netloc, '/', '', '', '')), site_id,
                                             datetime.today()))
                    for r, c in self._cbs:
                        if r.search(loc):
                            yield Request(loc, callback=c)
                            break
        db.commit()

    def _parse_oldsitemap(self, response):
        pass


def regex(x):
    if isinstance(x, six.string_types):
        return re.compile(x)
    return x


def iterloc(it, alt=False):
    for d in it:
        yield d['loc']
        # Also consider alternate URLs (xhtml:link rel="alternate")
        if alt and 'alternate' in d:
            for l in d['alternate']:
                yield l
