/** Fournisseurs : exposition combinant zone, fiabilité et délai. */

import { api } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import { jours, mad, pourcentage, tonnes } from '@/lib/format'
import {
  BadgeRisque, Bouton, Chargement, EtatVide, MessageErreur, Panneau,
} from '@/components/primitives'

export function PageFournisseurs() {
  const { donnees, erreur, enCours, recharger } = useChargement(() => api.fournisseurs())

  if (enCours) return <Chargement libelle="Évaluation de l'exposition fournisseurs…" />
  if (erreur) return <MessageErreur message={erreur} action={<Bouton onClick={recharger}>Réessayer</Bouton>} />
  if (!donnees) return null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ardoise-900">Fournisseurs</h1>
        <p className="mt-0.5 text-sm text-ardoise-500">
          L'exposition combine le risque de la zone, la fiabilité de livraison observée
          et le délai de réapprovisionnement.
        </p>
      </div>

      {donnees.fournisseurs.length === 0 ? (
        <EtatVide titre="Aucun fournisseur référencé" />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {donnees.fournisseurs.map((fournisseur) => (
            <Panneau key={fournisseur.id}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="font-semibold text-ardoise-900">{fournisseur.nom}</h2>
                  <p className="text-xs text-ardoise-500">
                    {fournisseur.site} · {fournisseur.zone_fr}
                  </p>
                </div>
                <BadgeRisque
                  niveau={fournisseur.code_exposition}
                  libelle={fournisseur.niveau_exposition}
                  taille="petite"
                />
              </div>

              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                <div>
                  <dt className="text-xs text-ardoise-500">Délai</dt>
                  <dd className="tabular-nums text-ardoise-800">{jours(fournisseur.delai_jours)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ardoise-500">Fiabilité</dt>
                  <dd className="tabular-nums text-ardoise-800">
                    {pourcentage(fournisseur.fiabilite)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-ardoise-500">Capacité</dt>
                  <dd className="tabular-nums text-ardoise-800">
                    {tonnes(fournisseur.capacite_quotidienne_tonnes)}/j
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-ardoise-500">Prix</dt>
                  <dd className="tabular-nums text-ardoise-800">
                    {mad(fournisseur.prix_mad_par_tonne)}/t
                  </dd>
                </div>
              </dl>

              {fournisseur.fournisseur_de_secours && (
                <p className="mt-2 inline-block rounded bg-ardoise-100 px-1.5 py-0.5 text-xs text-ardoise-600">
                  Fournisseur de secours
                </p>
              )}

              <ul className="mt-3 space-y-1 border-t border-ardoise-100 pt-2 text-xs text-ardoise-600">
                {fournisseur.implications_fr.map((implication, i) => (
                  <li key={i}>{implication}</li>
                ))}
              </ul>
            </Panneau>
          ))}
        </div>
      )}
    </div>
  )
}
