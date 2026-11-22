import base64
import json
import logging
import os
import zlib
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import asyncpg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from webapp.auth import InvalidInitData, validate_init_data
from webapp.data import local_timestamp, normalize_records, sessions, today

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / "ton.env")
load_dotenv(ROOT.parent / ".env")


@asynccontextmanager
async def lifespan(app):
    token, database = os.getenv("BOT_TOKEN"), os.getenv("DATABASE_URL")
    if not token or not database:
        raise RuntimeError("Задайте BOT_TOKEN и DATABASE_URL для веб-сервиса")
    app.state.bot_token = token
    pool = await asyncpg.create_pool(dsn=database, min_size=1, max_size=5, command_timeout=30)
    app.state.pool = pool
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, username TEXT);
                    CREATE TABLE IF NOT EXISTS records (
                        user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                        exercise TEXT, sets INT, reps TEXT, weight TEXT,
                        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    ALTER TABLE records ADD COLUMN IF NOT EXISTS weight TEXT;
                    CREATE INDEX IF NOT EXISTS records_user_date_idx ON records(user_id, date DESC);
                    CREATE TABLE IF NOT EXISTS miniapp_submissions (
                        user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                        request_id UUID NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(user_id, request_id)
                    );
                """)
        yield
    finally:
        await pool.close()


app = FastAPI(title="Gym Bot Mini App", lifespan=lifespan)


@app.middleware("http")
async def response_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
async def health(request: Request):
    async with request.app.state.pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}


async def get_current_user(request: Request, init_data: str = Header(default="", alias="X-Telegram-Init-Data")):
    try:
        user = validate_init_data(init_data, request.app.state.bot_token)
    except InvalidInitData as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    username = user.get("username") or user.get("first_name") or str(user["id"])
    async with request.app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            user["id"], username,
        )
    return user


async def get_rows(request, user_id, exercise=None):
    async with request.app.state.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT exercise, reps, weight, date FROM records
               WHERE user_id=$1 AND ($2::text IS NULL OR exercise=$2)
               ORDER BY date DESC""", user_id, exercise,
        )


@app.get("/api/exercises")
async def api_exercises(request: Request, user=Depends(get_current_user)):
    rows = await get_rows(request, user["id"])
    counts = {}
    for record in normalize_records(rows):
        counts[record["exercise"]] = counts.get(record["exercise"], 0) + 1
    exercises = {}
    for row in rows:
        if row["exercise"] and row["exercise"] not in exercises:
            exercises[row["exercise"]] = {
                "exercise": row["exercise"],
                "last_date": local_timestamp(row["date"]).isoformat() if row["date"] else None,
                "sets_count": counts.get(row["exercise"], 0),
            }
    return list(exercises.values())


@app.get("/api/history")
async def api_history(request: Request, user=Depends(get_current_user), limit: int = Query(30, ge=1, le=100)):
    return sessions(normalize_records(await get_rows(request, user["id"])))[:limit]


@app.get("/api/exercise/history")
@app.get("/api/exercise/{exercise}/history")
async def api_exercise_history(request: Request, exercise: str, user=Depends(get_current_user)):
    workouts = sessions(normalize_records(await get_rows(request, user["id"], exercise)))
    chart = []
    for workout in reversed(workouts):
        weights = [entry["weight"] for entry in workout["entries"]]
        chart.append({"date": workout["date"], "avg_weight": round(sum(weights) / len(weights), 1),
                      "top_weight": max(weights), "sets": workout["sets"], "volume": workout["volume"]})
    return {"exercise": exercise, "sessions": chart[-20:],
            "max_weight": max((point["top_weight"] for point in chart), default=0),
            "total_volume": round(sum(point["volume"] for point in chart), 1)}


class SetEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reps: int = Field(strict=True, ge=1, le=1000)
    weight: float = Field(strict=True, ge=0, le=2000, allow_inf_nan=False)


class AddRecordPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercise: str = Field(min_length=1, max_length=120)
    entries: list[SetEntry] = Field(min_length=1, max_length=30)
    request_id: UUID

    @field_validator("exercise")
    @classmethod
    def clean_exercise(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Укажите название упражнения")
        return value


@app.post("/api/records")
async def api_add_records(payload: AddRecordPayload, request: Request, user=Depends(get_current_user)):
    async with request.app.state.pool.acquire() as conn:
        async with conn.transaction():
            inserted = await conn.fetchval(
                """INSERT INTO miniapp_submissions (user_id, request_id) VALUES ($1, $2)
                   ON CONFLICT DO NOTHING RETURNING request_id""", user["id"], payload.request_id,
            )
            if inserted is None:
                return {"status": "ok", "added": 0, "duplicate": True}
            await conn.executemany(
                """INSERT INTO records (user_id, exercise, sets, reps, weight, date)
                   VALUES ($1, $2, 1, $3, $4, CURRENT_TIMESTAMP AT TIME ZONE 'UTC')""",
                [(user["id"], payload.exercise, str(entry.reps), format(entry.weight, "g"))
                 for entry in payload.entries],
            )
    return {"status": "ok", "added": len(payload.entries), "duplicate": False}


@app.get("/api/summary")
async def api_summary(request: Request, user=Depends(get_current_user)):
    records = normalize_records(await get_rows(request, user["id"]))
    workouts = sessions(records)
    current_day = today().isoformat()
    week_start = (today() - timedelta(days=6)).isoformat()
    volume = {}
    for entry in records:
        volume[entry["exercise"]] = volume.get(entry["exercise"], 0) + entry["reps"] * entry["weight"]
    today_workout = next((workout for workout in workouts if workout["date"] == current_day), None)
    return {
        "user": {"first_name": user.get("first_name", ""), "username": user.get("username", "")},
        "total_sessions": len(workouts),
        "week_sessions": sum(week_start <= workout["date"] <= current_day for workout in workouts),
        "last_workout": records[0]["date"] if records else None,
        "today": today_workout,
        "top_exercises": [{"exercise": exercise, "volume": round(total, 1)}
                          for exercise, total in sorted(volume.items(), key=lambda item: item[1], reverse=True)[:5]],
    }


# BEGIN FRONTEND BUNDLE — generated by scripts/build_frontend.cjs
_FRONTEND_BUNDLE = (
    "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"
)
# END FRONTEND BUNDLE


def mount_frontend(application, directory):
    """Serve the complete UI even when a manual upload omitted the static folder."""
    required = ("index.html", "app.js", "styles.css")
    if all((directory / name).is_file() for name in required):
        application.mount("/", StaticFiles(directory=directory, html=True), name="static")
        return

    frontend = json.loads(zlib.decompress(base64.b64decode(_FRONTEND_BUNDLE)).decode("utf-8"))
    logging.getLogger(__name__).info("Используем встроенный интерфейс Mini App")

    @application.get("/", include_in_schema=False)
    async def frontend_index():
        return HTMLResponse(frontend["index.html"])

    @application.get("/app.js", include_in_schema=False)
    async def frontend_script():
        return Response(frontend["app.js"], media_type="application/javascript")

    @application.get("/styles.css", include_in_schema=False)
    async def frontend_styles():
        return Response(frontend["styles.css"], media_type="text/css")


mount_frontend(app, ROOT / "static")
