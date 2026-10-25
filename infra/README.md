# infra/

Cloud infrastructure for CloudPulse will live here.

**Phase 0 (current):** intentionally empty. Local infrastructure is defined by the root
[`docker-compose.yml`](../docker-compose.yml) and the per-service Dockerfiles.

**Phase 5 (planned):** Azure deployment, with infrastructure-as-code (Terraform) introduced
only if it is justified by the deployment shape at that point. Likely candidates:

- Azure Container Apps (API + frontend) or App Service for Containers
- Azure Database for PostgreSQL – Flexible Server
- Azure Container Registry
- Azure Key Vault for secrets
- Azure Monitor / Log Analytics for the API's structured JSON logs

Nothing in this directory is deployed by CI today.
