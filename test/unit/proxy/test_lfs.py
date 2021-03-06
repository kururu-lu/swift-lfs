# Copyright (c) 2012 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import errno
import os
import unittest
import signal
import xattr
from contextlib import contextmanager
from shutil import rmtree
from tempfile import mkdtemp

from eventlet import spawn, wsgi, listen

from test.unit import connect_tcp, readuntil2crlfs
from swift.proxy import server as proxy_server
from swift.common.swob import Request
from swift.common.utils import mkdirs, NullLogger

# XXX The xattr-patching code is stolen from test/unit/gluster/test_utls.py
# This is necessary so tests could be run in environments like Koji.
# Maybe we need to share this. XXX
from collections import defaultdict
#
# Somewhat hacky way of emulating the operation of xattr calls. They are made
# against a dictionary that stores the xattr key/value pairs.
#
_xattrs = {}
_xattr_op_cnt = defaultdict(int)
_xattr_set_err = {}
_xattr_get_err = {}
_xattr_rem_err = {}

def _xkey(path, key):
    return "%s:%s" % (path, key)

def _setxattr(path, key, value, *args, **kwargs):
    _xattr_op_cnt['set'] += 1
    xkey = _xkey(path, key)
    if xkey in _xattr_set_err:
        e = IOError()
        e.errno = _xattr_set_err[xkey]
        raise e
    global _xattrs
    _xattrs[xkey] = value

def _getxattr(path, key, *args, **kwargs):
    _xattr_op_cnt['get'] += 1
    xkey = _xkey(path, key)
    if xkey in _xattr_get_err:
        e = IOError()
        e.errno = _xattr_get_err[xkey]
        raise e
    global _xattrs
    if xkey in _xattrs:
        ret_val = _xattrs[xkey]
    else:
        e = IOError("Fake IOError")
        e.errno = errno.ENODATA
        raise e
    return ret_val

def _removexattr(path, key, *args, **kwargs):
    _xattr_op_cnt['remove'] += 1
    xkey = _xkey(path, key)
    if xkey in _xattr_rem_err:
        e = IOError()
        e.errno = _xattr_rem_err[xkey]
        raise e
    global _xattrs
    if xkey in _xattrs:
        del _xattrs[xkey]
    else:
        e = IOError("Fake IOError")
        e.errno = errno.ENODATA
        raise e

def _initxattr():
    global _xattrs
    _xattrs = {}
    global _xattr_op_cnt
    _xattr_op_cnt = defaultdict(int)
    global _xattr_set_err, _xattr_get_err, _xattr_rem_err
    _xattr_set_err = {}
    _xattr_get_err = {}
    _xattr_rem_err = {}

    # Save the current methods
    global _xattr_set;    _xattr_set    = xattr.setxattr
    global _xattr_get;    _xattr_get    = xattr.getxattr
    global _xattr_remove; _xattr_remove = xattr.removexattr

    # Monkey patch the calls we use with our internal unit test versions
    xattr.setxattr    = _setxattr
    xattr.getxattr    = _getxattr
    xattr.removexattr = _removexattr

def _destroyxattr():
    # Restore the current methods just in case
    global _xattr_set;    xattr.setxattr    = _xattr_set
    global _xattr_get;    xattr.getxattr    = _xattr_get
    global _xattr_remove; xattr.removexattr = _xattr_remove
    # Destroy the stored values and
    global _xattrs; _xattrs = None


class S(object):
    def __init__(self):
        self.testdir = None
        self.servers = None
        self.sockets = None
        self.coros = None

def _setup(state, mode):
    state.testdir = os.path.join(mkdtemp(), 'tmp_test_proxy_server_lfs')
    conf = {'devices': state.testdir,
            'swift_dir': state.testdir,
            'mount_check': 'false',
            'allow_versions': 'True',
            'allow_account_management': 'yes',
            'lfs_mode': mode,
            'lfs_root': state.testdir}
    mkdirs(state.testdir)
    rmtree(state.testdir)
    prolis = listen(('localhost', 0))
    state.sockets = (prolis,)
    prosrv = proxy_server.Application(conf, FakeMemcacheReturnsNone(),
                                      None, FakeRing(), FakeRing(), FakeRing())
    state.servers = (prosrv,)
    nl = NullLogger()
    prospa = spawn(wsgi.server, prolis, prosrv, nl)
    state.coros = (prospa,)

    # Create account (can only be done with HEAD, never GET)
    # XXX Why not create a controller directly and invoke it?
    sock = connect_tcp(('localhost', prolis.getsockname()[1]))
    fd = sock.makefile()
    fd.write('HEAD /v1/a HTTP/1.1\r\nHost: localhost\r\n'
                 'Connection: close\r\nContent-Length: 0\r\n\r\n')
    fd.flush()
    headers = readuntil2crlfs(fd)
    # P3
    fp = open("/tmp/dump","a")
    print >>fp, "== HEAD"
    print >>fp, headers
    fp.close()
    exp = 'HTTP/1.1 201'
    assert(headers[:len(exp)] == exp)

    # Create container
    sock = connect_tcp(('localhost', prolis.getsockname()[1]))
    fd = sock.makefile()
    fd.write('PUT /v1/a/c HTTP/1.1\r\nHost: localhost\r\n'
             'Connection: close\r\nX-Auth-Token: t\r\n'
             'Content-Length: 0\r\n\r\n')
    fd.flush()
    # P3
    #headers = readuntil2crlfs(fd)
    headers = fd.read()
    # P3
    fp = open("/tmp/dump","a")
    print >>fp, "== PUT"
    print >>fp, headers
    fp.close()
    exp = 'HTTP/1.1 201'
    assert(headers[:len(exp)] == exp)

