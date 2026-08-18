from __future__ import annotations

from ipaddress import ip_address
from time import sleep
from urllib.parse import urlparse

from apps.storage.repositories.models import Repository
from apps.storage.services.internal.kopia_cli import (
    KopiaCliError,
    KopiaRepositoryAlreadyExistsError,
    connect_s3_repository,
    create_s3_repository,
    status as kopia_status,
)
from apps.storage.services.internal.repository_errors import (
    RepositoryAlreadyExistsError,
)
from apps.storage.services.internal.repository_ownership import (
    RepositoryOwnershipError,
    S3RepositoryInitializationState,
    establish_s3_repository_ownership,
    inspect_s3_repository_initialization,
    reset_s3_legacy_marker_for_initialization_recovery,
    verify_s3_repository_ownership,
)
from apps.storage.services.internal.s3_client import (
    S3ClientError,
    check_s3_bucket_readable,
    create_s3_bucket,
    delete_s3_bucket_if_empty,
    list_s3_buckets,
    list_s3_buckets_by_region,
    verify_s3_bucket_rw,
)
from apps.storage.services.internal.repository_secrets import (
    resolve_repository_secrets,
    scrub_secrets,
    secret_values_for_scrub,
)
from apps.storage.services.internal.repository_endpoints import (
    repository_control_endpoint,
)
from apps.storage.services.internal.s3_url_style import (
    S3_URL_STYLE_AUTO,
    S3_URL_STYLE_PATH,
    S3_URL_STYLE_VIRTUAL_HOSTED,
    normalize_s3_url_style,
)


class RepositoryInitializationError(Exception):
    pass


S3_URL_STYLE_PROBE_ATTEMPTS = 3
S3_URL_STYLE_PROBE_RETRY_DELAYS = (1, 2)


class S3UrlStyleProbeError(RepositoryInitializationError):
    pass


def resolve_s3_url_style(*, s3_url_style: str | None, **bucket_args) -> str:
    """Resolve Auto to a concrete S3 URL style using the target bucket."""
    normalized = normalize_s3_url_style(s3_url_style)
    if normalized != S3_URL_STYLE_AUTO:
        return normalized
    probe_args = {
        key: value for key, value in bucket_args.items() if key != "s3_url_style"
    }
    failures: dict[str, str] = {}
    successful_styles: set[str] = set()
    for style in (S3_URL_STYLE_PATH, S3_URL_STYLE_VIRTUAL_HOSTED):
        if style == S3_URL_STYLE_VIRTUAL_HOSTED and _s3_endpoint_is_ip_literal(
            bucket_args.get("endpoint")
        ):
            # botocore silently falls back to path-style for IP endpoints even
            # when asked for virtual addressing. Kopia does not, so consider
            # this candidate incompatible rather than accepting a false probe.
            failures[style] = (
                "Virtual Hosted Style requires a DNS endpoint, not an IP address."
            )
            continue
        for attempt in range(S3_URL_STYLE_PROBE_ATTEMPTS):
            try:
                check_s3_bucket_readable(**probe_args, s3_url_style=style)
                successful_styles.add(style)
                break
            except S3ClientError as exc:
                failures[style] = str(exc)
                if attempt < len(S3_URL_STYLE_PROBE_RETRY_DELAYS):
                    sleep(S3_URL_STYLE_PROBE_RETRY_DELAYS[attempt])

    if S3_URL_STYLE_VIRTUAL_HOSTED in successful_styles:
        return S3_URL_STYLE_VIRTUAL_HOSTED
    if S3_URL_STYLE_PATH in successful_styles:
        return S3_URL_STYLE_PATH

    raise S3UrlStyleProbeError(
        "Unable to determine S3 URL Style. "
        f"Virtual Hosted: {failures.get(S3_URL_STYLE_VIRTUAL_HOSTED, 'failed')}; "
        f"Path: {failures.get(S3_URL_STYLE_PATH, 'failed')}."
    )


