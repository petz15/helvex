# Fast ml-worker image: inherits heavy deps (pip, spaCy, geocoding DB) from ml-base.
# Only rebuilt when app code changes — typically under 5 minutes including QEMU.
ARG ML_BASE_IMAGE=ghcr.io/placeholder/ml-base:latest
FROM ${ML_BASE_IMAGE}

ARG BUILD_DATE=unknown
ARG BUILD_GIT_SHA=unknown

LABEL org.opencontainers.image.created=$BUILD_DATE
LABEL org.opencontainers.image.revision=$BUILD_GIT_SHA

ENV APP_BUILD_DATE=$BUILD_DATE
ENV APP_GIT_SHA=$BUILD_GIT_SHA

# Copy app code. data/ (geocoding DB) is git-ignored so it is NOT in the build
# context — the base image's data/ layer is preserved intact.
COPY . .

# Re-apply ownership in case the COPY changed data/ permissions.
RUN chown -R 1000:1000 /app/data

EXPOSE 8000

ENTRYPOINT ["sh", "entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
