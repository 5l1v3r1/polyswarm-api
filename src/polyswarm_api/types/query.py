import json


from .base import BasePSType



class Query(BasePSType):
    def __init__(self, polyswarm=None):
        super(Query, self).__init__(polyswarm)


class MetadataQuery(Query):
    """ Class representing a MetadataQuery """
    def __init__(self, query, raw=False, polyswarm=None):
        super(MetadataQuery, self).__init__(polyswarm)
        if not raw:
            query = {
                'query': {
                    'query_string': {
                        'query': query
                    }
                }
            }
        self.query = query
        self.validate()

    def validate(self):
        return

    def __repr__(self):
        return json.dumps(self.query)