def _teardown(state):
    for server in state.coros:
        server.kill()
    rmtree(os.path.dirname(state.testdir))


def setup():
    _initxattr()
    global _sg, _sp
    _sg = S()
    _setup(_sg, 'gluster')
    _sp = S()
    _setup(_sp, 'posix')

def teardown():
    _teardown(_sg)
    _teardown(_sp)
    _destroyxattr()


# XXX Get rid of the Ring eventually
class FakeRing(object):

    def __init__(self):
        # 9 total nodes (6 more past the initial 3) is the cap, no matter if
        # this is set higher.
        self.max_more_nodes = 0
        self.devs = {}

    def get_nodes(self, account, container=None, obj=None):
        devs = []
        for x in xrange(3):
            devs.append(self.devs.get(x))
            if devs[x] is None:
                self.devs[x] = devs[x] = \
                    {'ip': '10.0.0.%s' % x, 'port': 1000 + x, 'device': 'sda'}
        return 1, devs

    def get_part_nodes(self, part):
        return self.get_nodes('blah')[1]

    def get_more_nodes(self, nodes):
        # 9 is the true cap
        for x in xrange(3, min(3 + self.max_more_nodes, 9)):
            yield {'ip': '10.0.0.%s' % x, 'port': 1000 + x, 'device': 'sda'}


class FakeMemcache(object):

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def keys(self):
        return self.store.keys()

    def set(self, key, value, timeout=0):
        self.store[key] = value
        return True

    def incr(self, key, timeout=0):
        self.store[key] = self.store.setdefault(key, 0) + 1
        return self.store[key]

    @contextmanager
    def soft_lock(self, key, timeout=0, retries=5):
        yield True

    def delete(self, key):
        try:
            del self.store[key]
        except Exception:
            pass
        return True

class FakeMemcacheReturnsNone(FakeMemcache):

    def get(self, key):
        # Returns None as the timestamp of the container; assumes we're only
        # using the FakeMemcache for container existence checks.
        return None


