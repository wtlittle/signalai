# ============================================================
# ADD THIS ENDPOINT TO backend.py
#
# Drop it alongside your other /earnings-intel, /market-intel,
# /news endpoints in the routes section.
#
# Requires: SUPABASE_URL and SUPABASE_SERVICE_KEY env vars.
# Then delete this file once merged into backend.py.
# ============================================================

# Imports already expected to exist in backend.py:
#   import os, json, requests
#   from flask import request, jsonify


@app.route("/transcript-intel")
def transcript_intel():
    """
    GET /transcript-intel?ticker=MSFT[&earnings_date=2026-04-30]

    Returns the most-recent transcript_intel row for a given ticker.
    If earnings_date is supplied, returns that specific row.

    Response shape (matches note-enhancements.js renderTranscriptPanel()):
    {
      "ticker": "MSFT",
      "company_name": "Microsoft Corporation",
      "earnings_date": "2026-04-30",
      "quarter": "Q3 FY2026",
      "transcript_source": "motley_fool",
      "transcript_url": "https://www.fool.com/...",
      "beat_miss_summary": "...",
      "management_tone": "bullish",
      "mgmt_key_points": [...],
      "guidance_statements": [...],
      "qa_key_exchanges": [...],
      "tone_signals": [...],
      "key_metrics_discussed": [...],
      "notable_quotes": [...],
      "risk_factors_cited": [...],
      "harvested_at": "2026-05-01T10:00:00+00:00"
    }

    Errors:
      400  { "error": "ticker param required" }
      404  { "error": "no transcript intel found for TICKER" }
      500  { "error": "supabase not configured" | "supabase error: ..." }
    """
    ticker        = (request.args.get("ticker") or "").strip().upper()
    earnings_date = (request.args.get("earnings_date") or "").strip()

    if not ticker:
        return jsonify({"error": "ticker param required"}), 400

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not supabase_key:
        return jsonify({"error": "supabase not configured"}), 500

    headers = {
        "apikey":        supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type":  "application/json",
    }
    params = {
        "ticker": f"eq.{ticker}",
        "order":  "harvested_at.desc",
        "limit":  "1",
        "select": "*",
    }
    if earnings_date:
        params["earnings_date"] = f"eq.{earnings_date}"

    try:
        resp = requests.get(
            f"{supabase_url}/rest/v1/transcript_intel",
            headers=headers,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        return jsonify({"error": f"supabase error: {exc}"}), 500

    rows = resp.json() or []
    if not rows:
        return jsonify({"error": f"no transcript intel found for {ticker}"}), 404

    row = rows[0]

    # Normalize JSONB columns — guard against stringified JSON from older rows
    for col in [
        "mgmt_key_points", "guidance_statements", "qa_key_exchanges",
        "tone_signals", "key_metrics_discussed", "notable_quotes", "risk_factors_cited",
    ]:
        val = row.get(col)
        if isinstance(val, str):
            try:
                row[col] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                row[col] = []
        elif val is None:
            row[col] = []

    return jsonify(row)


# ============================================================
# note-enhancements.js integration
# ============================================================
# Update fetchTranscript() in note-enhancements.js:
#
#   async function fetchTranscript(ticker, earningsDate) {
#     try {
#       const qs = earningsDate ? `&earnings_date=${earningsDate}` : '';
#       const resp = await fetchWithFallback(
#         `/transcript-intel?ticker=${ticker}${qs}`
#       );
#       if (!resp.ok) return null;
#       return await resp.json();
#     } catch (e) { return null; }
#   }
#
# Then in renderTranscriptPanel(), check intel != null and render:
#   intel.mgmt_key_points     → "Key Points" bullets
#   intel.qa_key_exchanges    → "Q&A Highlights" section
#   intel.guidance_statements → "Guidance" section
#   intel.tone_signals        → "Tone Signals" chips
#   intel.transcript_url      → link to source (show if scraped from Fool)
# ============================================================
