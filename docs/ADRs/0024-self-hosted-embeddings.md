# ADR 0024: Embeddings in a deployment come from a self-hosted Ollama service

- **Status**: Accepted
- **Date**: 2026-10-01

## Context

The registry declares exactly one embedding model, `ollama:embed`
(nomic-embed-text, 768 dimensions), and the vector column is sized for it. Every
deployment description - the Terraform root and the Kubernetes set - ran the
API, the worker and the web app and nothing that could serve that model, and
none of them set `OLLAMA_BASE_URL`, so a deployed process would have looked for
Ollama on its own `localhost`.

That failure does not stop anything. Ingestion stores chunks without vectors,
the retriever's dense arm returns nothing, and fusion runs on the lexical arm
alone. Every research run completes and every report renders; search is simply
worse, and nothing a user, a health check or the load balancer sees says so.
The PRD has carried "embedding model and size" as an open question since v1.0.

Whichever model fills the index is expensive to change afterwards: vectors from
two models share a column and compare meaninglessly, so a change of model is a
re-embedding of everything stored, behind a resize migration if the width moves
(`migrations/embedding_width.py`).

Three options were considered:

1. **A hosted model** - OpenAI's text-embedding-3-small, asked for 768
   dimensions so the column would not change. No new service; a per-token cost;
   dense retrieval then depends on a vendor key, and the OpenAI adapter does
   not yet pass the `dimensions` parameter.
2. **Self-host the declared model** - run Ollama in the deployment, serving
   nomic-embed-text. Keeps the model, the width and the asymmetric task
   prefixes already declared; no vendor; costs a small always-on service, and
   embeds slower than a hosted API on CPU.
3. **Defer**, and make the missing model loud rather than silent.

## Decision

**Option 2.** Each deployment runs Ollama as a service of its own, serving one
model, reachable only by the API and the worker:

- **Terraform**: a fourth service from the shared `ecs-service` module, found at
  `ollama.<prefix>.internal` through a Cloud Map namespace
  (`modules/service-discovery`), in a security group whose only ingress is the
  API and worker groups and whose only egress is HTTPS. Ollama's API is
  unauthenticated, so reachability is its access control.
- **Kubernetes**: `infra/kubernetes/ollama.yaml`, a Deployment, a Service and a
  NetworkPolicy that say the same things.
- **Pinned weights.** The start script (`infra/terraform/files/ollama-entrypoint.sh`,
  carried byte for byte into the Kubernetes ConfigMap) pulls
  `nomic-embed-text:v1.5` and copies it to the unversioned name the registry
  requests. `latest` is a pointer the model library can move, and a model that
  changes under an existing index is the silent failure `EMBEDDING_MODEL` exists
  to prevent. The image is pinned by digest for the same reason.
- **Pinned choice.** Both deployments set `EMBEDDING_MODEL=ollama:embed` rather
  than relying on there being only one embedding model declared.
- **Terraform owns the image.** It is a backing service, like the database
  engine, so `deploy.yml` never rolls it. The module gained `image_owner` for
  this: a pipeline-owned service must ignore task definition changes and a
  Terraform-owned one must not, and since Terraform cannot make a lifecycle
  argument conditional, the module declares the service twice and a test holds
  the two bodies identical.

## Consequences

- Dense retrieval works in a deployment without a vendor key, at no per-token
  cost, with the model and prefixes the retrieval benchmark was designed around.
- A fourth service to run: an always-on task in staging and two in production,
  sized by decision rather than by measurement. CPU is what to raise if
  ingestion waits on embeddings.
- **A new alarm.** `ollama_down` fires when no embedding task has run for ten
  minutes, and it is the one alarm that treats missing data as breaching,
  because the failure it watches for is otherwise invisible.
- **Not scanned, and rate-limited at the source.** The image is third-party and
  `build.yml` does not scan it. It is pulled from Docker Hub, whose anonymous
  pull limit is per address, and every task shares the NAT gateway's - a
  deployment that scales this service often should mirror it into ECR.
- **Model download at every start.** About 274 MB from Ollama's registry over
  HTTPS, which the start period of the health check covers. A task is not
  registered in DNS until the model answers under the name the application uses.
- **Reversible at the usual price.** Moving to a hosted model later is a new
  registry entry, a new `EMBEDDING_MODEL`, and a re-embed - the procedure in
  `migrations/embedding_width.py`. Nothing here makes that harder.
- `apps/api/tests/test_infrastructure.py` fails when the pinned key stops being
  an Ollama embedding model, when the service would pull a name the registry
  does not ask for, when the pin becomes `latest`, when the width stops matching
  the column, or when the two deployments' scripts or images differ.
