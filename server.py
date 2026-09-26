#!/usr/bin/env python3
"""
Honeycombe Arts Hub — website + built-in CMS server.
Zero dependencies: runs anywhere with Python 3.8+.

    python3 server.py [port]

Public site:  http://localhost:8000
Staff admin:  http://localhost:8000/admin

The code lives in the hah/ package; this file is just the entry point.
"""
from hah.app import main

if __name__ == "__main__":
    main()
