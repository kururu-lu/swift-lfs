import unittest

from swift import log_processor

class DumbLogger(object):
    def __getattr__(self, n):
        return self.foo

    def foo(self, *a, **kw):
        pass

class DumbInternalProxy(object):
    def get_container_list(self, account, container, marker=None):
        n = '2010/03/14/13/obj1'
        if marker is None or n > marker:
            return [{'name': n}]
        else:
            return []

    def get_object(self, account, container, object_name):
        if object_name.endswith('.gz'):
            # same data as below, compressed with gzip -9
            yield '\x1f\x8b\x08'
            yield '\x08"\xd79L'
            yield '\x02\x03te'
            yield 'st\x00\xcbO'
            yield '\xca\xe2JI,I'
            yield '\xe4\x02\x00O\xff'
            yield '\xa3Y\t\x00\x00\x00'
        else:
            yield 'obj\n'
            yield 'data'

class TestLogProcessor(unittest.TestCase):
    
    access_test_line = 'Jul  9 04:14:30 saio proxy 1.2.3.4 4.5.6.7 '\
                    '09/Jul/2010/04/14/30 GET '\
                    '/v1/AUTH_acct/foo/bar?format=json&foo HTTP/1.0 200 - '\
                    'curl tk4e350daf-9338-4cc6-aabb-090e49babfbd '\
                    '6 95 - txfa431231-7f07-42fd-8fc7-7da9d8cc1f90 - 0.0262'
    stats_test_line = 'account,1,2,3'
    proxy_config = {'log-processor': {
                      
                    }
                   }

    def test_log_line_parser(self):
        p = log_processor.LogProcessor(self.proxy_config, DumbLogger())
        result = p.log_line_parser(self.access_test_line)
        self.assertEquals(result, {'code': 200,
           'processing_time': '0.0262',
           'auth_token': 'tk4e350daf-9338-4cc6-aabb-090e49babfbd',
           'month': '07',
           'second': '30',
           'year': '2010',
           'query': 'format=json&foo',
           'tz': '+0000',
           'http_version': 'HTTP/1.0',
           'object_name': 'bar',
           'etag': '-',
           'foo': 1,
           'method': 'GET',
           'trans_id': 'txfa431231-7f07-42fd-8fc7-7da9d8cc1f90',
           'client_ip': '1.2.3.4',
           'format': 1,
           'bytes_out': 95,
           'container_name': 'foo',
           'day': '09',
           'minute': '14',
           'account': 'acct',
           'reseller': 'AUTH',
           'hour': '04',
           'referrer': '-',
           'request': '/v1/AUTH_acct',
           'user_agent': 'curl',
           'bytes_in': 6,
           'lb_ip': '4.5.6.7'})

    def test_process_one_access_file(self):
        p = log_processor.LogProcessor(self.proxy_config, DumbLogger())
        def get_object_data(*a,**kw):
            return [self.access_test_line]
        p.get_object_data = get_object_data
        result = p.process_one_access_file('yarr', None)
        expected = ({('AUTH_acct', '2010', '07', '09', '04'):
                    {('public', 'object', 'GET', '2xx'): 1,
                    ('public', 'bytes_out'): 95,
                    'marker_query': 0,
                    'format_query': 1,
                    'delimiter_query': 0,
                    'path_query': 0,
                    ('public', 'bytes_in'): 6,
                    'prefix_query': 0}},
                    'yarr', {})
        self.assertEquals(result, expected)

    def test_process_one_stats_file(self):
        p = log_processor.LogProcessor(self.proxy_config, DumbLogger())
        def get_object_data(*a,**kw):
            return [self.stats_test_line]
        p.get_object_data = get_object_data
        result = p.process_one_stats_file('y/m/d/h/f', None)
        expected = ({('account', 'y', 'm', 'd', 'h'):
                    {'count': 1,
                    'object_count': 2,
                    'container_count': 1,
                    'bytes_used': 3}},
                    'y/m/d/h/f')
        self.assertEquals(result, expected)

    def test_get_data_listing(self):
        p = log_processor.LogProcessor(self.proxy_config, DumbLogger())
        p.private_proxy = DumbPrivateProxy()
        result = p.get_data_listing('foo')
        expected = ['2010/03/14/13/obj1']
        self.assertEquals(result, expected)
        result = p.get_data_listing('foo', listing_filter=expected)
        expected = []
        self.assertEquals(result, expected)
        result = p.get_data_listing('foo', start_date='2010031412',
                                            end_date='2010031414')
        expected = ['2010/03/14/13/obj1']
        self.assertEquals(result, expected)
        result = p.get_data_listing('foo', start_date='2010031414')
        expected = []
        self.assertEquals(result, expected)

    def test_get_object_data(self):
        p = log_processor.LogProcessor(self.proxy_config, DumbLogger())
        p.private_proxy = DumbPrivateProxy()
        result = list(p.get_object_data('c', 'o', False))
        expected = ['obj','data']
        self.assertEquals(result, expected)
        result = list(p.get_object_data('c', 'o.gz', True))
        self.assertEquals(result, expected)

    def test_get_stat_totals(self):
        p = log_processor.LogProcessor(self.proxy_config, DumbLogger())
        p.private_proxy = DumbPrivateProxy()
        def get_object_data(*a,**kw):
            return [self.stats_test_line]
        p.get_object_data = get_object_data
        result = list(p.get_stat_totals())
        expected = [({('account', '2010', '03', '14', '13'):
                    {'count': 1,
                    'object_count': 2,
                    'container_count': 1,
                    'bytes_used': 3}},
                    '2010/03/14/13/obj1')]
        self.assertEquals(result, expected)

    def test_get_aggr_access_logs(self):
        p = log_processor.LogProcessor(self.proxy_config, DumbLogger())
        p.private_proxy = DumbPrivateProxy()
        def get_object_data(*a,**kw):
            return [self.access_test_line]
        p.get_object_data = get_object_data
        result = list(p.get_aggr_access_logs())
        expected = [({('AUTH_7abbc116-8a07-4b63-819d-02715d3e0f31', '2010', '07', '09', '04'):
                    {('public', 'object', 'GET', '2xx'): 1,
                    ('public', 'bytes_out'): 95,
                    'marker_query': 0,
                    'format_query': 1,
                    'delimiter_query': 0,
                    'path_query': 0,
                    ('public', 'bytes_in'): 0,
                    'prefix_query': 0}},
                    '2010/03/14/13/obj1',
                    {})]
        self.assertEquals(result, expected)