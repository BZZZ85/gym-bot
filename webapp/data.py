"""Convert legacy bot records into the same Moscow-day view for all screens."""

import math
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def local_timestamp(stamp):
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(MOSCOW)


def numbers(value):
    result = []
    for part in str(value or "").replace(",", ".").split():
        try:
            number = float(part)
            result.append(number if math.isfinite(number) and number >= 0 else 0)
        except ValueError:
            result.append(0)
    return result


def normalize_records(rows):
    result = []
    for row in rows:
        if not row["exercise"] or not row["date"]:
            continue
        reps, weights = numbers(row["reps"]), numbers(row["weight"])
        local_stamp = local_timestamp(row["date"])
        for index, rep in enumerate(reps):
            if rep <= 0:
                continue
            weight = weights[min(index, len(weights) - 1)] if weights else 0
            result.append({
                "exercise": row["exercise"], "reps": rep, "weight": weight,
                "date": local_stamp.isoformat(), "day": local_stamp.date().isoformat(),
            })
    return result


def sessions(records):
    days = defaultdict(list)
    for record in records:
        days[record["day"]].append(record)
    return [
        {"date": day, "sets": len(entries),
         "volume": round(sum(r["reps"] * r["weight"] for r in entries), 1),
         "exercises": list(dict.fromkeys(r["exercise"] for r in entries)), "entries": entries}
        for day, entries in sorted(days.items(), reverse=True)
    ]


def today():
    return datetime.now(MOSCOW).date()
