"""nanny — supervised on-call automation.

The package is being carved out of the original single file (now `nanny.core`):
    nanny/core.py    — the bulk of nanny (being peeled into the modules below)
    nanny/store.py   — durable state behind one interface (memory | postgres)

Run it with `python -m nanny <mode>` (or the back-compat `python nanny.py <mode>`).
"""

__all__ = ["core", "store"]
