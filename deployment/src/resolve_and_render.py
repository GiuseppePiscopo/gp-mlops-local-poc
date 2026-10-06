#!/usr/bin/env python

"""Resolve an MLflow model alias and render a KServe InferenceService manifest.

The configured alias is resolved to a model version, which must be READY. The
model's MLflow download URI is translated to the configured object-storage URI,
then used to render and validate the manifest. The output directory receives
the manifest and a JSON lock file recording the resolved model provenance.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from mlflow.entities.model_registry import ModelVersion

from mlflow import MlflowClient

DEFAULT_CONFIG_PATH = Path("/app/config/deployment-target.local.yaml")
DEFAULT_TEMPLATE_DIRECTORY = Path("/app/templates")
DEFAULT_TEMPLATE_NAME = "inferenceservice.yaml.j2"


@dataclass(frozen=True)
class KServeSettings:
    namespace: str
    inference_service_name: str
    service_account_name: str
    storage_secret_name: str
    runtime: str
    model_format: str
    protocol_version: str


@dataclass(frozen=True)
class DeploymentSettings:
    tracking_uri: str
    artifact_proxy_uri_prefix: str
    artifact_storage_uri_prefix: str
    registered_model: str
    alias: str
    kserve: KServeSettings
    output_directory: Path


@dataclass(frozen=True)
class ModelResolution:
    registered_model: str
    requested_alias: str
    resolved_version: str
    source_run_id: str
    model_version_source_uri: str
    model_version_download_uri: str
    resolved_storage_uri: str
    status: str


@dataclass(frozen=True)
class DeploymentResult:
    resolution: ModelResolution
    rendered_manifest_path: str
    resolution_lock_path: str


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve an MLflow alias and render a KServe InferenceService manifest."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Deployment configuration file. Default: {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--registered-model",
        help="Override the registered model configured in the YAML file.",
    )
    parser.add_argument(
        "--alias",
        help="Override the model alias configured in the YAML file.",
    )
    return parser.parse_args()


def load_configuration(config_path: Path) -> dict:
    try:
        with config_path.open(encoding="utf-8") as config_file:
            configuration = yaml.safe_load(config_file)
    except OSError as error:
        raise ValueError(f"Unable to read configuration file '{config_path}': {error}") from error
    except yaml.YAMLError as error:
        raise ValueError(f"Unable to parse configuration file '{config_path}': {error}") from error

    if not isinstance(configuration, dict):
        raise ValueError(f"Configuration file '{config_path}' must contain a YAML mapping.")

    return configuration


def require_string(configuration: dict, path: tuple[str, ...]) -> str:
    value = configuration

    for key in path:
        if not isinstance(value, dict) or key not in value:
            dotted_path = ".".join(path)
            raise ValueError(f"Missing required configuration value: {dotted_path}")
        value = value[key]

    if not isinstance(value, str) or not value.strip():
        dotted_path = ".".join(path)
        raise ValueError(f"Configuration value must be a non-empty string: {dotted_path}")

    return value.strip()


def load_deployment_settings(arguments: argparse.Namespace) -> DeploymentSettings:
    configuration = load_configuration(arguments.config)

    kserve = KServeSettings(
        namespace=require_string(configuration, ("kserve", "namespace")),
        inference_service_name=require_string(
            configuration,
            ("kserve", "inference_service_name"),
        ),
        service_account_name=require_string(
            configuration,
            ("kserve", "service_account_name"),
        ),
        storage_secret_name=require_string(
            configuration,
            ("kserve", "storage_secret_name"),
        ),
        runtime=require_string(configuration, ("kserve", "runtime")),
        model_format=require_string(configuration, ("kserve", "model_format")),
        protocol_version=require_string(configuration, ("kserve", "protocol_version")),
    )

    return DeploymentSettings(
        tracking_uri=require_string(configuration, ("mlflow", "tracking_uri")),
        artifact_proxy_uri_prefix=require_string(
            configuration,
            ("mlflow", "artifact_proxy_uri_prefix"),
        ),
        artifact_storage_uri_prefix=require_string(
            configuration,
            ("mlflow", "artifact_storage_uri_prefix"),
        ),
        registered_model=(arguments.registered_model or require_string(configuration, ("model", "registered_model"))),
        alias=arguments.alias or require_string(configuration, ("model", "alias")),
        kserve=kserve,
        output_directory=Path(require_string(configuration, ("output", "directory"))),
    )


def resolve_model_version(
    client: MlflowClient,
    registered_model: str,
    alias: str,
) -> ModelVersion:
    return client.get_model_version_by_alias(
        name=registered_model,
        alias=alias,
    )


def ensure_model_version_ready(model_version: ModelVersion) -> None:
    if str(model_version.status) != "READY":
        raise ValueError(
            f"Model version '{model_version.name}' version "
            f"'{model_version.version}' is not READY: {model_version.status}"
        )


def resolve_model_download_uri(
    client: MlflowClient,
    model_version: ModelVersion,
) -> str:
    return client.get_model_version_download_uri(
        name=model_version.name,
        version=model_version.version,
    )


def translate_artifact_uri(
    download_uri: str,
    artifact_proxy_uri_prefix: str,
    artifact_storage_uri_prefix: str,
) -> str:
    if not download_uri.startswith(artifact_proxy_uri_prefix):
        raise ValueError(f"Model download URI does not use the configured artifact proxy prefix: '{download_uri}'")

    relative_path = download_uri.removeprefix(artifact_proxy_uri_prefix)

    return f"{artifact_storage_uri_prefix.rstrip('/')}/{relative_path.lstrip('/')}"


def resolve_deployment(settings: DeploymentSettings) -> ModelResolution:
    client = MlflowClient(tracking_uri=settings.tracking_uri)

    model_version = resolve_model_version(
        client=client,
        registered_model=settings.registered_model,
        alias=settings.alias,
    )

    ensure_model_version_ready(model_version)

    download_uri = resolve_model_download_uri(
        client=client,
        model_version=model_version,
    )

    storage_uri = translate_artifact_uri(
        download_uri=download_uri,
        artifact_proxy_uri_prefix=settings.artifact_proxy_uri_prefix,
        artifact_storage_uri_prefix=settings.artifact_storage_uri_prefix,
    )

    return ModelResolution(
        registered_model=model_version.name,
        requested_alias=settings.alias,
        resolved_version=model_version.version,
        source_run_id=model_version.run_id,
        model_version_source_uri=model_version.source,
        model_version_download_uri=download_uri,
        resolved_storage_uri=storage_uri,
        status=str(model_version.status),
    )


def render_manifest(
    settings: DeploymentSettings,
    resolution: ModelResolution,
) -> str:
    environment = Environment(
        loader=FileSystemLoader(DEFAULT_TEMPLATE_DIRECTORY),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )

    template = environment.get_template(DEFAULT_TEMPLATE_NAME)

    return template.render(
        settings=settings,
        resolution=resolution,
    )


def validate_manifest(manifest_content: str) -> None:
    try:
        manifest = yaml.safe_load(manifest_content)
    except yaml.YAMLError as error:
        raise ValueError(f"Rendered manifest is not valid YAML: {error}") from error

    if not isinstance(manifest, dict):
        raise ValueError("Rendered manifest must contain one YAML mapping.")

    if manifest.get("kind") != "InferenceService":
        raise ValueError("Rendered manifest kind must be InferenceService.")


def write_manifest(
    settings: DeploymentSettings,
    resolution: ModelResolution,
    manifest_content: str,
) -> Path:
    output_directory = settings.output_directory
    output_directory.mkdir(parents=True, exist_ok=True)

    output_path = output_directory / (f"{settings.kserve.inference_service_name}-v{resolution.resolved_version}.yaml")
    temporary_path = output_path.with_suffix(".yaml.tmp")

    try:
        temporary_path.write_text(manifest_content, encoding="utf-8")
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return output_path


def write_resolution_lock(
    manifest_path: Path,
    resolution: ModelResolution,
) -> Path:
    lock_path = manifest_path.with_suffix(".resolution.json")
    temporary_path = lock_path.with_suffix(".resolution.json.tmp")

    lock_content = {
        "rendered_manifest_filename": manifest_path.name,
        "resolution": asdict(resolution),
    }

    try:
        temporary_path.write_text(
            json.dumps(lock_content, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(lock_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return lock_path


def execute_deployment(arguments: argparse.Namespace) -> DeploymentResult:
    settings = load_deployment_settings(arguments)
    resolution = resolve_deployment(settings)

    manifest_content = render_manifest(
        settings=settings,
        resolution=resolution,
    )

    validate_manifest(manifest_content)

    output_path = write_manifest(
        settings=settings,
        resolution=resolution,
        manifest_content=manifest_content,
    )

    lock_path = write_resolution_lock(
        manifest_path=output_path,
        resolution=resolution,
    )

    return DeploymentResult(
        resolution=resolution,
        rendered_manifest_path=str(output_path),
        resolution_lock_path=str(lock_path),
    )


def main() -> int:
    arguments = parse_arguments()

    try:
        result = execute_deployment(arguments)
    except Exception as error:
        print(f"Deployment resolution failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
