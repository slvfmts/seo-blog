"""
Quality Gate Stage - Final verification of claims, redundancy, and internal links.
"""

import json
import os
import logging

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import QualityGateResult

logger = logging.getLogger(__name__)


class QualityGateStage(WritingStage):
    """
    Stage 8: Quality Gate (between SEO Polish and Meta)

    Three checks:
    1. Weirdness & redundancy cleanup
    2. Claim compliance against claim_bank
    3. Internal links finalization (3-7 links)
    """

    @property
    def name(self) -> str:
        return "quality_gate"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute quality gate stage."""
        log = context.start_stage(self.name)

        try:
            if not context.edited_md:
                logger.info("Quality Gate skipped: no edited content")
                context.complete_stage(tokens_used=0, metadata={"skipped": True})
                return context

            # Load prompt
            prompt_template = self._load_prompt("quality_gate_v1")

            # Serialize inputs
            prompt = prompt_template.replace("{{article_md}}", context.edited_md)

            # Claim bank
            if context.research and context.research.claim_bank:
                cb_dict = context.research.to_dict().get("claim_bank", {})
                prompt = prompt.replace("{{claim_bank_json}}", json.dumps(
                    cb_dict, ensure_ascii=False, indent=2,
                ))
            else:
                prompt = prompt.replace("{{claim_bank_json}}", "null")

            # Terminology canon
            if context.research and context.research.terminology_canon:
                tc_dict = context.research.to_dict().get("terminology_canon", {})
                prompt = prompt.replace("{{terminology_canon_json}}", json.dumps(
                    tc_dict, ensure_ascii=False, indent=2,
                ))
            else:
                prompt = prompt.replace("{{terminology_canon_json}}", "null")

            # Existing posts for internal linking
            if context.existing_posts:
                posts_summary = [
                    {"slug": p.get("slug", ""), "title": p.get("title", "")}
                    for p in context.existing_posts[:30]
                ]
                prompt = prompt.replace("{{existing_posts_json}}", json.dumps(
                    posts_summary, ensure_ascii=False, indent=2,
                ))
            else:
                prompt = prompt.replace("{{existing_posts_json}}", "[]")

            # Call LLM
            response_text, tokens = self._call_llm(
                prompt,
                max_tokens=16000,
                temperature=0.2,
            )

            # Parse response
            article_md, quality_report = self._parse_response(response_text)

            if article_md:
                context.edited_md = article_md

            context.quality_report = quality_report

            # Save intermediate
            if context.save_intermediate and context.output_dir:
                gate_path = os.path.join(context.output_dir, "07e_quality_gate.md")
                with open(gate_path, "w", encoding="utf-8") as f:
                    f.write(context.edited_md)

                if quality_report:
                    report_path = os.path.join(context.output_dir, "07f_quality_report.json")
                    with open(report_path, "w", encoding="utf-8") as f:
                        json.dump(quality_report, f, ensure_ascii=False, indent=2)

            context.complete_stage(
                tokens_used=tokens,
                metadata={
                    "quality_score": quality_report.get("quality_score", 0) if quality_report else 0,
                    "claims_removed": len(quality_report.get("claims_removed", [])) if quality_report else 0,
                    "links_total": quality_report.get("total_links", 0) if quality_report else 0,
                    "redundancy_fixes": len(quality_report.get("redundancy_fixes", [])) if quality_report else 0,
                },
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    def _parse_response(self, response_text: str) -> tuple[str | None, dict | None]:
        """
        Parse LLM response into quality report and final markdown.

        Expected format:
            ---REPORT_JSON---
            { JSON }
            ---FINAL_MD---
            <markdown>
        """
        response_text = response_text.strip()
        quality_report = None
        article_md = None

        report_sep = "---REPORT_JSON---"
        md_sep = "---FINAL_MD---"

        if report_sep in response_text and md_sep in response_text:
            # Split into parts
            report_start = response_text.index(report_sep) + len(report_sep)
            md_start_idx = response_text.index(md_sep)

            report_str = response_text[report_start:md_start_idx].strip()
            article_md = response_text[md_start_idx + len(md_sep):].strip()

            try:
                quality_report = json.loads(report_str)
                logger.info(
                    f"Quality Gate report: score={quality_report.get('quality_score', '?')}, "
                    f"claims_removed={len(quality_report.get('claims_removed', []))}, "
                    f"links={quality_report.get('total_links', 0)}"
                )
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse REPORT_JSON: {e}")

        elif md_sep in response_text:
            # Only markdown, no report
            article_md = response_text.split(md_sep, 1)[1].strip()
            logger.info("Quality Gate: got FINAL_MD but no REPORT_JSON")

        else:
            # Try to use the whole response as markdown
            logger.warning("Quality Gate: no separators found, using full response as article")
            article_md = response_text

        return article_md, quality_report
