"""The deployment descriptions agree with the code they deploy.

Phase 23 adds three descriptions of how to run this system - a compose file, a
Terraform root and a Kubernetes manifest set - and none of them can be executed
on the machine this repository is developed on. That is the problem these tests
exist for. Infrastructure that is never run does not fail; it drifts, silently,
until the day somebody applies it.

So each test here picks one fact that is stated in two places and asserts that
the two still say the same thing. A setting renamed in ``app/core/config.py``,
a probe path removed from the router, a shutdown grace period raised past the
container's stop timeout: each of those is a change that looks complete and
correct in its own diff, and leaves a deployment file quietly wrong.

What this is not: a substitute for applying any of it. Nothing here builds an
image, and nothing here calls AWS. ``terraform validate`` runs in CI, and the
images are built there; the claims those cannot check are the ones below.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from app.core.config import Settings

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "docker-compose.yml"
DOCKERFILE_API = REPO / "infra" / "docker" / "api.Dockerfile"
DOCKERFILE_WEB = REPO / "infra" / "docker" / "web.Dockerfile"
TERRAFORM = REPO / "infra" / "terraform"
KUBERNETES = REPO / "infra" / "kubernetes"

#: Variables a deployment sets that are deliberately not `Settings` fields.
#: An allowlist rather than a pattern, because the whole value of the drift
#: test is that an unrecognised name is a failure and not a judgement call.
NOT_SETTINGS = {
    # Read by botocore's credential and region resolution, not by us.
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    # Python and Node runtime configuration, set in the images.
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "NODE_ENV",
    "PORT",
    "HOSTNAME",
    "NEXT_TELEMETRY_DISABLED",
    # Inlined into the frontend bundle at build time; see web.Dockerfile.
    "NEXT_PUBLIC_API_MODE",
    "NEXT_PUBLIC_API_BASE_URL",
    "NEXT_PUBLIC_APP_NAME",
    "NEXT_BUILD_STANDALONE",
    # Set by the OpenTelemetry SDK's own environment contract, not by Settings.
    "OTEL_EXPORTER_OTLP_HEADERS",
}

SETTING_NAMES = {name.upper() for name in Settings.model_fields}


def _kubernetes_documents() -> list[dict]:
    documents: list[dict] = []
    for path in sorted(KUBERNETES.glob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if document:
                documents.append(document)
    return documents


def _pod_containers(document: dict) -> list[dict]:
    return document["spec"]["template"]["spec"]["containers"]


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _app_services() -> dict[str, dict]:
    """The compose services built from this repository's Dockerfiles."""
    services = _compose()["services"]
    return {name: spec for name, spec in services.items() if "build" in spec}


# ---------------------------------------------------------------------------
# The settings surface
# ---------------------------------------------------------------------------


def _declared_environment_names() -> dict[str, set[str]]:
    """Every environment variable name each deployment description sets."""
    names: dict[str, set[str]] = {}

    for service, spec in _app_services().items():
        names[f"compose:{service}"] = set(spec.get("environment", {}))
        names[f"compose:{service}:build-args"] = set(spec["build"].get("args", {}))

    # The Terraform environment blocks are HCL, and the names are the left-hand
    # sides of assignments inside `common_app_environment`, `api_environment`
    # and `worker_environment`. Matching upper-case identifiers is enough to
    # find them and cannot match a resource attribute, which is lower-case by
    # convention and by the provider's schema.
    locals_hcl = (TERRAFORM / "locals.tf").read_text(encoding="utf-8")
    names["terraform:locals"] = set(re.findall(r"^\s{4}([A-Z][A-Z0-9_]+)\s*=", locals_hcl, re.M))

    for document in _kubernetes_documents():
        if document.get("kind") == "ConfigMap":
            names["kubernetes:configmap"] = set(document["data"])
        elif document.get("kind") == "Secret":
            names["kubernetes:secret"] = set(document["stringData"])

    return names


@pytest.mark.parametrize("source", sorted(_declared_environment_names()))
def test_every_deployed_variable_is_a_declared_setting(source: str):
    """A deployment cannot configure something the settings layer has no name for.

    pydantic's ``extra="ignore"`` means a misspelled variable is not an error:
    it is silently dropped and the setting keeps its default. So
    ``MAX_ESTIMATED_COST_US`` in a task definition is a production deployment
    running on the development cost ceiling, with nothing anywhere saying so.
    The same trap catches a setting that is *renamed* in config.py while a
    deployment file still carries the old name.
    """
    unknown = _declared_environment_names()[source] - SETTING_NAMES - NOT_SETTINGS
    assert not unknown, (
        f"{source} sets {sorted(unknown)}, which app/core/config.py does not declare. "
        "Either the name is wrong or it belongs in NOT_SETTINGS with a reason."
    )


