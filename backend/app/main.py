"""Point d'entrée de l'application AtlasAgri Intelligence."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import auth, copilot, dashboard, map_view, outcomes, shipments, supply
from app.core.config import settings
from app.core.errors import AtlasAgriError
from app.core.logging import configure_logging, get_logger
from app.db.seed import seed_if_empty
from app.db.session import SessionLocal, create_all
from app.providers.registry import providers_health

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    create_all()

    # Le jeu de démonstration n'est inséré qu'hors production : une base client
    # ne doit jamais recevoir de données fictives, même vide au démarrage.
    if not settings.is_production:
        with SessionLocal() as session:
            seed_if_empty(session)

    logger.info(
        "AtlasAgri démarré",
        context={
            "environnement": settings.atlasagri_env,
            "copilote_actif": settings.agent_enabled,
            "fournisseur_meteo": settings.weather_provider,
        },
    )
    if settings.weather_provider == "offline":
        logger.warning(
            "Mode démonstration météo : toutes les valeurs seront étiquetées « Simulé »"
        )
    yield


app = FastAPI(
    title="AtlasAgri Intelligence",
    description=(
        "Plateforme de décision pour les chaînes d'approvisionnement agricoles "
        "marocaines. Prévoir. Anticiper. Réacheminer. Décider."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(AtlasAgriError)
async def business_error_handler(request: Request, exc: AtlasAgriError) -> JSONResponse:
    """Erreur métier : message français, sans détail technique.

    Le détail est journalisé côté serveur ; l'exposer révélerait la structure
    interne du système à un appelant non authentifié.
    """
    if exc.detail:
        logger.warning(
            "Erreur métier",
            context={"code": exc.code, "detail": exc.detail, "chemin": request.url.path},
        )
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "message_fr": exc.message_fr},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'][1:])} : {e['msg']}" for e in exc.errors()[:5]
    )
    return JSONResponse(
        status_code=422,
        content={"code": "donnees_invalides", "message_fr": f"Requête invalide — {details}"},
    )


@app.get("/api/sante", tags=["Supervision"])
async def health() -> dict:
    """État de l'application et de ses sources externes.

    L'utilisateur doit pouvoir constater qu'une source est en panne : c'est ce
    qui lui permet de pondérer sa confiance dans ce qu'il voit à l'écran.
    """
    return {
        "statut": "operationnel",
        "environnement": settings.atlasagri_env,
        "copilote_actif": settings.agent_enabled,
        "sources": await providers_health(),
    }


for router in (
    auth.router,
    dashboard.router,
    shipments.router,
    map_view.router,
    supply.router,
    outcomes.router,
    copilot.router,
):
    app.include_router(router, prefix="/api/v1")
