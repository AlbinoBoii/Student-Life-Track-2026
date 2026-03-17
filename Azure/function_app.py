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
from azure.data.tables import TableServiceClient

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
            "ts_ms": ts_ms,
            "ax": s.get("ax", 0),
            "ay": s.get("ay", 0),
            "az": s.get("az", 0),
            "motion_score": float(s.get("motion_score", 0)),
            "state": s.get("state", ""),
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
        filters.append(f"state eq '{state_filter}'")
    if since:
        filters.append(f"received_at ge '{since}'")
    if until:
        filters.append(f"received_at le '{until}'")

    odata_filter = " and ".join(filters) if filters else None

    table = _get_table_client()

    if odata_filter:
        entity_iter = table.query_entities(odata_filter, results_per_page=limit)
    else:
        entity_iter = table.list_entities(results_per_page=limit)

    rows = []
    for entity in entity_iter:
        rows.append({
            "device_id": entity.get("PartitionKey", ""),
            "boot_id": entity.get("boot_id", ""),
            "seq_no": entity.get("seq_no", 0),
            "ts_ms": entity.get("ts_ms", 0),
            "ax": entity.get("ax", 0),
            "ay": entity.get("ay", 0),
            "az": entity.get("az", 0),
            "motion_score": entity.get("motion_score", 0),
            "state": entity.get("state", ""),
            "wifi_rssi_dbm": entity.get("wifi_rssi_dbm"),
            "received_at": entity.get("received_at", ""),
        })
        if len(rows) >= limit:
            break

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
  .navbar{background:var(--surface);border-bottom:1px solid var(--border);padding:1rem 2rem;display:flex;align-items:center;gap:1rem;}
  .navbar h1{font-size:1.25rem;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
  .navbar .pill{font-size:.75rem;padding:.25rem .75rem;border-radius:9999px;background:var(--green);color:#000;font-weight:600;}
  .controls{display:flex;flex-wrap:wrap;gap:.75rem;padding:1.25rem 2rem;align-items:end;}
  .field{display:flex;flex-direction:column;gap:.25rem;}
  .field label{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);}
  .field input,.field select{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:.45rem .7rem;border-radius:.375rem;font-size:.85rem;}
  .btn{padding:.5rem 1.25rem;border:none;border-radius:.375rem;font-weight:600;cursor:pointer;font-size:.85rem;transition:all .15s;}
  .btn-primary{background:var(--accent);color:#000;}.btn-primary:hover{opacity:.85;}
  .btn-secondary{background:var(--surface);color:var(--text);border:1px solid var(--border);}.btn-secondary:hover{border-color:var(--accent);}
  .grid{display:grid;grid-template-columns:1fr;gap:1.25rem;padding:0 2rem 2rem;}
  @media(min-width:900px){.grid{grid-template-columns:1fr 1fr;}}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:.75rem;padding:1.25rem;position:relative;overflow:hidden;}
  .card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent),var(--accent2));}
  .card h2{font-size:.9rem;color:var(--muted);margin-bottom:.75rem;}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem;padding:0 2rem 1.25rem;}
  .stat{background:var(--surface);border:1px solid var(--border);border-radius:.5rem;padding:1rem;text-align:center;}
  .stat .val{font-size:1.5rem;font-weight:700;color:var(--accent);}
  .stat .lbl{font-size:.7rem;color:var(--muted);margin-top:.25rem;text-transform:uppercase;letter-spacing:.05em;}
  .spinner{display:none;width:1.5rem;height:1.5rem;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite;}
  @keyframes spin{to{transform:rotate(360deg)}}
  #status{font-size:.8rem;color:var(--muted);padding:0 2rem;}
</style>
</head>
<body>

<nav class="navbar">
  <h1>⚙ Dra-Washer Monitor</h1>
  <span class="pill" id="livePill">DASHBOARD</span>
</nav>

<div class="controls">
  <div class="field">
    <label>Device ID</label>
    <input id="deviceId" value="ESP32 Washer Monitor" placeholder="device id">
  </div>
  <div class="field">
    <label>Since</label>
    <input id="since" type="datetime-local">
  </div>
  <div class="field">
    <label>Until</label>
    <input id="until" type="datetime-local">
  </div>
  <div class="field">
    <label>State</label>
    <select id="stateFilter"><option value="">All</option><option>IDLE</option><option>RUNNING</option></select>
  </div>
  <div class="field">
    <label>Limit</label>
    <input id="limit" type="number" value="1000" min="1" max="10000">
  </div>
  <button class="btn btn-primary" onclick="loadData()">Load Data</button>
  <button class="btn btn-secondary" onclick="downloadCSV()">⬇ Download CSV</button>
  <div class="spinner" id="spinner"></div>
