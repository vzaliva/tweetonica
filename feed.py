import os

import datetime
import logging
from uuid import uuid1
from base64 import b64decode
from cgi import parse_qs

from google.appengine.ext import webapp
from google.appengine.ext.webapp import util
from google.appengine.ext import db

from PyRSS2Gen import RSS2, SyInfo, RSSItem, Guid

import queries
import twitter
import data
import constants
import misc

""" Timeline update frequency. Update no more often than this """
TIMILINE_UPDATE_FREQ = datetime.timedelta(0, 90)

REALM='phanalgesfeed'

""" How many timeline entries to fetch. No more than 200! """
FETCH_COUNT=100
MAX_PAGES_TO_FETCH=3

class ATOMHandler(webapp.RequestHandler):

    def get(self):
        params = parse_qs(self.request.query_string)

        if params.has_key('group'):
            group = params['group'][0]
        else:
            group = constants.DEFAULT_GROUP_NAME
        
        u = self._HTTP_authenticate()
        if not u:
            self.response.headers['WWW-Authenticate'] = 'Basic realm=%s' % REALM
            self.response.set_status(401)
            return

        t = twitter.Api(u.screen_name, u.password)
        if u.timeline_last_updated==None or \
               (u.timeline_last_updated+TIMILINE_UPDATE_FREQ) < datetime.datetime.now():
            groups = queries.loadGroups(u)
            self._updateTimeLine(u,t,groups)
        else:
            logging.debug("Timeline for %s is up to date" % u.screen_name)

        g = queries.getGroupByName(group, u)
        if not g:
            logging.warning("Request for non-existing group '%s' for user '%s'" % \
                            (g.name, u.screen_name))
            self.response.set_status(404)
            return
            
        self._generateFeed(u,g)
    
    def post(self):
        self.response.set_status(405)
        self.response.headers.add_header('Allow', 'GET')

    def put(self):
        self.response.set_status(405)
        self.response.headers.add_header('Allow', 'GET')

    def head(self):
        self.response.set_status(405)
        self.response.headers.add_header('Allow', 'GET')

    def options(self):
        self.response.set_status(405)
        self.response.headers.add_header('Allow', 'GET')

    def delete(self):
        self.response.set_status(405)
        self.response.headers.add_header('Allow', 'GET')

    def trace(self):
        self.response.set_status(405)
        self.response.headers.add_header('Allow', 'GET')


    # -- implementation method below  ---

    def _HTTP_authenticate(self):
        if not os.environ.has_key('HTTP_AUTHORIZATION'):
            return None
        ah = os.environ['HTTP_AUTHORIZATION']
        logging.debug("Auth header: %s" % ah)
        if not ah or len(ah)<6:
            logging.warning("Invalid auth string '%s'" % ah)
            return None
        try:
            ahd = b64decode(ah[6:])
        except TypeError:
            logging.warning("Error decoding auth string '%s'" % ah)
            return None
        ahds = ahd.split(':')
        if len(ahds)!=2:
            logging.warning("Error parsing auth string '%s'" % ahd)
            return None
        (username,password) = ahds
        logging.debug("Authenticating user '%s' with password '%s'" % \
                      (username,password))

        q = data.User.gql('WHERE screen_name = :1 and password=:2', \
                          username,password)
        users = q.fetch(1)
        if len(users)==1:
            logging.debug("User '%s' authenticated" % username)
            return users[0]
        else:
            logging.debug("No user of bad pass for %s" % username)
            return None
        
        return None
        
    def _updateTimeLine(self,u,t,groups):
        logging.debug("Updating timeline for user %s" % u.screen_name)

        # friend index index
        ui = {}
        
        page = 1
        done = False
        fetched = 0
        since = u.timeline_max_id
        while not done and page<=MAX_PAGES_TO_FETCH:
            try:
                logging.debug("Fetching page %d of %s timeline (since %d)" % \
                              (page, u.screen_name, since))
                timeline = t.GetFriendsTimeline(since_id = since,\
                                                page=page, count=FETCH_COUNT)
                page = page + 1
            except Exception:
                logging.exception("Error fetching friends timeline for %s" % \
                                  u.screen_name)
                raise json.JSONRPCError("Error fetching friends timeline",
                                        code=ERR_TWITTER_COMM_ERROR)
            if timeline==None or len(timeline)==0:
                break
            for e in timeline:
                logging.debug("Got timeline entry %d" % e.id)
                if e.id<=since:
                    done = True
                    break

                if u.timeline_max_id < e.id:
                    u.timeline_max_id = e.id
                eu = ui.get(e.user.screen_name, None)
                if eu == None:
                    eu = queries.getFriendByName(e.user.screen_name,u)
                    if eu == None:
                        logging.error("Timeline entry from unknown friend %s!" % \
                                      e.user.screen_name)
                        continue
                    else:
                         ui[e.user.screen_name]=eu
                self._addTimeLineEntry(e,u,eu)
                fetched = fetched+1
                
        u.timeline_last_updated=datetime.datetime.now()
        u.put()
        logging.debug("Fetced  %d timeline entries for %s" % \
                      (fetched, u.screen_name))

    def _addTimeLineEntry(self,e,u,friend):
        logging.debug("Adding timeline entry %d" % e.id)
        ts = datetime.datetime.utcfromtimestamp(e.GetCreatedAtInSeconds())
        s = data.StatusUpdate(id = e.id,
                              text = e.text,
                              created_at = ts,
                              truncated = False, #TODO
                              in_reply_to_status_id = -1, #TODO
                              in_reply_to_user_id = -1, #TODO
                              in_reply_to_screen_name = None, #TODO
                              group = friend.group.key(),
                              from_friend = friend.key())
        s.put()

    def _generateFeed(self,u,g):
        timeline = queries.getGroupTimeline(g)
        
        rss = RSS2(
            title = "Timeline for user %s group %s" % (u.screen_name, g.name),
            link = misc.groupRSS_URL(u.screen_name, g.name),
            description = "Timeline for user %s group %s" % (u.screen_name, g.name),
            language = 'en-us',
            managingEditor = 'lord+phalanges@crocodile.org (Vadim Zaliva)',
            lastBuildDate = datetime.datetime.now(),
            syInfo = SyInfo(SyInfo.HOURLY,1,"1901-01-01T00:00+00:00")
            )
        
        for e in timeline:
            # TODO: nice text formatting with links to @ and #, etc.
            link = "http://twitter.com/%s/status/%d" % \
                   (e.from_friend.screen_name, e.id)
            
            rss.items.append(RSSItem(title = e.text,
                                     link = link,
                                     guid = Guid(link),
                                     description = e.text))

        self.response.headers['Content-Type'] = 'application/rss+xml'
        rss.write_xml(self.response.out)


def main():
    logging.getLogger().setLevel(logging.DEBUG)
    logging.debug("Starting")
    app = webapp.WSGIApplication([('/feed', ATOMHandler)], debug=True)
    util.run_wsgi_app(app)

if __name__ == '__main__':
    main()
