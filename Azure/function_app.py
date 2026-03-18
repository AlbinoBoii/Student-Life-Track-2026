"""
Azure Functions (Python v2) – Washer Monitor Backend
=====================================================
Three HTTP-triggered functions:
  POST /api/ingest    – receive batched sensor data from ESP32
  GET  /api/samples   – query / export data (JSON or CSV)
  GET  /api/dashboard  – self-contained HTML dashboard
"""

import csv
import io
import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func
from azure.data.tables import TableServiceClient, EdmType, EntityProperty

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "")
CONN_STR = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
TABLE_NAME = "washersensordata"


def _get_table_client():
    """Return a TableClient, creating the table if needed."""
    svc = TableServiceClient.from_connection_string(CONN_STR)
    svc.create_table_if_not_exists(TABLE_NAME)
    return svc.get_table_client(TABLE_NAME)


# ---------------------------------------------------------------------------
# POST /api/ingest
# ---------------------------------------------------------------------------

@app.route(route="ingest", methods=["POST"])
def ingest(req: func.HttpRequest) -> func.HttpResponse:
    """Accept a JSON batch of sensor samples from the ESP32."""

    # --- auth ---
    api_key = req.headers.get("x-api-key", "")
    if not INGEST_API_KEY or api_key != INGEST_API_KEY:
        return func.HttpResponse(
            json.dumps({"ok": False, "error": "bad api key"}),
            status_code=401,
            mimetype="application/json",
        )

    # --- parse body ---
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"ok": False, "error": "invalid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    device_id = body.get("device_id", "unknown")
    boot_id = body.get("boot_id", "unknown")
    seq_no = body.get("seq_no", 0)
    samples = body.get("samples", [])

    if not samples:
        return func.HttpResponse(
            json.dumps({"ok": True, "accepted_seq_no": seq_no, "sample_count": 0}),
            mimetype="application/json",
        )

    # --- write to Table Storage ---
    table = _get_table_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    inserted = 0
    duplicate = False

    for s in samples:
        ts_ms = s.get("ts_ms", 0)
        row_key = f"{boot_id}_{seq_no}_{ts_ms}"

        entity = {
            "PartitionKey": device_id,
            "RowKey": row_key,
            "boot_id": boot_id,
            "seq_no": seq_no,
            "ts_ms": EntityProperty(int(ts_ms), EdmType.INT64),
            "ax": s.get("ax", 0),
            "ay": s.get("ay", 0),
            "az": s.get("az", 0),
            "motion_score": float(s.get("motion_score", 0)),
            "motion_avg": float(s.get("motion_avg", 0)),
            "overall_state": s.get("overall_state", s.get("state", "")),
            "sub_state": s.get("sub_state", ""),
            "wifi_rssi_dbm": s.get("wifi_rssi_dbm"),
            "received_at": now_iso,
        }

        try:
            table.create_entity(entity)
            inserted += 1
        except Exception as exc:
            if "EntityAlreadyExists" in str(exc):
                duplicate = True
            else:
                logging.error("Table insert error: %s", exc)

    return func.HttpResponse(
        json.dumps({
            "ok": True,
            "accepted_seq_no": seq_no,
            "sample_count": inserted,
            "duplicate": duplicate,
        }),
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# Helper: Aggregate rows by time bucket
# ---------------------------------------------------------------------------

def _aggregate_rows(rows, aggregate_str):
    """
    Group rows into time buckets and return aggregated statistics.

    aggregate_str: '1m', '5m', '15m', '1h'
    Returns: list of aggregated bucket dicts
    """
    bucket_ms_map = {
        '1m': 60_000,
        '5m': 300_000,
        '15m': 900_000,
        '1h': 3_600_000,
    }

    bucket_ms = bucket_ms_map.get(aggregate_str)
    if not bucket_ms:
        return rows  # Invalid aggregate, return raw

    if not rows:
        return []

    # Group by bucket
    buckets = {}
    for row in rows:
        ts_ms = row.get('ts_ms', 0)
        bucket_key = (ts_ms // bucket_ms) * bucket_ms

        if bucket_key not in buckets:
            buckets[bucket_key] = []
        buckets[bucket_key].append(row)

    # Aggregate each bucket
    aggregated = []
    for bucket_key in sorted(buckets.keys(), reverse=True):
        bucket_rows = buckets[bucket_key]
        n = len(bucket_rows)

        # Motion stats
        motion_scores = [r.get('motion_score', 0) for r in bucket_rows]
        motion_avgs = [r.get('motion_avg', 0) for r in bucket_rows]

        # Accelerometer stats
        ax_vals = [r.get('ax', 0) for r in bucket_rows]
        ay_vals = [r.get('ay', 0) for r in bucket_rows]
        az_vals = [r.get('az', 0) for r in bucket_rows]

        # State stats
        running_count = sum(1 for r in bucket_rows if (r.get('overall_state') or r.get('state', '')).upper() == 'RUNNING')
        running_percent = (running_count / n * 100) if n > 0 else 0

        # Sub-state distribution
        sub_state_modes = {'IDLE': 0, 'WASH': 0, 'SPINDRY': 0, '': 0}
        for r in bucket_rows:
            sub = r.get('sub_state', '')
            if sub in sub_state_modes:
                sub_state_modes[sub] += 1
            else:
                sub_state_modes[''] += 1

        aggregated_row = {
            'ts_ms': bucket_key,
            'motion_score_avg': sum(motion_scores) / n if motion_scores else 0,
            'motion_score_min': min(motion_scores) if motion_scores else 0,
            'motion_score_max': max(motion_scores) if motion_scores else 0,
            'ax_avg': sum(ax_vals) / n if ax_vals else 0,
            'ax_min': min(ax_vals) if ax_vals else 0,
            'ax_max': max(ax_vals) if ax_vals else 0,
            'ay_avg': sum(ay_vals) / n if ay_vals else 0,
            'ay_min': min(ay_vals) if ay_vals else 0,
            'ay_max': max(ay_vals) if ay_vals else 0,
            'az_avg': sum(az_vals) / n if az_vals else 0,
            'az_min': min(az_vals) if az_vals else 0,
            'az_max': max(az_vals) if az_vals else 0,
            'motion_avg_avg': sum(motion_avgs) / n if motion_avgs else 0,
            'sample_count': n,
            'running_percent': running_percent,
            'sub_state_modes': sub_state_modes,
        }
        aggregated.append(aggregated_row)

    return aggregated


# ---------------------------------------------------------------------------
# Helper: Calculate device health metrics
# ---------------------------------------------------------------------------

def _calculate_device_health(device_id, days=7):
    """
    Calculate device health metrics: uptime, boot events, data gaps, WiFi stats.

    Returns dict with health metrics or error response.
    """
    from datetime import timedelta, timezone

    # Query all samples for this device in the date range
    table = _get_table_client()
    now = datetime.now(timezone.utc)
    lookback_date = (now - timedelta(days=days)).isoformat()

    # OData filter for device and date range
    odata_filter = f"PartitionKey eq '{device_id}' and received_at ge '{lookback_date}'"

    try:
        entities = list(table.query_entities(odata_filter))
    except Exception as e:
        logging.error(f"Query error in device health: {e}")
        return {"error": str(e), "device_id": device_id}

    if not entities:
        return {
            "device_id": device_id,
            "total_uptime_seconds": 0,
            "uptime_percent": 0,
            "total_boots": 0,
            "boot_events": [],
            "data_gaps": [],
            "wifi_signal_stats": {"avg_rssi": 0, "min_rssi": 0, "max_rssi": 0, "samples_with_signal": 0},
            "last_contact": {"timestamp": None, "age_seconds": 0, "status": "offline"},
        }

    # Sort by received_at
    entities.sort(key=lambda x: x.get("received_at", ""))

    # Group by boot_id to identify boot events
    boot_groups = {}
    for entity in entities:
        boot_id = entity.get("boot_id", "unknown")
        if boot_id not in boot_groups:
            boot_groups[boot_id] = []
        boot_groups[boot_id].append(entity)

    # Calculate boot events
    boot_events = []
    for boot_id, boot_samples in boot_groups.items():
        if boot_samples:
            first_ts = min(s.get("received_at", "") for s in boot_samples)
            last_ts = max(s.get("received_at", "") for s in boot_samples)
            boot_events.append({
                "boot_id": boot_id,
                "boot_ts": first_ts,
                "samples_count": len(boot_samples),
                "last_activity": last_ts,
            })

    boot_events.sort(key=lambda x: x.get("boot_ts", ""))

    # Calculate data gaps (> 5 minutes without data)
    data_gaps = []
    gap_threshold = 5 * 60  # 5 minutes in seconds

    for i in range(len(entities) - 1):
        current_ts = datetime.fromisoformat(entities[i].get("received_at", "").replace("Z", "+00:00"))
        next_ts = datetime.fromisoformat(entities[i + 1].get("received_at", "").replace("Z", "+00:00"))

        gap_seconds = (next_ts - current_ts).total_seconds()
        if gap_seconds > gap_threshold:
            data_gaps.append({
                "start": entities[i].get("received_at", ""),
                "end": entities[i + 1].get("received_at", ""),
                "duration_seconds": int(gap_seconds),
                "reason": "No data (likely offline or power loss)"
            })

    # Calculate WiFi stats
    wifi_signals = []
    for entity in entities:
        rssi = entity.get("wifi_rssi_dbm")
        if rssi is not None and rssi != "":
            try:
                wifi_signals.append(int(rssi) if isinstance(rssi, int) else int(float(rssi)))
            except (ValueError, TypeError):
                pass

    wifi_stats = {
        "avg_rssi": round(sum(wifi_signals) / len(wifi_signals), 1) if wifi_signals else 0,
        "min_rssi": min(wifi_signals) if wifi_signals else 0,
        "max_rssi": max(wifi_signals) if wifi_signals else 0,
        "samples_with_signal": len(wifi_signals),
    }

    # Calculate total uptime
    first_ts = datetime.fromisoformat(entities[0].get("received_at", "").replace("Z", "+00:00"))
    last_ts = datetime.fromisoformat(entities[-1].get("received_at", "").replace("Z", "+00:00"))
    total_span_seconds = (last_ts - first_ts).total_seconds()

    # Uptime = total span - gaps
    gap_seconds_total = sum(g["duration_seconds"] for g in data_gaps)
    uptime_seconds = max(0, total_span_seconds - gap_seconds_total)
    uptime_percent = (uptime_seconds / total_span_seconds * 100) if total_span_seconds > 0 else 0

    # Last contact
    last_contact_ts = entities[-1].get("received_at", "")
    last_contact_dt = datetime.fromisoformat(last_contact_ts.replace("Z", "+00:00"))
    age_seconds = int((now - last_contact_dt).total_seconds())

    # Determine status
    status = "online" if age_seconds < 600 else "offline"  # 10 minutes

    return {
        "device_id": device_id,
        "total_uptime_seconds": int(uptime_seconds),
        "uptime_percent": round(uptime_percent, 1),
        "total_boots": len(boot_events),
        "boot_events": boot_events,
        "data_gaps": data_gaps,
        "wifi_signal_stats": wifi_stats,
        "last_contact": {
            "timestamp": last_contact_ts,
            "age_seconds": age_seconds,
            "status": status,
        },
    }


# ---------------------------------------------------------------------------
# GET /api/device-health
# ---------------------------------------------------------------------------

@app.route(route="device-health", methods=["GET"])
def device_health(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get device health and uptime metrics.

    Query params:
        device_id  – device identifier (required)
        days       – lookback period in days (default 7, max 90)
    """
    device_id = req.params.get("device_id", "")
    try:
        days = min(int(req.params.get("days", "7")), 90)
    except ValueError:
        days = 7

    if not device_id:
        return func.HttpResponse(
            json.dumps({"error": "device_id parameter required"}),
            status_code=400,
            mimetype="application/json",
        )

    health = _calculate_device_health(device_id, days)
    return func.HttpResponse(
        json.dumps(health),
        mimetype="application/json",
    )


@app.route(route="samples", methods=["GET"])
def samples(req: func.HttpRequest) -> func.HttpResponse:
    """
    Query stored samples.

    Query params:
        device_id  – filter by device (required for performance)
        since      – ISO timestamp lower bound
        until      – ISO timestamp upper bound
        state      – filter by washer state (IDLE / RUNNING)
        limit      – max raw samples (default 1000, max 30000). Ignored if aggregated.
        format     – 'json' (default) or 'csv'
        aggregate  – time bucket interval ('1m', '5m', '15m', '1h')
    """
    device_id = req.params.get("device_id", "")
    from_ts = req.params.get("from", "")
    to_ts = req.params.get("to", "")
    state_filter = req.params.get("state", "")
    limit = min(int(req.params.get("limit", "1000")), 30000)
    fmt = req.params.get("format", "json").lower()
    aggregate = req.params.get("aggregate", "")

    # Force 30k limit for raw data; aggregation uses its own internal logic
    if not aggregate:
        limit = min(limit, 30000)
    else:
        # For aggregation, we want to fetch more data, but still have a safety cap
        limit = 100000 

    # Build OData filter
    filters = []
    if device_id:
        filters.append(f"PartitionKey eq '{device_id}'")
    if state_filter:
        filters.append(f"overall_state eq '{state_filter}'")
    if from_ts:
        filters.append(f"received_at ge '{from_ts}'")
    if to_ts:
        filters.append(f"received_at le '{to_ts}'")

    odata_filter = " and ".join(filters) if filters else None

    table = _get_table_client()

    if odata_filter:
        entities = list(table.query_entities(odata_filter))
    else:
        entities = list(table.list_entities())

    entities.sort(key=lambda x: x.get("received_at", ""), reverse=True)
    entities = entities[:limit]

    rows = []
    for entity in entities:
        raw_ts = entity.get("ts_ms", 0)
        ts_ms = raw_ts.value if hasattr(raw_ts, "value") else raw_ts
        rows.append({
            "device_id": entity.get("PartitionKey", ""),
            "boot_id": entity.get("boot_id", ""),
            "seq_no": entity.get("seq_no", 0),
            "ts_ms": ts_ms,
            "ax": entity.get("ax", 0),
            "ay": entity.get("ay", 0),
            "az": entity.get("az", 0),
            "motion_score": entity.get("motion_score", 0),
            "motion_avg": entity.get("motion_avg", 0),
            "overall_state": entity.get("overall_state", entity.get("state", "")),
            "sub_state": entity.get("sub_state", ""),
            "wifi_rssi_dbm": entity.get("wifi_rssi_dbm"),
            "received_at": entity.get("received_at", ""),
        })

    # Apply aggregation if requested
    if aggregate:
        rows = _aggregate_rows(rows, aggregate)

    # --- CSV ---
    if fmt == "csv":
        if not rows:
            return func.HttpResponse(
                "no data",
                status_code=200,
                mimetype="text/plain",
            )
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        return func.HttpResponse(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=washer_samples.csv"},
        )

    # --- JSON ---
    response = {
        "count": len(rows),
        "samples": rows,
    }
    if aggregate:
        response["aggregated"] = True
        response["bucket_size"] = aggregate
    return func.HttpResponse(
        json.dumps(response),
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# PATCH /api/label
# ---------------------------------------------------------------------------

from azure.data.tables import UpdateMode

@app.route(route="label", methods=["PATCH"])
def label(req: func.HttpRequest) -> func.HttpResponse:
    """Bulk-update sub_state on existing rows."""
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"ok": False, "error": "invalid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    device_id = body.get("device_id", "")
    row_keys = body.get("row_keys", [])
    sub_state = body.get("sub_state", "")

    valid_sub_states = {"", "IDLE", "WASH", "SPINDRY"}
    if sub_state not in valid_sub_states:
        return func.HttpResponse(
            json.dumps({"ok": False, "error": f"Invalid sub_state. Must be one of {valid_sub_states}"}),
            status_code=400,
            mimetype="application/json",
        )

    if not device_id or not row_keys:
        return func.HttpResponse(
            json.dumps({"ok": False, "error": "device_id and row_keys are required"}),
            status_code=400,
            mimetype="application/json",
        )

    table = _get_table_client()
    updated = 0
    errors = 0

    import logging
    for rk in row_keys:
        try:
            table.update_entity(
                {"PartitionKey": device_id, "RowKey": rk, "sub_state": sub_state},
                mode=UpdateMode.MERGE
            )
            updated += 1
        except Exception as e:
            logging.error(f"Error updating entity {rk}: {e}")
            errors += 1

    return func.HttpResponse(
        json.dumps({"ok": True, "updated": updated, "errors": errors}),
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# GET /api/dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dra-Washer Monitor Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root{--bg:#0f172a;--surface:#1e293b;--border:#334155;--accent:#38bdf8;--accent2:#818cf8;--text:#e2e8f0;--muted:#94a3b8;--green:#22c55e;--red:#ef4444;}
  *{margin:0;padding:0;box-sizing:border-box;}
  body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;}
  .navbar{background:var(--surface);border-bottom:1px solid var(--border);padding:1rem 2rem;display:flex;align-items:center;justify-content:space-between;gap:1rem;}
  .navbar h1{font-size:1.25rem;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
  .navbar .pill{font-size:.70rem;padding:.2rem .6rem;border-radius:9999px;background:var(--border);color:var(--muted);font-weight:600;text-transform:uppercase;}
  .navbar .pill.live{background:rgba(34,197,94,0.2);color:var(--green);border:1px solid var(--green);}
  
  .tabs{display:flex;gap:1rem;padding:1rem 2rem 0;border-bottom:1px solid var(--border);background:var(--bg);}
  .tab-btn{background:none;border:none;color:var(--muted);padding:.75rem 1rem;cursor:pointer;font-weight:600;font-size:.9rem;border-bottom:2px solid transparent;transition:all .2s;}
  .tab-btn.active{color:var(--accent);border-bottom-color:var(--accent);}
  .tab-btn:hover:not(.active){color:var(--text);}

  .controls{display:flex;flex-wrap:wrap;gap:.75rem;padding:1.25rem 2rem;align-items:end;background:var(--surface);margin:1px 0;}
  .field{display:flex;flex-direction:column;gap:.25rem;}
  .field label{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);}
  .field input,.field select{background:var(--bg);border:1px solid var(--border);color:var(--text);padding:.45rem .7rem;border-radius:.375rem;font-size:.85rem;}
  .btn{padding:.5rem 1.25rem;border:none;border-radius:.375rem;font-weight:600;cursor:pointer;font-size:.85rem;transition:all .15s;}
  .btn-primary{background:var(--accent);color:#000;}.btn-primary:hover{opacity:.85;}
  .btn-secondary{background:var(--surface);color:var(--text);border:1px solid var(--border);}.btn-secondary:hover{border-color:var(--accent);}
  
  .grid{display:grid;grid-template-columns:1fr;gap:1.25rem;padding:2rem;}
  @media(min-width:900px){.grid{grid-template-columns:1fr 1fr;}}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:.75rem;padding:1.25rem;position:relative;overflow:hidden;}
  .card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent),var(--accent2));}
  .card h2{font-size:.9rem;color:var(--muted);margin-bottom:.75rem;}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem;padding:1rem 2rem;}
  .stat{background:var(--surface);border:1px solid var(--border);border-radius:.5rem;padding:1rem;text-align:center;}
  .stat .val{font-size:1.5rem;font-weight:700;color:var(--accent);}
  .stat .lbl{font-size:.7rem;color:var(--muted);margin-top:.25rem;text-transform:uppercase;letter-spacing:.05em;}
  .spinner{display:none;width:1.2rem;height:1.2rem;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite;}
  @keyframes spin{to{transform:rotate(360deg)}}
  #status{font-size:.75rem;color:var(--muted);padding:0 2rem;margin-top:-.5rem;}
  .hidden { display: none !important; }
</style>
</head>
<body>

<nav class="navbar">
  <h1>⚙ Dra-Washer Monitor</h1>
  <div style="display:flex;align-items:center;gap:1rem;">
    <div class="spinner" id="spinner"></div>
    <span class="pill" id="modePill">Historical</span>
    <div style="display:flex;align-items:center;gap:0.5rem;font-weight:600;font-size:0.85rem;">
      <div id="statusDot" style="width:8px;height:8px;border-radius:50%;background:#22c55e;transition:background 0.3s;"></div>
      <span id="statusText" style="color:#22c55e;transition:color 0.3s;">Online</span>
    </div>
  </div>
</nav>

<div class="tabs">
  <button class="tab-btn active" id="tabLive" onclick="switchTab('live')">Live</button>
  <button class="tab-btn" id="tabHist" onclick="switchTab('hist')">Historical Data</button>
  <button class="tab-btn" id="tabMl" onclick="switchTab('ml')">ML Training</button>
  <button class="tab-btn" id="tabDevice" onclick="switchTab('device')">Device Info</button>
</div>

<div class="controls">
  <div class="field">
    <label>Device ID</label>
    <input id="deviceId" value="ESP32 Washer Monitor" placeholder="device id">
  </div>
  <div class="field" id="fieldFrom">
    <label>FROM</label>
    <input id="from" type="datetime-local">
  </div>
  <div class="field" id="fieldTo">
    <label>TO</label>
    <input id="to" type="datetime-local">
  </div>
  <div class="field" id="fieldState">
    <label>State</label>
    <select id="stateFilter"><option value="">All</option><option>IDLE</option><option>RUNNING</option></select>
  </div>
  <div class="field">
    <label>Limit</label>
    <input id="limit" type="number" value="1000" min="1" max="30000">
    <span style="font-size: 0.65rem; color: var(--muted); display: block; margin-top: 0.25rem;">(Raw only)</span>

  </div>
  <div class="field hidden" id="fieldAggregate">
    <label>Aggregate By</label>
    <select id="aggregateLevel">
      <option value="">Raw Data</option>
      <option value="1m">1 Minute</option>
      <option value="5m">5 Minutes</option>
      <option value="15m">15 Minutes</option>
      <option value="1h">1 Hour</option>
    </select>
  </div>
  <div class="field hidden" id="fieldRefresh">
    <label>Refresh Every</label>
    <select id="refreshRate" onchange="resetRefreshTimer()">
      <option value="5000">5 Seconds</option>
      <option value="10000" selected>10 Seconds</option>
      <option value="30000">30 Seconds</option>
      <option value="60000">1 Minute</option>
    </select>
  </div>
  <div class="field" style="flex-direction:row; align-items:center; gap:0.5rem; justify-content:center; padding-bottom:0.4rem;">
    <input type="checkbox" id="showStateBg" checked onchange="loadData()">
    <label style="margin:0; text-transform:none; font-size:0.85rem; cursor:pointer;" for="showStateBg">Backgrounds</label>
  </div>
  <button class="btn btn-primary" id="btnLoad" onclick="loadData()">Load Data</button>
  <button class="btn btn-secondary" id="btnDownload" onclick="downloadCSV()">⬇ CSV</button>
</div>


<div class="controls hidden" id="mlControls" style="flex-direction:column; align-items:center; gap: 1rem;">
  <div class="field" style="flex-direction:row; align-items:center; justify-content:center; gap: 1rem;">
    <label>Label Selection:</label>
    <input type="radio" name="subState" id="ssIdle" value="IDLE" checked><label for="ssIdle" style="cursor:pointer; margin:0;">IDLE</label>
    <input type="radio" name="subState" id="ssWash" value="WASH"><label for="ssWash" style="cursor:pointer; margin:0; margin-right:0.5rem;">WASH</label>
    <input type="radio" name="subState" id="ssSpin" value="SPINDRY"><label for="ssSpin" style="cursor:pointer; margin:0;">SPINDRY</label>
  </div>
  <div style="display:flex; gap:0.75rem; flex-wrap:wrap; justify-content:center;">
    <button class="btn btn-primary" onclick="loadData()">📂 Load Data</button>
    <button class="btn btn-primary" onclick="applyLabel()">Apply Label</button>
    <button class="btn btn-secondary" onclick="clearSelection()">Clear Selection</button>
    <button class="btn btn-secondary" onclick="downloadLabelledCSV()">⬇ Labelled CSV</button>
  </div>
</div>

<div class="controls hidden" id="deviceControls" style="flex-direction:column; align-items:stretch; gap: 1rem;">
  <style>
    .device-status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }
    .device-card { background: var(--surface); border: 1px solid var(--border); border-radius: 0.5rem; padding: 1rem; }
    .device-card h3 { font-size: 0.85rem; color: var(--muted); margin: 0 0 0.5rem 0; text-transform: uppercase; letter-spacing: 0.05em; }
    .device-card .value { font-size: 1.5rem; font-weight: 700; color: var(--accent); margin: 0.25rem 0; }
    .device-card .label { font-size: 0.75rem; color: var(--muted); }
    .timeline-toggle { display: flex; gap: 0.5rem; margin: 1rem 0; }
    .timeline-toggle button { padding: 0.4rem 0.8rem; font-size: 0.85rem; background: var(--border); border: 1px solid var(--border); color: var(--text); border-radius: 0.375rem; cursor: pointer; transition: all 0.2s; }
    .timeline-toggle button.active { background: var(--accent); color: #000; }
    #deviceTimeline { width: 100%; height: 300px; }
    #wifiChart { width: 100%; height: 250px; margin-top: 1rem; }
    .uptime-high { color: #22c55e; } .uptime-medium { color: #f59e0b; } .uptime-low { color: #ef4444; }
  </style>

  <!-- Device Health Status Cards -->
  <div class="device-status-grid" id="deviceHealthCards">
    <div class="device-card">
      <h3>Device Status</h3>
      <div class="value" id="devStatus">—</div>
      <div class="label" id="devLastContact">—</div>
    </div>
    <div class="device-card">
      <h3>Uptime</h3>
      <div class="value" id="devUptime">—</div>
      <div class="label" id="devUptimePercent">—</div>
    </div>
    <div class="device-card">
      <h3>Boot Count</h3>
      <div class="value" id="devBootCount">—</div>
      <div class="label" id="devLastBoot">—</div>
    </div>
    <div class="device-card">
      <h3>WiFi Signal</h3>
      <div class="value" id="devSignal">—</div>
      <div class="label"><span id="devSignalStats">—</span></div>
    </div>
  </div>

  <!-- Timeline Toggle -->
  <div>
    <label style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); display: block; margin-bottom: 0.5rem;">Device Timeline</label>
    <div class="timeline-toggle">
      <button class="btn-timeline-toggle active" id="btnTimelineVisual" onclick="toggleTimelineView('visual')">Visual Timeline</button>
      <button class="btn-timeline-toggle" id="btnTimelineList" onclick="toggleTimelineView('list')">Event Log</button>
    </div>
    <div id="deviceTimeline" style="background: var(--surface); border: 1px solid var(--border); border-radius: 0.375rem; padding: 1rem;"></div>
    <div id="deviceEventLog" hidden style="background: var(--surface); border: 1px solid var(--border); border-radius: 0.375rem; padding: 1rem;"></div>
  </div>

  <!-- WiFi Chart -->
  <div>
    <label style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); display: block; margin-bottom: 0.5rem;">WiFi Signal Strength</label>
    <div id="wifiChart" style="background: var(--surface); border: 1px solid var(--border); border-radius: 0.375rem;"></div>
  </div>

  <!-- Refresh Button -->
  <button class="btn btn-primary" onclick="loadDeviceHealth()" style="align-self: flex-start;">🔄 Refresh Device Info</button>
</div>

<p id="status"></p>

<div class="stats">
  <div class="stat"><div class="val" id="statTotal">—</div><div class="lbl">Total Samples</div></div>
  <div class="stat"><div class="val" id="statMotionAvg">—</div><div class="lbl">Avg Motion</div></div>
  <div class="stat"><div class="val" id="statMotionMax">—</div><div class="lbl">Max Motion</div></div>
  <div class="stat"><div class="val" id="statRunning">—</div><div class="lbl">Running %</div></div>
  <div class="stat"><div class="val" id="statUnlabelled">—</div><div class="lbl">Unlabelled</div></div>
  <div class="stat"><div class="val" id="statLastReceived">—</div><div class="lbl">Last Received</div></div>
</div>

<div class="grid">
  <div class="card"><h2>Motion Score</h2><canvas id="chartMotion"></canvas></div>
  <div class="card"><h2>Accelerometer (ax, ay, az)</h2><canvas id="chartAccel"></canvas></div>
</div>

<script>
const BASE = window.location.origin;
let motionChart, accelChart;
let currentTab = 'live';
let refreshInterval = null;
let lastKnownStatus = 'online'; // Track previous status to avoid flipping
let timelineView = 'visual'; // Track timeline view mode
let wifiChart = null;

// =========================================================================
// Device Health Functions
// =========================================================================

async function loadDeviceHealth() {
  const deviceId = document.getElementById('deviceId').value;
  const status = document.getElementById('status');

  if (!deviceId) {
    status.textContent = 'Error: Device ID required';
    return;
  }

  status.textContent = 'Loading device health...';

  try {
    const url = `${BASE}/api/device-health?device_id=${encodeURIComponent(deviceId)}&days=7`;
    const res = await fetch(url);

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`HTTP ${res.status}: ${err}`);
    }

    const health = await res.json();

    if (health.error) {
      throw new Error(health.error);
    }

    renderDeviceHealth(health);
    renderBootTimeline(health.boot_events, health.data_gaps);
    renderWiFiChart(health);

    status.textContent = 'Device health loaded @ ' + new Date().toLocaleTimeString();
  } catch (e) {
    status.textContent = 'Error: ' + e.message;
  }
}

function renderDeviceHealth(health) {
  const devStatus = document.getElementById('devStatus');
  const devLastContact = document.getElementById('devLastContact');
  const devUptime = document.getElementById('devUptime');
  const devUptimePercent = document.getElementById('devUptimePercent');
  const devBootCount = document.getElementById('devBootCount');
  const devLastBoot = document.getElementById('devLastBoot');
  const devSignal = document.getElementById('devSignal');
  const devSignalStats = document.getElementById('devSignalStats');

  // Status and last contact
  const lastContact = health.last_contact;
  const statusClass = lastContact.status === 'online' ? 'uptime-high' : 'uptime-low';
  devStatus.textContent = lastContact.status.toUpperCase();
  devStatus.className = statusClass;
  devLastContact.textContent = lastContact.age_seconds < 300
    ? 'Just now'
    : (lastContact.age_seconds < 3600 ? Math.floor(lastContact.age_seconds / 60) + ' mins ago' : Math.floor(lastContact.age_seconds / 3600) + ' hrs ago');

  // Uptime
  const uptimeHours = Math.floor(health.total_uptime_seconds / 3600);
  const uptimeMins = Math.floor((health.total_uptime_seconds % 3600) / 60);
  devUptime.textContent = uptimeHours + 'h ' + uptimeMins + 'm';
  devUptimePercent.textContent = health.uptime_percent + '% uptime';

  // Color code uptime
  if (health.uptime_percent >= 95) {
    devUptime.className = 'uptime-high';
  } else if (health.uptime_percent >= 85) {
    devUptime.className = 'uptime-medium';
  } else {
    devUptime.className = 'uptime-low';
  }

  // Boot count
  devBootCount.textContent = health.total_boots;
  if (health.boot_events.length > 0) {
    const lastBoot = health.boot_events[health.boot_events.length - 1];
    devLastBoot.textContent = 'Last: ' + new Date(lastBoot.boot_ts).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  } else {
    devLastBoot.textContent = 'No boots recorded';
  }

  // WiFi signal
  const signal = health.wifi_signal_stats;
  devSignal.textContent = signal.avg_rssi + ' dBm';

  // Color code signal
  if (signal.avg_rssi > -70) {
    devSignal.className = 'uptime-high';
  } else if (signal.avg_rssi > -80) {
    devSignal.className = 'uptime-medium';
  } else {
    devSignal.className = 'uptime-low';
  }

  devSignalStats.textContent = 'Range: ' + signal.min_rssi + ' to ' + signal.max_rssi + ' dBm';
}

function renderBootTimeline(boots, gaps) {
  if (timelineView === 'visual') {
    renderTimelineVisual(boots, gaps);
  } else {
    renderEventLog(boots, gaps);
  }
}

function renderTimelineVisual(boots, gaps) {
  const container = document.getElementById('deviceTimeline');
  if (!container) return;

  const html = `
    <div style="font-size: 0.85rem; padding: 0.5rem;">
      <div style="display: flex; align-items: center; margin-bottom: 1rem;">
        <div style="flex: 1;">
          <div style="display: flex; height: 30px; background: var(--border); border-radius: 0.25rem; overflow: hidden; position: relative;">
            ${boots.map((boot, idx) => {
              const prevEnd = idx === 0 ? boots[0].boot_ts : boots[idx - 1].last_activity;
              const bootStart = new Date(boot.boot_ts);
              const bootEnd = new Date(boot.last_activity);
              const percent = ((bootEnd - bootStart) / (new Date(boots[boots.length - 1].last_activity) - new Date(boots[0].boot_ts))) * 100;
              return `<div style="background: #22c55e; flex: ${percent}; position: relative; border-right: 1px solid var(--bg);" title="Boot ${idx + 1}: ${bootStart.toLocaleString()}"></div>`;
            }).join('')}
          </div>
        </div>
      </div>
      <div style="font-size: 0.75rem; color: var(--muted);">
        <strong>Boots:</strong> ${boots.length} · <strong>Gaps:</strong> ${gaps.length}
      </div>
    </div>
  `;
  container.innerHTML = html;
}

function renderEventLog(boots, gaps) {
  const container = document.getElementById('deviceEventLog');
  if (!container) return;

  // Combine and sort all events
  const events = [
    ...boots.map(b => ({ type: 'boot', data: b })),
    ...gaps.map(g => ({ type: 'gap', data: g }))
  ].sort((a, b) => {
    const aTime = a.type === 'boot' ? a.data.boot_ts : a.data.start;
    const bTime = b.type === 'boot' ? b.data.boot_ts : b.data.start;
    return new Date(aTime) - new Date(bTime);
  });

  const html = `
    <table style="width: 100%; font-size: 0.85rem; border-collapse: collapse;">
      <thead>
        <tr style="border-bottom: 1px solid var(--border);">
          <th style="text-align: left; padding: 0.5rem; color: var(--muted); font-weight: 500;">Time</th>
          <th style="text-align: left; padding: 0.5rem; color: var(--muted); font-weight: 500;">Event</th>
          <th style="text-align: left; padding: 0.5rem; color: var(--muted); font-weight: 500;">Details</th>
        </tr>
      </thead>
      <tbody>
        ${events.map(event => {
          if (event.type === 'boot') {
            const b = event.data;
            return `<tr style="border-bottom: 1px solid var(--border);">
              <td style="padding: 0.5rem;">${new Date(b.boot_ts).toLocaleString()}</td>
              <td style="padding: 0.5rem;"><span style="color: #22c55e;">●</span> Boot</td>
              <td style="padding: 0.5rem; font-size: 0.75rem; color: var(--muted);">${b.samples_count} samples</td>
            </tr>`;
          } else {
            const g = event.data;
            const mins = Math.floor(g.duration_seconds / 60);
            const secs = g.duration_seconds % 60;
            return `<tr style="border-bottom: 1px solid var(--border);">
              <td style="padding: 0.5rem;">${new Date(g.start).toLocaleString()}</td>
              <td style="padding: 0.5rem;"><span style="color: #ef4444;">●</span> Gap</td>
              <td style="padding: 0.5rem; font-size: 0.75rem; color: var(--muted);">${mins}m ${secs}s</td>
            </tr>`;
          }
        }).join('')}
      </tbody>
    </table>
  `;
  container.innerHTML = html;
}

function toggleTimelineView(view) {
  timelineView = view;

  const btnVisual = document.getElementById('btnTimelineVisual');
  const btnList = document.getElementById('btnTimelineList');
  const timeline = document.getElementById('deviceTimeline');
  const eventLog = document.getElementById('deviceEventLog');

  if (view === 'visual') {
    btnVisual.classList.add('active');
    btnList.classList.remove('active');
    timeline.hidden = false;
    eventLog.hidden = true;
  } else {
    btnVisual.classList.remove('active');
    btnList.classList.add('active');
    timeline.hidden = true;
    eventLog.hidden = false;
  }
}

function renderWiFiChart(health) {
  // This is a placeholder - WiFi data points would come from aggregating raw samples
  // For now, show just the stats
  const container = document.getElementById('wifiChart');
  if (!container) return;

  const stats = health.wifi_signal_stats;
  const html = `
    <div style="padding: 1rem; text-align: center;">
      <div style="font-size: 1.25rem; color: var(--accent); font-weight: 700; margin-bottom: 0.5rem;">
        ${stats.avg_rssi} dBm
      </div>
      <div style="font-size: 0.85rem; color: var(--muted);">
        Average WiFi Signal Strength
      </div>
      <div style="margin-top: 1rem; display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; font-size: 0.8rem;">
        <div>
          <div style="color: var(--muted);">Min</div>
          <div style="color: var(--text);">${stats.min_rssi} dBm</div>
        </div>
        <div>
          <div style="color: var(--muted);">Max</div>
          <div style="color: var(--text);">${stats.max_rssi} dBm</div>
        </div>
      </div>
      <div style="margin-top: 1rem; font-size: 0.75rem; color: var(--muted);">
        ${stats.samples_with_signal} measurements
      </div>
    </div>
  `;
  container.innerHTML = html;
}

function initCharts() {
  const shared = {responsive:true,animation:{duration:300},scales:{
    x:{ticks:{color:'#94a3b8',maxTicksLimit:12},grid:{color:'#1e293b'}},
    y:{ticks:{color:'#94a3b8'},grid:{color:'#1e293b'}}},
    plugins:{legend:{labels:{color:'#e2e8f0'}}}};

  motionChart = new Chart(document.getElementById('chartMotion'),{
    type:'line',
    data:{labels:[],datasets:[
      {label:'Motion Score',data:[],borderColor:'#38bdf8',fill:false,tension:.3,pointRadius:0},
      {label:'10s Avg Motion',data:[],borderColor:'#f59e0b',hidden:false,tension:.3,pointRadius:0}
    ]},
    options:{...shared},
    plugins: [{
      
      id: 'customStateBg',
      beforeDraw: (chart) => {
        const ctx = chart.canvas.getContext('2d');
        const xAxis = chart.scales.x;
        const yAxis = chart.scales.y;
        const meta = chart.getDatasetMeta(0);
        if (!meta.data || meta.data.length === 0 || !window.loadedRows || window.loadedRows.length === 0) return;
        
        // Draw overall_state bg
        const showBg = document.getElementById('showStateBg') && document.getElementById('showStateBg').checked;
        if (showBg) {
            ctx.save();
            let startIdx = 0;
            let currentState = (window.loadedRows[0].overall_state || window.loadedRows[0].state || "").toUpperCase().trim();

            for (let i = 1; i <= window.loadedRows.length; i++) {
              const nextState = i < window.loadedRows.length ? (window.loadedRows[i].overall_state || window.loadedRows[i].state || "").toUpperCase().trim() : "END";
              
              if (i === window.loadedRows.length || nextState !== currentState) {
                let leftX = startIdx === 0 ? xAxis.left : meta.data[startIdx].x;
                let rightX = i === window.loadedRows.length ? xAxis.right : (meta.data[i] ? meta.data[i].x : xAxis.right);

                if (currentState === 'RUNNING') ctx.fillStyle = 'rgba(239, 68, 68, 0.2)'; 
                else if (currentState === 'IDLE') ctx.fillStyle = 'rgba(34, 197, 94, 0.2)'; 
                else ctx.fillStyle = 'transparent';
                
                if (ctx.fillStyle !== 'transparent') ctx.fillRect(leftX, yAxis.top, rightX - leftX, yAxis.bottom - yAxis.top);
                startIdx = i; currentState = nextState;
              }
            }
            ctx.restore();
        }
        
        // Draw sub_state overlays
        ctx.save();
        let subStartIdx = 0;
        let currentSubState = (window.loadedRows[0].sub_state || "").toUpperCase().trim();
        for (let i = 1; i <= window.loadedRows.length; i++) {
            const nextSub = i < window.loadedRows.length ? (window.loadedRows[i].sub_state || "").toUpperCase().trim() : "END";
            if (i === window.loadedRows.length || nextSub !== currentSubState) {
                if (currentSubState === 'WASH' || currentSubState === 'SPINDRY') {
                    let leftX = subStartIdx === 0 ? xAxis.left : meta.data[subStartIdx].x;
                    let rightX = i === window.loadedRows.length ? xAxis.right : (meta.data[i] ? meta.data[i].x : xAxis.right);
                    ctx.fillStyle = currentSubState === 'WASH' ? 'rgba(56, 189, 248, 0.4)' : 'rgba(245, 158, 11, 0.4)';
                    ctx.fillRect(leftX, yAxis.bottom - 20, rightX - leftX, 20);
                }
                subStartIdx = i; currentSubState = nextSub;
            }
        }
        ctx.restore();

        // Draw selection overlay
        if (currentTab === 'ml' && selectionStartIdx !== null && selectionEndIdx !== null) {
           const sIdx = Math.min(selectionStartIdx, selectionEndIdx);
           const eIdx = Math.max(selectionStartIdx, selectionEndIdx);
           const leftX = meta.data[sIdx].x;
           const rightX = meta.data[eIdx].x;
           ctx.save();
           ctx.fillStyle = 'rgba(255, 255, 255, 0.2)';
           ctx.fillRect(leftX, yAxis.top, rightX - leftX, yAxis.bottom - yAxis.top);
           ctx.strokeStyle = 'rgba(255, 255, 255, 0.8)';
           ctx.lineWidth = 2;
           ctx.strokeRect(leftX, yAxis.top, rightX - leftX, yAxis.bottom - yAxis.top);
           ctx.restore();
        }
      }
    }]
  });

  accelChart = new Chart(document.getElementById('chartAccel'),{
    type:'line',
    data:{labels:[],datasets:[
      {label:'ax',data:[],borderColor:'#f472b6',tension:.3,pointRadius:0},
      {label:'ay',data:[],borderColor:'#34d399',tension:.3,pointRadius:0},
      {label:'az',data:[],borderColor:'#fbbf24',tension:.3,pointRadius:0}
    ]},
    options:{...shared}
  });
}

function populateChartsAggregated(rows) {
  // For aggregated data, show avg with min/max bands
  const labels = rows.map(r=> new Date(r.ts_ms).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}));

  // Motion chart with min/max bands
  motionChart.data.labels = labels;
  motionChart.data.datasets[0].label = 'Motion Avg';
  motionChart.data.datasets[0].data = rows.map(r=>r.motion_score_avg);
  motionChart.data.datasets[0].borderColor = '#38bdf8';
  motionChart.data.datasets[0].pointRadius = 2;
  motionChart.data.datasets[0].fill = '+1'; // Fill to next dataset

  // Min/max band for motion (visual only, stacked dataset)
  motionChart.data.datasets[1].label = 'Motion Range';
  motionChart.data.datasets[1].data = rows.map(r=>r.motion_score_max - r.motion_score_min);
  motionChart.data.datasets[1].borderColor = 'transparent';
  motionChart.data.datasets[1].backgroundColor = 'rgba(56, 189, 248, 0.2)';
  motionChart.data.datasets[1].fill = true;
  motionChart.data.datasets[1].pointRadius = 0;
  motionChart.data.datasets[1].hidden = false;

  motionChart.update('none');

  // Accelerometer chart with min/max bands
  accelChart.data.labels = labels;
  accelChart.data.datasets[0].data = rows.map(r=>r.ax_avg);
  accelChart.data.datasets[0].pointRadius = 2;
  accelChart.data.datasets[1].data = rows.map(r=>r.ay_avg);
  accelChart.data.datasets[1].pointRadius = 2;
  accelChart.data.datasets[2].data = rows.map(r=>r.az_avg);
  accelChart.data.datasets[2].pointRadius = 2;

  accelChart.update('none');
}

function populateChartsRaw(rows) {
  // For raw data, show motion score with 10s average
  const labels = rows.map(r=> new Date(r.ts_ms).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}));
  motionChart.data.labels = labels;
  motionChart.data.customStateArray = rows.map(r=>r.state);
  motionChart.data.datasets[0].label = 'Motion Score';
  motionChart.data.datasets[0].data = rows.map(r=>r.motion_score);
  motionChart.data.datasets[0].borderColor = '#38bdf8';
  motionChart.data.datasets[0].pointRadius = 0;
  motionChart.data.datasets[0].fill = false;

  motionChart.data.datasets[1].label = '10s Avg Motion';
  motionChart.data.datasets[1].data = rows.map(r=>r.motion_avg || null);
  motionChart.data.datasets[1].borderColor = '#f59e0b';
  motionChart.data.datasets[1].hidden = false;
  motionChart.data.datasets[1].pointRadius = 0;
  motionChart.data.datasets[1].fill = false;

  motionChart.update('none');

  accelChart.data.labels = labels;
  accelChart.data.datasets[0].data = rows.map(r=>r.ax);
  accelChart.data.datasets[0].pointRadius = 0;
  accelChart.data.datasets[1].data = rows.map(r=>r.ay);
  accelChart.data.datasets[1].pointRadius = 0;
  accelChart.data.datasets[2].data = rows.map(r=>r.az);
  accelChart.data.datasets[2].pointRadius = 0;
  accelChart.update('none');
}

function switchTab(tab) {
  currentTab = tab;
  const isLive = (tab === 'live');
  const isMl = (tab === 'ml');
  const isDevice = (tab === 'device');

  document.getElementById('tabHist').classList.toggle('active', tab === 'hist');
  document.getElementById('tabLive').classList.toggle('active', isLive);
  document.getElementById('tabMl').classList.toggle('active', isMl);
  document.getElementById('tabDevice').classList.toggle('active', isDevice);

  document.getElementById('fieldFrom').classList.toggle('hidden', isLive || isDevice);
  document.getElementById('fieldTo').classList.toggle('hidden', isLive || isDevice);
  document.getElementById('fieldState').classList.toggle('hidden', isLive || isDevice);
  document.getElementById('fieldAggregate').classList.toggle('hidden', isLive || isDevice);

  const dlBtn = document.getElementById('btnDownload');
  if(dlBtn) dlBtn.classList.toggle('hidden', isLive || isMl || isDevice);

  document.getElementById('fieldRefresh').classList.toggle('hidden', !isLive);

  const mlCtrl = document.getElementById('mlControls');
  if(mlCtrl) mlCtrl.classList.toggle('hidden', !isMl);

  const devCtrl = document.getElementById('deviceControls');
  if(devCtrl) devCtrl.classList.toggle('hidden', !isDevice);

  document.getElementById('btnLoad').classList.toggle('hidden', isMl || isDevice);

  const modePill = document.getElementById('modePill');
  modePill.textContent = isLive ? 'LIVE' : (isMl ? 'ML TRAINING' : (isDevice ? 'DEVICE INFO' : 'Historical'));
  modePill.classList.toggle('live', isLive);

  if (isLive) {
    loadData();
    resetRefreshTimer();
  } else if (isDevice) {
    if (refreshInterval) {
      clearInterval(refreshInterval);
      refreshInterval = null;
    }
    loadDeviceHealth();
  } else {
    if (refreshInterval) {
      clearInterval(refreshInterval);
      refreshInterval = null;
    }
    // If we just clicked ML, reload to match filter or just rely on existing data
    if (isMl) {
       clearSelection();
       updateChartForML();
    } else {
       loadData();
    }
  }
}


function resetRefreshTimer() {
  if (refreshInterval) clearInterval(refreshInterval);
  if (currentTab === 'live') {
    const rate = parseInt(document.getElementById('refreshRate').value);
    refreshInterval = setInterval(loadData, rate);
  }
}


let selectionStartIdx = null;
let selectionEndIdx = null;
let isDragging = false;

function updateChartForML() {
   motionChart.update('none');
}

function clearSelection() {
   selectionStartIdx = null;
   selectionEndIdx = null;
   motionChart.update('none');
}

async function applyLabel() {
   if (selectionStartIdx === null || selectionEndIdx === null) {
      alert("Please select a region on the chart first.");
      return;
   }
   const sIdx = Math.min(selectionStartIdx, selectionEndIdx);
   const eIdx = Math.max(selectionStartIdx, selectionEndIdx);
   
   const meta = motionChart.getDatasetMeta(0);
   if (!meta.data || sIdx < 0 || eIdx >= meta.data.length || !window.loadedRows) return;
   
   const labelVal = document.querySelector('input[name="subState"]:checked').value;
   const rowKeys = [];
   for (let i = sIdx; i <= eIdx; i++) {
        const r = window.loadedRows[i];
        if (r && r.ts_ms) {
            rowKeys.push(r.boot_id + "_" + r.seq_no + "_" + r.ts_ms);
            r.sub_state = labelVal;
        }
   }
   
   if (rowKeys.length === 0) return;
   
   document.getElementById('status').textContent = 'Applying label to ' + rowKeys.length + ' samples...';
   
   try {
       const res = await fetch(BASE + '/api/label', {
           method: 'PATCH',
           headers: { 'Content-Type': 'application/json' },
           body: JSON.stringify({
               device_id: document.getElementById('deviceId').value,
               row_keys: rowKeys,
               sub_state: labelVal
           })
       });
       if (!res.ok) throw new Error("Failed to label");
       
       clearSelection();
       updateStats(window.loadedRows, window.isAggregatedData);
       document.getElementById('status').textContent = 'Successfully labelled ' + rowKeys.length + ' samples.';
   } catch (e) {
       document.getElementById('status').textContent = 'Label Error: ' + e.message;
   }
}

function downloadLabelledCSV() {
   if (!window.loadedRows) return;
   const rows = window.loadedRows.filter(r => r.sub_state);
   if (rows.length === 0) {
      alert("No data is labelled yet.");
      return;
   }
   const newline = String.fromCharCode(10);
   const header = Object.keys(rows[0]).join(",") + newline;
   const csv = rows.map(r => Object.values(r).join(",")).join(newline);
   const blob = new Blob([header + csv], { type: "text/csv" });
   const url = window.URL.createObjectURL(blob);
   const a = document.createElement("a");
   a.href = url;
   a.download = "washer_labelled_samples.csv";
   a.click();
   window.URL.revokeObjectURL(url);
}

function updateStats(rows, isAggregated) {
    if(!rows || !rows.length) return;

    if (isAggregated) {
        // Handle aggregated data
        const totalSamples = rows.reduce((sum, r) => sum + (r.sample_count || 0), 0);
        document.getElementById('statTotal').textContent = totalSamples;

        // Max motion: highest max across all buckets
        const maxMotions = rows.map(r => r.motion_score_max);
        document.getElementById('statMotionMax').textContent = Math.max(...maxMotions).toFixed(1);

        // Avg motion: average of all motion_score_avg values
        const avgMotions = rows.map(r => r.motion_score_avg);
        const overallAvg = avgMotions.reduce((a,b)=>a+b,0) / avgMotions.length;
        document.getElementById('statMotionAvg').textContent = overallAvg.toFixed(1);

        // Running %: weighted average
        let totalRunningCount = 0;
        rows.forEach(r => {
            const runningInBucket = (r.running_percent || 0) * (r.sample_count || 0) / 100;
            totalRunningCount += runningInBucket;
        });
        const runningPercent = totalSamples > 0 ? (totalRunningCount / totalSamples * 100) : 0;
        document.getElementById('statRunning').textContent = runningPercent.toFixed(1) + '%';

        // Unlabelled: sum of empty sub_state across buckets
        if (document.getElementById('statUnlabelled')) {
            const unlabelledCount = rows.reduce((sum, r) => sum + (r.sub_state_modes[''] || 0), 0);
            document.getElementById('statUnlabelled').textContent = unlabelledCount;
        }
    } else {
        // Handle raw data (original logic)
        document.getElementById('statTotal').textContent = rows.length;
        const motions = rows.map(r=>r.motion_score);
        const avg = motions.reduce((a,b)=>a+b,0)/motions.length;
        document.getElementById('statMotionAvg').textContent = avg.toFixed(1);
        document.getElementById('statMotionMax').textContent = Math.max(...motions).toFixed(1);
        const running = rows.filter(r=>(r.overall_state||r.state)==='RUNNING').length;
        document.getElementById('statRunning').textContent = (running/rows.length*100).toFixed(1)+'%';

        if (document.getElementById('statUnlabelled')) {
           const unlabelled = rows.filter(r=>!r.sub_state).length;
           document.getElementById('statUnlabelled').textContent = unlabelled;
        }
    }

    // Update last received timestamp and device status (works for both raw and aggregated)
    if (rows.length > 0) {
        // For aggregated data, we don't have received_at, so skip device status
        const lastRow = rows[rows.length - 1];
        if (lastRow.received_at) {
            const lastReceivedTime = new Date(lastRow.received_at);
            const formattedTime = lastReceivedTime.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
            document.getElementById('statLastReceived').textContent = formattedTime;

            // Update device status with hysteresis to prevent flipping
            const now = new Date();
            const ageMs = now - lastReceivedTime;
            const ageMins = ageMs / 60000;
            const dot = document.getElementById('statusDot');
            const statusText = document.getElementById('statusText');

            let newStatus = lastKnownStatus; // Keep previous status by default

            // Go online if fresh data (< 5 mins)
            if (ageMins < 5) {
                newStatus = 'online';
            }
            // Go offline only if stale (> 10 mins)
            else if (ageMins > 10) {
                newStatus = 'offline';
            }

            // Update UI only if status changed
            if (newStatus !== lastKnownStatus) {
                lastKnownStatus = newStatus;
                if (newStatus === 'online') {
                    dot.style.background = '#22c55e';
                    statusText.textContent = 'Online';
                    statusText.style.color = '#22c55e';
                } else {
                    dot.style.background = '#ef4444';
                    statusText.textContent = 'Offline';
                    statusText.style.color = '#ef4444';
                }
            }
        }
    }
}

function buildQuery() {
  const p = new URLSearchParams();
  const d = document.getElementById('deviceId').value;
  if(d) p.set('device_id', d);

  if (currentTab === 'live') {
    const oneHrAgo = new Date(Date.now() - 3600 * 1000);
    p.set('from', oneHrAgo.toISOString());
  } else {
    const f = document.getElementById('from').value;
    if(f) p.set('from', new Date(f).toISOString());
    const t = document.getElementById('to').value;
    if(t) p.set('to', new Date(t).toISOString());
    const agg = document.getElementById('aggregateLevel').value;
    if(agg) p.set('aggregate', agg);
  }

  const st = document.getElementById('stateFilter').value;
  if(st) p.set('state', st);
  p.set('limit', document.getElementById('limit').value || '1000');
  return p;
}

async function loadData() {
  const sp = document.getElementById('spinner');
  const st = document.getElementById('status');
  sp.style.display = 'inline-block';
  st.textContent = currentTab === 'live' ? 'Refreshing live data...' : 'Loading...';

  try {
    const q = buildQuery();
    q.set('format','json');
    const url = BASE + '/api/samples?' + q;
    const res = await fetch(url);

    if (!res.ok) {
       const text = await res.text();
       throw new Error(`HTTP ${res.status}: ${text}`);
    }

    const data = await res.json();
    const rows = data.samples || [];
    const isAggregated = data.aggregated === true;

    rows.reverse();
    window.loadedRows = rows;
    window.isAggregatedData = isAggregated;

    updateStats(rows, isAggregated);

    let sampleLabel = isAggregated ? 'buckets' : 'samples';
    st.textContent = rows.length + ' ' + sampleLabel + ' loaded @ ' + new Date().toLocaleTimeString();

    if (currentTab === 'live') {
      const rate = document.getElementById('refreshRate').value / 1000;
      st.textContent += ' (Next refresh in ' + rate + 's)';
    }

    // Populate charts based on data type
    if (isAggregated) {
      populateChartsAggregated(rows);
    } else {
      populateChartsRaw(rows);
    }
  } catch(e) {
    st.textContent = 'Error: '+e.message;
  } finally {
    sp.style.display = 'none';
  }
}

function downloadCSV() {
  const q = buildQuery();
  q.set('format','csv');
  window.open(BASE+'/api/samples?'+q, '_blank');
}


  // Add chart interaction for ML selection
  const canvas = document.getElementById('chartMotion');
  canvas.addEventListener('mousedown', (e) => {
     if (currentTab !== 'ml') return;
     const rect = canvas.getBoundingClientRect();
     const x = e.clientX - rect.left;
     
     const meta = motionChart.getDatasetMeta(0);
     let closestIndex = 0;
     let minDiff = Infinity;
     for (let i = 0; i < meta.data.length; i++) {
        const dx = Math.abs(meta.data[i].x - x);
        if (dx < minDiff) { minDiff = dx; closestIndex = i; }
     }
     selectionStartIdx = closestIndex;
     selectionEndIdx = closestIndex;
     isDragging = true;
     motionChart.update('none');
  });
  
  canvas.addEventListener('mousemove', (e) => {
     if (!isDragging || currentTab !== 'ml') return;
     const rect = canvas.getBoundingClientRect();
     const x = e.clientX - rect.left;
     
     const meta = motionChart.getDatasetMeta(0);
     let closestIndex = 0;
     let minDiff = Infinity;
     for (let i = 0; i < meta.data.length; i++) {
        const dx = Math.abs(meta.data[i].x - x);
        if (dx < minDiff) { minDiff = dx; closestIndex = i; }
     }
     selectionEndIdx = closestIndex;
     motionChart.update('none');
  });
  
  canvas.addEventListener('mouseup', () => { isDragging = false; });

initCharts();
switchTab('live'); // Initialize with Live tab active
</script>
</body>
</html>"""


@app.route(route="dashboard", methods=["GET"])
def dashboard(req: func.HttpRequest) -> func.HttpResponse:
    """Serve a self-contained HTML dashboard."""
    return func.HttpResponse(DASHBOARD_HTML, mimetype="text/html")