</div>

<p id="status"></p>

<div class="stats">
  <div class="stat"><div class="val" id="statTotal">—</div><div class="lbl">Total Samples</div></div>
  <div class="stat"><div class="val" id="statMotionAvg">—</div><div class="lbl">Avg Motion</div></div>
  <div class="stat"><div class="val" id="statMotionMax">—</div><div class="lbl">Max Motion</div></div>
  <div class="stat"><div class="val" id="statRunning">—</div><div class="lbl">Running %</div></div>
</div>

<div class="grid">
  <div class="card"><h2>Motion Score</h2><canvas id="chartMotion"></canvas></div>
  <div class="card"><h2>Accelerometer (ax, ay, az)</h2><canvas id="chartAccel"></canvas></div>
</div>

<script>
const BASE = window.location.origin;
let motionChart, accelChart;

function initCharts() {
  const shared = {responsive:true,animation:{duration:300},scales:{
    x:{ticks:{color:'#94a3b8',maxTicksLimit:12},grid:{color:'#1e293b'}},
    y:{ticks:{color:'#94a3b8'},grid:{color:'#1e293b'}}},
    plugins:{legend:{labels:{color:'#e2e8f0'}}}};

  motionChart = new Chart(document.getElementById('chartMotion'),{
    type:'line',
    data:{labels:[],datasets:[{label:'Motion Score',data:[],borderColor:'#38bdf8',backgroundColor:'rgba(56,189,248,.1)',fill:true,tension:.3,pointRadius:0}]},
    options:{...shared}
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

function buildQuery() {
  const p = new URLSearchParams();
  const d = document.getElementById('deviceId').value;
  if(d) p.set('device_id', d);
  const s = document.getElementById('since').value;
  if(s) p.set('since', new Date(s).toISOString());
  const u = document.getElementById('until').value;
  if(u) p.set('until', new Date(u).toISOString());
  const st = document.getElementById('stateFilter').value;
  if(st) p.set('state', st);
  p.set('limit', document.getElementById('limit').value || '1000');
  return p;
}

async function loadData() {
  const sp = document.getElementById('spinner');
  const st = document.getElementById('status');
  sp.style.display = 'inline-block';
  st.textContent = 'Loading…';

  try {
    const q = buildQuery();
    q.set('format','json');
    const url = BASE + '/api/samples?' + q;
    const res = await fetch(url);
    
    // Check if not OK before trying to parse JSON
    if (!res.ok) {
       const text = await res.text();
       throw new Error(`HTTP ${res.status}: ${text}`);
    }

    const text = await res.text();
    if (!text || text.trim() === "") {
        throw new Error("API returned an empty response body.");
    }
    
    let data;
    try {
        data = JSON.parse(text);
    } catch (err) {
        console.error("Raw response:", text);
        throw new Error("Failed to parse JSON. Open browser console to see raw response.");
    }

    const rows = data.samples || [];

    st.textContent = rows.length + ' samples loaded @ ' + new Date().toLocaleTimeString();

    // stats
    document.getElementById('statTotal').textContent = rows.length;
    if(rows.length) {
      const motions = rows.map(r=>r.motion_score);
      const avg = motions.reduce((a,b)=>a+b,0)/motions.length;
      document.getElementById('statMotionAvg').textContent = avg.toFixed(1);
      document.getElementById('statMotionMax').textContent = Math.max(...motions).toFixed(1);
      const running = rows.filter(r=>r.state==='RUNNING').length;
      document.getElementById('statRunning').textContent = (running/rows.length*100).toFixed(1)+'%';
    }

    // charts
    const labels = rows.map(r=>r.ts_ms);
    motionChart.data.labels = labels;
    motionChart.data.datasets[0].data = rows.map(r=>r.motion_score);
    motionChart.update();

    accelChart.data.labels = labels;
    accelChart.data.datasets[0].data = rows.map(r=>r.ax);
    accelChart.data.datasets[1].data = rows.map(r=>r.ay);
    accelChart.data.datasets[2].data = rows.map(r=>r.az);
    accelChart.update();
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

initCharts();
</script>
</body>
</html>"""


@app.route(route="dashboard", methods=["GET"])
def dashboard(req: func.HttpRequest) -> func.HttpResponse:
    """Serve a self-contained HTML dashboard."""
    return func.HttpResponse(DASHBOARD_HTML, mimetype="text/html")