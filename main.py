import os
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import asyncpg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
