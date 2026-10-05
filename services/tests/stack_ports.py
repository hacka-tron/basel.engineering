"""Ports of the MySQL and Redis the integration tests talk to.

They default to the shared local compose stack; set GLASSBOX_TEST_MYSQL_PORT and
GLASSBOX_TEST_REDIS_PORT to run against throwaway containers on spare ports.
"""

import os

TEST_MYSQL_PORT = os.environ.get("GLASSBOX_TEST_MYSQL_PORT", "3306")
TEST_REDIS_PORT = os.environ.get("GLASSBOX_TEST_REDIS_PORT", "6379")


def redis_url_for(db: int = 0) -> str:
    return f"redis://127.0.0.1:{TEST_REDIS_PORT}/{db}"
