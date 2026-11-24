from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from src.config.runtime import DSN


class Base(DeclarativeBase):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


engine_options = {'pool_pre_ping': True}
if DSN.startswith('sqlite'):
    engine_options['connect_args'] = {'timeout': 30, 'check_same_thread': False}
    if ':memory:' in DSN or DSN in {'sqlite://', 'sqlite:///:memory:'}:
        engine_options['poolclass'] = StaticPool

engine = create_engine(DSN, **engine_options)
Session = sessionmaker(engine, expire_on_commit=False)
