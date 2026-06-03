# Submission Checklist

Work through this top to bottom before submitting.

## Step 1 — Detection pipeline (your machine)

```bash
# Install pipeline deps
pip install ultralytics shapely numpy opencv-python-headless

# Rename your clips
mv "clips/CAM 3.mp4" clips/entry_cam.mp4
mv "clips/CAM 2.mp4" clips/billing_cam.mp4
mv "clips/CAM 1.mp4" clips/floor_cam1.mp4
mv "clips/CAM 4.mp4" clips/floor_cam2.mp4
mv "clips/CAM 5.mp4" clips/floor_cam3.mp4

# Build staff embedding bank
python pipeline/build_staff_bank.py --clips clips/ --auto
# Review staff_embeddings/candidate_XXXX.jpg thumbnails
# Rename staff ones: mv staff_embeddings/candidate_0002.npy staff_embeddings/staff_0002.npy
# Delete non-staff: rm staff_embeddings/candidate_*.npy

# Run detection
bash pipeline/run.sh ./clips ./data/events.jsonl

# Check output — should see hundreds/thousands of events
wc -l data/events.jsonl
head -3 data/events.jsonl
```

- [ ] events.jsonl exists and has events
- [ ] ENTRY and EXIT counts look reasonable
- [ ] is_staff=true events visible in output

## Step 2 — API (docker)

```bash
# Start the API
docker compose up --build

# In another terminal — feed events
python scripts/feed_events.py --events data/events.jsonl --api http://localhost:8000

# Run self-check assertions
python assertions.py --api http://localhost:8000 --store ST1008
```

- [ ] docker compose up works with no manual steps
- [ ] All 10 assertions pass
- [ ] GET /stores/ST1008/metrics returns meaningful numbers
- [ ] GET /health returns OK or WARN (not ERROR)

## Step 3 — Tests

```bash
pip install pytest pytest-cov
pytest tests/ -v --cov=app --cov-report=term-missing
```

- [ ] 20 tests pass
- [ ] Coverage >= 70%

## Step 4 — Docs check

```bash
wc -w docs/DESIGN.md     # must be > 250 words
wc -w docs/CHOICES.md    # must be > 250 words
```

- [ ] DESIGN.md > 250 words with "AI-Assisted Decisions" section
- [ ] CHOICES.md > 250 words with 3 decisions (model, schema, API)
- [ ] README.md has 5-command quickstart
- [ ] tests/test_api.py has PROMPT: / CHANGES MADE: header block
- [ ] tests/test_pipeline.py has PROMPT: / CHANGES MADE: header block
- [ ] tests/test_anomalies.py has PROMPT: / CHANGES MADE: header block

## Step 5 — Git and submission

```bash
# From store-intelligence/ root
bash scripts/git_setup.sh

# Push to private GitHub repo
git remote add origin https://github.com/YOUR_USERNAME/store-intelligence.git
git push -u origin main
```

- [ ] Private GitHub repo created
- [ ] docker compose up confirmed on a CLEAN clone (not your dev folder)
- [ ] Reviewer handle invited as collaborator
- [ ] Repo link submitted via challenge portal

## Step 6 — Dashboard (bonus +10 pts)

```bash
# Terminal dashboard (run while API is up)
python dashboard/dashboard.py --events data/events.jsonl --api http://localhost:8000

# OR via docker compose
docker compose --profile dashboard up
```

- [ ] Dashboard shows live metrics updating as events replay
- [ ] Local URL noted in README.md (for terminal: `python dashboard/dashboard.py`)
