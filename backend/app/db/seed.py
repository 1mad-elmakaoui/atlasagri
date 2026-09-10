"""Jeu de données initial.

Deux organisations sont créées. La seconde n'existe pas pour faire nombre :
elle sert à vérifier, en test comme en démonstration, qu'aucune donnée ne
traverse la frontière entre clients.

Les données métier (fournisseurs, volumes, prix) sont représentatives d'une
filière primeurs marocaine mais restent des données de démonstration. Les
coordonnées et les distances, elles, sont réelles.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import hash_password
from app.db.base import (
    Inventory,
    Product,
    Shipment,
    Site,
    Supplier,
    Tenant,
    User,
)
from app.domain.enums import ShipmentStatus, SiteType, TransportMode, UserRole

logger = get_logger(__name__)

DEMO_PASSWORD = "AtlasAgri2026!"


def seed_if_empty(session: Session) -> None:
    if session.execute(select(Tenant).limit(1)).scalars().first() is not None:
        logger.info("Base déjà peuplée, seed ignoré")
        return
    seed(session)


def seed(session: Session) -> None:
    logger.info("Insertion du jeu de données initial")

    primary = _seed_primary_tenant(session)
    _seed_isolation_witness_tenant(session)

    session.commit()
    logger.info(
        "Jeu de données inséré",
        context={"tenant": primary.slug, "mot_de_passe_demo": "défini dans seed.DEMO_PASSWORD"},
    )


# ---------------------------------------------------------------------------
# Organisation principale : coopérative agro-industrielle du Souss
# ---------------------------------------------------------------------------

def _seed_primary_tenant(session: Session) -> Tenant:
    tenant = Tenant(
        name="Souss Primeurs Export",
        slug="souss-primeurs",
        configuration={
            "sla_default_hours": 36,
            "devise": "MAD",
            "region_principale": "SOUSS_MASSA",
        },
    )
    session.add(tenant)
    session.flush()

    users = [
        ("directrice@souss-primeurs.ma", "Nadia Benkirane", UserRole.EXECUTIVE),
        ("supply@souss-primeurs.ma", "Youssef Amrani", UserRole.SUPPLY_CHAIN_MANAGER),
        ("operations@souss-primeurs.ma", "Karim Idrissi", UserRole.OPERATIONS_MANAGER),
        ("analyste@souss-primeurs.ma", "Salma Ouazzani", UserRole.ANALYST),
        ("admin@souss-primeurs.ma", "Administration", UserRole.ADMIN),
    ]
    for email, name, role in users:
        session.add(
            User(
                tenant_id=tenant.id,
                email=email,
                full_name=name,
                hashed_password=hash_password(DEMO_PASSWORD),
                role=role.value,
            )
        )

    # --- Sites : coordonnées réelles, rattachés au réseau routier ---
    sites_spec = [
        # (code, nom, type, région, lat, lon, nœud routier, capacité t, froid)
        ("F-BIOUGRA", "Domaine de Biougra", SiteType.FARM, "SOUSS_MASSA",
         30.2131, -9.3706, "BIOUGRA", 900, False),
        ("F-AITMELLOUL", "Serres d'Aït Melloul", SiteType.FARM, "SOUSS_MASSA",
         30.3342, -9.4960, "AIT_MELLOUL", 700, False),
        ("F-TAROUDANT", "Exploitation de Taroudant", SiteType.FARM, "SOUSS_MASSA",
         30.4703, -8.8770, "TAROUDANT", 600, False),
        ("E-AGADIR", "Entrepôt frigorifique d'Agadir", SiteType.WAREHOUSE, "SOUSS_MASSA",
         30.4278, -9.5981, "AGADIR", 2400, True),
        ("E-MARRAKECH", "Plateforme de Marrakech", SiteType.WAREHOUSE, "MARRAKECH_SAFI",
         31.6295, -7.9811, "MARRAKECH", 1500, True),
        ("E-CASA", "Entrepôt central de Casablanca", SiteType.WAREHOUSE, "CASABLANCA_SETTAT",
         33.5731, -7.5898, "CASABLANCA", 3200, True),
        ("E-ELJADIDA", "Entrepôt relais d'El Jadida", SiteType.WAREHOUSE, "CASABLANCA_SETTAT",
         33.2549, -8.5069, "ELJADIDA", 900, True),
        ("H-SAFI", "Plateforme de Safi", SiteType.HUB, "MARRAKECH_SAFI",
         32.2994, -9.2372, "SAFI", 800, False),
        ("C-CASA-MARCHE", "Marché de gros de Casablanca", SiteType.CUSTOMER, "CASABLANCA_SETTAT",
         33.5731, -7.5898, "CASABLANCA", None, False),
        ("C-RABAT", "Centrale d'achat de Rabat", SiteType.CUSTOMER, "RABAT_SALE_KENITRA",
         34.0209, -6.8416, "RABAT", None, False),
        ("P-TANGERMED", "Terminal de Tanger Med", SiteType.PORT, "TANGER_TETOUAN",
         35.8869, -5.5017, "TANGER_MED", 5000, True),
        ("F-DOUKKALA", "Périmètre des Doukkala", SiteType.FARM, "CASABLANCA_SETTAT",
         32.6522, -8.4267, "SIDI_BENNOUR", 1100, False),
        ("F-TADLA", "Périmètre du Tadla", SiteType.FARM, "BENI_MELLAL_KHENIFRA",
         32.3373, -6.3498, "BENI_MELLAL", 800, False),
    ]
    sites: dict[str, Site] = {}
    for code, name, stype, region, lat, lon, node, capacity, cold in sites_spec:
        site = Site(
            tenant_id=tenant.id,
            code=code,
            name=name,
            site_type=stype.value,
            region_code=region,
            latitude=lat,
            longitude=lon,
            road_node_code=node,
            capacity_tonnes=capacity,
            has_cold_storage=cold,
        )
        session.add(site)
        sites[code] = site
    session.flush()

    # --- Produits, avec leur profil d'optimisation ---
    products_spec = [
        ("P-TOM-EXP", "Tomate cerise export", "TOMATE", "EXPORT_SLA_STRICT", 9800),
        ("P-TOM-LOC", "Tomate ronde marché local", "TOMATE", "PERISSABLE_FROID", 4200),
        ("P-AGR-CLE", "Clémentine", "AGRUME", "PERISSABLE_FROID", 6400),
        ("P-BLE", "Blé tendre", "BLE", "VRAC_FAIBLE_MARGE", 3100),
        ("P-BET", "Betterave sucrière", "BETTERAVE", "VRAC_FAIBLE_MARGE", 520),
        ("P-POM", "Pomme de terre", "POMME_DE_TERRE", "PERISSABLE_STANDARD", 2800),
    ]
    products: dict[str, Product] = {}
    for code, name, crop, profile, value in products_spec:
        product = Product(
            tenant_id=tenant.id,
            code=code,
            name=name,
            crop_code=crop,
            optimization_profile=profile,
            unit_value_mad_per_tonne=value,
        )
        session.add(product)
        products[code] = product
    session.flush()

    # --- Fournisseurs ---
    suppliers_spec = [
        # (code, nom, site, culture, délai j, fiabilité, capacité t/j, prix MAD/t, secours)
        ("FRN-A", "Coopérative Al Massira", "F-BIOUGRA", "TOMATE", 2.0, 0.94, 120, 4100, False),
        ("FRN-B", "Domaines Souss Vert", "F-AITMELLOUL", "TOMATE", 3.0, 0.91, 90, 4350, False),
        ("FRN-C", "Groupement de Taroudant", "F-TAROUDANT", "TOMATE", 5.0, 0.88, 70, 3950, True),
        ("FRN-D", "Agrumes du Tadla", "F-TADLA", "AGRUME", 4.0, 0.90, 110, 6100, False),
        ("FRN-E", "Doukkala Céréales", "F-DOUKKALA", "BLE", 6.0, 0.93, 200, 3000, False),
        ("FRN-F", "Doukkala Maraîchage", "F-DOUKKALA", "POMME_DE_TERRE",
         3.0, 0.89, 95, 2650, False),
        ("FRN-G", "Sucreries des Doukkala", "F-DOUKKALA", "BETTERAVE", 2.0, 0.95, 260, 480, False),
    ]
    for code, name, site_code, crop, lead, rel, cap, price, backup in suppliers_spec:
        session.add(
            Supplier(
                tenant_id=tenant.id,
                code=code,
                name=name,
                site_id=sites[site_code].id,
                crop_code=crop,
                lead_time_days=lead,
                reliability=rel,
                daily_capacity_tonnes=cap,
                unit_price_mad_per_tonne=price,
                is_backup=backup,
            )
        )
    session.flush()

    # --- Stocks ---
    # La couverture de l'entrepôt de Casablanca en tomate export est
    # volontairement tendue (≈ 3,2 jours) : c'est le point de tension du
    # scénario de démonstration, où le délai fournisseur dépasse la couverture.
    inventory_spec = [
        # (site, produit, quantité t, stock de sécurité t, demande quotidienne t)
        ("E-CASA", "P-TOM-EXP", 128.0, 60.0, 40.0),
        ("E-CASA", "P-TOM-LOC", 210.0, 80.0, 55.0),
        ("E-CASA", "P-AGR-CLE", 340.0, 100.0, 48.0),
        ("E-CASA", "P-POM", 420.0, 120.0, 60.0),
        ("E-AGADIR", "P-TOM-EXP", 260.0, 90.0, 70.0),
        ("E-AGADIR", "P-TOM-LOC", 180.0, 70.0, 45.0),
        ("E-MARRAKECH", "P-TOM-LOC", 95.0, 50.0, 32.0),
        ("E-MARRAKECH", "P-AGR-CLE", 150.0, 60.0, 30.0),
        ("E-ELJADIDA", "P-POM", 240.0, 80.0, 35.0),
        ("E-ELJADIDA", "P-TOM-LOC", 60.0, 40.0, 28.0),
        ("E-CASA", "P-BLE", 1800.0, 500.0, 90.0),
        ("E-CASA", "P-BET", 900.0, 200.0, 120.0),
    ]
    for site_code, product_code, qty, safety, demand in inventory_spec:
        session.add(
            Inventory(
                tenant_id=tenant.id,
                site_id=sites[site_code].id,
                product_id=products[product_code].id,
                quantity_tonnes=qty,
                safety_stock_tonnes=safety,
                average_daily_demand_tonnes=demand,
            )
        )

    # --- Expéditions ---
    now = datetime.now(UTC)
    shipments_spec = [
        # (réf, produit, site origine, destination, volume, mode, départ +h, SLA +h, itinéraire)
        ("EXP-1842", "P-TOM-EXP", "E-AGADIR", "E-CASA", 180.0,
         TransportMode.REFRIGERATED_TRUCK, 22, 46,
         ["AGADIR", "IMINTANOUTE", "CHICHAOUA", "MARRAKECH", "BENGUERIR", "SETTAT",
          "BERRECHID", "CASABLANCA"]),
        ("EXP-1843", "P-TOM-LOC", "E-AGADIR", "E-MARRAKECH", 90.0,
         TransportMode.REFRIGERATED_TRUCK, 19, 34,
         ["AGADIR", "IMINTANOUTE", "CHICHAOUA", "MARRAKECH"]),
        ("EXP-1844", "P-AGR-CLE", "F-TADLA", "E-CASA", 140.0,
         TransportMode.REFRIGERATED_TRUCK, 20, 48,
         ["BENI_MELLAL", "FKIH_BEN_SALAH", "KHOURIBGA", "BERRECHID", "CASABLANCA"]),
        ("EXP-1845", "P-POM", "F-DOUKKALA", "E-CASA", 220.0,
         TransportMode.STANDARD_TRUCK, 30, 60,
         ["SIDI_BENNOUR", "ELJADIDA", "CASABLANCA"]),
        ("EXP-1846", "P-TOM-EXP", "E-AGADIR", "P-TANGERMED", 160.0,
         TransportMode.REFRIGERATED_TRUCK, 20, 52,
         ["AGADIR", "IMINTANOUTE", "CHICHAOUA", "MARRAKECH", "BENGUERIR", "SETTAT",
          "BERRECHID", "CASABLANCA", "MOHAMMEDIA", "RABAT", "KENITRA", "SOUK_ARBAA",
          "LARACHE", "TANGER", "TANGER_MED"]),
        ("EXP-1847", "P-BLE", "F-DOUKKALA", "E-CASA", 600.0,
         TransportMode.BULK_TRUCK, 26, 72,
         ["SIDI_BENNOUR", "ELJADIDA", "CASABLANCA"]),
        ("EXP-1848", "P-TOM-LOC", "F-BIOUGRA", "E-AGADIR", 75.0,
         TransportMode.REFRIGERATED_TRUCK, 4, 12,
         ["BIOUGRA", "AIT_MELLOUL", "INEZGANE", "AGADIR"]),
        ("EXP-1849", "P-AGR-CLE", "E-MARRAKECH", "C-RABAT", 85.0,
         TransportMode.REFRIGERATED_TRUCK, 18, 44,
         ["MARRAKECH", "BENGUERIR", "SETTAT", "BERRECHID", "CASABLANCA", "MOHAMMEDIA",
          "RABAT"]),
    ]
    for ref, prod, origin, dest, volume, mode, dep_h, sla_h, route in shipments_spec:
        session.add(
            Shipment(
                tenant_id=tenant.id,
                reference=ref,
                product_id=products[prod].id,
                origin_site_id=sites[origin].id,
                destination_site_id=sites[dest].id,
                volume_tonnes=volume,
                transport_mode=mode.value,
                status=ShipmentStatus.PLANNED.value,
                departure_at=now + timedelta(hours=dep_h),
                sla_deadline_at=now + timedelta(hours=sla_h),
                planned_route_nodes=route,
            )
        )

    return tenant


# ---------------------------------------------------------------------------
# Organisation témoin : sert à prouver l'isolation
# ---------------------------------------------------------------------------

def _seed_isolation_witness_tenant(session: Session) -> Tenant:
    tenant = Tenant(
        name="Gharb Agro",
        slug="gharb-agro",
        configuration={
            "sla_default_hours": 48,
            "devise": "MAD",
            "region_principale": "RABAT_SALE_KENITRA",
        },
    )
    session.add(tenant)
    session.flush()

    session.add(
        User(
            tenant_id=tenant.id,
            email="supply@gharb-agro.ma",
            full_name="Hicham Berrada",
            hashed_password=hash_password(DEMO_PASSWORD),
            role=UserRole.SUPPLY_CHAIN_MANAGER.value,
        )
    )

    site = Site(
        tenant_id=tenant.id,
        code="F-GHARB",
        name="Exploitation du Gharb",
        site_type=SiteType.FARM.value,
        region_code="RABAT_SALE_KENITRA",
        latitude=34.6907,
        longitude=-5.9906,
        road_node_code="SOUK_ARBAA",
        capacity_tonnes=1000,
        has_cold_storage=False,
    )
    warehouse = Site(
        tenant_id=tenant.id,
        code="E-KENITRA",
        name="Entrepôt de Kénitra",
        site_type=SiteType.WAREHOUSE.value,
        region_code="RABAT_SALE_KENITRA",
        latitude=34.2610,
        longitude=-6.5802,
        road_node_code="KENITRA",
        capacity_tonnes=1200,
        has_cold_storage=True,
    )
    session.add_all([site, warehouse])
    session.flush()

    product = Product(
        tenant_id=tenant.id,
        code="P-FRA",
        name="Fraise du Loukkos",
        crop_code="FRAISE",
        optimization_profile="EXPORT_SLA_STRICT",
        unit_value_mad_per_tonne=12500,
    )
    session.add(product)
    session.flush()

    session.add(
        Inventory(
            tenant_id=tenant.id,
            site_id=warehouse.id,
            product_id=product.id,
            quantity_tonnes=45.0,
            safety_stock_tonnes=20.0,
            average_daily_demand_tonnes=18.0,
        )
    )
    now = datetime.now(UTC)
    session.add(
        Shipment(
            tenant_id=tenant.id,
            reference="GHB-0031",
            product_id=product.id,
            origin_site_id=site.id,
            destination_site_id=warehouse.id,
            volume_tonnes=40.0,
            transport_mode=TransportMode.REFRIGERATED_TRUCK.value,
            status=ShipmentStatus.PLANNED.value,
            departure_at=now + timedelta(hours=10),
            sla_deadline_at=now + timedelta(hours=26),
            planned_route_nodes=["SOUK_ARBAA", "KENITRA"],
        )
    )
    return tenant
