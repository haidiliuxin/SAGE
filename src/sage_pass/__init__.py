"""SAGE-Pass backend package."""

import sys


_MINIMUM_PYTHON = (3, 11)

if sys.version_info < _MINIMUM_PYTHON:
    current = ".".join(str(part) for part in sys.version_info[:3])
    required = ".".join(str(part) for part in _MINIMUM_PYTHON)
    raise RuntimeError(
        f"SAGE-Pass requires Python {required} or newer; current Python is "
        f"{current}. Recreate .venv with Python 3.11 or 3.12."
    )

__version__ = "0.2.0"
