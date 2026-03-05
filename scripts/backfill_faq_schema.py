#!/usr/bin/env python3
"""
Backfill FAQ schema (JSON-LD) for existing articles on notes.editors.one.

Reads stage_results from DB, extracts FAQ pairs from content_md + intent,
builds BlogPosting + FAQPage + HowTo JSON-LD, and updates Ghost codeinjection_foot.

Usage:
    # Dry-run (default) — show what would be updated
    docker compose exec api python3 scripts/backfill_faq_schema.py

    # Apply to all articles
    docker compose exec api python3 scripts/backfill_faq_schema.py --apply

    # Apply to a single draft by ID
    docker compose exec api python3 scripts/backfill_faq_schema.py --apply --draft-id <uuid>
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy import create_engine, text

# Add project root to path so we can import pipeline code
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.writing_pipeline.contracts import IntentResult, MetaResult, OutlineResult
from src.services.writing_pipeline.core.context import WritingContext
from src.services.writing_pipeline.stages.meta import MetaStage
from src.services.publisher import GhostPublisher


def build_context_from_draft(draft: dict) -> WritingContext:
    """Reconstruct a minimal WritingContext from draft row + stage_results."""
    sr = draft["stage_results"] or {}
    intent_data = sr.get("intent")
    meta_data = sr.get("meta")
    structure_data = sr.get("structure")

    ctx = WritingContext(
        topic=draft["topic"] or draft["title"],
        region=draft.get("country", "ru") or "ru",
    )

    if intent_data:
        try:
            ctx.intent = IntentResult.from_dict(intent_data)
        except Exception:
            ctx.intent = None

    if structure_data:
        try:
            ctx.outline = OutlineResult.from_dict(structure_data)
        except Exception:
            ctx.outline = None

    # Use content_md as edited_md (the final article text)
    ctx.edited_md = draft["content_md"] or ""

    if meta_data:
        try:
            ctx.meta = MetaResult.from_dict(meta_data)
        except Exception:
            ctx.meta = None

    # search_results not saved in DB — source 3 (PAA) unavailable
    ctx.search_results = None

    return ctx


def get_ghost_client(blog: dict) -> GhostPublisher:
    """Create GhostPublisher from blog row."""
    return GhostPublisher(
        ghost_url=blog["ghost_url"],
        admin_key=blog["ghost_admin_key"],
    )


def fetch_ghost_post(publisher: GhostPublisher, post_id: str) -> Optional[dict]:
    """GET a Ghost post with codeinjection_foot and updated_at."""
    token = publisher._create_jwt_token()
    headers = {"Authorization": f"Ghost {token}"}

    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(
                f"{publisher.ghost_url}/ghost/api/admin/posts/{post_id}/",
                headers=headers,
                params={"fields": "id,title,slug,updated_at,published_at,codeinjection_foot"},
            )
            if resp.status_code == 200:
                return resp.json()["posts"][0]
            else:
                print(f"  ERROR: GET post {post_id} returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"  ERROR: GET post {post_id} failed: {e}")
    return None


def update_ghost_codeinjection(
    publisher: GhostPublisher,
    post_id: str,
    codeinjection_foot: str,
    updated_at: str,
) -> bool:
    """PUT only codeinjection_foot to Ghost post (no mobiledoc change)."""
    token = publisher._create_jwt_token()
    headers = {
        "Authorization": f"Ghost {token}",
        "Content-Type": "application/json",
    }

    post_data = {
        "posts": [{
            "codeinjection_foot": codeinjection_foot,
            "updated_at": updated_at,
        }]
    }

    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.put(
                f"{publisher.ghost_url}/ghost/api/admin/posts/{post_id}/",
                headers=headers,
                json=post_data,
            )
            if resp.status_code == 200:
                return True
            else:
                print(f"  ERROR: PUT post {post_id} returned {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        print(f"  ERROR: PUT post {post_id} failed: {e}")
    return False


def main():
    parser = argparse.ArgumentParser(description="Backfill FAQ schema for notes.editors.one articles")
    parser.add_argument("--apply", action="store_true", help="Actually update Ghost (default: dry-run)")
    parser.add_argument("--draft-id", type=str, help="Process a single draft by UUID")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    engine = create_engine(db_url)

    # ── Load drafts with their blog info ──
    query = text("""
        SELECT
            d.id, d.title, d.topic, d.content_md, d.stage_results,
            d.cms_post_id, d.meta_title, d.meta_description,
            s.country,
            b.ghost_url, b.ghost_admin_key
        FROM drafts d
        JOIN sites s ON d.site_id = s.id
        JOIN blogs b ON s.blog_id = b.id
        WHERE d.status = 'published'
          AND d.stage_results IS NOT NULL
          AND d.cms_post_id IS NOT NULL
          AND b.ghost_url = 'https://notes.editors.one'
    """)

    if args.draft_id:
        query = text(str(query) + " AND d.id = :draft_id")

    with engine.connect() as conn:
        if args.draft_id:
            rows = conn.execute(query, {"draft_id": args.draft_id}).mappings().all()
        else:
            rows = conn.execute(query).mappings().all()

    print(f"Found {len(rows)} published drafts on notes.editors.one\n")
    if not rows:
        return

    # ── Instantiate MetaStage with dummy client (FAQ extraction is pure logic) ──
    meta_stage = MetaStage.__new__(MetaStage)

    # ── Use first row's blog for Ghost client ──
    blog_info = {"ghost_url": rows[0]["ghost_url"], "ghost_admin_key": rows[0]["ghost_admin_key"]}
    publisher = get_ghost_client(blog_info)

    backup_path = "/tmp/faq_backfill_backup.json"
    backup = {}
    stats = {"total": 0, "with_faq": 0, "updated": 0, "skipped": 0, "errors": 0}

    for row in rows:
        draft = dict(row)
        stats["total"] += 1
        draft_id = str(draft["id"])
        title = draft["title"]
        cms_post_id = draft["cms_post_id"]

        print(f"── [{stats['total']}/{len(rows)}] {title}")
        print(f"   draft={draft_id}, ghost_post={cms_post_id}")

        try:
            if not draft["content_md"]:
                print("   SKIP: no content_md")
                stats["skipped"] += 1
                continue

            # Reconstruct context
            ctx = build_context_from_draft(draft)

            # Extract FAQ pairs (sources 1+2 only, no PAA)
            faq_pairs = meta_stage._extract_faq_pairs(ctx.edited_md, ctx)

            # Build meta for BlogPosting
            sr = draft["stage_results"] or {}
            meta_data = sr.get("meta") or {}
            meta_title = meta_data.get("meta_title") or draft["meta_title"] or title
            meta_description = meta_data.get("meta_description") or draft["meta_description"] or ""

            # Determine language
            region = (draft.get("country") or "ru").lower()
            lang = "ru" if region in ["ru", "россия", "russia"] else "en"

            # ── Build JSON-LD schemas ──
            schemas = []

            # 1. BlogPosting (always) — dates will be set from Ghost published_at
            blog_posting = {
                "@context": "https://schema.org",
                "@type": "BlogPosting",
                "headline": meta_title,
                "description": meta_description,
                "wordCount": len(ctx.edited_md.split()),
                "inLanguage": lang,
            }
            schemas.append(blog_posting)

            # 2. FAQPage (if pairs found)
            if faq_pairs:
                stats["with_faq"] += 1
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
                            },
                        }
                        for q, a in faq_pairs
                    ],
                }
                schemas.append(faq_schema)

            # 3. HowTo (if content_type is how-to)
            if ctx.intent and ctx.intent.content_type == "how-to":
                steps = meta_stage._extract_howto_steps(ctx.edited_md)
                if steps:
                    howto_schema = {
                        "@context": "https://schema.org",
                        "@type": "HowTo",
                        "name": ctx.outline.title if ctx.outline else meta_title,
                        "description": meta_description,
                        "step": [
                            {"@type": "HowToStep", "name": sn, "text": st}
                            for sn, st in steps
                        ],
                    }
                    schemas.append(howto_schema)

            print(f"   FAQ pairs: {len(faq_pairs)}, schemas: {len(schemas)}")
            if faq_pairs:
                for i, (q, a) in enumerate(faq_pairs, 1):
                    print(f"     Q{i}: {q}")
                    print(f"     A{i}: {a[:80]}...")

            if args.apply:
                # GET current post from Ghost (includes published_at for dates)
                post = fetch_ghost_post(publisher, cms_post_id)
                if not post:
                    stats["errors"] += 1
                    continue

                # Set BlogPosting dates from Ghost timestamps
                published_at = post.get("published_at", "")
                updated_at_ghost = post.get("updated_at", "")
                pub_date = published_at[:10] if published_at else datetime.now().strftime("%Y-%m-%d")
                mod_date = updated_at_ghost[:10] if updated_at_ghost else pub_date
                blog_posting["datePublished"] = pub_date
                blog_posting["dateModified"] = mod_date

                # Rebuild script tag with correct dates
                if len(schemas) == 1:
                    json_ld = json.dumps(schemas[0], ensure_ascii=False, indent=2)
                else:
                    json_ld = json.dumps(schemas, ensure_ascii=False, indent=2)
                new_schema_script = f'<script type="application/ld+json">\n{json_ld}\n</script>'

                # Preserve non-JSON-LD scripts from existing codeinjection_foot
                old_foot = post.get("codeinjection_foot") or ""
                non_schema_scripts = re.sub(
                    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>.*?</script>',
                    '',
                    old_foot,
                    flags=re.DOTALL | re.IGNORECASE,
                ).strip()

                if non_schema_scripts:
                    new_codeinjection = new_schema_script + "\n" + non_schema_scripts
                else:
                    new_codeinjection = new_schema_script

                # Backup current codeinjection_foot (write immediately for durability)
                backup[draft_id] = {
                    "title": title,
                    "cms_post_id": cms_post_id,
                    "old_codeinjection_foot": old_foot,
                }
                with open(backup_path, "w", encoding="utf-8") as f:
                    json.dump(backup, f, ensure_ascii=False, indent=2)

                # PUT new codeinjection_foot
                ok = update_ghost_codeinjection(
                    publisher, cms_post_id, new_codeinjection, post["updated_at"]
                )
                if ok:
                    stats["updated"] += 1
                    print("   UPDATED OK")
                else:
                    stats["errors"] += 1
            else:
                # Dry-run: set placeholder dates for preview
                blog_posting["datePublished"] = "(from Ghost)"
                blog_posting["dateModified"] = "(from Ghost)"
                if len(schemas) == 1:
                    json_ld = json.dumps(schemas[0], ensure_ascii=False, indent=2)
                else:
                    json_ld = json.dumps(schemas, ensure_ascii=False, indent=2)
                new_codeinjection = f'<script type="application/ld+json">\n{json_ld}\n</script>'
                print(f"   [DRY-RUN] Would update codeinjection_foot ({len(new_codeinjection)} chars)")

        except Exception as e:
            print(f"   ERROR: {e}")
            stats["errors"] += 1

        print()

    # ── Summary ──
    print("=" * 60)
    print(f"Total: {stats['total']}, with FAQ: {stats['with_faq']}, skipped: {stats['skipped']}")
    if args.apply:
        print(f"Updated: {stats['updated']}, Errors: {stats['errors']}")
        if backup:
            print(f"Backup saved to {backup_path}")
    else:
        print("[DRY-RUN] No changes made. Use --apply to update Ghost.")


if __name__ == "__main__":
    main()
