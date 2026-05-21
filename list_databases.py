import os
from sqlalchemy import create_engine, text

# Connect to postgres (default system database)
host = "192.168.1.100"
port = "5444"
user = "zefix_user_01"
password = "qsrg5gRen5s#BphY"

connection_string = f"postgresql://{user}:{password}@{host}:{port}/postgres"

try:
    engine = create_engine(connection_string, echo=False)
    with engine.connect() as conn:
        # List all databases
        result = conn.execute(text("""
            SELECT datname FROM pg_database WHERE datistemplate = false
            ORDER BY datname;
        """))

        print("Available databases:\n")
        for (dbname,) in result:
            print(f"  - {dbname}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
