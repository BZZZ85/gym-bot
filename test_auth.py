import hashlib
import hmac
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from webapp.auth import InvalidInitData, validate_init_data

TOKEN = "123456:test-token"
NOW = 1800000000


def signed_data(user=None, auth_date=NOW, **extra):
    fields = {"auth_date": str(auth_date), "user": json.dumps(user if user is not None else {"id": 42, "first_name": "Тест"}), **extra}
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class AuthTests(unittest.TestCase):
    def setUp(self):
        clock = patch("webapp.auth.time.time", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def test_valid_signed_user(self):
        self.assertEqual(validate_init_data(signed_data(query_id="test"), TOKEN)["id"], 42)

    def test_different_bot_cannot_authenticate(self):
        with self.assertRaises(InvalidInitData):
            validate_init_data(signed_data(), TOKEN + "different")

    def test_user_cannot_be_changed_after_signing(self):
        with self.assertRaises(InvalidInitData):
            validate_init_data(signed_data().replace("42", "43"), TOKEN)

    def test_expired_and_future_dates_rejected(self):
        for auth_date in (NOW - 86401, NOW + 31, "invalid"):
            with self.subTest(auth_date=auth_date), self.assertRaises(InvalidInitData):
                validate_init_data(signed_data(auth_date=auth_date), TOKEN)

    def test_duplicate_fields_rejected(self):
        with self.assertRaises(InvalidInitData):
            validate_init_data(signed_data() + "&auth_date=" + str(NOW), TOKEN)

    def test_invalid_user_shapes_rejected(self):
        for user in ({"id": True}, {"id": "42"}, {"id": -1}, {"id": 0}, {}, []):
            with self.subTest(user=user), self.assertRaises(InvalidInitData):
                validate_init_data(signed_data(user=user), TOKEN)

    def test_empty_and_malformed_data_rejected(self):
        for value in ("", "not-a-query-string", "hash=x&auth_date=1", "a=" + "x" * 16400):
            with self.subTest(value=value[:20]), self.assertRaises(InvalidInitData):
                validate_init_data(value, TOKEN)


if __name__ == "__main__":
    unittest.main()
