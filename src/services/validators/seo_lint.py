"""
SEO Lint Validator - checks basic SEO requirements.
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Severity(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


@dataclass
class Issue:
    check: str
    severity: Severity
    message: str
    value: Optional[str] = None
    expected: Optional[str] = None


@dataclass
class SEOLintReport:
    score: float
    status: str  # passed | warning | failed
    issues: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "status": self.status,
            "issues": [
                {
                    "check": i.check,
                    "severity": i.severity.value,
                    "message": i.message,
                    "value": i.value,
                    "expected": i.expected,
                }
                for i in self.issues
            ],
        }


class SEOLintValidator:
    """Validates SEO requirements for drafts."""

    def validate(
        self,
        content_md: str,
        title: str,
        meta_description: Optional[str],
        target_keyword: str,
        word_count_min: int = 1500,
        word_count_max: int = 2500,
    ) -> SEOLintReport:
        """
        Run all SEO lint checks.

        Returns SEOLintReport with score 0-100 and list of issues.
        """
        issues = []

        # 1. Title length check
        issues.append(self._check_title_length(title))

        # 2. Title contains keyword
        issues.append(self._check_title_keyword(title, target_keyword))

        # 3. Meta description length
        issues.append(self._check_meta_description(meta_description))

        # 4. H1 count
        issues.append(self._check_h1_count(content_md))

        # 5. H1 contains keyword
        issues.append(self._check_h1_keyword(content_md, target_keyword))

        # 6. Keyword density
        issues.append(self._check_keyword_density(content_md, target_keyword))

        # 7. Word count
        issues.append(self._check_word_count(content_md, word_count_min, word_count_max))

        # 8. Internal links (check for markdown links)
        issues.append(self._check_links(content_md))

        # 9. Has H2 headings for structure
        issues.append(self._check_h2_structure(content_md))

        # Calculate score
        score = self._calculate_score(issues)

        # Determine status
        has_fail = any(i.severity == Severity.FAIL for i in issues)
        has_warning = any(i.severity == Severity.WARNING for i in issues)

        if has_fail:
            status = "failed"
        elif has_warning:
            status = "warning"
        else:
            status = "passed"

        return SEOLintReport(score=score, status=status, issues=issues)

    def _check_title_length(self, title: str) -> Issue:
        """Check title length (optimal: 30-60 chars)."""
        length = len(title) if title else 0

        if 30 <= length <= 60:
            return Issue(
                check="title_length",
                severity=Severity.PASS,
                message=f"Title length OK ({length} chars)",
                value=str(length),
                expected="30-60",
            )
        elif 20 <= length < 30 or 60 < length <= 70:
            return Issue(
                check="title_length",
                severity=Severity.WARNING,
                message=f"Title length not optimal ({length} chars)",
                value=str(length),
                expected="30-60",
            )
        else:
            return Issue(
                check="title_length",
                severity=Severity.FAIL,
                message=f"Title length out of range ({length} chars)",
                value=str(length),
                expected="30-60",
            )

    def _check_title_keyword(self, title: str, keyword: str) -> Issue:
        """Check if title contains keyword."""
        if not title or not keyword:
            return Issue(
                check="title_keyword",
                severity=Severity.FAIL,
                message="Missing title or keyword",
            )

        title_lower = title.lower()
        keyword_lower = keyword.lower()

        if keyword_lower in title_lower:
            return Issue(
                check="title_keyword",
                severity=Severity.PASS,
                message="Title contains target keyword",
            )

        # Check partial match (any word from keyword)
        keyword_words = keyword_lower.split()
        matches = sum(1 for word in keyword_words if word in title_lower)

        if matches >= len(keyword_words) / 2:
            return Issue(
                check="title_keyword",
                severity=Severity.WARNING,
                message=f"Title partially contains keyword ({matches}/{len(keyword_words)} words)",
            )

        return Issue(
            check="title_keyword",
            severity=Severity.FAIL,
            message="Title does not contain target keyword",
            expected=keyword,
        )

    def _check_meta_description(self, meta_description: Optional[str]) -> Issue:
        """Check meta description length (optimal: 120-160 chars)."""
        if not meta_description:
            return Issue(
                check="meta_description",
                severity=Severity.WARNING,
                message="Meta description is missing",
                expected="120-160 chars",
            )

        length = len(meta_description)

        if 120 <= length <= 160:
            return Issue(
                check="meta_description",
                severity=Severity.PASS,
                message=f"Meta description length OK ({length} chars)",
                value=str(length),
                expected="120-160",
            )
        elif 80 <= length < 120 or 160 < length <= 200:
            return Issue(
                check="meta_description",
                severity=Severity.WARNING,
                message=f"Meta description not optimal ({length} chars)",
                value=str(length),
                expected="120-160",
            )
        else:
            return Issue(
                check="meta_description",
                severity=Severity.FAIL,
                message=f"Meta description length out of range ({length} chars)",
                value=str(length),
                expected="120-160",
            )

    def _check_h1_count(self, content: str) -> Issue:
        """Check that there's exactly one H1 heading."""
        # Match markdown H1: starts with single # followed by space
        h1_pattern = r'^# [^\n]+'
        h1_matches = re.findall(h1_pattern, content, re.MULTILINE)
        count = len(h1_matches)

        if count == 1:
            return Issue(
                check="h1_count",
                severity=Severity.PASS,
                message="Exactly one H1 heading found",
                value=str(count),
                expected="1",
            )
        elif count == 0:
            return Issue(
                check="h1_count",
                severity=Severity.WARNING,
                message="No H1 heading found (will use title)",
                value="0",
                expected="1",
            )
        else:
            return Issue(
                check="h1_count",
                severity=Severity.FAIL,
                message=f"Multiple H1 headings found ({count})",
                value=str(count),
                expected="1",
            )

    def _check_h1_keyword(self, content: str, keyword: str) -> Issue:
        """Check if H1 contains keyword."""
        h1_pattern = r'^# ([^\n]+)'
        h1_match = re.search(h1_pattern, content, re.MULTILINE)

        if not h1_match:
            return Issue(
                check="h1_keyword",
                severity=Severity.WARNING,
                message="No H1 to check for keyword",
            )

        h1_text = h1_match.group(1).lower()
        keyword_lower = keyword.lower()

        if keyword_lower in h1_text:
            return Issue(
                check="h1_keyword",
                severity=Severity.PASS,
                message="H1 contains target keyword",
            )

        return Issue(
            check="h1_keyword",
            severity=Severity.WARNING,
            message="H1 does not contain target keyword",
        )

    def _check_keyword_density(self, content: str, keyword: str) -> Issue:
        """Check keyword density (optimal: 1-2%)."""
        if not content or not keyword:
            return Issue(
                check="keyword_density",
                severity=Severity.FAIL,
                message="Missing content or keyword",
            )

        # Clean content (remove markdown)
        clean_content = re.sub(r'[#*_\[\]()]', ' ', content.lower())
        words = clean_content.split()
        total_words = len(words)

        if total_words == 0:
            return Issue(
                check="keyword_density",
                severity=Severity.FAIL,
                message="No words in content",
            )

        # Count keyword occurrences
        keyword_lower = keyword.lower()
        keyword_count = clean_content.count(keyword_lower)

        # Density as percentage
        density = (keyword_count * len(keyword_lower.split()) / total_words) * 100

        if 1.0 <= density <= 2.0:
            return Issue(
                check="keyword_density",
                severity=Severity.PASS,
                message=f"Keyword density OK ({density:.1f}%)",
                value=f"{density:.1f}%",
                expected="1-2%",
            )
        elif 0.5 <= density < 1.0 or 2.0 < density <= 3.0:
            return Issue(
                check="keyword_density",
                severity=Severity.WARNING,
                message=f"Keyword density not optimal ({density:.1f}%)",
                value=f"{density:.1f}%",
                expected="1-2%",
            )
        else:
            return Issue(
                check="keyword_density",
                severity=Severity.FAIL,
                message=f"Keyword density out of range ({density:.1f}%)",
                value=f"{density:.1f}%",
                expected="1-2%",
            )

    def _check_word_count(self, content: str, min_words: int, max_words: int) -> Issue:
        """Check word count is within range."""
        # Clean markdown for accurate word count
        clean_content = re.sub(r'[#*_\[\]()]', ' ', content)
        words = clean_content.split()
        count = len(words)

        if min_words <= count <= max_words:
            return Issue(
                check="word_count",
                severity=Severity.PASS,
                message=f"Word count OK ({count} words)",
                value=str(count),
                expected=f"{min_words}-{max_words}",
            )

        # Allow 10% tolerance
        tolerance = 0.1
        if min_words * (1 - tolerance) <= count <= max_words * (1 + tolerance):
            return Issue(
                check="word_count",
                severity=Severity.WARNING,
                message=f"Word count slightly off ({count} words)",
                value=str(count),
                expected=f"{min_words}-{max_words}",
            )

        return Issue(
            check="word_count",
            severity=Severity.FAIL,
            message=f"Word count out of range ({count} words)",
            value=str(count),
            expected=f"{min_words}-{max_words}",
        )

    def _check_links(self, content: str) -> Issue:
        """Check for links in content."""
        # Match markdown links: [text](url)
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        links = re.findall(link_pattern, content)
        count = len(links)

        if count >= 2:
            return Issue(
                check="links",
                severity=Severity.PASS,
                message=f"Found {count} links",
                value=str(count),
                expected="2+",
            )
        elif count == 1:
            return Issue(
                check="links",
                severity=Severity.WARNING,
                message="Only 1 link found",
                value="1",
                expected="2+",
            )
        else:
            return Issue(
                check="links",
                severity=Severity.WARNING,
                message="No links found",
                value="0",
                expected="2+",
            )

    def _check_h2_structure(self, content: str) -> Issue:
        """Check for H2 headings (structure)."""
        h2_pattern = r'^## [^\n]+'
        h2_matches = re.findall(h2_pattern, content, re.MULTILINE)
        count = len(h2_matches)

        if count >= 3:
            return Issue(
                check="h2_structure",
                severity=Severity.PASS,
                message=f"Good structure with {count} H2 sections",
                value=str(count),
                expected="3+",
            )
        elif count >= 1:
            return Issue(
                check="h2_structure",
                severity=Severity.WARNING,
                message=f"Only {count} H2 section(s)",
                value=str(count),
                expected="3+",
            )
        else:
            return Issue(
                check="h2_structure",
                severity=Severity.FAIL,
                message="No H2 headings - poor structure",
                value="0",
                expected="3+",
            )

    def _calculate_score(self, issues: list) -> float:
        """Calculate overall score based on issues."""
        if not issues:
            return 100.0

        # Weights for each check
        weights = {
            "title_length": 10,
            "title_keyword": 15,
            "meta_description": 10,
            "h1_count": 10,
            "h1_keyword": 10,
            "keyword_density": 15,
            "word_count": 10,
            "links": 10,
            "h2_structure": 10,
        }

        total_weight = sum(weights.values())
        earned_points = 0

        for issue in issues:
            weight = weights.get(issue.check, 10)
            if issue.severity == Severity.PASS:
                earned_points += weight
            elif issue.severity == Severity.WARNING:
                earned_points += weight * 0.5
            # FAIL = 0 points

        return round((earned_points / total_weight) * 100, 1)
