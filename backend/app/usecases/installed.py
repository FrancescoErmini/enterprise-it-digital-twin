"""Use-case packages installed in this deployment. Each exposes build_usecase() -> UseCase."""

INSTALLED_USECASES: tuple[str, ...] = (
    "app.usecases.security.gcp",
)
