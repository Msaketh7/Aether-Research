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
import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from tests.support.settings import signing_key_pem

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

    # Only the ConfigMaps and Secrets a pod loads into its environment, because
    # those are the settings. ollama.yaml carries a ConfigMap as well - a script
    # mounted as a file - and reading every ConfigMap as settings would report
    # its file name as an unknown setting and, worse, used to let whichever
    # ConfigMap was read last replace the real one.
    loaded = _environment_sources()
    for document in _kubernetes_documents():
        kind, name = document.get("kind"), document["metadata"]["name"]
        if kind == "ConfigMap" and name in loaded["configMapRef"]:
            names.setdefault("kubernetes:configmap", set()).update(document["data"])
        elif kind == "Secret" and name in loaded["secretRef"]:
            names.setdefault("kubernetes:secret", set()).update(document["stringData"])

    return names


def _environment_sources() -> dict[str, set[str]]:
    """The ConfigMaps and Secrets some pod takes its environment from, by name."""
    loaded: dict[str, set[str]] = {"configMapRef": set(), "secretRef": set()}
    for document in _kubernetes_documents():
        if document.get("kind") not in {"Deployment", "Job"}:
            continue
        for container in _pod_containers(document):
            for source in container.get("envFrom", []):
                for kind, names in loaded.items():
                    if kind in source:
                        names.add(source[kind]["name"])
    return loaded


def _kubernetes_named(kind: str, name: str) -> dict:
    return next(
        document
        for document in _kubernetes_documents()
        if document.get("kind") == kind and document["metadata"]["name"] == name
    )


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

    configmap = _kubernetes_named("ConfigMap", "aether-config")
    assert configmap["data"]["DEV_IDENTITY_ENABLED"] == "false"


# ---------------------------------------------------------------------------
# What a deployed environment cannot do without
# ---------------------------------------------------------------------------

#: Settings a deployed environment refuses to start without, each with a value
#: that satisfies it. A table rather than a derivation, because what satisfies a
#: validator is not something a test can guess - but the test below holds it to
#: app/core/config.py in both directions, so it cannot drift from the code.
REQUIRED_OUTSIDE_DEVELOPMENT: dict[str, Callable[[], str]] = {
    "JWT_PRIVATE_KEY": signing_key_pem,
}

DEPLOYED_ENVIRONMENTS = ("staging", "production")


def _isolated_settings(monkeypatch: pytest.MonkeyPatch, **values: object) -> Settings:
    """Settings from exactly these values: no `.env` file, no shell variable.

    This machine's shell sets OPENAI_API_KEY and a developer's `.env` sets
    more, and either would satisfy a requirement the deployment itself does not.
    """
    for key in list(os.environ):
        if key.upper() in SETTING_NAMES:
            monkeypatch.delenv(key)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize("app_env", DEPLOYED_ENVIRONMENTS)
def test_the_required_table_is_exactly_what_a_deployment_cannot_start_without(
    app_env: str, monkeypatch: pytest.MonkeyPatch
):
    """Complete, because Settings starts with these and nothing else; minimal,
    because it refuses to start without any one of them.

    Together those make the table a statement about config.py rather than a
    list somebody keeps: a validator that starts requiring a new setting fails
    the first half, and one that stops requiring an old one fails the second.
    """
    values = {name.lower(): make() for name, make in REQUIRED_OUTSIDE_DEVELOPMENT.items()}

    _isolated_settings(monkeypatch, app_env=app_env, **values)

    for name in values:
        without = {key: value for key, value in values.items() if key != name}
        with pytest.raises(ValidationError, match=name.upper()):
            _isolated_settings(monkeypatch, app_env=app_env, **without)


def _laptop_defaults() -> set[str]:
    """Settings whose default only works on a developer's machine.

    Derived, so that a setting added later with a localhost default becomes a
    deployment obligation without anybody remembering to list it. None of these
    stops a deployment starting - which is the problem: a DATABASE_URL left at
    its default starts, then fails every request; an OLLAMA_BASE_URL left at its
    default starts, and retrieval quietly loses its dense arm.
    """
    found: set[str] = set()
    for name, field in Settings.model_fields.items():
        default = field.default
        text = default.get_secret_value() if isinstance(default, SecretStr) else str(default)
        if re.search(r"localhost|127\.0\.0\.1|example\.(com|org|net)", text):
            found.add(name.upper())
    return found


