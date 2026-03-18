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
# GET /api/samples
# ---------------------------------------------------------------------------

@app.route(route="samples", methods=["GET"])
def samples(req: func.HttpRequest) -> func.HttpResponse:
    """
    Query stored samples.

    Query params:
        device_id  – filter by device (required for performance)
        since      – ISO timestamp lower bound
        until      – ISO timestamp upper bound
        state      – filter by washer state (IDLE / RUNNING)
        limit      – max rows to return (default 1000, max 10000)
        format     – 'json' (default) or 'csv'
    """
    device_id = req.params.get("device_id", "")
    since = req.params.get("since", "")
    until = req.params.get("until", "")
    state_filter = req.params.get("state", "")
    limit = min(int(req.params.get("limit", "1000")), 10000)
    fmt = req.params.get("format", "json").lower()

    # Build OData filter
    filters = []
    if device_id:
        filters.append(f"PartitionKey eq '{device_id}'")
    if state_filter:
        filters.append(f"overall_state eq '{state_filter}'")
    if since:
        filters.append(f"received_at ge '{since}'")
    if until:
        filters.append(f"received_at le '{until}'")

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
    return func.HttpResponse(
        json.dumps({"count": len(rows), "samples": rows}),
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
  <div style="display:flex;align-items:center;gap:0.5rem;">
    <div class="spinner" id="spinner"></div>
    <div style="display:flex;align-items:center;gap:0.5rem;">
      <div id="statusDot" style="width:10px;height:10px;border-radius:50%;background:#22c55e;transition:background 0.3s;"></div>
      <span class="pill" id="modePill">Historical</span>
    </div>
  </div>
</nav>

<div class="tabs">
  <button class="tab-btn active" id="tabLive" onclick="switchTab('live')">Live</button>
  <button class="tab-btn" id="tabHist" onclick="switchTab('hist')">Historical Data</button>
  <button class="tab-btn" id="tabMl" onclick="switchTab('ml')">ML Training</button>
</div>

<div class="controls">
  <div class="field">
    <label>Device ID</label>
    <input id="deviceId" value="ESP32 Washer Monitor" placeholder="device id">
  </div>
  <div class="field" id="fieldSince">
    <label>Since</label>
    <input id="since" type="datetime-local">
  </div>
  <div class="field" id="fieldUntil">
    <label>Until</label>
    <input id="until" type="datetime-local">
  </div>
  <div class="field" id="fieldState">
    <label>State</label>
    <select id="stateFilter"><option value="">All</option><option>IDLE</option><option>RUNNING</option></select>
  </div>
  <div class="field">
    <label>Limit</label>
    <input id="limit" type="number" value="1000" min="1" max="10000">
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


function switchTab(tab) {
  currentTab = tab;
  const isLive = (tab === 'live');
  const isMl = (tab === 'ml');
  
  document.getElementById('tabHist').classList.toggle('active', tab === 'hist');
  document.getElementById('tabLive').classList.toggle('active', isLive);
  document.getElementById('tabMl').classList.toggle('active', isMl);
  
  document.getElementById('fieldSince').classList.toggle('hidden', isLive);
  document.getElementById('fieldUntil').classList.toggle('hidden', isLive);
  document.getElementById('fieldState').classList.toggle('hidden', isLive);
  
  const dlBtn = document.getElementById('btnDownload');
  if(dlBtn) dlBtn.classList.toggle('hidden', isLive || isMl);
  
  document.getElementById('fieldRefresh').classList.toggle('hidden', !isLive);
  
  const mlCtrl = document.getElementById('mlControls');
  if(mlCtrl) mlCtrl.classList.toggle('hidden', !isMl);
  
  document.getElementById('btnLoad').classList.toggle('hidden', isMl);
  
  const modePill = document.getElementById('modePill');
  modePill.textContent = isLive ? 'LIVE' : (isMl ? 'ML TRAINING' : 'Historical');
  modePill.classList.toggle('live', isLive);

  if (isLive) {
    loadData();
    resetRefreshTimer();
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
       updateStats(window.loadedRows);
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

function updateStats(rows) {
    if(!rows || !rows.length) return;
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

    // Update last received timestamp and device status
    if (rows.length > 0) {
        const lastRow = rows[rows.length - 1];
        const lastReceivedTime = new Date(lastRow.received_at);
        const formattedTime = lastReceivedTime.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
        document.getElementById('statLastReceived').textContent = formattedTime;

        // Update device status indicator
        const now = new Date();
        const ageMs = now - lastReceivedTime;
        const ageMins = ageMs / 60000;
        const dot = document.getElementById('statusDot');

        if (ageMins < 2) {
            dot.style.background = '#22c55e'; // Green - online
        } else if (ageMins < 5) {
            dot.style.background = '#f59e0b'; // Orange - warning
        } else {
            dot.style.background = '#ef4444'; // Red - offline
        }
    }
}

function buildQuery() {
  const p = new URLSearchParams();
  const d = document.getElementById('deviceId').value;
  if(d) p.set('device_id', d);
  
  if (currentTab === 'live') {
    const oneHrAgo = new Date(Date.now() - 3600 * 1000);
    p.set('since', oneHrAgo.toISOString());
  } else {
    const s = document.getElementById('since').value;
    if(s) p.set('since', new Date(s).toISOString());
    const u = document.getElementById('until').value;
    if(u) p.set('until', new Date(u).toISOString());
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
    rows.reverse();
    window.loadedRows = rows;

    updateStats(rows);

    st.textContent = rows.length + ' samples loaded @ ' + new Date().toLocaleTimeString();

    if (currentTab === 'live') {
      const rate = document.getElementById('refreshRate').value / 1000;
      st.textContent += ' (Next refresh in ' + rate + 's)';
    }

    // charts
    const labels = rows.map(r=> new Date(r.ts_ms).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}));
    motionChart.data.labels = labels;
    motionChart.data.customStateArray = rows.map(r=>r.state);
    motionChart.data.datasets[0].data = rows.map(r=>r.motion_score);
    motionChart.data.datasets[1].data = rows.map(r=>r.motion_avg || null);
    motionChart.update('none'); // Update without animation for smoothness

    accelChart.data.labels = labels;
    accelChart.data.datasets[0].data = rows.map(r=>r.ax);
    accelChart.data.datasets[1].data = rows.map(r=>r.ay);
    accelChart.data.datasets[2].data = rows.map(r=>r.az);
    accelChart.update('none');
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