def test_the_deployment_never_supplies_static_object_storage_credentials():
    """S3 is reached through the task role, and an access key would defeat that.

    ``app/core/config.py`` leaves ``s3_access_key_id`` unset on purpose so that
    botocore's default chain resolves the ECS task role. Setting a key here
    would work, which is exactly why it has to be refused: a long-lived
    credential in a task definition is one that cannot be rotated by rotating
    anything.
    """
    static = {"S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"}

    locals_names = _declared_environment_names()["terraform:locals"]
    assert not (locals_names & static), "Terraform sets static S3 credentials"

    configmap = _declared_environment_names()["kubernetes:configmap"]
    assert not (configmap & static), "the Kubernetes ConfigMap sets static S3 credentials"


def test_the_committed_kubernetes_secret_holds_no_values():
    """Its purpose is to document the shape, and a value here is a leaked value."""
    for document in _kubernetes_documents():
        if document.get("kind") != "Secret":
            continue
        for key, value in document["stringData"].items():
            assert value == "", f"{key} has a value committed to the repository"


def test_production_deployments_refuse_the_development_identity():
    """Two independent things must be wrong before an anonymous request is a user.

    ``app/auth/principal.py`` already refuses the development identity outside
    the local and test environments. This asserts the second lock: the
    deployments also turn it off explicitly, so neither the allowlist nor the
    setting is the only thing standing between a deployment and an
    unauthenticated caller being served as somebody.
    """
    locals_hcl = (TERRAFORM / "locals.tf").read_text(encoding="utf-8")
    assert re.search(r'DEV_IDENTITY_ENABLED\s*=\s*"false"', locals_hcl)

    configmap = next(d for d in _kubernetes_documents() if d.get("kind") == "ConfigMap")
    assert configmap["data"]["DEV_IDENTITY_ENABLED"] == "false"


# ---------------------------------------------------------------------------
# The images
# ---------------------------------------------------------------------------


def test_the_api_image_keeps_the_repository_path_depth():
    """The WORKDIR is load-bearing, and shortening it crashes the process on import.

    ``app/core/config.py`` computes ``REPO_ROOT`` as
    ``Path(__file__).resolve().parents[4]``. At ``/app/app/core/config.py``
    there are not four directories above ``app/`` and the index raises
    IndexError - at import time, before logging exists, with a traceback that
    names a path arithmetic error rather than a container layout. The obvious
    Dockerfile ``WORKDIR /app`` is exactly the one that does this.
    """
    workdirs = re.findall(r"^WORKDIR\s+(\S+)", DOCKERFILE_API.read_text(encoding="utf-8"), re.M)
    assert workdirs, "the API image sets no WORKDIR"

    for workdir in workdirs:
        depth = len([part for part in workdir.strip("/").split("/") if part])
        assert depth >= 3, (
            f"WORKDIR {workdir} leaves fewer than four directories above app/; "
            "REPO_ROOT in app/core/config.py raises IndexError there"
        )


@pytest.mark.parametrize("dockerfile", [DOCKERFILE_API, DOCKERFILE_WEB], ids=["api", "web"])
def test_images_drop_root_before_the_entrypoint(dockerfile: Path):
    """A USER instruction, and not root.

    Both images run a process that parses untrusted input - fetched web pages
    and uploaded documents - so this is not hygiene, it is the last boundary
    after the parser's own isolation (ADR 0012).
    """
    text = dockerfile.read_text(encoding="utf-8")
    users = re.findall(r"^USER\s+(\S+)", text, re.M)

    assert users, f"{dockerfile.name} never drops root"
    assert users[-1] not in {"root", "0", "0:0"}, f"{dockerfile.name} ends as root"


