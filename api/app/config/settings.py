from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application configuration, sourced from environment variables.

    Never hardcode secrets here. See .env.example for the documented shape.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"

    control_database_url: str = (
        "postgresql+psycopg://origami:origami_dev_password@localhost:5433/origami_control"
    )
    tenant_database_url: str = (
        "postgresql+psycopg://origami:origami_dev_password@localhost:5434/origami_tenant_shared"
    )

    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = "origami"
    s3_secret_access_key: str = "origami_dev_password"
    s3_bucket_name: str = "origami-server-local"
    s3_region: str = "us-east-1"

    oidc_issuer: str = "http://localhost:8081/realms/origami"
    oidc_audience: str = "origami-server"
    oidc_jwks_url: str = "http://localhost:8081/realms/origami/protocol/openid-connect/certs"
    # Local-only escape hatch: signs/verifies internal session tokens with
    # app_secret_key instead of validating against a live OIDC provider.
    # Must never be true outside local/test environments.
    auth_dev_mode: bool = False

    license_lease_private_key_path: str = "./infrastructure/keys/license_lease_private.pem"
    license_lease_public_key_path: str = "./infrastructure/keys/license_lease_public.pem"
    license_lease_policy_version: int = 1
    license_lease_default_ttl_hours: int = 72

    # How long a console sign-in lasts before it has to be repeated. Short
    # by comparison with farmos_token_ttl_days below on purpose: this one
    # carries platform-wide authority, the tablet's carries one farm's.
    platform_session_ttl_hours: int = 12

    app_secret_key: str = "change-me-in-every-environment"
    # Who may call this API from a browser. Comma-separated, and compared
    # against the Origin header, which is scheme://host[:port] and never
    # carries a path or a trailing slash — cors_origins_list below strips
    # one if it is given anyway, because pasting a URL from the address bar
    # is the obvious way to fill this in and a silent non-match is a
    # miserable thing to debug.
    #
    # The deployed host is here so an image works out of the box where it
    # is actually running; localhost:3000 stays for admin-web's dev server.
    # Neither covers a tablet app, whose origin is its own (a WebView's
    # localhost, or null from a file:// build) — set CORS_ALLOWED_ORIGINS
    # for the deployment to add it.
    cors_allowed_origins: str = "https://leb-container-test-staging.azurewebsites.net,http://localhost:3000"
    log_level: str = "INFO"

    # Built admin console (admin-web's static export), served by this app at
    # / so the whole product ships as one container. Relative to the API's
    # working directory, which is api/ in development; the image sets an
    # absolute path. When the directory is absent the API simply runs
    # without a console — that is the normal state for a test run.
    admin_web_dir: str = "../admin-web/out"

    # Outbound mail. Unset by default, and the product is honest about
    # that rather than silently dropping messages: an invitation whose
    # email cannot be sent is still shown to the admin as a link to pass
    # on by hand (see app/notifications/email.py).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_starttls: bool = True
    smtp_use_ssl: bool = False
    email_from: str = ""

    # The address invited people are sent to — this deployment's own public
    # URL. Used to build invitation links, so it has to be what a customer
    # can actually open, not an internal hostname.
    public_base_url: str = "http://localhost:8000"

    # FarmOS tablet app's own username/password login (app/farmos/) — a
    # long TTL is deliberate: "log in once, stay logged in" per the
    # contract, with GET /auth/me re-validating the stored token on each
    # relaunch rather than a refresh flow.
    farmos_token_ttl_days: int = 180

    @property
    def cors_origins_list(self) -> list[str]:
        """The allowlist, normalised to what a browser actually sends.

        A trailing slash is stripped rather than honoured: an Origin header
        is only ever scheme://host[:port], so "https://example.com/" would
        match nothing, and the failure is invisible — the browser blocks
        the request and the server logs nothing at all. "*" is left alone;
        it is a wildcard, not a URL.
        """
        origins = []
        for raw in self.cors_allowed_origins.split(","):
            origin = raw.strip()
            if not origin:
                continue
            origins.append(origin if origin == "*" else origin.rstrip("/"))
        return origins

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