def _hcl_string_list(text: str, name: str) -> set[str]:
    match = re.search(rf"\b{name}\s*=\s*\[([^\]]*)\]", text)
    assert match, f"{name} is not a list literal any more; this parser needs updating"
    return set(re.findall(r'"([A-Z0-9_]+)"', match.group(1)))


def _terraform_supplied() -> set[str]:
    """Every setting a Terraform task is given, other than the overridable keys.

    `provider_secret_names` is left out on purpose: it is a variable, so an
    environment can replace it, and a required setting that lives there can be
    dropped by an override that only meant to remove a search provider.
    """
    locals_hcl = (TERRAFORM / "locals.tf").read_text(encoding="utf-8")
    return (
        _declared_environment_names()["terraform:locals"]
        | _hcl_string_list(locals_hcl, "derived_secret_names")
        | _hcl_string_list(locals_hcl, "required_secret_names")
    )


def _kubernetes_supplied() -> set[str]:
    names = _declared_environment_names()
    return names["kubernetes:configmap"] | names["kubernetes:secret"]


@pytest.mark.parametrize("source", ["terraform", "kubernetes"])
def test_every_deployment_supplies_what_a_deployed_environment_needs(source: str):
    """The reverse of the drift test above, and the one that was missing.

    That test asks whether every variable a deployment sets is a setting. This
    asks whether every setting a deployment needs is set - and until it existed,
    neither the Terraform nor the manifests supplied JWT_PRIVATE_KEY, so the
    first apply of either would have started an API and a worker that refused
    to boot.
    """
    laptop = _laptop_defaults()
    assert {"DATABASE_URL", "REDIS_URL", "OLLAMA_BASE_URL"} <= laptop, (
        "the localhost-default scan found less than it should; check its pattern"
    )
    needed = set(REQUIRED_OUTSIDE_DEVELOPMENT) | laptop

    supplied = _terraform_supplied() if source == "terraform" else _kubernetes_supplied()

    missing = needed - supplied
    assert not missing, (
        f"{source} does not supply {sorted(missing)}: a deployment either refuses "
        "to start without these or runs against a developer's localhost"
    )


def test_a_declared_secret_that_has_not_been_written_reads_as_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    """The placeholder must be non-empty for AWS and blank for the application.

    Terraform's provider sends `secret_string` only when it is set, and an
    empty string is not, so `""` reaches PutSecretValue as no value and the
    first apply fails. The placeholder is therefore a space - which only works
    because the settings layer strips a credential before deciding it is
    absent. Every declared name is checked, because the stripping is a list of
    fields in config.py and a secret missing from that list would turn the
    placeholder into a one-character API key.
    """
    module = (TERRAFORM / "modules" / "secrets" / "main.tf").read_text(encoding="utf-8")
    resource = 'resource "aws_secretsmanager_secret_version" "declared_placeholder"'
    block = module.split(resource, 1)[1]
    placeholder = json.loads(re.search(r'secret_string\s*=\s*("[^"]*")', block).group(1))
    assert placeholder, "an empty placeholder is dropped by the provider and refused by AWS"

    variables = (TERRAFORM / "variables.tf").read_text(encoding="utf-8")
    provider_block = variables.split('variable "provider_secret_names"', 1)[1]
    locals_hcl = (TERRAFORM / "locals.tf").read_text(encoding="utf-8")
    declared = _hcl_string_list(provider_block, "default") | _hcl_string_list(
        locals_hcl, "required_secret_names"
    )
    assert "JWT_PRIVATE_KEY" in declared

    settings = _isolated_settings(
        monkeypatch, app_env="test", **{name.lower(): placeholder for name in declared}
    )
    for name in declared:
        assert getattr(settings, name.lower()) is None, (
            f"{name} reads the placeholder {placeholder!r} as a value"
        )


# ---------------------------------------------------------------------------
# The embedding service
# ---------------------------------------------------------------------------