def test_the_api_and_the_worker_are_one_image():
    """ADR 0001, asserted rather than asserted-in-prose.

    One codebase and two process types means one image and two commands. Three
    deployment descriptions could each quietly grow a second image; this is
    what stops the first one.
    """
    services = _app_services()
    api_like = {
        name: spec
        for name, spec in services.items()
        if spec["build"].get("dockerfile", "").endswith("api.Dockerfile")
        or spec["build"] == services["api"]["build"]
    }
    assert set(api_like) == {"api", "worker", "migrate"}
    assert len({spec["image"] for spec in api_like.values()}) == 1

    # Terraform: the worker service is passed the same variable as the API.
    main_hcl = (TERRAFORM / "main.tf").read_text(encoding="utf-8")
    worker_block = main_hcl.split('module "worker_service"', 1)[1]
    assert re.search(r"image\s*=\s*var\.api_image", worker_block), (
        "the worker service is no longer built from the API image"
    )

    # Kubernetes: the worker and the migration Job run the API image.
    images = {
        document["metadata"]["name"]: _pod_containers(document)[0]["image"]
        for document in _kubernetes_documents()
        if document.get("kind") in {"Deployment", "Job"}
    }
    assert images["aether-worker"] == images["aether-api"] == images["aether-migrate"]


def test_the_worker_is_started_by_overriding_the_command():
    """The one image's CMD starts the API; the worker is the same image, told otherwise."""
    expected = ["python", "-m", "app.workers.runner"]

    assert _app_services()["worker"]["command"] == expected

    main_hcl = (TERRAFORM / "main.tf").read_text(encoding="utf-8")
    worker_block = main_hcl.split('module "worker_service"', 1)[1]
    assert json.loads(re.search(r"command\s*=\s*(\[[^\]]*\])", worker_block).group(1)) == expected

    worker = next(
        d
        for d in _kubernetes_documents()
        if d.get("kind") == "Deployment" and d["metadata"]["name"] == "aether-worker"
    )
    assert _pod_containers(worker)[0]["command"] == expected


