from datetime import datetime
from auth_utils import redis_client, get_user_role
import json

def log_action(username: str, action: str):
    if not username:
        return

    role = get_user_role(username) or "user"

    log_entry = {
        'timestamp': datetime.utcnow().isoformat(),
        'username': username,
        'role': role,
        'action': action
    }

    redis_client.rpush(f"logs:{username}", json.dumps(log_entry))
