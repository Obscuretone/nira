from nira_app.web import NiraWebApp
from nira_app.storage import NiraStore
from pathlib import Path
import tempfile
import traceback

with tempfile.TemporaryDirectory() as td:
    store = NiraStore(Path(td))
    store.initialize("EMH")
    app = NiraWebApp(store)
    from wsgiref.util import setup_testing_defaults

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/tickets/new"}
    setup_testing_defaults(environ)

    try:

        def sr(s, h):
            print(s)

        match = app.router.match("GET", "/tickets/new")
        handler, params = match
        res = handler(query={}, form={}, **params)
        print("Success:", res)
    except Exception:
        traceback.print_exc()
