try:
    from . import http
    from . import api
    from . import types
    from . import exceptions
    from ._version import get_version
except ImportError:
    from polyswarm_api import http
    from polyswarm_api import api
    from polyswarm_api import types
    from polyswarm_api import exceptions
    from polyswarm_api._version import get_version