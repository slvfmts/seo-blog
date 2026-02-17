"""
SeoAnalyzer - Programmatic SEO analysis with Russian morphology support.

Uses pymorphy3 for lemmatization so that all word forms
("фрилансом", "фрилансе", "фриланса") are counted as one keyword.
"""

import re
import logging
from typing import Optional

import pymorphy3

from ..contracts import SeoCheckResult, SeoAnalysis

logger = logging.getLogger(__name__)

# Density thresholds
DENSITY_MIN = 0.005  # 0.5%
DENSITY_OK_MIN = 0.01  # 1.0%
DENSITY_OK_MAX = 0.02  # 2.0%
DENSITY_WARN_MAX = 0.03  # 3.0%


class SeoAnalyzer:
    """
    Programmatic SEO analysis with morphological keyword matching.

    Checks:
    - Keyword density (1-2% target)
    - Keyword in H1
    - Keyword in intro (first 100 words)
    - Keyword in conclusion (last H2 section)
    - Secondary keywords coverage
    - Keywords in subheadings (H2/H3)
    """

    def __init__(self, primary_keyword: str, secondary_keywords: Optional[list[str]] = None):
        self.morph = pymorphy3.MorphAnalyzer()
        self.primary_keyword = primary_keyword
        self.secondary_keywords = secondary_keywords or []

        # Pre-compute lemmas for keywords
        self.primary_lemmas = self._lemmatize_phrase(primary_keyword)
        self.secondary_lemmas = {
            kw: self._lemmatize_phrase(kw) for kw in self.secondary_keywords
        }

    def _lemmatize_word(self, word: str) -> str:
        """Lemmatize a single word to its normal form."""
        parsed = self.morph.parse(word)
        if parsed:
            return parsed[0].normal_form
        return word.lower()

    def _lemmatize_phrase(self, phrase: str) -> list[str]:
        """Lemmatize a multi-word phrase, returning list of lemmas."""
        words = re.findall(r'[а-яёa-z0-9]+', phrase.lower())
        return [self._lemmatize_word(w) for w in words if len(w) > 1]

    def _lemmatize_text(self, text: str) -> list[str]:
        """Lemmatize full text, returning list of lemmas."""
        words = re.findall(r'[а-яёa-z0-9]+', text.lower())
        return [self._lemmatize_word(w) for w in words if len(w) > 1]

    def _count_phrase_in_lemmas(self, text_lemmas: list[str], phrase_lemmas: list[str]) -> int:
        """
        Count occurrences of a phrase (as lemma sequence) in text lemmas.

        For single-word keywords, counts individual occurrences.
        For multi-word keywords, counts sequential matches.
        """
        if not phrase_lemmas:
            return 0

        if len(phrase_lemmas) == 1:
            target = phrase_lemmas[0]
            return sum(1 for lemma in text_lemmas if lemma == target)

        # Multi-word: sliding window
        count = 0
        phrase_len = len(phrase_lemmas)
        for i in range(len(text_lemmas) - phrase_len + 1):
            if text_lemmas[i:i + phrase_len] == phrase_lemmas:
                count += 1
        return count

    def _phrase_in_text(self, text: str, phrase_lemmas: list[str]) -> bool:
        """Check if a phrase (as lemmas) appears in text."""
        text_lemmas = self._lemmatize_text(text)
        return self._count_phrase_in_lemmas(text_lemmas, phrase_lemmas) > 0

    def _extract_h1(self, markdown: str) -> str:
        """Extract H1 heading text from markdown."""
        match = re.search(r'^#\s+(.+)$', markdown, re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _extract_intro(self, markdown: str) -> str:
        """Extract introduction (text before first H2)."""
        # Find first H2
        match = re.search(r'^##\s+', markdown, re.MULTILINE)
        if match:
            intro = markdown[:match.start()]
        else:
            intro = markdown[:500]

        # Remove H1 line
        intro = re.sub(r'^#\s+.+$', '', intro, count=1, flags=re.MULTILINE)
        # Remove subtitle (italic line after H1)
        intro = re.sub(r'^_[^_]+_\s*$', '', intro, count=1, flags=re.MULTILINE)
        return intro.strip()

    def _extract_conclusion(self, markdown: str) -> str:
        """Extract conclusion (last H2 section)."""
        # Find all H2 positions
        h2_positions = [m.start() for m in re.finditer(r'^##\s+', markdown, re.MULTILINE)]
        if not h2_positions:
            return ""
        last_h2_start = h2_positions[-1]
        return markdown[last_h2_start:]

    def _extract_subheadings(self, markdown: str) -> list[str]:
        """Extract all H2 and H3 headings."""
        return re.findall(r'^#{2,3}\s+(.+)$', markdown, re.MULTILINE)

    def analyze(self, markdown: str) -> SeoAnalysis:
        """
        Run full SEO analysis on markdown text.

        Returns SeoAnalysis with check results and needs_fix flag.
        """
        checks: list[SeoCheckResult] = []
        keywords_found: dict[str, int] = {}

        # Lemmatize full text once
        text_lemmas = self._lemmatize_text(markdown)
        total_words = len(text_lemmas)

        if total_words == 0:
            return SeoAnalysis(checks=[], needs_fix=False, keyword_density=0, keywords_found={})

        # --- Check 1: Keyword density ---
        primary_count = self._count_phrase_in_lemmas(text_lemmas, self.primary_lemmas)
        keywords_found[self.primary_keyword] = primary_count
        density = primary_count / total_words if total_words > 0 else 0

        if DENSITY_OK_MIN <= density <= DENSITY_OK_MAX:
            density_status = "pass"
            density_details = f"Keyword density {density:.1%} is in optimal range (1-2%)"
        elif DENSITY_MIN <= density < DENSITY_OK_MIN:
            density_status = "warning"
            density_details = f"Keyword density {density:.1%} is below optimal (target: 1-2%)"
        elif DENSITY_OK_MAX < density <= DENSITY_WARN_MAX:
            density_status = "warning"
            density_details = f"Keyword density {density:.1%} is above optimal (target: 1-2%)"
        elif density < DENSITY_MIN:
            density_status = "fail"
            density_details = f"Keyword density {density:.1%} is too low (minimum: 0.5%)"
        else:
            density_status = "fail"
            density_details = f"Keyword density {density:.1%} is too high — risk of keyword stuffing (max: 3%)"

        checks.append(SeoCheckResult(
            check="keyword_density",
            status=density_status,
            value=round(density, 4),
            threshold="0.01-0.02",
            details=density_details,
        ))

        # --- Check 2: Keyword in H1 ---
        h1_text = self._extract_h1(markdown)
        h1_has_keyword = self._phrase_in_text(h1_text, self.primary_lemmas) if h1_text else False
        checks.append(SeoCheckResult(
            check="keyword_in_h1",
            status="pass" if h1_has_keyword else "fail",
            value=h1_has_keyword,
            threshold=True,
            details=f"H1: \"{h1_text[:80]}\" — keyword {'found' if h1_has_keyword else 'NOT found'}",
        ))

        # --- Check 3: Keyword in intro ---
        intro_text = self._extract_intro(markdown)
        intro_has_keyword = self._phrase_in_text(intro_text, self.primary_lemmas) if intro_text else False
        checks.append(SeoCheckResult(
            check="keyword_in_intro",
            status="pass" if intro_has_keyword else "fail",
            value=intro_has_keyword,
            threshold=True,
            details=f"Intro ({len(intro_text.split())} words) — keyword {'found' if intro_has_keyword else 'NOT found'}",
        ))

        # --- Check 4: Keyword in conclusion ---
        conclusion_text = self._extract_conclusion(markdown)
        conclusion_has_keyword = self._phrase_in_text(conclusion_text, self.primary_lemmas) if conclusion_text else False
        checks.append(SeoCheckResult(
            check="keyword_in_conclusion",
            status="pass" if conclusion_has_keyword else "warning",
            value=conclusion_has_keyword,
            threshold=True,
            details=f"Conclusion — keyword {'found' if conclusion_has_keyword else 'not found'}",
        ))

        # --- Check 5: Secondary keywords coverage ---
        missing_secondary = []
        for kw, lemmas in self.secondary_lemmas.items():
            count = self._count_phrase_in_lemmas(text_lemmas, lemmas)
            keywords_found[kw] = count
            if count == 0:
                missing_secondary.append(kw)

        total_secondary = len(self.secondary_keywords)
        covered = total_secondary - len(missing_secondary)

        if total_secondary == 0:
            sec_status = "pass"
            sec_details = "No secondary keywords defined"
        elif not missing_secondary:
            sec_status = "pass"
            sec_details = f"All {total_secondary} secondary keywords found"
        elif len(missing_secondary) <= total_secondary * 0.3:
            sec_status = "warning"
            sec_details = f"{covered}/{total_secondary} secondary keywords found. Missing: {', '.join(missing_secondary[:5])}"
        else:
            sec_status = "fail"
            sec_details = f"Only {covered}/{total_secondary} secondary keywords found. Missing: {', '.join(missing_secondary[:5])}"

        checks.append(SeoCheckResult(
            check="secondary_keywords",
            status=sec_status,
            value=covered,
            threshold=total_secondary,
            details=sec_details,
        ))

        # --- Check 6: Keywords in subheadings ---
        subheadings = self._extract_subheadings(markdown)
        subheadings_with_keyword = 0
        for sh in subheadings:
            if self._phrase_in_text(sh, self.primary_lemmas):
                subheadings_with_keyword += 1
            else:
                # Check secondary keywords in subheadings too
                for _kw, lemmas in self.secondary_lemmas.items():
                    if self._phrase_in_text(sh, lemmas):
                        subheadings_with_keyword += 1
                        break

        total_subheadings = len(subheadings)
        if total_subheadings == 0:
            sh_status = "warning"
            sh_details = "No subheadings found"
        elif subheadings_with_keyword >= 1:
            sh_status = "pass"
            sh_details = f"{subheadings_with_keyword}/{total_subheadings} subheadings contain keywords"
        else:
            sh_status = "warning"
            sh_details = f"No subheadings contain keywords (0/{total_subheadings})"

        checks.append(SeoCheckResult(
            check="keywords_in_subheadings",
            status=sh_status,
            value=subheadings_with_keyword,
            threshold=1,
            details=sh_details,
        ))

        # Determine if fixes are needed (any "fail" check)
        needs_fix = any(c.status == "fail" for c in checks)

        return SeoAnalysis(
            checks=checks,
            needs_fix=needs_fix,
            keyword_density=round(density, 4),
            keywords_found=keywords_found,
        )
