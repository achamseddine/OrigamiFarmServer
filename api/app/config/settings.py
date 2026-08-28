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

    app_secret_key: str = "change-me-in-every-environment"
    cors_allowed_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    # FarmOS tablet app's own username/password login (app/farmos/) — a
    # long TTL is deliberate: "log in once, stay logged in" per the
    # contract, with GET /auth/me re-validating the stored token on each
    # relaunch rather than a refresh flow.
    farmos_token_ttl_days: int = 180

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
