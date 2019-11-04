import time
import os

try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

from . import const
from .endpoint import PolyswarmFuturesExecutor, PolyswarmRequestGenerator
from .types.artifact import ArtifactType, LocalArtifact
from .types.hash import to_hash
from .types.query import MetadataQuery
from .types import result
from .types.hunt import YaraRuleset, Hunt


class PolyswarmAPI(object):
    """A synchronous interface to the public and private PolySwarm APIs."""

    def __init__(self, key, uri='https://api.polyswarm.network/v1', timeout=const.DEFAULT_SCAN_TIMEOUT,
                 community='lima', validate_schemas=False, executor=None, generator=None):
        """
        :param key: PolySwarm API key
        :param uri: PolySwarm API URI
        :param timeout: How long to wait for operations to complete.
        :param community: Community to scan against.
        :param validate_schemas: Validate JSON objects when creating response objects. Will impact performance.
        """
        self.executor = executor or PolyswarmFuturesExecutor(key)
        self.generator = generator or PolyswarmRequestGenerator(self, uri, community)

        self.timeout = timeout
        self._engine_map = None
        self.validate = validate_schemas

    def search(self, *hashes, **kwargs):
        """
        Search a list of hashes.

        :param hashes: A list of Hashable objects (Artifact, LocalArtifact, Hash) or hex-encoded SHA256/SHA1/MD5
        :param kwargs: Arguments to pass to search. Supported: with_instances, with_metadata (booleans)
        :return: Generator of SearchResult objects
        """

        hashes = [to_hash(h) for h in hashes]

        for h in hashes:
            self.executor.push(self.generator.search_hash(h, **kwargs))

        for request in self.executor.execute():
            yield request.result

    def search_by_feature(self, feature, *artifacts):
        """
        Search artifacts by feature

        :param artifacts: List of LocalArtifact objects
        :param feature: Feature to use
        :return: SearchResult generator
        """
        raise NotImplementedError

    def search_by_metadata(self, *queries, **kwargs):
        """
        Search artifacts by metadata

        :param queries: List of MetadataQuery objects (or query_strings)
        :return: SearchResult generator
        """
        for query in queries:
            if not isinstance(query, MetadataQuery):
                query = MetadataQuery(query, polyswarm=self)
            self.executor.push(self.generator.search_metadata(query, **kwargs))

        for request in self.executor.execute():
            yield request.result

    def download(self, out_dir, *hashes):
        hashes = [to_hash(h) for h in hashes]

        for h in hashes:
            path = os.path.join(out_dir, h.hash)
            self.executor.push(self.generator.download(h.hash, h.hash_type, path, create=True))

        for request in self.executor.execute():
            yield request.result

    def download_to_filehandle(self, h, fh):
        """
        Grab the data of artifact indentified by hash, and write the data to a file handle
        :param h: hash
        :param fh: file handle
        :return: DownloadResult object
        """
        h = to_hash(h)

        return result.DownloadResult(h, self.generator.download(h, fh).result())

    def submit(self, *artifacts):
        """
        Submit artifacts to polyswarm and return UUIDs

        :param artifacts: List of LocalArtifacts or paths to local files
        :return: SubmitResult generator
        """
        for artifact in artifacts:
            if not isinstance(artifact, LocalArtifact):
                artifact = LocalArtifact(path=artifact, artifact_name=os.path.basename(artifact),
                                         analyze=False, polyswarm=self)
            self.executor.push(self.generator.submit(artifact))
        for request in self.executor.execute():
            yield request.result

    def rescan_submit(self, *hashes, **kwargs):
        """
        Submit rescans to polyswarm and return UUIDs

        :param artifact_type: What type to use when rescanning artifact
        :param hashes: Hashable objects (Artifact, LocalArtifact, or Hash) or hex-encoded SHA256/SHA1/MD5
        :return: SubmitResult generator
        """
        hashes = [to_hash(h) for h in hashes]

        for h in hashes:
            self.executor.push(self.generator.rescan(h, **kwargs))

        for request in self.executor.execute():
            yield request.result

    def scan(self, *artifacts):
        """
        Submit artifacts to polyswarm and wait for scan results

        :param artifacts: List of LocalArtifacts or paths to local files
        :return: ScanResult generator
        """
        for submission in self.submit(*artifacts):
            s = submission.wait_for_scan()
            s.artifact = submission.artifact
            yield s

    def rescan(self, *hashes, **kwargs):
        """
        Rescan artifacts via polyswarm

        :param hashes: Hashable objects (Artifact, LocalArtifact, or Hash) or hex-encoded SHA256/SHA1/MD5
        :param kwargs: Keyword arguments for the scan (none currently supported)
        :return: ScanResult generator
        """
        for submission in self.rescan_submit(*hashes, **kwargs):
            if submission.status_code == 200:
                s = submission.wait_for_scan()
                s.artifact = submission.artifact
                yield s
            else:
                yield submission

    def wait_for(self, *uuids):
        """
        Wait for submissions to scan successfully

        :param uuids: List of UUIDs to wait for
        :return: ScanResult generator
        """
        start = time.time()
        for uuid in uuids:
            while True:
                scan_result = next(self.lookup(uuid))

                if scan_result.ready:
                    yield scan_result
                    break
                elif -1 < self.timeout < time.time() - start:
                    scan_result.timeout = True
                    yield scan_result
                    break
                else:
                    time.sleep(3)

    def lookup(self, *uuids):
        """
        Lookup a submission by UUID.

        :param uuids: UUIDs to lookup
        :return: ScanResult object generator
        """
        for uuid in uuids:
            self.executor.push(self.generator.lookup_uuid(uuid))

        for request in self.executor.execute():
            yield request.result

    def score(self, *uuids):
        """
        Lookup a PolyScore(s) for a given submission, by UUID

        :param uuids: UUIDs to lookup
        :return: ScoreResult object generator
        """
        for uuid in uuids:
            self.executor.push(self.generator.score(uuid))

        for request in self.executor.execute():
            yield request.result

    def scan_directory(self, directory, recursive=False):
        """
        Scan a directory of files via PolySwarm

        :param directory: Directory to scan
        :param recursive: Whether to look for files recursively
        :return: ScanResult generator
        """
        if recursive:
            file_list = [os.path.join(path, file)
                         for (path, dirs, files) in os.walk(directory)
                         for file in files if os.path.isfile(os.path.join(path, file))]
        else:
            file_list = [os.path.join(directory, file) for file in os.listdir(directory)
                         if os.path.isfile(os.path.join(directory, file))]

        return self.scan(*file_list)

    def scan_urls(self, *urls):
        """
        Scan URLs via PolySwarm

        :param urls: URLs to scan
        :return: ScanResult generator
        """
        _urls = []

        for url in urls:
            if not isinstance(url, LocalArtifact):
                url = LocalArtifact(content=url.encode("utf8"), artifact_name=url, artifact_type=ArtifactType.URL,
                                    analyze=False, polyswarm=self)
            _urls.append(url)

        return self.scan(*_urls)

    def _resolve_engine_name(self, eth_pub):
        if not self._engine_map:
            self.executor.push(self.generator._get_engine_names())
            for request in self.executor.execute():
                self._engine_map = request.result.result
                break
        return self._engine_map.get(eth_pub.lower(), eth_pub) if self._engine_map is not None else ''

    def check_version(self):
        """
        Checks GitHub to see if you have the latest version installed.
        TODO this will be re-enabled when better version info is available in the API

        :return: True,latest_version tuple if latest, False,latest_version tuple if not
        """
        raise NotImplementedError

    def live(self, rules):
        """
        Create a new live hunt, and replace the currently running YARA rules.

        :param rules: YaraRuleset object or string containing YARA rules to install
        :return: HuntSubmissionResult object
        """
        if not isinstance(rules, YaraRuleset):
            rules = YaraRuleset(rules, polyswarm=self)
        return next(self.executor.push(self.generator.submit_live_hunt(rules)).execute()).result

    def historical(self, rules):
        """
        Run a new historical hunt.

        :param rules: YaraRuleset object or string containing YARA rules to install
        :return: HuntSubmissionResult object
        """
        if not isinstance(rules, YaraRuleset):
            rules = YaraRuleset(rules, polyswarm=self)
        return next(self.executor.push(self.generator.submit_historical_hunt(rules)).execute()).result

    def live_delete(self, hunt_id):
        """
        Delete a live hunt.

        :param hunt_id: Hunt ID
        :return: HuntDeletionResult object
        """
        return next(self.executor.push(self.generator.live_delete(hunt_id)).execute()).result

    def live_list(self):
        """
        List all the live hunts

        :return: HuntListResult object
        """
        return next(self.executor.push(self.generator.live_list()).execute()).result

    def historical_delete(self, hunt_id):
        """
        Delete a historical hunts.

        :param hunt_id: Hunt ID
        :return: HuntDeletionResult object
        """
        return next(self.executor.push(self.generator.historical_delete(hunt_id)).execute()).result

    def historical_list(self):
        """
        List all historical hunts

        :return: HuntListResult object
        """
        return next(self.executor.push(self.generator.historical_list()).execute()).result

    def _get_hunt_results(self, hunt, endpoint_func, **kwargs):
        if hunt and not isinstance(hunt, Hunt):
            hunt = Hunt.from_id(hunt, self)

        if hunt:
            kwargs['id'] = hunt.hunt_id

        # at least make this consistent in the API
        # should change this
        if 'with_instances' in kwargs:
            kwargs['with_bounty_results'] = kwargs['with_instances']
            del kwargs['with_instances']

        all_matches = []
        self.executor.push(endpoint_func(**kwargs))
        while True:
            request = next(self.executor.execute())
            if not request.result.result.results:
                break
            else:
                # We should be yielding everyting here, but instead we
                # gather the objects as we are expecting nested results
                # yield from request.result.result.results
                all_matches.extend(request.result.result.results)
                self.executor.push(request.next_page())
        request.result.result.results = all_matches
        return request.result

    def live_results(self, hunt=None, **kwargs):
        """
        Get results from a live hunt

        :param hunt_id: ID of the hunt (None if latest rule results are desired)
        :return: HuntResult object
        """
        return self._get_hunt_results(hunt, self.generator.live_lookup, **kwargs)

    def historical_results(self, hunt=None, **kwargs):
        """
        Get results from a historical hunt

        :param hunt_id: ID of the hunt (None if latest hunt results are desired)
        :return: HuntResult object
        """
        return self._get_hunt_results(hunt, self.generator.historical_lookup, **kwargs)

    def stream(self, destination=None, since=const.MAX_SINCE_TIME_STREAM):
        """
        Access the stream of artifacts (ask info@polyswarm.io about access)

        :param destination: Directory to save the files
        :param since: How far back to grab artifacts in minutes (up to 2 days)
        :return: DownloadResult generator
        """
        self.executor.push(self.generator.stream(since=since))
        for request in self.executor.execute():
            for url in request.result:
                path = os.path.join(destination, os.path.basename(urlparse(url).path))
                self.executor.push(self.generator.download_archive(url, path, create=True))

            for request in self.executor.execute():
                yield request.result