def _terraform_local(name: str) -> str:
    locals_hcl = (TERRAFORM / "locals.tf").read_text(encoding="utf-8")
    match = re.search(rf'^\s*{name}\s*=\s*"([^"]+)"', locals_hcl, re.M)
    assert match, f"local.{name} is not a string literal in locals.tf"
    return match.group(1)


def test_the_deployed_embedding_model_is_the_one_the_embedding_service_serves():
    """Four files name the embedding model, and a disagreement is silent.

    The registry declares it, the deployments pin it, the embedding service
    pulls it and the vector column is sized for it. If the pinned key is not an
    Ollama embedding model, or the service pulls a different name than the
    registry asks for, ingestion fails to embed and retrieval runs on its
    lexical arm alone - every run still completes, and nothing says why search
    got worse.
    """
    from app.core.enums import LlmProvider
    from app.db.models.source import EMBEDDING_DIMENSIONS
    from app.models.registry import load_registry

    key = _terraform_local("embedding_model_key")
    model = _terraform_local("ollama_model")
    pin = _terraform_local("ollama_model_pin")

    spec = load_registry(None).specs[key]
    assert spec.supports_embeddings, f"{key} is not an embedding model"
    assert spec.provider is LlmProvider.OLLAMA, f"{key} is not served by Ollama"
    assert spec.model_id == model, "the service would pull a model the registry does not ask for"
    assert spec.embedding_dimensions == EMBEDDING_DIMENSIONS
    # A version, not `latest`: a pointer the model library can move would let a
    # task started next month embed with different weights than the index.
    assert pin.startswith(f"{model}:") and not pin.endswith(":latest")

    locals_hcl = (TERRAFORM / "locals.tf").read_text(encoding="utf-8")
    assert re.search(r"EMBEDDING_MODEL\s*=\s*local\.embedding_model_key", locals_hcl)

    configmap = _kubernetes_named("ConfigMap", "aether-config")["data"]
    assert configmap["EMBEDDING_MODEL"] == key

    deployment = _kubernetes_named("Deployment", "aether-ollama")
    container = _pod_containers(deployment)[0]
    env = {item["name"]: item["value"] for item in container["env"]}
    assert env["OLLAMA_MODEL"] == model
    assert env["OLLAMA_MODEL_PIN"] == pin
    assert container["readinessProbe"]["exec"]["command"][-1] == model

    variables = (TERRAFORM / "variables.tf").read_text(encoding="utf-8")
    image = re.search(r'variable "ollama_image"[\s\S]*?default\s*=\s*"([^"]+)"', variables).group(1)
    assert container["image"] == image, "the two deployments run different Ollama builds"


def test_the_application_is_pointed_at_the_embedding_service_the_deployment_runs():
    """OLLAMA_BASE_URL names the service by the name and port it is published on."""
    port = int(_terraform_local_number("ollama_port"))

    locals_hcl = (TERRAFORM / "locals.tf").read_text(encoding="utf-8")
    assert re.search(
        r'OLLAMA_BASE_URL\s*=\s*"http://\$\{module\.discovery\.service_hostnames\["ollama"\]\}'
        r':\$\{local\.ollama_port\}"',
        locals_hcl,
    )
    main_hcl = (TERRAFORM / "main.tf").read_text(encoding="utf-8")
    discovery = main_hcl.split('module "discovery"', 1)[1].split("\n}\n", 1)[0]
    assert re.search(r'services\s*=\s*\[[^\]]*"ollama"', discovery)
    ollama = main_hcl.split('module "ollama_service"', 1)[1].split("\n}\n", 1)[0]
    assert re.search(r"container_port\s*=\s*local\.ollama_port", ollama)
    assert 'module.discovery.service_arns["ollama"]' in ollama

    service = _kubernetes_named("Service", "aether-ollama")
    url = _kubernetes_named("ConfigMap", "aether-config")["data"]["OLLAMA_BASE_URL"]
    assert url == f"http://{service['metadata']['name']}:{service['spec']['ports'][0]['port']}"
    assert service["spec"]["ports"][0]["port"] == port


