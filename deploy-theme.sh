#!/bin/bash
set -e
echo "=== Deploying notes theme ==="
scp -r themes/notes/ root@95.163.230.43:/opt/seo-blog/themes/notes/
ssh root@95.163.230.43 "cd /opt/seo-blog && docker cp themes/notes/. seo-blog-ghost-2-1:/var/lib/ghost/content/themes/notes/ && docker restart seo-blog-ghost-2-1"
echo "Done. Theme reloaded."
