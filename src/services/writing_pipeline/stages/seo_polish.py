"""
SEO Polish Stage - Hybrid programmatic analysis + conditional LLM fixes.

1. Extracts keywords from pipeline context (research.keyword_clusters)
2. Runs SeoAnalyzer (pymorphy3-based) to check keyword placement
3. If all checks pass → skips LLM, 0 tokens
4. If issues found → calls LLM for minimal targeted fixes
5. Re-analyzes to verify improvements
"""

import json
import os
import logging

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import SeoPolishResult
from .seo_analyzer import SeoAnalyzer

logger = logging.getLogger(__name__)


class SeoPolishStage(WritingStage):
    """
    Stage 7: SEO Polish (between Linking and Meta)

    Hybrid approach:
    - Layer 1: pymorphy3 lemmatization
    - Layer 2: Programmatic SEO analysis (free, instant)
    - Layer 3: LLM fixes only when needed (conditional)
    """

    @property
    def name(self) -> str:
        return "seo_polish"

    def _extract_keywords(self, context: WritingContext) -> tuple[str, list[str]]:
        """
        Extract primary and secondary keywords from pipeline context.

        Returns (primary_keyword, secondary_keywords).
        """
        primary = ""
        secondary = []

        # Try keyword_clusters from research
        if (
            context.research
            and context.research.keyword_clusters
        ):
            clusters = context.research.keyword_clusters
            primary = clusters.primary_cluster.primary_keyword
            for cluster in clusters.secondary_clusters:
                secondary.append(cluster.primary_keyword)
                # Also add individual keywords from secondary clusters (top 3 each)
                for kw in cluster.keywords[:3]:
                    if kw != cluster.primary_keyword and kw not in secondary:
                        secondary.append(kw)

        # Fallback: use topic as primary keyword
        if not primary and context.intent:
            primary = context.intent.topic

        if not primary:
            primary = context.topic

        # Limit secondary keywords to avoid noise
        secondary = secondary[:10]

        return primary, secondary

    def _format_issues(self, analysis) -> str:
        """Format failed/warning checks as a numbered list for LLM prompt."""
        lines = []
        for i, check in enumerate(analysis.checks, 1):
            if check.status in ("fail", "warning"):
                lines.append(f"{i}. [{check.check}] ({check.status}): {check.details}")
        return "\n".join(lines)

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute SEO Polish stage."""
        log = context.start_stage(self.name)

        try:
            if not context.edited_md:
                logger.info("SEO Polish skipped: no edited content")
                context.complete_stage(tokens_used=0, metadata={"skipped": True, "reason": "no_content"})
                return context

            # 1. Extract keywords from context
            primary_keyword, secondary_keywords = self._extract_keywords(context)
            logger.info(f"SEO Polish: primary='{primary_keyword}', secondary={secondary_keywords[:5]}")

            if not primary_keyword:
                logger.info("SEO Polish skipped: no keywords found")
                context.complete_stage(tokens_used=0, metadata={"skipped": True, "reason": "no_keywords"})
                return context

            # 2. Run programmatic analysis
            analyzer = SeoAnalyzer(primary_keyword, secondary_keywords)
            analysis_before = analyzer.analyze(context.edited_md)

            logger.info(
                f"SEO analysis: density={analysis_before.keyword_density:.1%}, "
                f"needs_fix={analysis_before.needs_fix}, "
                f"checks: {', '.join(f'{c.check}={c.status}' for c in analysis_before.checks)}"
            )

            # 3. If no fixes needed → skip LLM
            if not analysis_before.needs_fix:
                logger.info("SEO Polish: all checks passed, skipping LLM")

                result = SeoPolishResult(
                    analysis_before=analysis_before,
                    analysis_after=None,
                    llm_called=False,
                    changes_made=[],
                    tokens_used=0,
                )

                self._save_intermediate(context, result)
                context.complete_stage(
                    tokens_used=0,
                    metadata={
                        "llm_called": False,
                        "keyword_density": analysis_before.keyword_density,
                        "checks_passed": len([c for c in analysis_before.checks if c.status == "pass"]),
                        "checks_total": len(analysis_before.checks),
                    },
                )
                return context

            # 4. LLM call for fixes
            logger.info(f"SEO Polish: {len(analysis_before.failed_checks)} failed checks, calling LLM")

            prompt_template = self._load_prompt("seo_polish_v1")
            prompt = prompt_template.replace("{{primary_keyword}}", primary_keyword)
            prompt = prompt.replace("{{secondary_keywords}}", ", ".join(secondary_keywords) if secondary_keywords else "(none)")
            prompt = prompt.replace("{{issues}}", self._format_issues(analysis_before))
            prompt = prompt.replace("{{article_md}}", context.edited_md)

            response_text, in_t, out_t = self._call_llm(
                prompt,
                max_tokens=16000,
                temperature=0.2,
            )
            tokens = in_t + out_t

            polished_md = response_text.strip()

            # 5. Re-analyze to verify improvements
            analysis_after = analyzer.analyze(polished_md)

            # Only apply changes if analysis improved or stayed the same
            before_fails = len(analysis_before.failed_checks)
            after_fails = len(analysis_after.failed_checks)

            if after_fails <= before_fails:
                context.edited_md = polished_md
                changes_made = [
                    f"Fixed: {c.check} ({c.details})"
                    for c in analysis_before.failed_checks
                    if not any(
                        ac.check == c.check and ac.status == "fail"
                        for ac in analysis_after.checks
                    )
                ]
            else:
                # LLM made things worse — keep original
                logger.warning("SEO Polish: LLM worsened the text, keeping original")
                analysis_after = analysis_before
                changes_made = ["LLM changes rejected — original kept"]

            result = SeoPolishResult(
                analysis_before=analysis_before,
                analysis_after=analysis_after,
                llm_called=True,
                changes_made=changes_made,
                tokens_used=tokens,
            )

            self._save_intermediate(context, result)
            context.complete_stage(
                input_tokens=in_t,
                output_tokens=out_t,
                metadata={
                    "llm_called": True,
                    "keyword_density_before": analysis_before.keyword_density,
                    "keyword_density_after": analysis_after.keyword_density,
                    "fails_before": before_fails,
                    "fails_after": after_fails,
                    "changes_made": len(changes_made),
                },
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    def _save_intermediate(self, context: WritingContext, result: SeoPolishResult):
        """Save intermediate result to file."""
        if context.save_intermediate and context.output_dir:
            output_path = os.path.join(context.output_dir, "07d_seo_polish.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
