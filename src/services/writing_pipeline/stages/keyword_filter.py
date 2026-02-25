"""
KeywordFilter — rule-based + fuzzy dedup + optional LLM filtering.

Inserted between keyword collection and clustering to remove junk,
deduplicate near-identical keywords, and optionally filter by relevance via LLM.

Usage:
    filt = KeywordFilter(client=anthropic_client, model="claude-sonnet-4-20250514")
    cleaned = filt.filter(
        keywords={"seo аудит", "аудит seo", "скачать бесплатно seo"},
        topic="SEO-аудит сайта",
        language="ru",
        volume_map={"seo аудит": 1200, "аудит seo": 800},
        use_llm=True,
    )
"""

import logging
import re
import json
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)


# Navigational / junk patterns (compiled once)
_DOMAIN_RE = re.compile(r"\b[a-zа-яё0-9-]+\.(ru|com|org|net|io|рф|su|pro|info|biz|me|tv|cc)\b", re.I)
_URL_RE = re.compile(r"https?://|www\.", re.I)
_NO_LETTERS_RE = re.compile(r"^[^a-zа-яёa-z]+$", re.I)

# Navigational stop-phrases per language
_NAV_STOPWORDS_RU = {
    "войти", "вход", "авторизация", "скачать бесплатно", "скачать торрент",
    "торрент", "бесплатно без регистрации", "логин", "пароль",
    "регистрация", "личный кабинет", "официальный сайт",
}
_NAV_STOPWORDS_EN = {
    "login", "sign in", "sign up", "download free", "torrent",
    "free download", "crack", "serial key", "keygen", "official site",
}

# Cyrillic / Latin detectors
_HAS_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
_HAS_LATIN = re.compile(r"[a-zA-Z]")


