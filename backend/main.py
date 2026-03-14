import os
from contextlib import asynccontextmanager
from typing import List, Optional

import psycopg
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL")
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if not INGEST_API_KEY:
    raise RuntimeError("INGEST_API_KEY is not set")


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


def init_db() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    id BIGSERIAL PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    boot_id TEXT NOT NULL,
                    seq_no BIGINT NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sample_count INTEGER NOT NULL,
                    ts_start_ms BIGINT,
                    ts_end_ms BIGINT,
                    UNIQUE (device_id, boot_id, seq_no)
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    id BIGSERIAL PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    boot_id TEXT NOT NULL,
                    seq_no BIGINT NOT NULL,
                    ts_ms BIGINT NOT NULL,
                    ax INTEGER NOT NULL,
                    ay INTEGER NOT NULL,
                    az INTEGER NOT NULL,
                    motion_score DOUBLE PRECISION NOT NULL,
                    state TEXT NOT NULL,
                    wifi_rssi_dbm INTEGER,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_samples_device_ts
                ON samples (device_id, ts_ms);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_samples_state
                ON samples (state);
                """
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Washer Backend", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/ingest")
def ingest(batch: Batch, x_api_key: str | None = Header(default=None)):
    if x_api_key != INGEST_API_KEY:
        raise HTTPException(status_code=401, detail="bad api key")

    ts_values = [s.ts_ms for s in batch.samples]
    ts_start = min(ts_values) if ts_values else None
    ts_end = max(ts_values) if ts_values else None

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                # Insert batch metadata first. If duplicate, skip sample insert.
                cur.execute(
                    """
                    INSERT INTO batches (
                        device_id, boot_id, seq_no, sample_count, ts_start_ms, ts_end_ms
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (device_id, boot_id, seq_no) DO NOTHING
                    RETURNING id;
                    """,
                    (
                        batch.device_id,
                        batch.boot_id,
                        batch.seq_no,
                        len(batch.samples),
                        ts_start,
                        ts_end,
                    ),
                )

                inserted = cur.fetchone()

                if inserted is None:
                    return {
                        "ok": True,
                        "duplicate": True,
                        "accepted_seq_no": batch.seq_no,
                        "sample_count": len(batch.samples),
                    }

                if batch.samples:
                    rows = [
                        (
                            batch.device_id,
                            batch.boot_id,
                            batch.seq_no,
                            s.ts_ms,
                            s.ax,
                            s.ay,
                            s.az,
                            s.motion_score,
                            s.state,
                            s.wifi_rssi_dbm,
                        )
                        for s in batch.samples
                    ]

                    cur.executemany(
                        """
                        INSERT INTO samples (
                            device_id, boot_id, seq_no, ts_ms,
                            ax, ay, az, motion_score, state, wifi_rssi_dbm
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                        """,
                        rows,
                    )

        return {
            "ok": True,
            "accepted_seq_no": batch.seq_no,
            "sample_count": len(batch.samples),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"database error: {str(e)}")