# infra/

There is **no cloud infrastructure** for CloudPulse, and none is being created.

CloudPulse is deliberately local-first: the full stack runs on one machine with Docker Compose
([`docker-compose.yml`](../docker-compose.yml) for development, plus
[`docker-compose.hardened.yml`](../docker-compose.hardened.yml) for a production-style local run).
That infrastructure, and its backup, verification and CI automation, is the working deployment story
for this project. See the README's
[Deployment](../README.md#deployment-local-production-style) section.

How CloudPulse could be deployed to a cloud later (and why that needs authentication and a budget
decision first) is a design document only:
[`docs/cloud-reference-architecture.md`](../docs/cloud-reference-architecture.md).

This directory intentionally contains no Terraform, no provider configuration and nothing that can be
applied. A large untested infrastructure-as-code project would add a claim the project cannot back up,
and any real resource risks a charge. Nothing here is deployed by CI.