class KeywordFilter:
    """Two-level keyword filter: rules → fuzzy dedup → optional LLM relevance."""

    def __init__(
        self,
        client: Optional[anthropic.Anthropic] = None,
        model: str = "claude-sonnet-4-20250514",
    ):
        self.client = client
        self.model = model

    def filter(
        self,
        keywords: set[str] | list[str],
        topic: str,
        language: str = "ru",
        volume_map: Optional[dict[str, int]] = None,
        use_llm: bool = True,
        llm_threshold: int = 50,
        niche_context: Optional[dict] = None,
    ) -> list[str]:
        """
        Full filter pipeline: rules → dedup → optional LLM.

        Args:
            keywords: raw keyword set/list
            topic: article topic (for LLM relevance context)
            language: "ru" or "en" (for wrong-language detection)
            volume_map: {keyword_lower: volume} for dedup preference
            use_llm: whether to use LLM filtering
            llm_threshold: min keyword count to trigger LLM filter

        Returns:
            Filtered list of keywords (order: by volume desc, then alpha)
        """
        raw_count = len(keywords)
        kw_list = list(keywords)

        # Level 1: Rule-based filter
        kw_list = self._rule_filter(kw_list, language)
        after_rules = len(kw_list)

        # Level 2: Fuzzy dedup
        kw_list = self._fuzzy_dedup(kw_list, volume_map or {})
        after_dedup = len(kw_list)

        # Level 3: LLM relevance filter (optional)
        after_llm = after_dedup
        if use_llm and self.client and len(kw_list) > llm_threshold:
            kw_list = self._llm_filter(kw_list, topic, language, niche_context)
            after_llm = len(kw_list)

        logger.info(
            f"KeywordFilter: {raw_count} → rules:{after_rules} → dedup:{after_dedup} → llm:{after_llm}"
        )

        return kw_list

    # ── Level 1: Rule-based ──────────────────────────────────────────

    def _rule_filter(self, keywords: list[str], language: str) -> list[str]:
        """Apply cheap rule-based filters."""
        result = []
        nav_stopwords = _NAV_STOPWORDS_RU if language == "ru" else _NAV_STOPWORDS_EN

        for kw in keywords:
            kw_stripped = kw.strip()

            # Too short or too long
            if len(kw_stripped) < 3 or len(kw_stripped) > 100:
                continue

            # Contains URL
            if _URL_RE.search(kw_stripped):
                continue

            # No letters at all (pure numbers/symbols)
            if _NO_LETTERS_RE.match(kw_stripped):
                continue

            # Contains domain-like pattern
            if _DOMAIN_RE.search(kw_stripped):
                continue

            # Navigational stopwords (substring match)
            kw_lower = kw_stripped.lower()
            if any(sw in kw_lower for sw in nav_stopwords):
                continue

            # Wrong language filter:
            # For RU topics: skip keywords that are ONLY Latin (no Cyrillic)
            # For EN topics: skip keywords that are ONLY Cyrillic (no Latin)
            if language == "ru":
                # Allow mixed, skip pure-Latin keywords (unless very short — could be abbreviation)
                if _HAS_LATIN.search(kw_stripped) and not _HAS_CYRILLIC.search(kw_stripped):
                    # Exception: short abbreviations like "SEO", "SEM", "CRM" — allow up to 5 chars
                    words = kw_stripped.split()
                    if not all(len(w) <= 5 for w in words):
                        continue
            elif language == "en":
                if _HAS_CYRILLIC.search(kw_stripped) and not _HAS_LATIN.search(kw_stripped):
                    continue

            result.append(kw_stripped)

        return result

    # ── Level 2: Fuzzy dedup ─────────────────────────────────────────

    @staticmethod
    def _normalize_key(kw: str) -> str:
        """Normalize keyword for dedup: lowercase, remove hyphens, sort words."""
        s = kw.lower().strip()
        s = s.replace("-", " ").replace("–", " ").replace("—", " ")
        s = re.sub(r"\s+", " ", s).strip()
        words = sorted(s.split())
        return " ".join(words)

    def _fuzzy_dedup(self, keywords: list[str], volume_map: dict[str, int]) -> list[str]:
        """
        Remove near-duplicates in two passes:
        1. Exact normalized form match (sort-word dedup)
        2. Token overlap >80% between remaining keywords (merge into longer variant)
        """
        if not keywords:
            return keywords

        # Pass 1: Group by normalized form, keep highest-volume variant
        norm_groups: dict[str, list[str]] = {}
        for kw in keywords:
            norm = self._normalize_key(kw)
            norm_groups.setdefault(norm, []).append(kw)

        # Pick best from each group
        pass1 = []
        for norm, variants in norm_groups.items():
            # Sort by volume descending, then by length descending (prefer longer)
            best = max(
                variants,
                key=lambda v: (volume_map.get(v.lower().strip(), 0), len(v)),
            )
            pass1.append(best)

        # Pass 2: Token overlap >80% — merge similar keywords
        if len(pass1) <= 1:
            return pass1

        # Build token sets
        token_sets = []
        for kw in pass1:
            tokens = set(kw.lower().replace("-", " ").split())
            token_sets.append(tokens)

        merged = [False] * len(pass1)
        result = []

        for i in range(len(pass1)):
            if merged[i]:
                continue

            best_idx = i
            best_vol = volume_map.get(pass1[i].lower().strip(), 0)
            best_len = len(pass1[i])

            for j in range(i + 1, len(pass1)):
                if merged[j]:
                    continue

                # Token overlap
                t_i = token_sets[i]
                t_j = token_sets[j]
                if not t_i or not t_j:
                    continue

                overlap = len(t_i & t_j)
                min_len = min(len(t_i), len(t_j))
                if min_len == 0:
                    continue

                if overlap / min_len > 0.80:
                    merged[j] = True
                    # Keep the one with higher volume, or longer
                    vol_j = volume_map.get(pass1[j].lower().strip(), 0)
                    if vol_j > best_vol or (vol_j == best_vol and len(pass1[j]) > best_len):
                        best_idx = j
                        best_vol = vol_j
                        best_len = len(pass1[j])

            result.append(pass1[best_idx])

        return result

    # ── Level 3: LLM batch filter ────────────────────────────────────

    def _llm_filter(self, keywords: list[str], topic: str, language: str, niche_context: Optional[dict] = None) -> list[str]:
        """
        Send numbered list to LLM, get back indices of relevant keywords.

        Cost: ~200 input tokens + ~50 output tokens ≈ $0.001
        """
        import os
        prompt_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "prompts",
        )
        prompt_path = os.path.join(prompt_dir, "keyword_relevance_filter_v1.txt")

        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read()
        except FileNotFoundError:
            logger.warning("keyword_relevance_filter_v1.txt not found, skipping LLM filter")
            return keywords

        # Build niche context block for prompt
        niche_block = ""
        if niche_context:
            parts = [f"## Контекст ниши: {niche_context.get('site_name', '')}"]
            if niche_context.get("include"):
                parts.append(f"Ниша включает: {', '.join(niche_context['include'])}")
            if niche_context.get("exclude"):
                parts.append(f"НЕ включает: {', '.join(niche_context['exclude'])}")
            if niche_context.get("target_audience"):
                parts.append(f"ЦА: {niche_context['target_audience']}")
            parts.append("ВАЖНО: Оставляй ТОЛЬКО ключевые слова, релевантные этой нише.")
            niche_block = "\n".join(parts)

        # Build numbered list
        numbered = "\n".join(f"{i+1}. {kw}" for i, kw in enumerate(keywords))

        prompt = template.replace("{{topic}}", topic)
        prompt = prompt.replace("{{language}}", language)
        prompt = prompt.replace("{{niche_context}}", niche_block)
        prompt = prompt.replace("{{numbered_keywords}}", numbered)

        try:
            import time

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    chunks = []
                    with self.client.messages.stream(
                        model=self.model,
                        max_tokens=512,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                    ) as stream:
                        for text in stream.text_stream:
                            chunks.append(text)

                    response_text = "".join(chunks)
                    break
                except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                    is_retryable = isinstance(e, anthropic.APIConnectionError) or (
                        hasattr(e, 'status_code') and e.status_code in (429, 500, 502, 503, 529)
                    ) or 'overloaded' in str(e).lower()
                    if is_retryable and attempt < max_retries - 1:
                        wait = 2 ** (attempt + 1)
                        logger.warning(f"LLM filter retry {attempt+1}: {e}. Waiting {wait}s...")
                        time.sleep(wait)
                        continue
                    raise

            # Parse response: expect comma-separated numbers like "1, 3, 5, 7"
            # or JSON array [1, 3, 5, 7]
            response_text = response_text.strip()

            # Try JSON array first
            try:
                indices = json.loads(response_text)
                if isinstance(indices, list):
                    pass  # good
                else:
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                # Parse comma/space separated numbers
                indices = [int(x) for x in re.findall(r"\d+", response_text)]

            # Convert 1-based indices to 0-based, filter valid range
            filtered = []
            seen = set()
            for idx in indices:
                real_idx = idx - 1  # 1-based → 0-based
                if 0 <= real_idx < len(keywords) and real_idx not in seen:
                    filtered.append(keywords[real_idx])
                    seen.add(real_idx)

            if len(filtered) < 3:
                logger.warning(f"LLM filter returned only {len(filtered)} keywords, keeping all")
                return keywords

            logger.info(f"LLM filter: kept {len(filtered)}/{len(keywords)} keywords")
            return filtered

        except Exception as e:
            logger.warning(f"LLM keyword filter failed: {e}, keeping all keywords")
            return keywords
