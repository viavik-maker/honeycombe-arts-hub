"""Start-up: prepare the data folder and database, register every route,
start the background worker, serve."""
import os
import signal
import sys
import threading
from http.server import ThreadingHTTPServer

# importing these modules registers their routes and jobs
from . import (accounts, activities, book_api, booking_settings, bookings_staff, cms, config, db,  # noqa: F401
               carers, family_api, finance, finance_reports, gdpr, guests, importer, incidents, intray, marketing, messaging, outbox,
               pages, payments_stripe, people, portal_pages, registers, reports, send_support, sms_hooks, staff,
               staff_bookings, system, waitlist)
from .content import bootstrap_seed
from .web import Handler
from .worker import Worker


def prepare_data():
    os.makedirs(config.DATA, exist_ok=True)
    os.makedirs(config.UPLOADS, exist_ok=True)
    bootstrap_seed()
    applied = db.migrate()
    if applied:
        print("  database: applied migrations %s" % ", ".join("%04d" % v for v in applied))
    moved = cms.migrate_messages_json()
    if moved:
        print("  inbox: moved %d contact messages into the database" % moved)
    moved = marketing.migrate_subscribers_json()
    if moved:
        print("  newsletter: moved %d subscribers into the database" % moved)


class Server(ThreadingHTTPServer):
    # the default listen backlog is 5: in a busy minute (bookings opening) a
    # burst of visitors would be made to wait a second and retry
    request_queue_size = 128
    daemon_threads = True


def make_server(address):
    return Server(address, Handler)


def main():
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PORT", 8000))
    prepare_data()
    server = make_server(("0.0.0.0", port))
    system.WORKER = Worker()
    if not system.WORKER.start():
        system.WORKER = None

    def stop(signum, frame):
        # Render sends SIGTERM before a deploy swaps instances: finish cleanly
        if system.WORKER:
            system.WORKER.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
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
    finally:
        if system.WORKER:
            system.WORKER.stop()
