import os

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import (
    ConfigurationError,
    ConnectionFailure,
    ServerSelectionTimeoutError,
)

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is missing from the environment.")

print("MongoDB URI loaded from the environment.\n")


def test_connection(name, client_args):
    print(f"--- Attempting: {name} ---")
    try:
        client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            **client_args,
        )
        client.admin.command("ping")
        print("SUCCESS\n")
        return True
    except ConfigurationError as error:
        print(f"FAILED (Configuration/DNS Error):\n{error}\n")
    except ServerSelectionTimeoutError as error:
        print(f"FAILED (Timeout/SSL Handshake Error):\n{error}\n")
    except ConnectionFailure as error:
        print(f"FAILED (Connection Error):\n{error}\n")
    except Exception as error:
        print(f"FAILED (Unexpected Error):\n{error}\n")
    return False


test_connection(
    "Standard Connection (with certifi)",
    {"tlsCAFile": certifi.where()},
)
test_connection("Using System CA Certificates (No certifi)", {})
