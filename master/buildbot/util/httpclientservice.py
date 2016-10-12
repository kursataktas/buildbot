# This file is part of Buildbot. Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import json as jsonmodule
import textwrap
import urlparse

from twisted.internet import defer
from twisted.web.client import Agent
from twisted.web.client import HTTPConnectionPool

from buildbot import config
from buildbot.util import service
from buildbot.util.logger import Logger

try:
    import txrequests
except ImportError:
    txrequests = None

try:
    import treq
except ImportError:
    treq = None

log = Logger()


class HTTPClientService(service.SharedService):
    """A SharedService class that can make http requests to remote services.

    I can use either txrequests or treq, depending on what I find installed

    I provide minimal get/post/put/delete API with automatic baseurl joining, and json data encoding
    that is suitable for use from buildbot services.
    """
    TREQ_PROS_AND_CONS = textwrap.dedent("""
       txrequests is based on requests and is probably a bit more mature, but it requires threads to run,
       so has more overhead.
       treq is better integrated in twisted and is more and more feature equivalent

       txrequests is 2.8x slower than treq due to the use of threads.

       http://treq.readthedocs.io/en/latest/#feature-parity-w-requests
       pip install txrequests
           or
       pip install treq
    """)
    # Those could be in theory be overridden in master.cfg by using
    # import buildbot.util.httpclientservice.HTTPClientService.PREFER_TREQ = True
    # We prefer at the moment keeping it simple
    PREFER_TREQ = False
    MAX_THREADS = 5

    def __init__(self, base_url, auth=None, headers=None):
        service.SharedService.__init__(self)
        self._base_url = base_url
        self._auth = auth
        self._headers = headers
        self._session = None

    @staticmethod
    def checkAvailable(from_module):
        """Call me at checkConfig time to properly report config error
           if neither txrequests or treq is installed
        """
        if txrequests is None and treq is None:
            config.error("neither txrequests nor treq is installed, but {} is requiring it\n\n{}".format(
                from_module, HTTPClientService.TREQ_PROS_AND_CONS))

    def startService(self):
        # treq only supports basicauth, so we force txrequests if the auth is something else
        if self._auth is not None and not isinstance(self._auth, tuple):
            self.PREFER_TREQ = False
        if txrequests is not None and not self.PREFER_TREQ:
            self._session = txrequests.Session()
            self._doRequest = self._doTxRequest
        elif treq is None:
            raise ImportError("{classname} requires either txrequest or treq install."
                              " Users should call {classname}.checkAvailable() during checkConfig()"
                              " to properly alert the user.".format(classname=self.__class__.__name__))
        else:
            self._doRequest = self._doTReq
            self._pool = HTTPConnectionPool(self.master.reactor)
            self._pool.maxPersistentPerHost = self.MAX_THREADS
            self._agent = Agent(self.master.reactor, pool=self._pool)

    def stopService(self):
        if self._session:
            return self._session.close()
        else:
            return self._pool.closeCachedConnections()

    def _prepareRequest(self, ep, kwargs):
        url = urlparse.urljoin(self._base_url, ep)
        if self._auth is not None and 'auth' not in kwargs:
            kwargs['auth'] = self._auth
        headers = kwargs.get('headers', {})
        if self._headers is not None:
            headers.update(self._headers)
        kwargs['headers'] = headers
        return url, kwargs

    def _doTxRequest(self, method, ep, **kwargs):
        url, kwargs = self._prepareRequest(ep, kwargs)

        class ResponseWrapper(object):
            """ treq response API is more adapted to"""
            def __init__(self, deferred):
                self._deferred = deferred

            def content(self):
                @self._deferred.addCallback
                def makeContent(res):
                    return res.content
                return self._deferred

            def json(self):
                @self._deferred.addCallback
                def makeText(res):
                    return res.json()
                return self._deferred

        def readContent(session, res):
            # this forces reading of the content
            res.content
            return res
        # read the whole content in the thread
        kwargs['background_callback'] = readContent
        return defer.succeed(ResponseWrapper(
            self._session.request(method, url, **kwargs)))

    def _doTReq(self, method, ep, data=None, json=None, **kwargs):
        url, kwargs = self._prepareRequest(ep, kwargs)
        # treq requires header values to be an array
        kwargs['headers'] = dict([(k, [v]) for k, v in kwargs['headers'].items()])
        kwargs['agent'] = self._agent
        if isinstance(json, dict):
            data = jsonmodule.dumps(json)
            kwargs['headers']['Content-Type'] = ['application/json']
            return getattr(treq, method)(url, data=data, **kwargs)
        if isinstance(data, dict):
            return getattr(treq, method)(url, data=data, **kwargs)
        else:
            return getattr(treq, method)(url, **kwargs)

    # lets be nice to the auto completers, and don't generate that code
    def get(self, ep, **kwargs):
        return self._doRequest("get", ep, **kwargs)

    def put(self, ep, **kwargs):
        return self._doRequest("put", ep, **kwargs)

    def delete(self, ep, **kwargs):
        return self._doRequest("delete", ep, **kwargs)

    def post(self, ep, **kwargs):
        return self._doRequest("post", ep, **kwargs)
