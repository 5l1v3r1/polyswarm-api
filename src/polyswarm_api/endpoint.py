from future.utils import raise_from
import logging
from concurrent import futures
from copy import deepcopy

from . import const
from . import utils
from . import http
from . import exceptions
from .types import parsers

try:
    from json.decoder import JSONDecodeError
except ImportError:
    JSONDecodeError = ValueError


logger = logging.getLogger(__name__)


class PolyswarmRequest(object):
    """This class holds a requests-compatible dictionary and extra infor we need to parse the reponse."""
    def __init__(self, api_instance, request_parameters, result_parser=None, json_response=True):
        self.api_instance = api_instance
        self.request_parameters = request_parameters
        self.result_parser = result_parser
        self.json_response = json_response
        self.raw_result = None
        self.status_code = None
        self.status = None
        self.result = None
        self.errors = None
        self.total = None
        self.limit = None
        self.offset = None
        self.order_by = None
        self.direction = None

    def execute(self, session):
        self.raw_result = session.request(**self.request_parameters)

        if self.result_parser is not None:
            self.parse_result(self.raw_result)

        return self

    def _bad_status_message(self):
        return "Got unexpected result code: {}, message: {}".format(self.status_code, self.result)

    def _extract_json_body(self, result):
        try:
            self.json = result.json()
            self.result = self.json.get('result')
            self.status = self.json.get('status')
            self.errors = self.json.get('errors')
        except JSONDecodeError as e:
            logger.error("Server returned non-JSON response.")
            raise raise_from(exceptions.RequestFailedException(self), e)

    def parse_result(self, result):
        self.status_code = result.status_code
        if self.status_code // 100 != 2:
            self._extract_json_body(result)

            if self.status_code == 429:
                raise exceptions.UsageLimitsExceeded(const.USAGE_EXCEEDED_MESSAGE)

            raise exceptions.RequestFailedException(self, self._bad_status_message())
        else:
            if self.json_response:
                self._extract_json_body(result)
                self.total = self.json.get('total')
                self.limit = self.json.get('limit')
                self.offset = self.json.get('offset')
                self.order_by = self.json.get('order_by')
                self.direction = self.json.get('direction')
                self.result = self.result_parser.parse_result(self.json.get('result'))
            else:
                self.result = self.result_parser.parse_result(result)

    def next_page(self):
        new_parameters = deepcopy(self.request_parameters)
        new_parameters.setdefault('params', {})['offset'] = self.offset
        new_parameters.setdefault('params', {})['limit'] = self.limit
        new_parameters['params']['offset'] += new_parameters['params']['limit']
        return PolyswarmRequest(
            self.api_instance,
            new_parameters,
            result_parser=self.result_parser,
        )


