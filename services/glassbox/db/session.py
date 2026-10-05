import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker


def get_database_url() -> URL:
    return URL.create(
        "mysql+pymysql",
        username=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        database=os.environ["MYSQL_DATABASE"],
    )


def create_db_engine() -> Engine:
    # hide_parameters: a failed statement's error text omits the bound values, so
    # logged errors never carry visitor questions, answers or chunk text.
    return create_engine(get_database_url(), pool_pre_ping=True, hide_parameters=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine())


def get_session() -> Session:
    return get_session_factory()()
