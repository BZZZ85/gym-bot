"""Telegram Mini App authentication; never trust initDataUnsafe on the server."""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InvalidInitData(ValueError):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict:
    if not init_data or len(init_data) > 16384:
        raise InvalidInitData("Откройте приложение заново из Telegram-бота")
    try:
        fields = parse_qsl(init_data, strict_parsing=True, keep_blank_values=True)
        pairs = dict(fields)
        if len(fields) != len(pairs):
            raise ValueError("Duplicate fields")
        received_hash = pairs.pop("hash")
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise ValueError("Invalid signature")
        age = time.time() - int(pairs["auth_date"])
        if age < -30 or age > max_age:
            raise ValueError("Expired or future authentication")
        user = json.loads(pairs["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
            raise ValueError("Invalid user")
        return user
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise InvalidInitData("Не удалось проверить вход. Откройте приложение заново из бота") from exc
