#!/usr/bin/env python3
"""Back-compat shim: `python nanny.py <mode>` still works.

The implementation now lives in the `nanny/` package (run it directly with
`python -m nanny <mode>`). This file just forwards to it.
"""
from nanny.core import main

if __name__ == "__main__":
    main()