class TestProxyServerLFS(unittest.TestCase):

    #def setUp(self):
    #    _setup(self)

    #def tearDown(self):
    #    _teardown(self)

    def _test_GET_newest_large_file(self, state):
        calls = [0]

        def handler(_junk1, _junk2):
            calls[0] += 1

        old_handler = signal.signal(signal.SIGPIPE, handler)
        try:
            prolis = state.sockets[0]
            prosrv = state.servers[0]
            sock = connect_tcp(('localhost', prolis.getsockname()[1]))
            fd = sock.makefile()
            obj = 'a' * (1024 * 1024)
            path = '/v1/a/c/o.large'
            fd.write('PUT %s HTTP/1.1\r\n'
                     'Host: localhost\r\n'
                     'Connection: close\r\n'
                     'X-Storage-Token: t\r\n'
                     'Content-Length: %s\r\n'
                     'Content-Type: application/octet-stream\r\n'
                     '\r\n%s' % (path, str(len(obj)),  obj))
            fd.flush()
            headers = readuntil2crlfs(fd)
            exp = 'HTTP/1.1 201'
            self.assertEqual(headers[:len(exp)], exp)
            req = Request.blank(path,
                                environ={'REQUEST_METHOD': 'GET'},
                                headers={'Content-Type':
                                         'application/octet-stream',
                                         'X-Newest': 'true'})
            res = req.get_response(prosrv)
            self.assertEqual(res.status_int, 200)
            self.assertEqual(res.body, obj)
            self.assertEqual(calls[0], 0)
        finally:
            signal.signal(signal.SIGPIPE, old_handler)

    def test_GET_newest_large_file(self):
        self._test_GET_newest_large_file(_sg)
        self._test_GET_newest_large_file(_sp)

    ## reproduce byte for byte but with LFSObjectController
    #def test_PUT_max_size(self):
    #    with save_globals():
    #        set_http_connect(201, 201, 201)
    #        controller = proxy_server.ObjectController(self.app, 'account',
    #                                                   'container', 'object')
    #        req = Request.blank('/a/c/o', {}, headers={
    #            'Content-Length': str(MAX_FILE_SIZE + 1),
    #            'Content-Type': 'foo/bar'})
    #        self.app.update_request(req)
    #        res = controller.PUT(req)
    #        self.assertEquals(res.status_int, 413)

    # Copied verbatim. Not sure if needed if we have it in test_server.py.
    def _test_chunked_put_bad_version(self, state):
        prolis = state.sockets[0]
        # Check bad version
        sock = connect_tcp(('localhost', prolis.getsockname()[1]))
        fd = sock.makefile()
        fd.write('GET /v0 HTTP/1.1\r\nHost: localhost\r\n'
                 'Connection: close\r\nContent-Length: 0\r\n\r\n')
        fd.flush()
        headers = readuntil2crlfs(fd)
        exp = 'HTTP/1.1 412'
        self.assertEquals(headers[:len(exp)], exp)

    def test_chunked_put_bad_version(self):
        self._test_chunked_put_bad_version(_sg)
        self._test_chunked_put_bad_version(_sp)

    # Copied verbatim. Not sure if needed if we have it in test_server.py.
    def _test_chunked_put_bad_path(self, state):
        prolis = state.sockets[0]
        # Check bad path
        sock = connect_tcp(('localhost', prolis.getsockname()[1]))
        fd = sock.makefile()
        fd.write('GET invalid HTTP/1.1\r\nHost: localhost\r\n'
                 'Connection: close\r\nContent-Length: 0\r\n\r\n')
        fd.flush()
        headers = readuntil2crlfs(fd)
        exp = 'HTTP/1.1 404'
        self.assertEquals(headers[:len(exp)], exp)

    def test_chunked_put_bad_path(self):
        self._test_chunked_put_bad_path(_sg)
        self._test_chunked_put_bad_path(_sp)

    # Homemade: POST to account and verify that it works at all.
    def _test_account_POST(self, state):
        prolis = state.sockets[0]
        prosrv = state.servers[0]
        path = '/v1/a'
        key = 'Test'
        value = 'Value'

        # Set a metadata header
        # Go whole hog on TCP connection so we test more this time.
        sock = connect_tcp(('localhost', prolis.getsockname()[1]))
        fd = sock.makefile()
        fd.write('POST %s HTTP/1.1\r\nHost: kvm-rei:8080\r\n'
                 'Accept: */*\r\nX-Timestamp: 1\r\nX-Account-Meta-%s: %s\r\n'
                 'Connection: close\r\nContent-Length: 0\r\n\r\n' %
                 (path, key, value))
        fd.flush()
        headers = readuntil2crlfs(fd)
        exp = 'HTTP/1.1 204'
        self.assertEquals(headers[:len(exp)], exp)

        # Get the metadata value and verify it
        req = Request.blank(path, environ={'REQUEST_METHOD': 'HEAD'})
        res = req.get_response(prosrv)
        self.assertEquals(res.status_int, 204)
        self.assertEquals(res.headers.get('x-account-meta-%s' % key), value)

    def test_account_POST(self):
        self._test_account_POST(_sg)
        self._test_account_POST(_sp)

    # POST to container at back end, modelled after a container test
    def _test_POST_HEAD_metadata(self, state):
        prosrv = state.servers[0]
        # This container is pre-created
        path = '/v1/a/c'

        # Set metadata header
        req = Request.blank(path, environ={'REQUEST_METHOD': 'POST'},
            headers={'X-Container-Meta-Test': 'Value'})
        res = req.get_response(prosrv)
        self.assertEquals(res.status_int, 204)
        req = Request.blank(path, environ={'REQUEST_METHOD': 'HEAD'})
        res = req.get_response(prosrv)
        self.assertEquals(res.status_int, 204)
        self.assertEquals(res.headers.get('x-container-meta-test'), 'Value')
        # Update metadata header
        req = Request.blank(path, environ={'REQUEST_METHOD': 'POST'},
            headers={'X-Container-Meta-Test': 'New Value'})
        res = req.get_response(prosrv)
        self.assertEquals(res.status_int, 204)
        req = Request.blank(path, environ={'REQUEST_METHOD': 'HEAD'})
        res = req.get_response(prosrv)
        self.assertEquals(res.status_int, 204)
        self.assertEquals(res.headers.get('x-container-meta-test'),
                          'New Value')
        # Remove metadata header (by setting it to empty)
        req = Request.blank(path, environ={'REQUEST_METHOD': 'POST'},
            headers={'X-Container-Meta-Test': ''})
        res = req.get_response(prosrv)
        self.assertEquals(res.status_int, 204)
        req = Request.blank(path, environ={'REQUEST_METHOD': 'HEAD'})
        res = req.get_response(prosrv)
        self.assertEquals(res.status_int, 204)
        self.assert_('x-container-meta-test' not in res.headers)

    def test_container_POST(self):
        self._test_POST_HEAD_metadata(_sg)
        self._test_POST_HEAD_metadata(_sp)

    # Test if POST works at all.
    def _test_object_POST(self, state):
        prolis = state.sockets[0]
        prosrv = state.servers[0]
        # This container is pre-created. Object is not.
        path = '/v1/a/c/o'

        # Create the object.
        sock = connect_tcp(('localhost', prolis.getsockname()[1]))
        fd = sock.makefile()
        obj = 'test'
        fd.write('PUT %s HTTP/1.1\r\n'
                 'Host: localhost\r\n'
                 'Connection: close\r\n'
                 'X-Storage-Token: t\r\n'
                 'Content-Length: %s\r\n'
                 'Content-Type: application/octet-stream\r\n'
                 '\r\n%s' % (path, str(len(obj)),  obj))
        fd.flush()
        headers = readuntil2crlfs(fd)
        exp = 'HTTP/1.1 201'
        self.assertEqual(headers[:len(exp)], exp)

        key = 'Test'
        value = 'Value'

        # Go whole hog on TCP connection so we test more this time.
        sock = connect_tcp(('localhost', prolis.getsockname()[1]))
        fd = sock.makefile()
        fd.write('POST %s HTTP/1.1\r\nHost: kvm-rei:8080\r\n'
                 'Accept: */*\r\nX-Timestamp: 1\r\nX-Object-Meta-%s: %s\r\n'
                 'Connection: close\r\nContent-Length: 0\r\n\r\n' %
                 (path, key, value))
        fd.flush()
        headers = readuntil2crlfs(fd)
        exp = 'HTTP/1.1 202'
        self.assertEquals(headers[:len(exp)], exp)

        # Get the metadata value and verify it
        req = Request.blank(path, environ={'REQUEST_METHOD': 'HEAD'})
        res = req.get_response(prosrv)
        self.assertEquals(res.status_int, 204)
        self.assertEquals(res.headers.get('x-object-meta-%s' % key), value)

    # ... a couple more potentially interesting POST-on-object scenarios

    #def test_PUT_POST_requires_container_exist(self):
    #    with save_globals():
    #        self.app.object_post_as_copy = False
    #        self.app.memcache = FakeMemcacheReturnsNone()
    #        controller = proxy_server.ObjectController(self.app, 'account',
    #                                                   'container', 'object')

    #        set_http_connect(200, 404, 404, 404, 200, 200, 200)
    #        req = Request.blank('/a/c/o', environ={'REQUEST_METHOD': 'PUT'})
    #        self.app.update_request(req)
    #        resp = controller.PUT(req)
    #        self.assertEquals(resp.status_int, 404)

    #        set_http_connect(200, 404, 404, 404, 200, 200)
    #        req = Request.blank('/a/c/o', environ={'REQUEST_METHOD': 'POST'},
    #                            headers={'Content-Type': 'text/plain'})
    #        self.app.update_request(req)
    #        resp = controller.POST(req)
    #        self.assertEquals(resp.status_int, 404)

    #def test_POST_calls_authorize(self):
    #    called = [False]

    #    def authorize(req):
    #        called[0] = True
    #        return HTTPUnauthorized(request=req)
    #    with save_globals():
    #        self.app.object_post_as_copy = False
    #        set_http_connect(200, 200, 201, 201, 201)
    #        controller = proxy_server.ObjectController(self.app, 'account',
    #                                                   'container', 'object')
    #        req = Request.blank('/a/c/o', environ={'REQUEST_METHOD': 'POST'},
    #                            headers={'Content-Length': '5'}, body='12345')
    #        req.environ['swift.authorize'] = authorize
    #        self.app.update_request(req)
    #        res = controller.POST(req)
    #    self.assert_(called[0])

    def test_object_POST(self):
        self._test_object_POST(_sg)
        self._test_object_POST(_sp)

    # def test_DELETE(self):

    # XXX Test that numbers of objects are updated in containers
    # XXX Test that numbers of containers are updated in accounts
    # XXX Test lists of containers
    # XXX write a test for container listings with marker and delimiter 4.2.1.3
    # XXX Test lists of objects (delimiter and marker)

if __name__ == '__main__':
    setup()
    try:
        unittest.main()
    finally:
        teardown()
