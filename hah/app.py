"""Start-up: prepare the data folder, register every route, serve."""
import os
import sys
from http.server import ThreadingHTTPServer

from . import auth, cms, config, pages  # noqa: F401  (importing cms/pages registers their routes)
from .content import bootstrap_seed
from .web import Handler


def prepare_data():
    os.makedirs(config.DATA, exist_ok=True)
    os.makedirs(config.UPLOADS, exist_ok=True)
    bootstrap_seed()
    auth.init_auth()


def make_server(address):
    return ThreadingHTTPServer(address, Handler)


def main():
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PORT", 8000))
    prepare_data()
    server = make_server(("0.0.0.0", port))
    print(f"""
  Honeycombe Arts Hub
  ──────────────────
  Public site : http://localhost:{port}
  Staff admin : http://localhost:{port}/admin
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye!")
