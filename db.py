import asyncpg

# Подключение к Render PostgreSQL
DB_URL = "postgresql://bot_database_k8c0_user:MC2rp7rpUnVdRi63LLld1t5HG9m8uOrR@dpg-d4g51l6mcj7s73crk300-a.oregon-postgres.render.com/bot_database_k8c0"

pool = None

async def create_pool():
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(
            DB_URL,
            ssl="require",       # обязательно для Render
            min_size=1,
            max_size=10
        )
        print("🔌 Connected to Render PostgreSQL!")


async def close_pool():
    global pool
    if pool:
        await pool.close()


# ====================== USERS ======================
async def add_user(user_id, username):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users (user_id, username)
               VALUES ($1, $2)
               ON CONFLICT (user_id) DO NOTHING""",
            user_id, username
        )


# ================== EXERCISES =======================
async def get_exercises(user_id: int):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name FROM exercises WHERE user_id = $1",
            user_id
        )
        return [r["name"] for r in rows]


async def add_exercise(user_id: int, name: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO exercises (user_id, name)
               VALUES ($1, $2)
               ON CONFLICT DO NOTHING""",
            user_id, name
        )


# ================== RECORDS =========================
async def add_record(user_id: int, exercise: str, approach: int, reps: str, weight: str, volume: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_approaches (user_id, exercise, approach, reps, weight, volume)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            user_id, exercise, approach, reps, weight, volume
        )


async def get_user_records(user_id: int):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT *
               FROM user_approaches
               WHERE user_id=$1
               ORDER BY id DESC""",
            user_id
        )
        return [dict(r) for r in rows]


async def delete_user_exercise(user_id: int, exercise: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM user_approaches WHERE user_id=$1 AND exercise=$2",
            user_id, exercise
        )
        await conn.execute(
            "DELETE FROM exercises WHERE user_id=$1 AND name=$2",
            user_id, exercise
        )


# ================= REMINDERS ========================
async def add_reminder(user_id: int, days: str, time: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO reminders (user_id, days, time) VALUES ($1, $2, $3)",
            user_id, days, time
        )


async def get_reminders(user_id: int = None):
    async with pool.acquire() as conn:
        if user_id:
            rows = await conn.fetch(
                "SELECT * FROM reminders WHERE user_id=$1",
                user_id
            )
        else:
            rows = await conn.fetch("SELECT * FROM reminders")
        return [dict(r) for r in rows]


async def remove_reminders(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM reminders WHERE user_id=$1",
            user_id
        )
