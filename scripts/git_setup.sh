#!/bin/bash
# scripts/git_setup.sh — Initialize git repo and make first commit
# Run from the store-intelligence/ root directory

set -e
echo "=== Store Intelligence — Git Setup ==="

# Init repo
git init
git branch -M main

# Stage everything
git add .

# Initial commit
git commit -m "Initial commit: Store Intelligence API — Brigade Bangalore (ST1008)

- Detection pipeline: YOLOv8n + ByteTrack + Shapely zone mapping
- Staff detection: OSNet embedding bank (histogram fallback)
- Re-entry detection: cosine similarity within 10-minute window
- Intelligence API: FastAPI + SQLite, 6 endpoints
- POS correlation: 24 transactions, 5-minute window
- Tests: 20 tests, 75% coverage
- Dashboard: Rich terminal, live metrics + funnel + anomalies
- Docs: DESIGN.md + CHOICES.md (AI-assisted decisions documented)"

echo ""
echo "Done. Next steps:"
echo "  1. Create a PRIVATE repo on GitHub"
echo "  2. git remote add origin <your-repo-url>"
echo "  3. git push -u origin main"
echo "  4. Invite the reviewer (handle provided in challenge email)"
