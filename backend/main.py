from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
import os

app = FastAPI(title="Washer Backend")

INGEST_API_KEY = os.getenv("INGEST_API_KEY", "")

class Sample(BaseModel):
    ts_ms: int
    ax: int
    ay: int
    az: int
    motion_score: float
    state: str
    wifi_rssi_dbm: Optional[int] = None

class Batch(BaseModel):
    device_id: str
    boot_id: str
    seq_no: int
    samples: List[Sample] = Field(default_factory=list)

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.post("/ingest")
def ingest(batch: Batch, x_api_key: str | None = Header(default=None)):
    if not INGEST_API_KEY or x_api_key != INGEST_API_KEY:
        raise HTTPException(status_code=401, detail="bad api key")

    print(
        {
            "device_id": batch.device_id,
            "boot_id": batch.boot_id,
            "seq_no": batch.seq_no,
            "sample_count": len(batch.samples),
        }
    )

    return {
        "ok": True,
        "accepted_seq_no": batch.seq_no,
        "sample_count": len(batch.samples),
    }