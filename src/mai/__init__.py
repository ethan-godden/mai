"""Mai: a local-first research agent."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mai")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
