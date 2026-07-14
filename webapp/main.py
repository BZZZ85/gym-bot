import hashlib
import hmac
import json
import os
import time
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import parse_qsl

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
INIT_DATA_MAX_AGE = 24 * 60 * 60  # секунд; за это время initData считается свежей

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не найден. Задайте его в Variables сервиса webapp на Railway.")
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL не найден. Задайте его в Variables сервиса webapp на Railway.")

app = FastAPI(title="Gym Bot Mini App")

pool: asyncpg.Pool | None = None


@app.on_event("startup")
async def startup() -> None:
    global pool
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=5)
    # На случай, если webapp запустят раньше, чем бот успеет создать таблицы
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                exercise TEXT,
                sets INT,
                reps TEXT,
                weight TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


@app.on_event("shutdown")
async def shutdown() -> None:
    if pool:
        await pool.close()


@app.get("/health")
async def health():
    return {"status": "ok"}


# ===== Проверка подлинности Telegram initData =====
# https://docs.telegram-mini-apps.com/platform/init-data

def _validate_init_data(init_data: str, bot_token: str, max_age: int) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Нет initData")

    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise HTTPException(status_code=401, detail="Некорректный формат initData")

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="В initData нет hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=401, detail="Неверная подпись initData")

    auth_date = int(pairs.get("auth_date", 0))
    if max_age and (time.time() - auth_date) > max_age:
        raise HTTPException(status_code=401, detail="initData устарела — откройте приложение заново из бота")

    return pairs


async def get_current_user(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data")
) -> dict:
    pairs = _validate_init_data(x_telegram_init_data, BOT_TOKEN, INIT_DATA_MAX_AGE)
    user_raw = pairs.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="В initData нет данных пользователя")

    user = json.loads(user_raw)
    user_id = int(user["id"])
    username = user.get("username") or user.get("first_name") or str(user_id)

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            user_id, username,
        )

    return {"id": user_id, "username": username, "first_name": user.get("first_name", "")}


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt_number(value: float) -> str:
    """82.0 -> '82', 82.5 -> '82.5' — чтобы в базе не плодить лишние '.0'."""
    return str(int(value)) if float(value).is_integer() else str(value)


# ===== API =====

@app.get("/api/exercises")
async def api_exercises(user: dict = Depends(get_current_user)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT exercise, MAX(date) AS last_date, COUNT(*) AS sets_count
            FROM records
            WHERE user_id=$1 AND exercise IS NOT NULL AND exercise <> ''
            GROUP BY exercise
            ORDER BY last_date DESC
            """,
            user["id"],
        )

    return [
        {
            "exercise": r["exercise"],
            "last_date": r["last_date"].isoformat() if r["last_date"] else None,
            "sets_count": r["sets_count"],
        }
        for r in rows
    ]


@app.get("/api/exercise/{exercise}/history")
async def api_exercise_history(exercise: str, user: dict = Depends(get_current_user)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT reps, weight, date
            FROM records
            WHERE user_id=$1 AND exercise=$2
            ORDER BY date ASC
            """,
            user["id"], exercise,
        )

    sessions: dict[str, dict] = defaultdict(lambda: {"reps": [], "weights": []})
    for r in rows:
        weight = _to_float(r["weight"])
        reps = _to_float(r["reps"])
        if weight <= 0 and reps <= 0:
            continue  # запись-плейсхолдер при создании нового упражнения
        day = r["date"].date().isoformat()
        sessions[day]["reps"].append(reps)
        sessions[day]["weights"].append(weight)

    chart = []
    max_weight = 0.0
    total_volume = 0.0
    for day in sorted(sessions.keys()):
        data = sessions[day]
        weights = data["weights"]
        reps = data["reps"]
        avg_weight = round(sum(weights) / len(weights), 1) if weights else 0
        top_weight = max(weights) if weights else 0
        volume = sum(rep * w for rep, w in zip(reps, weights))
        max_weight = max(max_weight, top_weight)
        total_volume += volume
        chart.append(
            {
                "date": day,
                "avg_weight": avg_weight,
                "top_weight": top_weight,
                "sets": len(weights),
                "volume": round(volume, 1),
            }
        )

    suggested_next = round(max_weight * 1.05 + 0.49) if max_weight else 0

    return {
        "exercise": exercise,
        "sessions": chart[-20:],
        "max_weight": max_weight,
        "total_volume": round(total_volume, 1),
        "suggested_next_weight": suggested_next,
    }


class SetEntry(BaseModel):
    reps: float
    weight: float


class AddRecordPayload(BaseModel):
    exercise: str
    entries: list[SetEntry]


@app.post("/api/records")
async def api_add_records(payload: AddRecordPayload, user: dict = Depends(get_current_user)):
    exercise = payload.exercise.strip()
    if not exercise:
        raise HTTPException(status_code=400, detail="Название упражнения не может быть пустым")
    if not payload.entries:
        raise HTTPException(status_code=400, detail="Нужен хотя бы один подход")

    async with pool.acquire() as conn:
        async with conn.transaction():
            for entry in payload.entries:
                await conn.execute(
                    """
                    INSERT INTO records (user_id, exercise, sets, reps, weight, date)
                    VALUES ($1, $2, 1, $3, $4, NOW())
                    """,
                    user["id"],
                    exercise,
                    _fmt_number(entry.reps),
                    _fmt_number(entry.weight),
                )

    return {"status": "ok", "added": len(payload.entries)}


@app.get("/api/summary")
async def api_summary(user: dict = Depends(get_current_user)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT exercise, reps, weight, date
            FROM records
            WHERE user_id=$1
            ORDER BY date DESC
            """,
            user["id"],
        )

    if not rows:
        return {"total_sessions": 0, "week_sessions": 0, "last_workout": None, "top_exercises": []}

    sessions_by_day: dict[str, list] = defaultdict(list)
    volume_by_exercise: dict[str, float] = defaultdict(float)
    for r in rows:
        day = r["date"].date().isoformat()
        sessions_by_day[day].append(r)
        volume_by_exercise[r["exercise"]] += _to_float(r["reps"]) * _to_float(r["weight"])

    week_ago = date.today() - timedelta(days=7)
    week_sessions = sum(1 for day in sessions_by_day if date.fromisoformat(day) >= week_ago)

    top_exercises = sorted(volume_by_exercise.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return {
        "total_sessions": len(sessions_by_day),
        "week_sessions": week_sessions,
        "last_workout": rows[0]["date"].isoformat(),
        "top_exercises": [{"exercise": ex, "volume": round(vol, 1)} for ex, vol in top_exercises],
    }


# Отдаём статику (index.html и т.д.) для всего, что не /api/*
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
