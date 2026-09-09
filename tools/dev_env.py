"""Create local development secrets. Never overwrite an existing environment."""

import base64
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "supervisor/.env"
if path.exists():
    raise SystemExit("supervisor/.env already exists; refusing to overwrite")
password = secrets.token_urlsafe(24)
text = (root / "supervisor/.env.example").read_text()
text = text.replace("POSTGRES_PASSWORD=REPLACE", "POSTGRES_PASSWORD=" + password)
text = text.replace("cdn:REPLACE@", "cdn:" + password + "@")
text = text.replace(
    "REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS", secrets.token_urlsafe(48)
)
text = text.replace(
    "REPLACE_WITH_BASE64_32_RANDOM_BYTES",
    base64.urlsafe_b64encode(os.urandom(32)).decode(),
)
text = text.replace("REPLACE_WITH_AT_LEAST_12_CHARACTERS", secrets.token_urlsafe(24))
path.touch(mode=0o600)
path.write_text(text)
print(
    "Created supervisor/.env (0600). Retrieve ADMIN_PASSWORD locally; change it at first login."
)