def test_the_build_context_excludes_environment_files_and_dependency_trees():
    """`.env` in a build context is a secret in an image layer.

    Both images build from the repository root, so the context is the whole
    repository - including the `.env` a developer created with `make env`, the
    31,000-file virtualenv and the 42,000-file node_modules. The exclusions are
    the only thing making that context sane, and one of them is a security
    control rather than a build-time optimisation.
    """
    patterns = {
        line.strip()
        for line in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    for required in (".env", "node_modules/", ".venv/", ".git/"):
        assert required in patterns, f".dockerignore does not exclude {required}"

    # And the one negation that must survive: the committed example is not a
    # secret, and `.env.*` would otherwise take it.
    assert "!.env.example" in patterns


def test_compose_builds_the_dockerfiles_that_exist():
    for name, spec in _app_services().items():
        dockerfile = REPO / spec["build"]["dockerfile"]
        context = REPO / spec["build"]["context"]
        assert dockerfile.is_file(), f"{name} builds a Dockerfile that is not there: {dockerfile}"
        assert context.is_dir(), f"{name} builds from a context that is not there: {context}"


# ---------------------------------------------------------------------------
# Probes, timeouts and the numbers that have to agree with the application
# ---------------------------------------------------------------------------


def _probe_paths() -> dict[str, set[str]]:
    """Every HTTP path a deployment description asks the API for, by source.

    Returned per source rather than as one set, so that a regex that stops
    matching shows up as a source with nothing in it rather than as a check
    that quietly passes because it found nothing to check.
    """
    variables = (TERRAFORM / "variables.tf").read_text(encoding="utf-8")
    terraform = set(
        re.findall(
            r'api_health_check_path[\s\S]{0,900}?default\s*=\s*"([^"]+)"',
            variables,
        )
    )

    kubernetes: set[str] = set()
    for document in _kubernetes_documents():
        if document.get("kind") != "Deployment" or document["metadata"]["name"] != "aether-api":
            continue
        for container in _pod_containers(document):
            for probe in ("livenessProbe", "readinessProbe", "startupProbe"):
                http_get = container.get(probe, {}).get("httpGet")
                if http_get:
                    kubernetes.add(http_get["path"])

    # The image's own HEALTHCHECK, whose URL is assembled in Python inside a
    # shell-free exec form. Only the path literal is wanted.
    healthcheck = " ".join(
        line
        for line in DOCKERFILE_API.read_text(encoding="utf-8").splitlines()
        if "127.0.0.1" in line
    )
    image = set(re.findall(r"'(/[\w/-]+)'", healthcheck))

    return {"terraform": terraform, "kubernetes": kubernetes, "image": image}


@pytest.mark.parametrize("source", ["terraform", "kubernetes", "image"])
def test_every_probe_path_is_a_route_the_api_serves(source: str):
    """A health check against a path that 404s takes the whole service out.

    The load balancer, the orchestrator and the container runtime each decide
    whether a replica is alive by asking for a path written in a text file.
    Nothing connects that string to the router, so a route renamed leaves three
    deployment descriptions pointing at a 404 - and a 404 is an unhealthy
    replica, on every replica at once.
    """
    from app.main import create_app

    # Walked rather than read off `app.routes`, and rather than off the OpenAPI
    # schema. FastAPI wraps an `include_router` call in a router object that
    # holds its own routes, so the top level lists two wrappers and not the
    # paths inside them; and the schema omits `/metrics` and the unlisted
    # `/health/ready` alias, either of which a probe may legitimately use.
    def walk(router: object) -> set[str]:
        paths: set[str] = set()
        for route in getattr(router, "routes", []):
            nested = getattr(route, "original_router", None)
            if nested is not None:
                paths |= walk(nested)
            elif hasattr(route, "path"):
                paths.add(route.path)
        return paths

    served = walk(create_app())
    probed = _probe_paths()[source]

    assert probed, f"no probe path was found in {source}; the check found nothing to check"
    for path in probed:
        assert path in served, f"{source} probes {path}, which the API does not serve"


def test_the_load_balancer_outlasts_a_streaming_connection():
    """An idle timeout below SSE_MAX_CONNECTION_SECONDS cuts live runs.

    The symptom is a frontend that reconnects in a loop while a research run is
    in progress, and it looks exactly like a broken stream. Nothing in the
    application logs it, because from the application's side the client went
    away.
    """
    alb = (TERRAFORM / "modules" / "alb" / "variables.tf").read_text(encoding="utf-8")
    idle = int(
        re.search(r'variable "idle_timeout_seconds"[\s\S]*?default\s*=\s*(\d+)', alb).group(1)
    )

    assert idle > Settings(app_env="test").sse_max_connection_seconds

    nginx = yaml.safe_load((KUBERNETES / "ingress.yaml").read_text(encoding="utf-8"))
    read_timeout = int(
        nginx["metadata"]["annotations"]["nginx.ingress.kubernetes.io/proxy-read-timeout"]
    )
    assert read_timeout > Settings(app_env="test").sse_max_connection_seconds


def test_the_worker_is_given_longer_to_stop_than_it_needs_to_hand_runs_back():
    """A worker killed mid-shutdown leaves runs waiting for a lease to expire.

    On SIGTERM the worker stops taking work and hands back what it holds, which
    is bounded by WORKER_SHUTDOWN_GRACE_SECONDS. A container stop timeout below
    that converts an ordinary deploy into runs that sit `running` with a dead
    owner until the sweep notices.
    """
    grace = Settings(app_env="test").worker_shutdown_grace_seconds

    main_hcl = (TERRAFORM / "main.tf").read_text(encoding="utf-8")
    worker_block = main_hcl.split('module "worker_service"', 1)[1]
    stop_timeout = int(re.search(r"stop_timeout_seconds\s*=\s*(\d+)", worker_block).group(1))
    assert stop_timeout > grace

    worker = next(
        d
        for d in _kubernetes_documents()
        if d.get("kind") == "Deployment" and d["metadata"]["name"] == "aether-worker"
    )
    assert worker["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] > grace


def test_the_worker_metrics_port_is_the_one_the_worker_listens_on():
    """Three files publish a port number the worker chooses from its settings."""
    port = Settings(app_env="test").worker_metrics_port

    published = _app_services()["worker"]["ports"]
    assert any(str(port) in mapping for mapping in published)

    locals_hcl = (TERRAFORM / "locals.tf").read_text(encoding="utf-8")
    assert re.search(rf"worker_metrics_port\s*=\s*{port}\b", locals_hcl)

    worker = next(
        d
        for d in _kubernetes_documents()
        if d.get("kind") == "Deployment" and d["metadata"]["name"] == "aether-worker"
    )
    assert _pod_containers(worker)[0]["ports"][0]["containerPort"] == port


# ---------------------------------------------------------------------------
# Terraform structure
# ---------------------------------------------------------------------------


def _module_dirs() -> list[Path]:
    return sorted(p for p in (TERRAFORM / "modules").iterdir() if p.is_dir())


@pytest.mark.parametrize("module", _module_dirs(), ids=lambda p: p.name)
def test_every_module_variable_it_uses_is_one_it_declares(module: Path):
    """`terraform validate` catches this, and it does not run on this machine.

    The check is cheap and the failure it prevents is a module that is valid in
    isolation and wrong when called - a variable read but never declared is a
    reference to nothing.
    """
    declared = set()
    used = set()

    for path in module.glob("*.tf"):
        text = path.read_text(encoding="utf-8")
        declared |= set(re.findall(r'^variable\s+"([^"]+)"', text, re.M))
        used |= set(re.findall(r"\bvar\.([a-z_][a-z0-9_]*)", text))

    assert used <= declared, f"{module.name} reads undeclared variables: {sorted(used - declared)}"


def test_every_module_is_reachable_from_the_root():
    """A module nothing calls is a module nothing reviews."""
    root = "\n".join(path.read_text(encoding="utf-8") for path in TERRAFORM.glob("*.tf"))
    sources = set(re.findall(r'source\s*=\s*"\./modules/([^"]+)"', root))

    assert sources == {module.name for module in _module_dirs()}


@pytest.mark.parametrize(
    "tfvars",
    sorted((TERRAFORM / "environments").glob("*.tfvars")),
    ids=lambda p: p.stem,
)
def test_every_environment_sets_only_declared_variables(tfvars: Path):
    """An undeclared variable in a .tfvars is a warning, not an error.

    Terraform prints a warning and carries on, so a renamed variable leaves an
    environment silently running on the default of the new one - which for
    something like `single_nat_gateway` is a cost decision, and for
    `deletion_protection` is worse than that.
    """
    root_vars = set(
        re.findall(
            r'^variable\s+"([^"]+)"',
            (TERRAFORM / "variables.tf").read_text(encoding="utf-8"),
            re.M,
        )
    )
    assigned = set(re.findall(r"^([a-z_][a-z0-9_]*)\s*=", tfvars.read_text(encoding="utf-8"), re.M))

    assert assigned <= root_vars, f"{tfvars.name} sets {sorted(assigned - root_vars)}"


def test_the_provider_lock_covers_the_platforms_this_project_builds_on():
    """A lock file recorded on one platform fails `terraform init` on another.

    `terraform init` verifies the provider against the checksums in the lock
    file, and a lock written on Windows carries only windows_amd64 hashes - so
    CI, on Linux, refuses to initialise with an error about the lock rather
    than about the platform.
    """
    lock = (TERRAFORM / ".terraform.lock.hcl").read_text(encoding="utf-8")
    blocks = lock.split('provider "')[1:]

    assert blocks, "the provider lock records no providers"

    for block in blocks:
        name = block.split('"', 1)[0]
        # The file does not name platforms - it records one `h1:` zip hash per
        # platform it was locked for, unlabelled. So the check is the count: a
        # single hash is a lock written on one developer's machine, and that is
        # the state that breaks CI.
        assert len(re.findall(r'"h1:', block)) > 1, (
            f"{name} is locked for a single platform; run "
            "`terraform providers lock -platform=linux_amd64 -platform=windows_amd64`"
        )


# ---------------------------------------------------------------------------
# Kubernetes structure
# ---------------------------------------------------------------------------


def test_every_manifest_declares_what_it_is():
    for document in _kubernetes_documents():
        assert document.get("apiVersion"), document
        assert document.get("kind"), document
        assert document["metadata"].get("name"), document


def test_every_pod_runs_as_the_image_user_and_not_as_root():
    """The manifests restate the image's uid, because the cluster does not read it.

    A Deployment without `runAsNonRoot` runs whatever the image says, and a
    later base-image change that drops the USER line would go unnoticed. The
    two together mean the uid is asserted in the place that enforces it.
    """
    for document in _kubernetes_documents():
        if document.get("kind") not in {"Deployment", "Job"}:
            continue
        security = document["spec"]["template"]["spec"]["securityContext"]
        assert security["runAsNonRoot"] is True, document["metadata"]["name"]
        assert security["runAsUser"] == 10001, document["metadata"]["name"]


def test_every_container_declares_what_it_needs_and_what_it_may_not_exceed():
    """No request is a pod the scheduler cannot place sensibly; no limit is a noisy neighbour."""
    for document in _kubernetes_documents():
        if document.get("kind") not in {"Deployment", "Job"}:
            continue
        for container in _pod_containers(document):
            name = f"{document['metadata']['name']}/{container['name']}"
            resources = container.get("resources", {})
            assert resources.get("requests", {}).get("memory"), name
            assert resources.get("limits", {}).get("memory"), name
