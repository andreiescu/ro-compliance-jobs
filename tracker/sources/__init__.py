"""Registry of job sources. Each module listed here exposes ``SOURCE``."""
from __future__ import annotations

import importlib

MODULES = [
    "linkedin",
    "sg",
    "hipo",
    "posturi_gov",
    "banks",
    "workday",
    "ats",
]


def all_sources() -> list:
    return [importlib.import_module(f"{__name__}.{m}").SOURCE for m in MODULES]


def by_name() -> dict:
    return {s.name: s for s in all_sources()}
