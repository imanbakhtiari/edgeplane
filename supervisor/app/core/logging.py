import json
import logging
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "component": record.name,
                "message": record.getMessage(),
            }
        )


def configure():
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.getLogger("cdn").handlers = [handler]
    logging.getLogger("cdn").setLevel(logging.INFO)
    logging.getLogger("cdn").propagate = False
