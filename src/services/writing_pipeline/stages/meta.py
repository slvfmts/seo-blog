"""
Meta Stage - Generates SEO metadata and Schema.org JSON-LD from finished article.
"""

import json
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import MetaResult


class MetaStage(WritingStage):
    """
    Stage 6: Meta

    Generates optimized SEO metadata from the finished article:
    - meta_title (<=60 chars, keyword near start)
    - meta_description (<=160 chars, keyword + CTA)
    - slug (lowercase, hyphens, 3-5 words)
    - Schema.org JSON-LD (BlogPosting, FAQPage, HowTo)
    """

    @property
    def name(self) -> str:
        return "meta"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute meta generation stage."""
        log = context.start_stage(self.name)

        try:
            if context.edited_md is None:
                raise ValueError("Editing stage must be completed before meta generation")
            if context.intent is None:
                raise ValueError("Intent stage must be completed before meta generation")
            if context.outline is None:
                raise ValueError("Structure stage must be completed before meta generation")

            # Step 1: LLM-generated meta (title, description, slug)
            prompt_template = self._load_prompt("meta_v1")

            # Use brief's target_keyword when available (matches SEO lint validation)
            brief = context.config.get("brief")
            if brief:
                brief_data = brief if isinstance(brief, dict) else brief.to_dict()
                target_terms = brief_data.get("target_terms", [])
                target_keyword = target_terms[0] if target_terms else context.intent.topic
            else:
                target_keyword = context.intent.topic

            prompt = prompt_template.replace("{{topic}}", context.intent.topic)
            prompt = prompt.replace("{{primary_intent}}", context.intent.primary_intent)
            prompt = prompt.replace("{{audience_role}}", context.intent.audience.role)
            prompt = prompt.replace("{{article_title}}", context.outline.title)
            prompt = prompt.replace("{{article_md}}", context.edited_md)
            prompt = prompt.replace("{{target_keyword}}", target_keyword)

            response_text, in_t, out_t = self._call_llm(
                prompt,
                max_tokens=1024,
                temperature=0.4,
            )

            data = self._parse_json_response(response_text)
            meta = MetaResult.from_dict(data)

            # Validate and truncate if needed
            if len(meta.meta_title) > 60:
                meta.meta_title = meta.meta_title[:57] + "..."
            if len(meta.meta_description) > 160:
                meta.meta_description = meta.meta_description[:157] + "..."

            # Step 2: Build Schema.org JSON-LD (programmatic, no LLM)
            schema_json_ld = self._build_schema_jsonld(context, meta)
            meta.schema_json_ld = schema_json_ld

            # Inject schema into edited_md
            if schema_json_ld:
                context.edited_md = context.edited_md.rstrip() + "\n\n" + schema_json_ld + "\n"

            # Store in context
            context.meta = meta

            # Save intermediate result
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "08_meta.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(meta.to_dict(), f, ensure_ascii=False, indent=2)

            context.complete_stage(
                input_tokens=in_t,
                output_tokens=out_t,
                metadata={
                    "meta_title_len": len(meta.meta_title),
                    "meta_description_len": len(meta.meta_description),
                    "slug": meta.slug,
                    "has_schema": schema_json_ld is not None,
                }
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    def _build_schema_jsonld(self, context: WritingContext, meta: MetaResult) -> Optional[str]:
        """
        Build Schema.org JSON-LD markup programmatically from article data.

        Generates:
        - BlogPosting (always)
        - FAQPage (if outline has faq content blocks)
        - HowTo (if content_type is how-to and outline has steps blocks)

        Returns:
            JSON-LD script tag string, or None if building fails.
        """
        try:
            schemas = []

            # Determine language
            region = context.region.lower()
            lang = "ru" if region in ["ru", "россия", "russia"] else "en"
            today = datetime.now().strftime("%Y-%m-%d")

            # 1. BlogPosting (always)
            blog_posting = {
                "@context": "https://schema.org",
                "@type": "BlogPosting",
                "headline": meta.meta_title,
                "description": meta.meta_description,
                "datePublished": today,
                "dateModified": today,
                "wordCount": len(context.edited_md.split()),
                "inLanguage": lang,
            }
            schemas.append(blog_posting)

            # 2. FAQPage (if outline has faq blocks)
            faq_pairs = self._extract_faq_pairs(context.edited_md)
            if faq_pairs:
                faq_schema = {
                    "@context": "https://schema.org",
                    "@type": "FAQPage",
                    "mainEntity": [
                        {
                            "@type": "Question",
                            "name": q,
                            "acceptedAnswer": {
                                "@type": "Answer",
                                "text": a,
                            }
                        }
                        for q, a in faq_pairs
                    ]
                }
                schemas.append(faq_schema)

            # 3. HowTo (if content_type is how-to and has steps)
            if context.intent and context.intent.content_type == "how-to":
                steps = self._extract_howto_steps(context.edited_md)
                if steps:
                    howto_schema = {
                        "@context": "https://schema.org",
                        "@type": "HowTo",
                        "name": context.outline.title if context.outline else meta.meta_title,
                        "description": meta.meta_description,
                        "step": [
                            {
                                "@type": "HowToStep",
                                "name": step_name,
                                "text": step_text,
                            }
                            for step_name, step_text in steps
                        ]
                    }
                    schemas.append(howto_schema)

            # Combine into a single script tag
            if len(schemas) == 1:
                json_ld = json.dumps(schemas[0], ensure_ascii=False, indent=2)
            else:
                json_ld = json.dumps(schemas, ensure_ascii=False, indent=2)

            return f'<script type="application/ld+json">\n{json_ld}\n</script>'

        except Exception:
            return None

    def _extract_faq_pairs(self, markdown: str) -> List[tuple]:
        """
        Extract FAQ question-answer pairs from markdown.

        Looks for patterns like:
        **Question text?**
        Answer text.

        Returns list of (question, answer) tuples.
        """
        pairs = []
        # Pattern: bold text ending with ? followed by non-bold answer
        pattern = re.compile(
            r'\*\*(.+?\?)\*\*\s*\n\s*(.+?)(?=\n\s*\*\*|\n\s*#{1,3}\s|\n\s*\n\s*\n|\Z)',
            re.DOTALL,
        )

        for match in pattern.finditer(markdown):
            question = match.group(1).strip()
            answer = match.group(2).strip()
            # Clean up answer: take first paragraph, limit length
            answer_lines = answer.split("\n\n")[0].strip()
            if answer_lines and len(answer_lines) > 10:
                pairs.append((question, answer_lines))

        return pairs[:10]  # Limit to 10 FAQ items

    def _extract_howto_steps(self, markdown: str) -> List[tuple]:
        """
        Extract numbered steps from markdown.

        Looks for patterns like:
        1. Step name
        Step description text...

        Returns list of (step_name, step_text) tuples.
        """
        steps = []
        # Pattern: numbered list items
        pattern = re.compile(
            r'^\d+\.\s+\*{0,2}(.+?)\*{0,2}\s*$',
            re.MULTILINE,
        )

        matches = list(pattern.finditer(markdown))
        for i, match in enumerate(matches):
            step_name = match.group(1).strip()
            # Get text between this step and the next (or end)
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else start + 500
            step_text = markdown[start:end].strip()
            # Take first paragraph of step text
            step_text = step_text.split("\n\n")[0].strip() if step_text else step_name
            if not step_text:
                step_text = step_name
            steps.append((step_name, step_text))

        return steps[:15]  # Limit to 15 steps
