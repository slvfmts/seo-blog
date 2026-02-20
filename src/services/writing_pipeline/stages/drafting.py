"""
Drafting Stage - Writes article text following the outline.
"""

import json
import logging

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import DraftMeta

logger = logging.getLogger(__name__)


class DraftingStage(WritingStage):
    """
    Stage 4: Drafting (v3)

    Writes the article text strictly following:
    - Outline structure (H2/H3, content blocks)
    - Word count targets
    - Research pack facts (no new facts added)
    - Tone from intent spec
    - Claim gating from claim_bank (v3)
    - Unique angle enforcement (v3)
    - Anti-keyword-bag via terminology_canon (v3)
    """

    @property
    def name(self) -> str:
        return "drafting"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute drafting stage."""
        log = context.start_stage(self.name)

        try:
            if context.intent is None:
                raise ValueError("Intent stage must be completed before drafting")
            if context.research is None:
                raise ValueError("Research stage must be completed before drafting")
            if context.outline is None:
                raise ValueError("Structure stage must be completed before drafting")

            # Load and fill prompt template
            prompt_template = self._load_prompt("drafting_v3")

            intent_json = json.dumps(context.intent.to_dict(), ensure_ascii=False, indent=2)
            outline_json = json.dumps(context.outline.to_dict(), ensure_ascii=False, indent=2)
            research_json = json.dumps(context.research.to_dict(), ensure_ascii=False, indent=2)

            prompt = prompt_template.replace("{{intent_spec_json}}", intent_json)
            prompt = prompt.replace("{{outline_json}}", outline_json)
            prompt = prompt.replace("{{research_pack_json}}", research_json)

            # Serialize v3 fields from research pack
            research = context.research

            if research.unique_angle:
                prompt = prompt.replace("{{unique_angle_json}}", json.dumps(
                    research.unique_angle.__dict__ if hasattr(research.unique_angle, '__dict__') else {},
                    ensure_ascii=False, indent=2,
                ))
            else:
                prompt = prompt.replace("{{unique_angle_json}}", "null")

            if research.claim_bank:
                cb_dict = context.research.to_dict().get("claim_bank", {})
                prompt = prompt.replace("{{claim_bank_json}}", json.dumps(
                    cb_dict, ensure_ascii=False, indent=2,
                ))
            else:
                prompt = prompt.replace("{{claim_bank_json}}", "null")

            if research.cluster_overlap_map:
                overlap_dict = context.research.to_dict().get("cluster_overlap_map", [])
                prompt = prompt.replace("{{cluster_overlap_map_json}}", json.dumps(
                    overlap_dict, ensure_ascii=False, indent=2,
                ))
            else:
                prompt = prompt.replace("{{cluster_overlap_map_json}}", "null")

            if research.example_snippets:
                snippets_dict = context.research.to_dict().get("example_snippets", [])
                prompt = prompt.replace("{{example_snippets_json}}", json.dumps(
                    snippets_dict, ensure_ascii=False, indent=2,
                ))
            else:
                prompt = prompt.replace("{{example_snippets_json}}", "null")

            if research.terminology_canon:
                tc_dict = context.research.to_dict().get("terminology_canon", {})
                prompt = prompt.replace("{{terminology_canon_json}}", json.dumps(
                    tc_dict, ensure_ascii=False, indent=2,
                ))
            else:
                prompt = prompt.replace("{{terminology_canon_json}}", "null")

            # Call LLM with high token limit for full article
            response_text, tokens = self._call_llm(
                prompt,
                max_tokens=16000,
                temperature=0.7,
            )

            # Parse response: markdown + optional DRAFT_META
            draft_md, draft_meta = self._parse_draft_response(response_text)

            context.draft_md = draft_md
            context.draft_meta = draft_meta

            # Calculate word count
            word_count = len(context.draft_md.split())

            # Save intermediate result if configured
            if context.save_intermediate and context.output_dir:
                import os
                output_path = os.path.join(context.output_dir, "06_draft.md")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(context.draft_md)

                # Save draft_meta if present
                if draft_meta:
                    meta_path = os.path.join(context.output_dir, "06b_draft_meta.json")
                    with open(meta_path, "w", encoding="utf-8") as f:
                        json.dump(draft_meta.to_dict(), f, ensure_ascii=False, indent=2)

            context.complete_stage(
                tokens_used=tokens,
                metadata={
                    "word_count": word_count,
                    "target_words": context.outline.target_total_words,
                    "has_draft_meta": draft_meta is not None,
                    "used_claims": len(draft_meta.used_allowed_claims) if draft_meta else 0,
                    "softened_claims": draft_meta.softened_claims_count if draft_meta else 0,
                }
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    def _parse_draft_response(self, response_text: str) -> tuple[str, DraftMeta | None]:
        """
        Parse LLM response into markdown draft and optional DRAFT_META.

        Response format:
            <markdown article text>
            ---DRAFT_META---
            { JSON }
        """
        response_text = response_text.strip()

        separator = "---DRAFT_META---"
        if separator in response_text:
            parts = response_text.split(separator, 1)
            draft_md = parts[0].strip()
            meta_json_str = parts[1].strip()

            try:
                meta_data = json.loads(meta_json_str)
                draft_meta = DraftMeta.from_dict(meta_data)
                logger.info(
                    f"Parsed DRAFT_META: {len(draft_meta.used_allowed_claims)} claims used, "
                    f"{draft_meta.softened_claims_count} softened, "
                    f"{len(draft_meta.overlap_compressions)} compressions"
                )
                return draft_md, draft_meta
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to parse DRAFT_META: {e}")
                return draft_md, None
        else:
            logger.info("No DRAFT_META separator found in response, draft only")
            return response_text, None
