from sqlalchemy import inspect, text

from src.models.base import Base, engine


# create_all creates brand-new tables with every current column. These additive
# statements only cover a persistent volume initialized by an earlier snapshot.
COLUMN_ADDITIONS = {
    'observations': {
        'version': 'INTEGER NOT NULL DEFAULT 0',
    },
    'analysis_runs': {
        'sample_snapshot': 'JSON',
        'data_revision': 'INTEGER NOT NULL DEFAULT 0',
        'base_stage': 'INTEGER',
        'correction_id': 'INTEGER',
        'is_current_recommendation': 'BOOLEAN NOT NULL DEFAULT 0',
    },
}


def ensure_schema(database_engine=engine):
    Base.metadata.create_all(database_engine)
    inspector = inspect(database_engine)
    existing_tables = set(inspector.get_table_names())
    with database_engine.begin() as connection:
        for table, columns in COLUMN_ADDITIONS.items():
            if table not in existing_tables:
                continue
            present = {column['name'] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