def _terraform_local_number(name: str) -> str:
    locals_hcl = (TERRAFORM / "locals.tf").read_text(encoding="utf-8")
    match = re.search(rf"^\s*{name}\s*=\s*(\d+)\s*$", locals_hcl, re.M)
    assert match, f"local.{name} is not a number literal in locals.tf"
    return match.group(1)


def test_both_deployments_start_the_embedding_service_with_the_same_script():
    """One script, carried twice; a fix to one copy must reach the other."""
    script = (TERRAFORM / "files" / "ollama-entrypoint.sh").read_text(encoding="utf-8")

    main_hcl = (TERRAFORM / "main.tf").read_text(encoding="utf-8")
    ollama = main_hcl.split('module "ollama_service"', 1)[1].split("\n}\n", 1)[0]
    assert 'file("${path.module}/files/ollama-entrypoint.sh")' in ollama

    configmap = _kubernetes_named("ConfigMap", "aether-ollama-entrypoint")
    assert configmap["data"]["entrypoint.sh"] == script


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


def test_the_web_runtime_carries_no_package_manager():
    """The base image's npm is where every finding the image scan reported lived.

    Ten HIGH and one CRITICAL, all inside npm's own dependency tree, none of it
    loaded by the Next server - and build.yml refuses to push an image with a
    HIGH finding, so until npm was removed no web image could be published at
    all. Put back, or dropped by a rewrite of the runtime stage, and the next
    advisory against npm's dependencies blocks every release again.
    """
    runtime = DOCKERFILE_WEB.read_text(encoding="utf-8").split(" AS runtime", 1)[1]
    removals = " ".join(
        instruction
        for instruction in re.split(r"\n(?=[A-Z]+\s)", runtime)
        if instruction.startswith("RUN") and "rm -rf" in instruction
    )
    for path in ("/usr/local/lib/node_modules", "/usr/local/bin/npm", "/usr/local/bin/npx"):
        assert path in removals, f"the web runtime stage no longer removes {path}"


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


def test_the_two_service_resources_differ_only_in_who_owns_the_revision():
    """Terraform cannot make a lifecycle argument conditional, so there are two.

    modules/ecs-service declares the service twice - once ignoring the task
    definition, for the images deploy.yml rolls, and once not, for the Ollama
    image Terraform rolls - and everything else about them must be identical.
    An edit to the network, the load balancer or the deployment settings that
    reaches one and not the other would apply to some services and not others,
    with nothing in the plan saying so.
    """
    module = (TERRAFORM / "modules" / "ecs-service" / "main.tf").read_text(encoding="utf-8")

    def body(name: str) -> str:
        text = module.split(f'resource "aws_ecs_service" "{name}" {{', 1)[1].split("\n}\n", 1)[0]
        text = re.sub(r"^\s*count\s*=.*$", "", text, flags=re.M)
        return re.sub(r"\n  lifecycle \{[\s\S]*?\n  \}\n", "\n", text)

    pipeline, terraform = body("this"), body("terraform_owned")
    assert pipeline == terraform

    owned = module.split('resource "aws_ecs_service" "terraform_owned"', 1)[1]
    assert re.search(r"ignore_changes\s*=\s*\[desired_count\]", owned), (
        "the Terraform-owned service must not ignore its task definition, or an "
        "image bump applies and rolls nothing"
    )


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


# ---------------------------------------------------------------------------
# The deploy role and the workflow that assumes it
# ---------------------------------------------------------------------------

DEPLOY_WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"
DEPLOY_ROLE = TERRAFORM / "modules" / "deploy-role" / "main.tf"

#: AWS operations a step makes through an action rather than a `run:` block,
#: which the scan below cannot see. Each is what that action calls.
ACTION_CALLS = {
    "aws-actions/amazon-ecr-login": {"ecr:GetAuthorizationToken"},
    "aws-actions/amazon-ecs-render-task-definition": {"ecs:DescribeTaskDefinition"},
}

#: `aws ecs wait <waiter>` polls a describe call, which is what IAM sees.
WAITERS = {
    "services-stable": "ecs:DescribeServices",
    "tasks-stopped": "ecs:DescribeTasks",
}


def _deploy_workflow() -> dict:
    return yaml.safe_load(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))


