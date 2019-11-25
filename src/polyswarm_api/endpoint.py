import logging
import json
from future.utils import raise_from
from concurrent import futures
from copy import deepcopy

from . import const
from . import http
from . import exceptions
from .types import resources

try:
    from json.decoder import JSONDecodeError
except ImportError:
    JSONDecodeError = ValueError


logger = logging.getLogger(__name__)


class RequestParamsEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            return json.JSONEncoder.default(self, obj)
        except Exception:
            return str(obj)


class PolyswarmRequest(object):
    """This class holds a requests-compatible dictionary and extra infor we need to parse the reponse."""
    def __init__(self, api_instance, request_parameters, key=None, result_parser=None, json_response=True,
                 raise_on_error=True, **kwargs):
        self.api_instance = api_instance
        # we should not access the api_instance session directly, but provide as a
        # parameter in the constructor, but this will do for the moment
        self.session = self.api_instance.session or http.PolyswarmHTTP(key, retries=const.DEFAULT_RETRIES)
        self.request_parameters = request_parameters
        self.result_parser = result_parser
        self.json_response = json_response
        self.raise_on_error = raise_on_error
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
        self.parser_kwargs = kwargs
        self._exception = None

    def execute(self):
        try:
            self.raw_result = self.session.request(**self.request_parameters)
            if self.result_parser is not None:
                self.parse_result(self.raw_result)
        except Exception as e:
            if self.raise_on_error:
                raise e
            else:
                self._exception = e
                self.result = resources.Error(self, e, self.api_instance)
        return self

    def _bad_status_message(self):
        return "Request:\n{}\n" \
               "Got unexpected result code: {}\n" \
               "Message: {}".format(json.dumps(self.request_parameters, indent=4,
                                               sort_keys=True, cls=RequestParamsEncoder),
                                    self.status_code,
                                    self.result)

    def _extract_json_body(self, result):
        self.json = result.json()
        self.result = self.json.get('result')
        self.status = self.json.get('status')
        self.errors = self.json.get('errors')

    def parse_result(self, result):
        self.status_code = result.status_code
        try:
            if self.status_code // 100 != 2:
                self._extract_json_body(result)
                if self.status_code == 429:
                    raise exceptions.UsageLimitsExceededException(self, const.USAGE_EXCEEDED_MESSAGE)
                if self.status_code == 404:
                    raise exceptions.NotFoundException(self, self.result)
                raise exceptions.RequestFailedException(self, self._bad_status_message())
            else:
                if self.json_response:
                    self._extract_json_body(result)
                    self.total = self.json.get('total')
                    self.limit = self.json.get('limit')
                    self.offset = self.json.get('offset')
                    self.order_by = self.json.get('order_by')
                    self.direction = self.json.get('direction')
                    if 'result' in self.json:
                        result = self.json['result']
                    elif 'results' in self.json:
                        result = self.json['results']
                    else:
                        raise exceptions.RequestFailedException(
                            self,
                            'The response standard must contain either the "result" or "results" key.'
                        )
                    if isinstance(result, list):
                        self.result = self.result_parser.parse_result_list(self.api_instance, result, **self.parser_kwargs)
                    else:
                        self.result = self.result_parser.parse_result(self.api_instance, result, **self.parser_kwargs)
                else:
                    self.result = self.result_parser.parse_result(self.api_instance, result, **self.parser_kwargs)
        except JSONDecodeError as e:
            if self.status_code == 404:
                raise raise_from(exceptions.NotFoundException(self, 'The requested endpoint does not exist.'), e)
            else:
                raise raise_from(exceptions.RequestFailedException(self, 'Server returned non-JSON response.'), e)

    def __next__(self):
        return self.next_page()

    def next_page(self):
        if self._exception is not None:
            raise StopIteration()
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

    def download(self, hash_value, hash_type, output_file, create=False, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': self.download_fmt.format(self.download_base, hash_type, hash_value),
                'stream': True,
            },
            json_response=False,
            result_parser=resources.LocalArtifact,
            output_file=output_file,
            create=create,
            raise_on_error=raise_on_error,
        )

    def download_archive(self, u, output_file, create=False, raise_on_error=True):
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
            json_response=False,
            result_parser=resources.LocalArtifact,
            output_file=output_file,
            create=create,
            raise_on_error=raise_on_error,
        )

    def stream(self, since=const.MAX_SINCE_TIME_STREAM, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/download/stream'.format(self.consumer_base),
                'params': {'since': since},
            },
            result_parser=resources.ArtifactArchive,
            raise_on_error=raise_on_error,
        )

    def search_hash(self, h, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': self.search_base,
                'params': {
                    'type': h.hash_type,
                    'hash': h.hash,
                },
            },
            result_parser=resources.ArtifactInstance,
            raise_on_error=raise_on_error,
        )

    def search_metadata(self, q, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': self.search_base,
                'params': {
                    'type': 'metadata',
                },
                'json': q.query,
            },
            result_parser=resources.ArtifactInstance,
            raise_on_error=raise_on_error,
        )

    def submit(self, artifact, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'POST',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': self.community_base,
                'files': {
                    'file': (artifact.artifact_name, artifact.open()),
                },
                # very oddly, when included in files parameter this errors out
                'data': {'artifact-type': artifact.artifact_type.name}
            },
            result_parser=resources.Submission,
            raise_on_error=raise_on_error,
        )

    def rescan(self, h, raise_on_error=False):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'POST',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/rescan/{}/{}'.format(self.community_base, h.hash_type, h.hash)
            },
            result_parser=resources.Submission,
            raise_on_error=raise_on_error,
        )

    def lookup_uuid(self, uuid, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/uuid/{}'.format(self.community_base, uuid)
            },
            result_parser=resources.Submission,
            raise_on_error=raise_on_error,
        )

    def _get_engine_names(self, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/microengines/list'.format(self.uri),
                'headers': {'Authorization': None},
            },
            result_parser=resources.Engine,
            raise_on_error=raise_on_error,
        )

    def create_live_hunt(self, rule, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'POST',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/live'.format(self.hunt_base),
                'json': {'yara': rule.ruleset},
            },
            result_parser=resources.Hunt,
            raise_on_error=raise_on_error,
        )

    def get_live_hunt(self, hunt_id=None, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/live'.format(self.hunt_base),
                'params': {
                    'id': hunt_id,
                },
            },
            result_parser=resources.Hunt,
            raise_on_error=raise_on_error,
        )

    def update_live_hunt(self, hunt_id=None, active=False, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'PUT',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/live'.format(self.hunt_base),
                'json': {
                    'id': hunt_id,
                    'active': active,
                },
            },
            result_parser=resources.Hunt,
            raise_on_error=raise_on_error,
        )

    def delete_live_hunt(self, hunt_id, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'DELETE',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/live'.format(self.hunt_base),
                'params': {'id': hunt_id}
            },
            result_parser=resources.Hunt,
            raise_on_error=raise_on_error,
        )

    def live_list(self, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/live/list'.format(self.hunt_base),
                'params': {'all': 'true'},
            },
            result_parser=resources.Hunt,
            raise_on_error=raise_on_error,
        )

    def live_hunt_results(self, hunt_id=None, since=None, raise_on_error=True):
        req = {
            'method': 'GET',
            'timeout': const.DEFAULT_HTTP_TIMEOUT,
            'url': '{}/live/results'.format(self.hunt_base),
            'params': {
                'since': since,
                'id': hunt_id,
            },
        }
        return PolyswarmRequest(
            self.api_instance,
            req,
            result_parser=resources.HuntResult,
            raise_on_error=raise_on_error,
        )

    def create_historical_hunt(self, rule, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'POST',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/historical'.format(self.hunt_base),
                'json': {'yara': rule.ruleset},
            },
            result_parser=resources.Hunt,
            raise_on_error=raise_on_error,
        )

    def get_historical_hunt(self, hunt_id, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/historical'.format(self.hunt_base),
                'params': {'id': hunt_id}
            },
            result_parser=resources.Hunt,
            raise_on_error=raise_on_error,
        )

    def delete_historical_hunt(self, hunt_id, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'DELETE',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/historical'.format(self.hunt_base),
                'params': {'id': hunt_id}
            },
            result_parser=resources.Hunt,
            raise_on_error=raise_on_error,
        )

    def historical_list(self, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/historical/list'.format(self.hunt_base),
                'params': {'all': 'true'},
            },
            result_parser=resources.Hunt,
            raise_on_error=raise_on_error,
        )

    def historical_hunt_results(self, hunt_id=None, raise_on_error=True):
        req = {
            'method': 'GET',
            'timeout': const.DEFAULT_HTTP_TIMEOUT,
            'url': '{}/historical/results'.format(self.hunt_base),
            'params': {
                'id': hunt_id,
            },
        }
        return PolyswarmRequest(
            self.api_instance,
            req,
            result_parser=resources.HuntResult,
            raise_on_error=raise_on_error,
        )

    def score(self, uuid, raise_on_error=True):
        return PolyswarmRequest(
            self.api_instance,
            {
                'method': 'GET',
                'timeout': const.DEFAULT_HTTP_TIMEOUT,
                'url': '{}/submission/{}/polyscore'.format(self.consumer_base, uuid)
            },
            result_parser=resources.PolyScore,
            raise_on_error=raise_on_error,
        )


class PolyswarmRequestExecutor(object):
    """ This class accepts requests from a PolyswarmRequestGenerator and executes it """
    def __init__(self):
        self.requests = []

    def push(self, request):
        raise NotImplementedError()

    def execute(self):
        raise NotImplementedError()


class PolyswarmFuturesExecutor(PolyswarmRequestExecutor):
    def __init__(self):
        self.executor = futures.ThreadPoolExecutor(const.DEFAULT_WORKER_COUNT)
        super(PolyswarmFuturesExecutor, self).__init__()

    def push(self, request):
        self.requests.append(self.executor.submit(request.execute))
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
            responses.append(request.execute())
        self.requests = []
        return responses
