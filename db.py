import asyncpg
import asyncio

pool = None

async def create_pool():
global pool
for attempt in range(5):
try:
pool = await asyncpg.create_pool(
user="neondb_owner",
password="npg_0eRPsTi9tJAj",
database="neondb",
host="ep-winter-snow-ab9o1qut-pooler.eu-west-2.aws.neon.tech",
port=5432,
ssl="require",
min_size=1,
max_size=5,
command_timeout=60
)
print("✅ Database pool created successfully!")
return
except Exception as e:
print(f"❌ Failed to connect (attempt {attempt+1}/5): {e}")
await asyncio.sleep(3)
raise RuntimeError("Could not connect to database after 5 attempts")

async def close_pool():
global pool
if pool:
await pool.close()

# ===== Пользователи =====

async def add_user(user_id, username):
async with pool.acquire() as conn:
await conn.execute(
"INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT DO NOTHING",
user_id, username
)

# ===== Записи (Records) =====

async def add_record(user_id: int, exercise: str, approach: int, reps: str, weight: str, volume: str):
async with pool.acquire() as conn:
await conn.execute(
"INSERT INTO records (user_id, exercise, approach, reps, weight, volume) "
"VALUES ($1, $2, $3, $4, $5, $6)",
user_id, exercise, approach, reps, weight, volume
)

async def get_user_records(user_id: int):
async with pool.acquire() as conn:
rows = await conn.fetch(
"SELECT * FROM records WHERE user_id=$1 ORDER BY date DESC", user_id
)
return [dict(r) for r in rows]

async def delete_user_exercise(user_id: int, exercise: str):
async with pool.acquire() as conn:
await conn.execute(
"DELETE FROM records WHERE user_id=$1 AND exercise=$2", user_id, exercise
)

# ===== Напоминания =====

async def add_reminder(user_id: int, days: str, time: str):
async with pool.acquire() as conn:
await conn.execute(
"INSERT INTO reminders (user_id, days, time) VALUES ($1, $2, $3)",
user_id, days, time
)

async def get_reminders(user_id: int = None):
async with pool.acquire() as conn:
if user_id:
rows = await conn.fetch("SELECT * FROM reminders WHERE user_id=$1", user_id)
else:
rows = await conn.fetch("SELECT * FROM reminders")
return [dict(r) for r in rows]

async def remove_reminders(user_id: int):
async with pool.acquire() as conn:
await conn.execute("DELETE FROM reminders WHERE user_id=$1", user_id)
