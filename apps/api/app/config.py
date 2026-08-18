"""API configuration from environment (US-1.8)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str
    supabase_publishable_key: str
    cors_origins: str = "http://localhost:3000"
    database_url: str = ""
    # US-87.6: the API's Postgres connection pool. `max_size` is a SHARED
    # budget — Supabase counts connections across this API, the web app's
    # server components and any direct session — so exhausting it is a worse
    # outage than the connection churn pooling replaces. Raise deliberately,
    # with the whole fleet counted, not because a graph looked busy.
    #
    # us-119.1: 10 -> 20. The runner-facing handlers now run their database
    # calls on the executor below instead of the event loop, so up to
    # `db_executor_threads` of them can want a connection at once, and a pool
    # of 10 would just move the queue from the loop to the pool. 20 assumes
    # DATABASE_URL points at Supabase's transaction pooler (Supavisor, the
    # `pgbouncer.get_auth` evidence in pool.py) — where 20 client connections
    # are cheap. Against the direct 5432 port it would be a fifth of the
    # database's whole budget; do not raise it there.
    db_pool_min_size: int = 1
    db_pool_max_size: int = 20
    # Seconds a caller waits for a free slot before erroring rather than
    # hanging. Must stay under the request timeout it sits inside.
    db_pool_timeout_s: float = 10.0
    # us-119.1: the default executor `asyncio.to_thread` runs database calls
    # on. asyncio's own default is min(32, cpu + 4) — 8 threads on the prod
    # box — sized for CPU work; these calls are network waits, and eight of
    # them holding threads means the ninth request queues exactly as it used
    # to queue on the loop. 32 is asyncio's own ceiling. Kept above
    # `db_pool_max_size` on purpose: a thread that cannot get a connection
    # waits `db_pool_timeout_s` and fails with the pool's message, which is
    # a diagnosable event; a request that cannot get a thread just waits.
    db_executor_threads: int = 32
    # Recycle connections: a pooler or middlebox drops long-idle TCP
    # sessions, and a connection that dies in a caller's hands is a 500.
    db_pool_max_lifetime_s: float = 30 * 60.0
    db_pool_max_idle_s: float = 5 * 60.0
    supabase_service_role_key: str = ""  # US-1.27 admin console — never sent to the browser; set manually in apps/api/.env from the Supabase dashboard
    github_token: str = ""  # unset -> PR merges are simulated (US-1.12)
    github_app_id: str = ""
    github_app_slug: str = ""
    # PEM contents with literal \n (single-line .env value); use the
    # .github_app_private_key_pem property below, never this field raw.
    github_app_private_key: str = ""
    github_app_state_secret: str = ""
    web_base_url: str = "http://localhost:3000"
    # US-26.4: written into every agent slot's env file as FACTORY_API_URL —
    # the address the supervisor dials back on. Must be reachable FROM the
    # agent machine, so localhost only works for a runner on this host.
    api_base_url: str = "http://localhost:8000"
    # US-3.8 factory git remote: where the smart-HTTP proxy forwards to.
    # Overridden in tests to point at a local stub.
    git_upstream_base: str = "https://github.com"
    # US-5.25 workspace snapshot: archives above this hand off to the
    # factory git remote with an actionable error, never a truncated zip.
    workspace_zip_max_bytes: int = 25 * 1024 * 1024
    # US-16.8: the deployment Build Mill files its OWN unhandled errors
    # against. Unset disables self-reporting silently — a developer running
    # locally gets nothing, not a warning on every request.
    self_report_deployment_id: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url}/rest/v1"

    @property
    def github_app_private_key_pem(self) -> str:
        return self.github_app_private_key.replace("\\n", "\n")


@lru_cache
def get_settings() -> Settings:
    return Settings()
