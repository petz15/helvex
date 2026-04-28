# Building and Pushing the pgvector PostgreSQL Image

CloudNativePG's admission webhook requires semantic versioning for the `imageName` field (e.g., `16.3.0`). The community pgvector images don't follow this format, so we provide a Dockerfile to build a custom image that combines CloudNativePG's official PostgreSQL 16 image with pgvector compiled from source.

## Build Process

### Prerequisites
- Docker or a container builder (podman, etc.)
- Access to a container registry (Docker Hub, GitHub Container Registry, private registry, etc.)

### Build and Push

```bash
# Build the image with your registry details
bash infra/build-pgvector-image.sh ghcr.io/your-username/pgvector-postgresql 16.3.0

# The script outputs the docker push command
docker push ghcr.io/your-username/pgvector-postgresql:16.3.0
```

### Configure Helmfile

Update `values-prod.yaml` or your environment-specific values:

```yaml
postgres:
  imageName: ghcr.io/your-username/pgvector-postgresql:16.3.0
```

Or pass via helmfile:

```bash
helmfile -e prod apply --state-values-set postgres.imageName=ghcr.io/your-username/pgvector-postgresql:16.3.0
```

## Image Details

- **Base**: ghcr.io/cloudnative-pg/postgresql:16.3 (official CloudNativePG image)
- **pgvector**: Built from source (v0.7.0)
- **Size**: ~500 MB (includes build tools removed after compilation)
- **Admins Webhook**: Passes semantic version validation (format: X.Y.Z)

## Notes

- The pgvector extension is created automatically during cluster initialization via Alembic migration `0069_add_noga_embeddings_pgvector.py`
- This image is compatible with CloudNativePG and can be used for both development and production
- If you prefer not to maintain a custom image, consider using CloudNativePG's standard image and installing pgvector via a bootstrap process (requires pgvector shared library to be pre-installed)
