"""
Regenerate covers for published posts + strip duplicate H1 from body.

Usage (inside Docker container):
    python /app/scripts/regen_covers.py
"""

import os
import sys
import re
import json
import hmac
import hashlib
import base64
import time
from datetime import datetime

import httpx
import anthropic

# Ghost-2 via HTTPS (ghost redirects HTTP→HTTPS, admin API needs direct HTTPS)
GHOST_URL = os.environ.get("GHOST_URL_PROD", "https://notes.editors.one")
GHOST_ADMIN_KEY = os.environ.get("GHOST_ADMIN_KEY_PROD", "")
if not GHOST_ADMIN_KEY:
    raise ValueError("GHOST_ADMIN_KEY_PROD env var is required (format: key_id:secret)")

# LLM / Image — from env
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_PROXY_URL = os.environ.get("ANTHROPIC_PROXY_URL", "")
ANTHROPIC_PROXY_SECRET = os.environ.get("ANTHROPIC_PROXY_SECRET", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_PROXY_URL = os.environ.get("OPENAI_PROXY_URL", "")
OPENAI_PROXY_SECRET = os.environ.get("OPENAI_PROXY_SECRET", "")

# Prompts
COVER_SCENE_PROMPT = """You are an art director for a pixel-art blog. Read the article and describe ONE specific, unique scene for the cover in 3-5 sentences in English.

Rules:
- The scene is a visual METAPHOR for the article's core idea — not a literal depiction of the topic
- Be CREATIVE with settings. Choose from a WIDE range: a rooftop garden, a busy marketplace, a cat napping on a stack of books, a lighthouse at dawn, a workshop with tools, a train station, a forest path, a kitchen table with scattered notes, a cityscape from above, a boat on a calm lake — pick what resonates with THIS specific article
- VARY the time of day and lighting: bright morning sun, overcast afternoon, neon-lit night, foggy dawn, golden sunset, blue hour — not always evening
- VARY the color mood: cool blues, lush greens, warm oranges, muted pastels, vibrant neons — not always warm tones
- Include 3-5 specific objects that tell a story about the scene
- AVOID: generic desk-with-laptop-and-window scenes. Every article deserves its own world
- NO people, NO hands, NO faces, NO text, NO letters, NO numbers, NO logos
- Write ONLY the scene description, nothing else

[ARTICLE TEXT]
"""

COVER_STYLE_PREFIX = (
    "Wide 16:9 high-quality pixel art illustration. "
    "Detailed retro pixel art style inspired by Owlboy and Eastward. "
    "Fine pixel detail on objects and textures, slight dithering for smooth gradients. "
    "Rich, atmospheric, NOT flat or cartoony. "
    "No text, no letters, no numbers, no logos, no people, no hands. "
    "Scene: "
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt() -> str:
    kid, secret = GHOST_ADMIN_KEY.split(":")
    iat = int(datetime.now().timestamp())
    h = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    p = _b64url(json.dumps({"iat": iat, "exp": iat + 300, "aud": "/admin/"}).encode())
    sig = hmac.new(bytes.fromhex(secret), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def _client():
    return httpx.Client(verify=False, timeout=60.0)


def _headers():
    return {"Authorization": f"Ghost {_jwt()}", "Content-Type": "application/json"}


def get_posts(limit=5):
    with _client() as c:
        r = c.get(f"{GHOST_URL}/ghost/api/admin/posts/", headers=_headers(),
                  params={"status": "published", "order": "published_at desc",
                          "limit": limit, "formats": "mobiledoc"})
        r.raise_for_status()
        return r.json()["posts"]


def extract_md(mobiledoc_str):
    doc = json.loads(mobiledoc_str)
    for card in doc.get("cards", []):
        if card[0] == "markdown" and len(card) > 1:
            return card[1].get("markdown", "")
    return ""


def md_to_mobiledoc(md):
    return json.dumps({
        "version": "0.3.1", "ghostVersion": "4.0",
        "markups": [], "atoms": [],
        "cards": [["markdown", {"markdown": md}]],
        "sections": [[10, 0]],
    })


def update_post(post_id, updated_at, **fields):
    data = {"posts": [{"updated_at": updated_at, **fields}]}
    with _client() as c:
        r = c.put(f"{GHOST_URL}/ghost/api/admin/posts/{post_id}/",
                  headers=_headers(), json=data)
        if r.status_code != 200:
            print(f"  ERROR update: {r.status_code} {r.text[:300]}")
            return None
        return r.json()["posts"][0]


def upload_image(image_bytes, filename):
    h = {"Authorization": f"Ghost {_jwt()}"}
    ct = "image/webp" if filename.endswith(".webp") else "image/png"
    with _client() as c:
        r = c.post(f"{GHOST_URL}/ghost/api/admin/images/upload/",
                   headers=h, files={"file": (filename, image_bytes, ct)},
                   data={"ref": filename})
    if r.status_code == 201:
        imgs = r.json().get("images", [])
        if imgs:
            return imgs[0].get("url")
    print(f"  Upload failed: {r.status_code} {r.text[:200]}")
    return None


def generate_cover(article_md, slug, title):
    import openai as oai

    # Claude → scene description
    ckw = {"api_key": ANTHROPIC_API_KEY}
    if ANTHROPIC_PROXY_URL:
        ckw["base_url"] = ANTHROPIC_PROXY_URL
        if ANTHROPIC_PROXY_SECRET:
            ckw["default_headers"] = {"x-proxy-token": ANTHROPIC_PROXY_SECRET}
    claude = anthropic.Anthropic(**ckw)

    resp = claude.messages.create(
        model="claude-sonnet-4-20250514", max_tokens=300, temperature=0.9,
        messages=[{"role": "user", "content": COVER_SCENE_PROMPT + article_md[:6000]}],
    )
    scene = resp.content[0].text.strip()
    print(f"  Scene: {scene[:180]}...")

    # gpt-image-1.5 → pixel art
    # OpenAI proxy reuses the same x-proxy-token as Anthropic proxy
    okw = {"api_key": OPENAI_API_KEY}
    if OPENAI_PROXY_URL:
        okw["base_url"] = OPENAI_PROXY_URL
        proxy_secret = OPENAI_PROXY_SECRET or ANTHROPIC_PROXY_SECRET
        if proxy_secret:
            okw["default_headers"] = {"x-proxy-token": proxy_secret}
    oc = oai.OpenAI(**okw)

    img = oc.images.generate(
        model="gpt-image-1.5", prompt=COVER_STYLE_PREFIX + scene,
        size="1536x1024", quality="medium", n=1,
    )
    raw = base64.b64decode(img.data[0].b64_json)
    print(f"  Raw PNG: {len(raw)} bytes")

    # Convert to WebP
    from PIL import Image
    from io import BytesIO
    pil_img = Image.open(BytesIO(raw))
    buf = BytesIO()
    pil_img.save(buf, format="WEBP", quality=85, method=6)
    data = buf.getvalue()
    print(f"  WebP: {len(data)} bytes ({100 * len(data) / len(raw):.0f}% of original)")

    url = upload_image(data, f"{slug}__cover_v2.webp")
    alt = f"{title} — обложка статьи" if title else "Обложка статьи"
    return url, alt


def main():
    posts = get_posts(5)
    # Skip the "Coming soon" placeholder if present
    posts = [p for p in posts if p.get("slug") != "coming-soon"]
    print(f"Processing {len(posts)} posts\n")

    for i, post in enumerate(posts):
        pid, title, slug = post["id"], post["title"], post["slug"]
        updated_at = post["updated_at"]
        print(f"[{i+1}/{len(posts)}] {title}")

        md = extract_md(post.get("mobiledoc", ""))
        if not md:
            print("  SKIP: no markdown\n")
            continue

        # Strip H1
        orig = md
        md = re.sub(r'\A\s*#\s+[^\n]+\n*', '', md)
        h1_fixed = md != orig
        if h1_fixed:
            print("  Stripped H1")

        # Generate cover
        try:
            cover_url, cover_alt = generate_cover(md, slug, title)
            print(f"  Cover: {cover_url}")
        except Exception as e:
            print(f"  Cover ERROR: {e}")
            cover_url, cover_alt = None, None

        # Update Ghost
        fields = {}
        if h1_fixed:
            fields["mobiledoc"] = md_to_mobiledoc(md)
        if cover_url:
            fields["feature_image"] = cover_url
            fields["feature_image_alt"] = cover_alt

        if fields:
            r = update_post(pid, updated_at, **fields)
            print(f"  {'OK' if r else 'FAILED'}")
        print()
        time.sleep(2)

    print("Done!")


if __name__ == "__main__":
    main()