def _deploy_calls() -> set[str]:
    """Every IAM action deploy.yml needs, read off what it actually runs."""
    steps = [step for job in _deploy_workflow()["jobs"].values() for step in job.get("steps", [])]
    scripts = "\n".join(step.get("run", "") for step in steps)

    services = set(re.findall(r"\baws ([a-z0-9-]+) [a-z]", scripts))
    assert services == {"ecs"}, (
        f"deploy.yml now calls AWS services {sorted(services)}; map them here and "
        "grant them in modules/deploy-role"
    )

    calls: set[str] = set()
    for operation, waiter in re.findall(r"\baws ecs ([a-z-]+)(?: ([a-z-]+))?", scripts):
        if operation == "wait":
            calls.add(WAITERS[waiter])
        else:
            calls.add("ecs:" + "".join(part.capitalize() for part in operation.split("-")))

    for step in steps:
        calls |= ACTION_CALLS.get(step.get("uses", "").split("@", 1)[0], set())

    # `imagetools create` copies the images into ECR, which is a push.
    if "imagetools create" in scripts:
        calls |= {"ecr:PutImage", "ecr:InitiateLayerUpload", "ecr:CompleteLayerUpload"}
    return calls


def test_the_deploy_role_grants_every_aws_call_the_deploy_workflow_makes():
    """Written in two files, and a call the role does not grant fails mid-deploy.

    Mid-deploy is the worst place for it: after the images are mirrored and the
    revisions registered, with the rollback step about to run under the same
    role. The workflow has never executed, so this is the only thing that has
    compared the two.
    """
    calls = _deploy_calls()
    assert "ecs:RunTask" in calls, "the scan found less than it should; check its pattern"

    role = DEPLOY_ROLE.read_text(encoding="utf-8")
    granted = set(re.findall(r'"((?:ecs|ecr|iam):[A-Za-z]+)"', role))

    missing = calls - granted
    assert not missing, f"deploy.yml makes {sorted(missing)}, which the deploy role does not grant"
    # Registering or running a task definition passes the roles it names.
    assert "iam:PassRole" in granted


def test_only_the_deploy_job_of_the_matching_environment_can_assume_the_role():
    """The trust is one GitHub environment of one repository, compared exactly.

    The environment is where the deploy job's approval rule lives, so a role
    trusting the repository rather than the environment could be assumed by a
    workflow on any branch - and a StringLike with a wildcard is how that
    happens without anybody writing "any branch".
    """
    role = DEPLOY_ROLE.read_text(encoding="utf-8")
    assert '"repo:${var.github_repository}:environment:${var.github_environment}"' in role
    assert not re.search(r'test\s*=\s*"StringLike"', role)

    main_hcl = (TERRAFORM / "main.tf").read_text(encoding="utf-8")
    block = main_hcl.split('module "deploy_role"', 1)[1].split("\n}\n", 1)[0]
    assert re.search(r"github_environment\s*=\s*var\.environment", block)

    # And the names line up: a GitHub environment the workflow deploys to is a
    # Terraform environment whose role trusts it.
    workflow = _deploy_workflow()
    # PyYAML reads the bare key `on` as the boolean true.
    options = set(workflow[True]["workflow_dispatch"]["inputs"]["environment"]["options"])
    environments = {
        re.search(r'^environment\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M).group(1)
        for path in (TERRAFORM / "environments").glob("*.tfvars")
    }
    assert options <= environments


def test_the_terraform_outputs_supply_every_variable_the_deploy_workflow_reads():
    """Setting up a GitHub environment is copying one output, not a scavenger hunt."""
    read = set(re.findall(r"\bvars\.([A-Z0-9_]+)", DEPLOY_WORKFLOW.read_text(encoding="utf-8")))
    assert read, "the scan found no variables; check its pattern"

    outputs = (TERRAFORM / "outputs.tf").read_text(encoding="utf-8")
    block = outputs.split('output "github_environment_variables"', 1)[1].split("\n}\n", 1)[0]
    supplied = set(re.findall(r"^\s{4}([A-Z0-9_]+)\s*=", block, re.M))

    missing = read - supplied
    assert not missing, f"deploy.yml reads {sorted(missing)}, which no Terraform output supplies"
