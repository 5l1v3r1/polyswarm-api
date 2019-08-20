import asyncio

from aiohttp import web
from polyswarm_api import PolyswarmAPI
from polyswarm_api.result import PolyswarmSearchResults
from test.utils import PolyApiBaseTestCase
from unittest.mock import patch
from urllib import parse
import json


class SearchQueryTestCase(PolyApiBaseTestCase):

    async def get_application(self):
        async def success_response(request):
            if request.query['type'] == "sha256":
                del request
                response = self._get_test_text_resource('275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f_hash_search.json')
                return web.Response(text=response, content_type='application/json')

            del request

            response = self._get_test_text_resource('search_query_server_success_response.json')
            return web.Response(text=response, content_type='application/json')

        async def non_json_response(request):
            del request
            return web.Response(text='Definitely NOT JSON')

        async def not_found_response(request):
            del request
            return web.Response(text='{}', content_type='application/json', status=404)

        async def invalid_query_response(request):
            del request
            return web.Response(text='Search query is not valid', status=400)

        async def micro_response(request):
            del request
            return web.Response(text=self._get_test_text_resource("engine_map.json"), content_type="application/json")

        app = web.Application()
        app.router.add_get('/v1/search', success_response)
        app.router.add_get('/v1/microengines/list', micro_response)
        app.router.add_get('/v2/search', not_found_response)
        app.router.add_get('/v3/search', invalid_query_response)
        app.router.add_get('/v4/search', non_json_response)
        return app

    def test_hash_search(self):
        test_uri = 'http://localhost:{}/v1'.format(self.server.port)
        test_client = PolyswarmAPI(self.test_api_key, uri=test_uri)
        expected_results = self._get_test_json_resource('275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f_hash_search.json')
        # block on this.

        asyncio.get_event_loop().run_until_complete(test_client.ps_api.engine_resolver.get_engines())
        results = test_client.search_hash('275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f')

        # del results['search']
        # self.assertDictEqual(results, expected_results)
        # we don't compare dicts because we enrich client side
        # now we test result class
        srp = PolyswarmSearchResults(results)
        latest_bounty_result = srp.get_latest_bounty_with_assertions()
        self.assertTrue(latest_bounty_result)
        latest_assertions = latest_bounty_result.get_file_assertions()
        x = latest_assertions.malicious
        self.assertGreater(len(x), 0)
        y = latest_bounty_result.permalink
        self.assertTrue(latest_assertions)
        self.assertTrue(latest_bounty_result.permalink)

    def test_search_query(self):
        test_uri = 'http://localhost:{}/v1'.format(self.server.port)
        test_client = PolyswarmAPI(self.test_api_key, uri=test_uri)
        expected_results = self._get_test_json_resource('expected_search_success_results.json')
        results = test_client.search_query(self.test_query)
        self.assertDictEqual(results, expected_results)


    def test_search_query_not_found_from_server(self):
        test_uri = 'http://localhost:{}/v2'.format(self.server.port)
        test_client = PolyswarmAPI(self.test_api_key, uri=test_uri)
        expected_results = self._get_test_json_resource('expected_search_query_not_found_results.json')
        results = test_client.search_query(self.test_query)
        self.assertDictEqual(results, expected_results)

    def test_search_query_invalid_query_from_server(self):
        test_uri = 'http://localhost:{}/v3'.format(self.server.port)
        test_client = PolyswarmAPI(self.test_api_key, uri=test_uri)

        with patch('polyswarm_api.logger.error') as mock_logger_error:
            test_client.search_query(self.test_query)
        self.assertEqual(mock_logger_error.call_args[0][0], 'Server request failed: %s')
        self.assertEqual(str(mock_logger_error.call_args[0][1]),
                         'Received non-json response from PolySwarm API: Search query is not valid')

    def test_search_query_non_json_response_from_server(self):
        test_uri = 'http://localhost:{}/v4'.format(self.server.port)
        test_client = PolyswarmAPI(self.test_api_key, uri=test_uri)
        expected_results = self._get_test_json_resource('expected_search_query_non_json_results.json')
        with patch('polyswarm_api.logger.error') as mock_logger_error:
            results = test_client.search_query(self.test_query)
        self.assertEqual(mock_logger_error.call_args[0][0], 'Server request failed: %s')
        self.assertEqual(str(mock_logger_error.call_args[0][1]),
                         'Received non-json response from PolySwarm API: Definitely NOT JSON')
        self.assertDictEqual(results, expected_results)
