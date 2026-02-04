"""
Data sources for the Research stage.

Available sources:
- SerperDataSource: Serper.dev API for SERP data
- WebFetchDataSource: Web content fetching (future)
"""

from .serper import SerperDataSource

__all__ = [
    "SerperDataSource",
]
