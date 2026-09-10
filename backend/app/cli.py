"""Commandes d'exploitation.

    python -m app.cli calibrer [--region SOUSS_MASSA] [--annees 10] [--tenant slug]
    python -m app.cli fiabilite [--tenant slug]

La calibration est une opération d'administration, pas une action utilisateur :
elle modifie les seuils qui fondent toutes les évaluations de l'organisation.
Elle vit donc en ligne de commande, exécutable par un administrateur ou une
tâche planifiée, et non derrière un bouton de l'interface.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.config import settings
from app.core.errors import AtlasAgriError
from app.core.logging import configure_logging
from app.core.security import RequestContext
from app.db.base import Tenant, User
from app.db.repositories import TenantRepository
from app.db.session import SessionLocal, create_all
from app.domain.enums import UserRole
from app.services.calibration import ThresholdCalibrationService
from app.services.reliability import ReliabilityService


def _context(session, tenant_slug: str | None) -> TenantRepository:
    query = session.query(Tenant)
    tenant = (
        query.filter(Tenant.slug == tenant_slug).first() if tenant_slug else query.first()
    )
    if tenant is None:
        raise SystemExit(
            f"Organisation introuvable : « {tenant_slug or '(première)'} ». "
            "Vérifier DATABASE_URL et le slug fourni."
        )
    admin = (
        session.query(User)
        .filter(User.tenant_id == tenant.id, User.role == UserRole.ADMIN.value)
        .first()
    ) or session.query(User).filter(User.tenant_id == tenant.id).first()

    return TenantRepository(
        session,
        RequestContext(
            user_id=admin.id if admin else "cli",
            tenant_id=tenant.id,
            role=UserRole.ADMIN,
            email=admin.email if admin else "cli@atlasagri",
        ),
    )


async def _calibrer(args: argparse.Namespace) -> int:
    create_all()
    with SessionLocal() as session:
        repo = _context(session, args.tenant)
        service = ThresholdCalibrationService(repo)

        print(f"Fournisseur météo : {settings.weather_provider}")
        if settings.weather_provider != "openmeteo":
            print(
                "Attention : seule l'archive Open-Meteo fournit un historique réel. "
                "La calibration sera refusée sur des données simulées.",
                file=sys.stderr,
            )

        try:
            rapport = (
                await service.calibrate_region(args.region, years=args.annees)
                if args.region
                else await service.calibrate_active_regions(years=args.annees)
            )
        except AtlasAgriError as exc:
            print(f"\nCalibration interrompue : {exc.message_fr}", file=sys.stderr)
            return 1

        resume = rapport.as_summary_fr()
        print(f"\nFenêtre : {resume['fenetre']}")
        print(f"Seuils calibrés : {resume['seuils_calibres']}")
        print(f"Seuils écartés  : {resume['seuils_ignores']}\n")

        if resume["detail_calibres"]:
            print(f"{'RÉGION':<22}{'CULTURE':<14}{'VARIABLE':<24}{'MODÉRÉ':>8}{'ÉLEVÉ':>8}{'CRIT':>8}{'N':>7}")
            print("-" * 91)
            for d in resume["detail_calibres"]:
                crit = f"{d['critique']}" if d["critique"] is not None else "—"
                print(
                    f"{d['region'][:21]:<22}{d['culture'][:13]:<14}{d['variable'][:23]:<24}"
                    f"{d['modere']:>8}{d['eleve']:>8}{crit:>8}{d['echantillon']:>7}"
                )

        if resume["detail_ignores"]:
            print("\nÉcartés — l'aléa ne se produit pas dans la zone, ou les bornes")
            print("ne discriminent pas. Le seuil provisoire reste en vigueur :")
            for d in resume["detail_ignores"]:
                print(f"  · {d['region']} / {d['culture']} / {d['variable']}")
                print(f"    {d['motif_fr']}")
        return 0


def _fiabilite(args: argparse.Namespace) -> int:
    create_all()
    with SessionLocal() as session:
        repo = _context(session, args.tenant)
        rapport = ReliabilityService(repo).evaluate()
        print(json.dumps(rapport, ensure_ascii=False, indent=2, default=str))
        return 0


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        prog="atlasagri", description="Commandes d'exploitation AtlasAgri"
    )
    sub = parser.add_subparsers(dest="commande", required=True)

    calibrer = sub.add_parser(
        "calibrer", help="Dérive les seuils de risque depuis l'historique météo local"
    )
    calibrer.add_argument("--region", help="Code de région ; toutes les régions actives par défaut")
    calibrer.add_argument("--annees", type=int, default=10, help="Profondeur d'historique")
    calibrer.add_argument("--tenant", help="Slug de l'organisation")

    fiabilite = sub.add_parser(
        "fiabilite", help="Confronte les prédictions passées aux résultats observés"
    )
    fiabilite.add_argument("--tenant", help="Slug de l'organisation")

    args = parser.parse_args()
    if args.commande == "calibrer":
        return asyncio.run(_calibrer(args))
    return _fiabilite(args)


if __name__ == "__main__":
    raise SystemExit(main())
