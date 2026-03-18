FROM python:3.12-slim

WORKDIR /app

ARG BUILD_DATE=unknown
ARG BUILD_GIT_SHA=unknown

LABEL org.opencontainers.image.created=$BUILD_DATE
LABEL org.opencontainers.image.revision=$BUILD_GIT_SHA

ENV APP_BUILD_DATE=$BUILD_DATE
ENV APP_GIT_SHA=$BUILD_GIT_SHA

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python - <<'EOF'
import sys
if sys.version_info >= (3, 12):
    from typing import ForwardRef
    _orig = ForwardRef._evaluate
    def _p(self, g, l, *a, **kw):
        kw.setdefault("recursive_guard", frozenset())
        return _orig(self, g, l, *a, **kw)
    ForwardRef._evaluate = _p
import spacy.cli
spacy.cli.download("de_core_news_md")
EOF

COPY . .

# Build geocoding datasets (both git-ignored, downloaded during image build):
# 1. GeoNames PLZ centroid table (~800 KB) — PLZ-level fallback
# 2. swisstopo Amtliches Gebäudeadressverzeichnis (~143 MB zip) — building-level primary
RUN python -c "from app.api.geocoding_client import _load_plz_table; _load_plz_table()" \
    && echo "PLZ table ready: $(wc -l < data/plz_ch.tsv) entries"
RUN python -c "from app.api.geocoding_client import build_geocoding_db; build_geocoding_db()" \
    && echo "Building DB ready: $(du -sh data/geocoding.db)"


EXPOSE 8000

ENTRYPOINT ["sh", "entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