class PolyswarmRequestGenerator(object):
    """ This class will return requests-compatible arguments for the API """
    def __init__(self, api_instance, uri, community):
        self.api_instance = api_instance
        self.uri = uri
        self.community = community

        self.consumer_base = '{uri}/consumer'.format(uri=self.uri)
        self.search_base = '{uri}/search'.format(uri=self.uri)
        self.download_base = '{uri}/download'.format(uri=self.uri)
        self.community_base = '{consumer_uri}/{community}'.format(consumer_uri=self.consumer_base, community=community)
        self.hunt_base = '{uri}/hunt'.format(uri=self.uri)
        self.stream_base = '{uri}/download/stream'.format(uri=self.uri)

        self.download_fmt = '{}/{}/{}'
        self.hash_search_fmt = '{}/{}/{}'

    def download(self, hash_value, hash_type, output_file, file_handle=None, create=False):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': self.download_fmt.format(self.download_base, hash_type, hash_value),
                'stream': True,
            },
            result_parser=parsers.DownloadResult(output_file, polyswarm=self.api_instance,
                                                 file_handle=file_handle, create=create),
            json_response=False,
        )

    def download_archive(self, u, output_file, file_handle=None, create=False):
        """ This method is special, in that it is simply for downloading from S3 """
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': u,
                'stream': True,
                'headers': {'Authorization': None}
            },
            result_parser=parsers.DownloadResult(output_file, polyswarm=self.api_instance,
                                                 file_handle=file_handle, create=create),
            json_response=False,
        )

    def stream(self, since=const.MAX_SINCE_TIME_STREAM):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/download/stream'.format(self.consumer_base),
                'params': {'since': since},
            },
            result_parser=parsers.StreamResult()
        )

    def search_hash(self, h, with_instances=True, with_metadata=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': self.search_base,
                'params': {
                    'type': h.hash_type,
                    'hash': h.hash,
                    'with_instances': utils.bool_to_int[with_instances],
                    'with_metadata': utils.bool_to_int[with_metadata]
                },
            },
            result_parser=parsers.SearchResult(h),
        )

    def search_metadata(self, q, with_instances=True, with_metadata=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': self.search_base,
                'params': {
                    'type': 'metadata',
                    'with_instances': utils.bool_to_int[with_instances],
                    'with_metadata': utils.bool_to_int[with_metadata]
                },
                'json': q.query,
            },
            result_parser=parsers.SearchResult(q),
        )

    def submit(self, artifact):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'POST',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': self.community_base,
                'files': {
                    'file': (artifact.artifact_name, artifact.file_handle),
                },
                # very oddly, when included in files parameter this errors out
                'data': {'artifact-type': artifact.artifact_type.name}
            },
            result_parser=parsers.SubmitResult(polyswarm=self.api_instance)
        )

    def rescan(self, h, **kwargs):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'POST',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/rescan/{}/{}'.format(self.community_base, h.hash_type, h.hash)
            },
            result_parser=parsers.SubmitResult(polyswarm=self.api_instance)
        )

    def lookup_uuid(self, uuid, **kwargs):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/uuid/{}'.format(self.community_base, uuid)
            },
            result_parser=parsers.SubmitResult(polyswarm=self.api_instance)
        )

    def _get_engine_names(self):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/microengines/list'.format(self.uri),
                'headers': {'Authorization': None},
            },
            result_parser=parsers.EngineNamesResult(polyswarm=self.api_instance)
        )

    def submit_live_hunt(self, rule):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'POST',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/live'.format(self.hunt_base),
                'json': {'yara': rule.ruleset},
            },
            result_parser=parsers.HuntSubmissionResult(rule, polyswarm=self.api_instance),
        )

    def live_lookup(self, with_bounty_results=True, with_metadata=True,
                    limit=const.RESULT_CHUNK_SIZE, offset=0, id=None,
                    since=0):
        req = {
            'method': 'GET',
            'timeout': const.DEFAULT_HTTP_TIMEOUT,
            'url': '{}/live/results'.format(self.hunt_base),
            'params': {
                'with_bounty_results': utils.bool_to_int[with_bounty_results],
                'with_metadata': utils.bool_to_int[with_metadata],
                'limit': limit,
                'offset': offset,
                'since': since,
            },
        }

        if id:
            req['params']['id'] = id

        return PolyswarmRequest(
            self.api_instance,
            req,
            result_parser=parsers.HuntResult(hunt_id=id, polyswarm=self.api_instance)
        )

    def submit_historical_hunt(self, rule):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'POST',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/historical'.format(self.hunt_base),
                'json': {'yara': rule.ruleset},
            },
            result_parser=parsers.HuntSubmissionResult(rule, polyswarm=self.api_instance),
        )

    def historical_lookup(self, with_bounty_results=True, with_metadata=True,
                          limit=const.RESULT_CHUNK_SIZE, offset=0, id=None,
                          since=0):
        req = {
            'method': 'GET',
            'timeout': const.DEFAULT_HTTP_TIMEOUT,
            'url': '{}/historical/results'.format(self.hunt_base),
            'params': {
                'with_bounty_results': utils.bool_to_int[with_bounty_results],
                'with_metadata': utils.bool_to_int[with_metadata],
                'limit': limit,
                'offset': offset,
                'since': since,
            },
        }

        if id:
            req['params']['id'] = id

        return PolyswarmRequest(
            self.api_instance,
            req,
            result_parser=parsers.HuntResult(hunt_id=id, polyswarm=self.api_instance)
        )

    def historical_delete(self, hunt_id):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'DELETE',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/historical'.format(self.hunt_base),
                'params': {'hunt_id': hunt_id}
            },
            result_parser=parsers.HuntDeletionResult(polyswarm=self.api_instance)
        )

    def live_delete(self, hunt_id):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'DELETE',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/live'.format(self.hunt_base),
                'params': {'hunt_id': hunt_id}
            },
            result_parser=parsers.HuntDeletionResult(polyswarm=self.api_instance)
        )

    def historical_list(self):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/historical'.format(self.hunt_base),
                'params': {'all': 'true'},
            },
            result_parser=parsers.HuntListResult(polyswarm=self.api_instance)
        )

    def live_list(self):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/live'.format(self.hunt_base),
                'params': {'all': 'true'},
            },
            result_parser=parsers.HuntListResult(polyswarm=self.api_instance)
        )

    def score(self, uuid):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/submission/{}/polyscore'.format(self.consumer_base, uuid)
            },
            result_parser=parsers.ScoreResult(polyswarm=self.api_instance)
        )


class PolyswarmRequestExecutor(object):
    """ This class accepts requests from a PolyswarmRequestGenerator and executes it """
    def __init__(self, key, session=None):
        self.session = session or http.PolyswarmHTTP(key, retries=const.DEFAULT_RETRIES)
        self.requests = []

    def push(self, request):
        raise NotImplementedError()

    def execute(self):
        raise NotImplementedError()


class PolyswarmFuturesExecutor(PolyswarmRequestExecutor):
    def __init__(self, key):
        self.executor = futures.ThreadPoolExecutor(const.DEFAULT_WORKER_COUNT)
        super(PolyswarmFuturesExecutor, self).__init__(key)

    def push(self, request):
        self.requests.append(self.executor.submit(request.execute, self.session))
        return self

    def execute(self, as_completed=False):
        requests = self.requests
        # flush before looping in case we have nested executions
        self.requests = []
        if as_completed:
            for future in futures.as_completed(requests):
                yield future.result()
        else:
            futures.wait(requests)
            for request in requests:
                yield request.result()


class PolyswarmSynchronousExecutor(PolyswarmRequestExecutor):
    def push(self, request):
        self.requests.append(request)

    def execute(self):
        responses = []
        for request in self.requests:
            responses.append(request.execute(self.session))
        self.requests = []
        return responses
