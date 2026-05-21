import os
from sqlalchemy import create_engine, text

# Build connection string
host = "192.168.1.100"
port = "5444"
user = "zefix_user_01"
password = "qsrg5gRen5s#BphY"
db = "zefix_analyzer"

connection_string = f"postgresql://{user}:{password}@{host}:{port}/{db}"

try:
    engine = create_engine(connection_string, echo=False)
    with engine.connect() as conn:
        # Count total and matching rows
        result = conn.execute(text("""
            SELECT
              COUNT(*) as total_rows,
              SUM(CASE WHEN purpose::text ILIKE '%Die Gesellschaft kann%' THEN 1 ELSE 0 END) as matching_rows
            FROM companies;
        """))
        row = result.fetchone()
        print(f"Total rows: {row[0]}")
        print(f"Matching rows: {row[1]}")
        print("\n" + "="*80)

        # Get sample excerpts
        result = conn.execute(text("""
            SELECT
              id,
              SUBSTRING(purpose::text FROM POSITION('Die Gesellschaft kann' IN purpose::text) - 50
                        FOR 600) as excerpt
            FROM companies
            WHERE purpose::text ILIKE '%Die Gesellschaft kann%'
            LIMIT 10;
        """))

        print("\nSample excerpts (10 rows):\n")
        for i, (cid, excerpt) in enumerate(result, 1):
            print(f"\n--- Company ID {cid} ---")
            if excerpt:
                print(excerpt)

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
