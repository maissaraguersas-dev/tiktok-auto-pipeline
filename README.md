# TikTok Auto Pipeline

> Automated AI-powered TikTok content pipeline. Discovers viral content, applies imperceptible AI mutations to bypass duplicate detection, generates viral copy with LLMs, and publishes with anti-bot protection.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-supported-blue.svg)](https://www.docker.com/)

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Pipeline Phases](#pipeline-phases)
- [Deployment](#deployment)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)
- [Disclaimer](#disclaimer)

---

## Features

| Feature | Description |
|---------|-------------|
| **Viral Discovery** | Monitors trending hashtags and creators, filtering by view velocity, engagement ratios, and share counts |
| **Smart Deduplication** | SQLite/PostgreSQL database prevents processing the same video twice |
| **No-Watermark Downloads** | Multi-method downloader (yt-dlp, Playwright, API) extracts clean HD video |
| **AI Video Mutation** | FFmpeg-based imperceptible alterations: micro-speed changes, color shifts, subtle crops, MD5 hash mutation |
| **Viral Copy Generation** | OpenAI/Anthropic LLM integration for high-retention captions and optimized hashtags |
| **Dual Upload Engine** | Both Playwright browser automation and direct API upload with auto-fallback |
| **Anti-Detection** | Proxy rotation, human-like delays, browser fingerprint randomization, session cookie management |
| **Docker Ready** | Full containerization with health checks and resource limits |

---

## Architecture

```
                    Pipeline Execution Flow
    
    Phase 1: DISCOVER          Phase 2: DEDUPLICATE
    +-------------+            +------------------+
    | Trend       |            | Database Check   |
    | Scraper     |----------->| Skip if Exists   |
    +-------------+            +------------------+
                                         |
                                         v
    Phase 5: PUBLISH          Phase 3: DOWNLOAD
    +----------------+         +------------------+
    | Upload (API/   |         | No-Watermark     |
    |  Playwright)   |         | Fetch (yt-dlp)   |
    | Cleanup Files  |<--------| Save to storage/ |
    +----------------+         |   raw/           |
         |                     +------------------+
         |                                |
         v                                v
    +---------+                  Phase 4: MUTATE + WRITE
    |  DONE   |                  +----------------------+
    +---------+                  | FFmpeg Processing:   |
                                 | - Speed: 1.01x       |
                                 | - Mirror (50%)       |
                                 | - Color micro-shift  |
                                 | - Crop 2px           |
                                 | - MD5 mutation       |
                                 |                      |
                                 | LLM Copywriting:     |
                                 | - Viral caption      |
                                 | - Trending hashtags  |
                                 +----------------------+
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- FFmpeg installed system-wide
- (Optional) Docker & Docker Compose

### Option 1: Local Installation

```bash
# Clone repository
git clone https://github.com/maissaraguersas-dev/tiktok-auto-pipeline.git
cd tiktok-auto-pipeline

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Configure environment
cp .env.example .env
# Edit .env with your API keys and settings

# Configure cookies
cp config/cookies.json config/cookies.json.bak
# Edit config/cookies.json with your TikTok session cookies

# Run single execution
python main.py

# Run continuously (every hour)
python main.py --loop

# Run with custom interval (30 minutes)
python main.py --loop --interval 1800
```

### Option 2: Docker Deployment

```bash
# Configure environment
cp .env.example .env
# Edit .env with your settings

# Start services (with PostgreSQL)
docker-compose up -d

# Or start with SQLite only (no PostgreSQL)
docker-compose up -d pipeline

# View logs
docker-compose logs -f pipeline

# Run one-shot execution
docker-compose run --rm pipeline python main.py

# Run health check
docker-compose run --rm pipeline python main.py --health

# Stop all services
docker-compose down
```

---

## Configuration

All configuration is managed through environment variables (via `.env` file).

### Required Settings

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key for caption generation | `sk-...` |
| `ANTHROPIC_API_KEY` | Anthropic API key (fallback) | `sk-ant-...` |

### Scraping Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MIN_VIEWS` | `100000` | Minimum views threshold |
| `MIN_VIEWS_TIMEFRAME_HOURS` | `24` | Time window for view count |
| `MIN_LIKE_TO_VIEW_RATIO` | `0.05` | Minimum engagement ratio |
| `MIN_SHARES` | `500` | Minimum shares threshold |

### Processing Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SPEED_FACTOR` | `1.01` | Speed multiplier (1.01 = 1% faster) |
| `MIRROR_PROBABILITY` | `0.5` | Chance of horizontal mirror |
| `COLOR_ADJUSTMENT_RANGE` | `0.02` | Color shift intensity |
| `CROP_PIXELS` | `2` | Micro-crop amount |

### Upload Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `UPLOAD_METHOD` | `auto` | `auto`, `playwright`, or `api` |
| `UPLOAD_INTERVAL_MINUTES` | `60` | Minutes between uploads |
| `RANDOMIZE_INTERVAL` | `true` | Add jitter to interval |
| `HEADLESS` | `true` | Run browser in headless mode |

### Getting TikTok Session Cookies

1. Log into TikTok in Chrome/Edge
2. Open DevTools (F12) → Application → Cookies
3. Copy these cookies to `config/cookies.json`:
   - `sessionid`
   - `sessionid_ss`
   - `sid_tt`
   - `uid_tt`
   - `tt_csrf_token`

---

## Pipeline Phases

### Phase 1: Discovery & Scrape

The scraper monitors TikTok's trending content by:
- Scraping trending hashtags from the Discover page
- Monitoring configured target creators
- Filtering by engagement metrics (view velocity, like-to-view ratio, shares)
- Extracting full metadata (author, music, description, stats)

### Phase 2: Deduplication Check

Each discovered video is cross-referenced against the database:
- SHA-256 hash of `video_id + author_id` used as unique key
- If video exists → skipped (logged as duplicate)
- If new → proceeds to download

### Phase 3: Clean Download

Multi-method downloader attempts (in order):
1. **yt-dlp** - Direct CDN extraction (no watermark, best quality)
2. **API endpoint** - TikTok internal API
3. **Playwright fallback** - Browser rendering for edge cases

### Phase 4: AI Mutation & Metadata Generation

**Video Processing (FFmpeg):**
- Speed scaling: base 1.01x ± 0.005 variation
- Horizontal mirroring: 50% probability
- Color grading: brightness/contrast/saturation ±0.01
- Micro-cropping: 0-2 pixels
- Subtle noise injection for MD5 hash mutation
- Slight re-encoding to ensure unique digital fingerprint

**Copy Generation (LLM):**
- Viral caption under 150 characters
- 5-8 trending hashtags (broad + niche mix)
- Context-aware based on original content

### Phase 5: Managed Publication & Cleanup

- Upload via Playwright (browser automation) or API
- Human-like delays between actions (randomized)
- Proxy rotation for each upload
- On success: local files deleted, database updated
- On failure: error logged, files retained for retry

---

## Deployment

### Docker Compose (Recommended)

```yaml
docker-compose up -d
```

Services:
- `pipeline` - Main application (auto-restart, health checks)
- `postgres` - PostgreSQL database (optional, SQLite used by default)
- `adminer` - Database admin UI (optional, profile: `admin`)

### Systemd Service (Linux)

```ini
# /etc/systemd/system/tiktok-pipeline.service
[Unit]
Description=TikTok Auto Pipeline
After=network.target

[Service]
Type=simple
User=pipeline
WorkingDirectory=/opt/tiktok-auto-pipeline
ExecStart=/opt/tiktok-auto-pipeline/venv/bin/python main.py --loop
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable tiktok-pipeline
sudo systemctl start tiktok-pipeline
sudo systemctl status tiktok-pipeline
```

### Cron Job (Alternative)

```bash
# Run every 2 hours
0 */2 * * * cd /opt/tiktok-auto-pipeline && /opt/venv/bin/python main.py >> /var/log/tiktok-pipeline.log 2>&1
```

---

## Monitoring

### CLI Commands

```bash
# View database statistics
python main.py --stats

# Run health checks
python main.py --health

# Verbose logging
python main.py -v

# Check logs
tail -f logs/pipeline.log
tail -f logs/errors.log
```

### Database Schema

| Table | Purpose |
|-------|---------|
| `tracked_videos` | Processed videos with full metadata and status |
| `pipeline_logs` | Structured execution logs |
| `proxy_health` | Proxy rotation health metrics |

### Key Metrics

- **Discovery Rate**: Videos found per run
- **Success Rate**: Successful uploads / attempts
- **Engagement Score**: Composite metric (views, likes, shares, velocity)
- **Proxy Health**: Success rate per proxy

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `playwright not found` | Run `playwright install chromium` |
| `ffmpeg not found` | Install FFmpeg: `apt install ffmpeg` or `brew install ffmpeg` |
| `No videos discovered` | Lower `MIN_VIEWS` threshold, check proxy settings |
| `Upload fails` | Verify cookies are valid and not expired |
| `Database locked` | Use PostgreSQL instead of SQLite for concurrent access |
| `Proxy connection error` | Check proxy credentials, try `PROXY_ENABLED=false` to test without |

### Debug Mode

```bash
# Enable debug logging
DEBUG=true python main.py -v
```

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Scraping | Playwright, yt-dlp |
| Video Processing | FFmpeg |
| AI / LLM | OpenAI API, Anthropic API |
| Database | SQLite (default) / PostgreSQL |
| HTTP Client | httpx |
| Container | Docker, Docker Compose |

---

## Disclaimer

This tool is for **educational and research purposes only**. Using automated tools with TikTok may violate their [Terms of Service](https://www.tiktok.com/terms-of-service). Users are responsible for ensuring compliance with all applicable laws and platform policies. The authors assume no liability for misuse of this software.

Key considerations:
- Respect rate limits to avoid IP bans
- Use residential proxies to reduce detection risk
- Monitor account health regularly
- Follow TikTok's Community Guidelines for all content

---

## License

MIT License - see [LICENSE](LICENSE) file for details.

---

## Contributing

Contributions welcome! Please read the contributing guidelines and submit PRs to the repository.

---

<p align="center">
  Built with Python + FFmpeg + LLMs
</p>
