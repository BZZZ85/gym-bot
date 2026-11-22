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
