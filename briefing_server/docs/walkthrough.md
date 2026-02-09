# Briefing Server Walkthrough

We have successfully decoupled the TrendRadar API into a standalone **Briefing Server** (`briefing_server/`), ensuring **zero modifications** to the core TrendRadar codebase.

## 1. Architecture Changes

- **New Service Directory**: `briefing_server/`
  - `main.py`: FastAPI application entry point.
  - `service.py`: `BriefingService` logic (handles rule parsing, data fetching, AI analysis).
  - `utils.py`: Re-implemented `parse_frequency_rules` to avoid core dependency.
- **Core Reverted**:
  - `trendradar/core/frequency.py`: Restored original `load_frequency_words`.
  - `trendradar/ai/analyzer.py`: Restored original `analyze` method (removed `_construct_user_prompt`).

## 2. API Verification

### Health Check

```bash
curl http://localhost:8000/health
# Output: {"status":"ok","version":"6.0.0"}
```

### Briefing Generation (Stream Mode)

The API supports Server-Sent Events (SSE) for streaming AI responses.

```bash
curl -N -X POST "http://localhost:8000/api/v1/briefing?stream_ai=true" \
     -H "Content-Type: application/json" \
     -d '{
           "rules": ["AI", "Technology"],
           "allowed_sources": ["weibo", "toutiao"]
         }'
```

_Response:_

- If data matches: Streams AI generated content.
- If no data: Returns "今日无相关动态。".

## 3. How to Run

Start the server using `uvicorn`:

```bash
uvicorn briefing_server.main:app --host 0.0.0.0 --port 8000 --reload
```

## 4. Key Features

- **Stateless**: Does not store user configs. Rules are passed via API.
- **Decoupled**: Uses its own utility functions to interpret rules and build prompts, ensuring the core TrendRadar remains a stable CLI/Report tool.
- **Streaming**: Full support for real-time AI response streaming.
