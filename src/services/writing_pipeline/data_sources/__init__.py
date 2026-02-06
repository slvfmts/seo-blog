"""
Data sources for the Research stage.

Available sources:
- SerperDataSource: Serper.dev API for SERP data
- JinaReader: Jina Reader API for extracting clean markdown from URLs
- TrafilaturaExtractor: Local fallback for content extraction
- DataForSEO: DataForSEO API for keyword metrics
"""

from .serper import SerperDataSource

# These are imported lazily to avoid dependency issues
# Use the helper functions to check availability

__all__ = [
    "SerperDataSource",
    "get_jina_reader",
    "get_trafilatura_extractor",
    "get_dataforseo_client",
    "is_trafilatura_available",
]


def get_jina_reader(api_key=None):
    """
    Get a JinaReader instance.

    Args:
        api_key: Optional API key for higher rate limits

    Returns:
        JinaReader instance
    """
    from .jina_reader import JinaReader
    return JinaReader(api_key=api_key)


def get_trafilatura_extractor(**kwargs):
    """
    Get a TrafilaturaExtractor instance.

    Returns:
        TrafilaturaExtractor instance or None if not available
    """
    try:
        from .trafilatura_ext import TrafilaturaExtractor, is_trafilatura_available
        if is_trafilatura_available():
            return TrafilaturaExtractor(**kwargs)
    except ImportError:
        pass
    return None


def is_trafilatura_available():
    """Check if trafilatura is available."""
    try:
        from .trafilatura_ext import is_trafilatura_available as check
        return check()
    except ImportError:
        return False


def get_dataforseo_client(login, password, **kwargs):
    """
    Get a DataForSEO client instance.

    Args:
        login: DataForSEO API login
        password: DataForSEO API password

    Returns:
        DataForSEO instance
    """
    from .dataforseo import DataForSEO
    return DataForSEO(login=login, password=password, **kwargs)
