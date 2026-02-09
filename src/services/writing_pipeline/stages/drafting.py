"""
Drafting Stage - Writes article text following the outline.
"""

import json
import re
import logging

from ..core.stage import WritingStage
from ..core.context import WritingContext

logger = logging.getLogger(__name__)


class DraftingStage(WritingStage):
    """
    Stage 4: Drafting

    Writes the article text strictly following:
    - Outline structure (H2/H3, content blocks)
    - Word count targets
    - Research pack facts (no new facts added)
    - Tone from intent spec
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
            prompt_template = self._load_prompt("drafting_v1")

            intent_json = json.dumps(context.intent.to_dict(), ensure_ascii=False, indent=2)
            outline_json = json.dumps(context.outline.to_dict(), ensure_ascii=False, indent=2)
            research_json = json.dumps(context.research.to_dict(), ensure_ascii=False, indent=2)

            # Format existing posts for internal linking
            if context.existing_posts:
                posts_for_prompt = [
                    {"title": p["title"], "url": p["url"]}
                    for p in context.existing_posts
                ]
                existing_posts_json = json.dumps(posts_for_prompt, ensure_ascii=False, indent=2)
            else:
                existing_posts_json = "[]"

            prompt = prompt_template.replace("{{intent_spec_json}}", intent_json)
            prompt = prompt.replace("{{outline_json}}", outline_json)
            prompt = prompt.replace("{{research_pack_json}}", research_json)
            prompt = prompt.replace("{{existing_posts}}", existing_posts_json)

            # Call LLM with high token limit for full article
            response_text, tokens = self._call_llm(
                prompt,
                max_tokens=16000,
                temperature=0.7,
            )

            # Draft is markdown text, not JSON
            draft_md = response_text.strip()

            # Validate internal links — remove hallucinated URLs
            draft_md = self._validate_internal_links(draft_md, context.existing_posts)

            context.draft_md = draft_md

            # Calculate word count
            word_count = len(context.draft_md.split())

            # Save intermediate result if configured
            if context.save_intermediate and context.output_dir:
                import os
                output_path = os.path.join(context.output_dir, "06_draft.md")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(context.draft_md)

            context.complete_stage(
                tokens_used=tokens,
                metadata={
                    "word_count": word_count,
                    "target_words": context.outline.target_total_words,
                }
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    def _validate_internal_links(self, draft_md: str, existing_posts: list) -> str:
        """Remove hallucinated internal links not in existing_posts list.

        If existing_posts is empty, all non-external links are stripped
        (anchor text preserved). If existing_posts has entries, only links
        whose URL matches a known post URL are kept.
        """
        if not existing_posts:
            # No posts exist — any internal-looking link is hallucinated.
            # We can't know the site domain, so remove ALL markdown links
            # that are NOT clearly external (http(s) to well-known domains).
            # Simpler: just strip every markdown link, keep anchor text.
            removed = 0

            def strip_all(match):
                nonlocal removed
                removed += 1
                return match.group(1)

            result = re.sub(r'\[([^\]]+)\]\([^)]+\)', strip_all, draft_md)
            if removed:
                logger.info(f"Internal link validation: removed {removed} links (no existing posts)")
            return result

        # Build set of valid URLs
        valid_urls = set()
        for p in existing_posts:
            url = p.get("url", "")
            if url:
                valid_urls.add(url)
                # Also accept without trailing slash and vice versa
                valid_urls.add(url.rstrip("/"))
                valid_urls.add(url.rstrip("/") + "/")

        kept = 0
        removed = 0

        def check_link(match):
            nonlocal kept, removed
            anchor = match.group(1)
            url = match.group(2)

            # Keep external links (not matching any known post)
            # Heuristic: if URL starts with http and is not in valid_urls,
            # check if it looks like it could be an internal link
            normalized = url.rstrip("/")
            if normalized in valid_urls or (normalized + "/") in valid_urls:
                kept += 1
                return match.group(0)  # keep valid link

            # Check if it's clearly external (different domain from posts)
            # Extract domain from valid_urls to determine our site domain
            site_domains = set()
            for v in valid_urls:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(v)
                    if parsed.netloc:
                        site_domains.add(parsed.netloc)
                except Exception:
                    pass

            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                if parsed.netloc and parsed.netloc not in site_domains:
                    # External link — keep it
                    kept += 1
                    return match.group(0)
            except Exception:
                pass

            # Internal-looking link not in valid_urls — hallucinated
            removed += 1
            return anchor

        result = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', check_link, draft_md)
        if removed:
            logger.warning(f"Internal link validation: removed {removed} hallucinated links, kept {kept}")
        return result
