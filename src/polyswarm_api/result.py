import asyncio

from .engine_resolver import EngineResolver
class SingleBountyAssertion(object):
    def __init__(self, ba):
        self.__dict__ = ba



class BountyAssertions(object):
    def __init__(self, bounty_assertion):
        self.bounty_assertion = bounty_assertion

    def __len__(self):
        return len(self.assertions)

    @property
    def assertions(self):
        return [SingleBountyAssertion(b) for b in self.bounty_assertion['assertions']]

    @property
    def malicious(self):
        return [b for b in self.assertions if b.verdict]

    @property
    def non_malicious(self):
        return [b for b in self.assertions if not b.verdict]

    @property
    def permalink(self):
        return self.bounty_assertion['permalink']


class BountyResult(object):
    def __init__(self, br):
        self.__dict__ = br

    def has_assertions(self):
        try:
            return self.has_files() and self.get_file_assertions().assertions
        except AttributeError:
            pass
        return False

    def has_files(self):
        try:
            return len(self.files)
        except AttributeError:
            pass
        return False

    def get_file_assertions(self, file_idx=0):
        return BountyAssertions(self.files[file_idx])



class PolyswarmSearchResults(object):

    def __init__(self, raw_result):
        self.raw_result = raw_result


    @property
    def result(self):
        return self.raw_result['result']

    @property
    def search_result(self):

        return self.result[0]

    def __bool__(self):
        return bool(self.result)

    def get_latest_bounty_with_assertions(self):
        try:
            for artifact_instance in self.search_result.get('artifact_instances', []):
                bountyresult = artifact_instance.get("bountyresult")
                if bountyresult is None:
                    continue
                br = BountyResult(bountyresult)
                if br.has_assertions():
                    return br
        except IndexError:
            pass

