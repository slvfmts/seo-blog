"""
Plagiarism Validator - checks text similarity with competitors.
"""

import re
import difflib
import httpx
from dataclasses import dataclass, field
from typing import Optional
from bs4 import BeautifulSoup


@dataclass
class SimilarityMatch:
    url: str
    similarity: float  # 0.0 - 1.0
    matched_text: Optional[str] = None


@dataclass
class PlagiarismReport:
    score: float  # 0-100 (higher = more original)
    status: str  # passed | warning | failed
    max_similarity: float  # 0.0 - 1.0
    matches: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "status": self.status,
            "max_similarity": self.max_similarity,
            "max_similarity_percent": f"{self.max_similarity * 100:.1f}%",
            "matches": [
                {
                    "url": m.url,
                    "similarity": m.similarity,
                    "similarity_percent": f"{m.similarity * 100:.1f}%",
                    "matched_text": m.matched_text,
                }
                for m in self.matches
            ],
        }


class PlagiarismValidator:
    """Validates content originality by comparing with competitor texts."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    async def validate(
        self,
        content: str,
        competitor_urls: list[str],
        threshold_warning: float = 0.15,  # 15%
        threshold_fail: float = 0.30,  # 30%
    ) -> PlagiarismReport:
        """
        Check content similarity against competitor URLs.

        Args:
            content: Article content (markdown)
            competitor_urls: List of URLs to compare against
            threshold_warning: Similarity above this triggers warning
            threshold_fail: Similarity above this triggers failure

        Returns:
            PlagiarismReport with score and matches
        """
        if not content:
            return PlagiarismReport(
                score=0,
                status="failed",
                max_similarity=1.0,
                matches=[],
            )

        if not competitor_urls:
            return PlagiarismReport(
                score=100,
                status="passed",
                max_similarity=0.0,
                matches=[],
            )

        # Clean our content
        clean_content = self._clean_text(content)

        # Fetch and compare with each competitor
        matches = []
        for url in competitor_urls[:5]:  # Limit to 5 URLs
            try:
                competitor_text = await self._fetch_text(url)
                if competitor_text:
                    clean_competitor = self._clean_text(competitor_text)
                    similarity = self._calculate_similarity(clean_content, clean_competitor)

                    if similarity > 0.05:  # Only track significant matches
                        matches.append(
                            SimilarityMatch(
                                url=url,
                                similarity=similarity,
                                matched_text=self._find_longest_match(clean_content, clean_competitor),
                            )
                        )
            except Exception:
                # Skip URLs that fail to fetch
                continue

        # Sort by similarity (highest first)
        matches.sort(key=lambda m: m.similarity, reverse=True)

        # Calculate max similarity and score
        max_similarity = matches[0].similarity if matches else 0.0
        score = max(0, 100 - (max_similarity * 100))

        # Determine status
        if max_similarity >= threshold_fail:
            status = "failed"
        elif max_similarity >= threshold_warning:
            status = "warning"
        else:
            status = "passed"

        return PlagiarismReport(
            score=round(score, 1),
            status=status,
            max_similarity=round(max_similarity, 3),
            matches=matches[:3],  # Return top 3 matches
        )

    def _clean_text(self, text: str) -> str:
        """Clean text for comparison - remove markdown, extra whitespace."""
        # Remove markdown formatting
        clean = re.sub(r'[#*_\[\]()]', ' ', text)
        # Remove URLs
        clean = re.sub(r'https?://\S+', ' ', clean)
        # Remove extra whitespace
        clean = ' '.join(clean.split())
        # Lowercase for comparison
        return clean.lower()

    async def _fetch_text(self, url: str) -> Optional[str]:
        """Fetch and extract text from URL."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                response = await client.get(url, headers=headers)
                response.raise_for_status()

                # Parse HTML and extract text
                soup = BeautifulSoup(response.text, "html.parser")

                # Remove script and style elements
                for element in soup(["script", "style", "nav", "header", "footer", "aside"]):
                    element.decompose()

                # Get text from main content area
                main_content = soup.find("article") or soup.find("main") or soup.find("body")
                if main_content:
                    return main_content.get_text(separator=" ", strip=True)

                return soup.get_text(separator=" ", strip=True)

        except Exception:
            return None

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate text similarity using difflib SequenceMatcher."""
        if not text1 or not text2:
            return 0.0

        # Use SequenceMatcher for similarity ratio
        matcher = difflib.SequenceMatcher(None, text1, text2)
        return matcher.ratio()

    def _find_longest_match(self, text1: str, text2: str, min_length: int = 50) -> Optional[str]:
        """Find the longest matching substring between two texts."""
        if not text1 or not text2:
            return None

        matcher = difflib.SequenceMatcher(None, text1, text2)
        match = matcher.find_longest_match(0, len(text1), 0, len(text2))

        if match.size >= min_length:
            return text1[match.a : match.a + match.size][:200]  # Limit to 200 chars

        return None


class SimplePlagiarismValidator:
    """
    Simple synchronous plagiarism validator for when async isn't needed.
    Uses cached/pre-fetched competitor content.
    """

    def validate_against_texts(
        self,
        content: str,
        competitor_texts: dict[str, str],  # {url: text}
        threshold_warning: float = 0.15,
        threshold_fail: float = 0.30,
    ) -> PlagiarismReport:
        """
        Check content similarity against pre-fetched competitor texts.

        Args:
            content: Article content
            competitor_texts: Dict of {url: extracted_text}

        Returns:
            PlagiarismReport
        """
        if not content:
            return PlagiarismReport(
                score=0,
                status="failed",
                max_similarity=1.0,
                matches=[],
            )

        if not competitor_texts:
            return PlagiarismReport(
                score=100,
                status="passed",
                max_similarity=0.0,
                matches=[],
            )

        # Clean our content
        clean_content = self._clean_text(content)

        matches = []
        for url, text in competitor_texts.items():
            if not text:
                continue

            clean_competitor = self._clean_text(text)
            similarity = self._calculate_similarity(clean_content, clean_competitor)

            if similarity > 0.05:
                matches.append(
                    SimilarityMatch(
                        url=url,
                        similarity=similarity,
                        matched_text=self._find_longest_match(clean_content, clean_competitor),
                    )
                )

        matches.sort(key=lambda m: m.similarity, reverse=True)

        max_similarity = matches[0].similarity if matches else 0.0
        score = max(0, 100 - (max_similarity * 100))

        if max_similarity >= threshold_fail:
            status = "failed"
        elif max_similarity >= threshold_warning:
            status = "warning"
        else:
            status = "passed"

        return PlagiarismReport(
            score=round(score, 1),
            status=status,
            max_similarity=round(max_similarity, 3),
            matches=matches[:3],
        )

    def _clean_text(self, text: str) -> str:
        clean = re.sub(r'[#*_\[\]()]', ' ', text)
        clean = re.sub(r'https?://\S+', ' ', clean)
        clean = ' '.join(clean.split())
        return clean.lower()

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        if not text1 or not text2:
            return 0.0
        matcher = difflib.SequenceMatcher(None, text1, text2)
        return matcher.ratio()

    def _find_longest_match(self, text1: str, text2: str, min_length: int = 50) -> Optional[str]:
        if not text1 or not text2:
            return None
        matcher = difflib.SequenceMatcher(None, text1, text2)
        match = matcher.find_longest_match(0, len(text1), 0, len(text2))
        if match.size >= min_length:
            return text1[match.a : match.a + match.size][:200]
        return None