def _s3_endpoint_is_ip_literal(endpoint: object) -> bool:
    raw = str(endpoint or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    try:
        ip_address(parsed.hostname or "")
    except ValueError:
        return False
    return True


def initialize_s3_repository(
    repository: Repository,
    *,
    recovery: bool = False,
) -> None:
    """Initialize or recover an S3-backed Kopia repository."""
    config = dict(repository.config or {})
    secrets_payload = resolve_repository_secrets(repository)
    try:
        bucket_args = dict(
            endpoint=repository_control_endpoint(config),
            region=str(config.get("region") or ""),
            bucket=str(repository.s3_bucket or ""),
            access_key_id=str(config.get("access_key_id") or ""),
            secret_access_key=str(secrets_payload.get("secret_access_key") or ""),
            s3_url_style=normalize_s3_url_style(
                config.get("s3_url_style"), platform=repository.s3_platform
            ),
            use_tls=config.get("use_tls") is not False,
        )
        bucket_created = False
        if repository.s3_bucket_mode == Repository.S3BucketMode.NEW:
            bucket_created = create_s3_bucket(
                **bucket_args,
                allow_existing_owned=recovery,
            )
        if bucket_args["s3_url_style"] == S3_URL_STYLE_AUTO:
            try:
                resolved_url_style = resolve_s3_url_style(**bucket_args)
            except S3UrlStyleProbeError as exc:
                if (
                    repository.s3_bucket_mode != Repository.S3BucketMode.NEW
                    or not bucket_created
                ):
                    raise
                rollback = delete_s3_bucket_if_empty(**bucket_args)
                rollback_detail = ""
                if rollback.get("status") != "deleted":
                    rollback_detail = (
                        f" The newly created bucket could not be removed: "
                        f"{rollback.get('reason', 'unknown error')}."
                    )
                raise RepositoryInitializationError(
                    _sanitize(f"{exc}{rollback_detail}", repository)
                ) from exc
        else:
            resolved_url_style = bucket_args["s3_url_style"]
            if repository.s3_bucket_mode != Repository.S3BucketMode.NEW:
                check_s3_bucket_readable(**bucket_args)

        if config.get("s3_url_style") != resolved_url_style:
            config["s3_url_style"] = resolved_url_style
            repository.config = config
            repository.save(update_fields=["config", "updated_at"])
        # Kopia requires an empty Bucket+Prefix during first initialization.
        # Persisted repositories therefore inspect without writing the HFL
        # owner marker, initialize Kopia first, and establish ownership only
        # after the physical repository exists. Unsaved unit/legacy callers
        # retain the low-level create-only behavior and cannot authorize any
        # later destructive lifecycle operation.
        if repository.pk is None:
            create_s3_repository(repository)
            return

        initialization_state = inspect_s3_repository_initialization(repository)
        if initialization_state == S3RepositoryInitializationState.OWNED:
            try:
                connect_s3_repository(repository)
                kopia_status(repository)
            except KopiaCliError:
                if not recovery:
                    raise
                reset_s3_legacy_marker_for_initialization_recovery(repository)
                create_s3_repository(repository)
                establish_s3_repository_ownership(repository)
            return
        if initialization_state == S3RepositoryInitializationState.OCCUPIED:
            if not recovery:
                raise RepositoryAlreadyExistsError(
                    "The selected object Prefix already contains data."
                )
            try:
                connect_s3_repository(repository)
                kopia_status(repository)
            except KopiaCliError as exc:
                raise RepositoryAlreadyExistsError(
                    _sanitize(
                        "The residual object Prefix is not a recoverable Kopia repository.",
                        repository,
                    )
                ) from exc
            establish_s3_repository_ownership(repository)
            return

        create_s3_repository(repository)
        establish_s3_repository_ownership(repository)
    except S3UrlStyleProbeError as exc:
        raise RepositoryInitializationError(_sanitize(str(exc), repository)) from exc
    except KopiaRepositoryAlreadyExistsError as exc:
        raise RepositoryAlreadyExistsError(_sanitize(str(exc), repository)) from exc
    except (S3ClientError, KopiaCliError, RepositoryOwnershipError) as exc:
        raise RepositoryInitializationError(_sanitize(str(exc), repository)) from exc


def validate_s3_connection(
    *,
    platform: str | None = None,
    endpoint: str | None,
    region: str | None,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
) -> list[str]:
    try:
        normalized_platform = str(platform or "").strip().lower()
        normalized_region = str(region or "").strip()
        if (
            normalized_platform
            and normalized_platform != Repository.S3Platform.CUSTOM
            and normalized_region
        ):
            return list_s3_buckets_by_region(
                platform=normalized_platform,
                endpoint=endpoint,
                region=normalized_region,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                s3_url_style=s3_url_style,
                use_tls=use_tls,
            )
        return list_s3_buckets(
            endpoint=endpoint,
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            s3_url_style=s3_url_style,
            use_tls=use_tls,
        )
    except S3ClientError as exc:
        raise RepositoryInitializationError(
            _sanitize(
                str(exc),
                {
                    "access_key_id": access_key_id,
                    "secret_access_key": secret_access_key,
                },
            )
        ) from exc


def verify_s3_bucket_access(
    *,
    endpoint: str | None,
    region: str | None,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    s3_url_style: str | None = None,
    use_tls: bool = True,
) -> dict:
    try:
        return verify_s3_bucket_rw(
            endpoint=endpoint,
            region=region,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            s3_url_style=s3_url_style,
            use_tls=use_tls,
        )
    except S3ClientError as exc:
        raise RepositoryInitializationError(
            _sanitize(
                str(exc),
                {
                    "access_key_id": access_key_id,
                    "secret_access_key": secret_access_key,
                },
            )
        ) from exc


def check_s3_repository(
    repository: Repository,
    *,
    refresh_namespace: bool = False,
    adopt_legacy_ownership: bool = True,
) -> None:
    config = repository.config or {}
    secrets_payload = resolve_repository_secrets(repository)
    try:
        check_s3_bucket_readable(
            endpoint=repository_control_endpoint(config),
            region=str(config.get("region") or ""),
            bucket=str(repository.s3_bucket or ""),
            access_key_id=str(config.get("access_key_id") or ""),
            secret_access_key=str(secrets_payload.get("secret_access_key") or ""),
            s3_url_style=normalize_s3_url_style(
                config.get("s3_url_style"), platform=repository.s3_platform
            ),
            use_tls=config.get("use_tls") is not False,
        )
        connect_s3_repository(repository)
        kopia_status(repository)
        verify_s3_repository_ownership(
            repository,
            adopt_legacy=adopt_legacy_ownership,
            refresh_namespace=refresh_namespace,
        )
    except (S3ClientError, KopiaCliError, RepositoryOwnershipError) as exc:
        raise RepositoryInitializationError(_sanitize(str(exc), repository)) from exc


def _sanitize(message: str, source) -> str:
    if isinstance(source, Repository):
        config = source.config or {}
        try:
            secrets_payload = resolve_repository_secrets(source)
        except Exception:
            secrets_payload = {}
    else:
        config = source or {}
        secrets_payload = config
    return str(
        scrub_secrets(
            message,
            extra_values=secret_values_for_scrub(None, secrets_payload)
            + [str(config.get("access_key_id") or "")],
        )
    )
