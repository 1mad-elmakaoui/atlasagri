/**
 * Détail d'une expédition : l'écran de décision.
 *
 * Enchaînement voulu — la conclusion, puis la carte qui la justifie, puis la
 * comparaison, puis les preuves. Un décideur pressé s'arrête au premier bloc ;
 * un décideur qui veut contester descend jusqu'au dernier.
 */

import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import type { CoucheCarte } from '@/lib/types'
import { dateHeure, heures, tonnes } from '@/lib/format'
import {
  BadgeRisque, Bouton, Chargement, MessageErreur, Panneau,
} from '@/components/primitives'
import { CarteOperationnelle } from '@/components/CarteOperationnelle'
import {
  ChronologieDecision, OptionsEcartees, PanneauPreuves, PanneauRecommandation,
  TableauComparaison,
} from '@/components/decision'

export function PageExpedition() {
  const { reference = '' } = useParams()
  const { donnees, erreur, enCours, recharger } = useChargement(
    () => api.expedition(reference), [reference],
  )
  const [couches, setCouches] = useState<CoucheCarte | null>(null)
  const [selection, setSelection] = useState<string | null>(null)

  useEffect(() => {
    let annule = false
    api.couchesCarte(reference)
      .then((c) => { if (!annule) setCouches(c) })
      .catch(() => { if (!annule) setCouches(null) })
    return () => { annule = true }
  }, [reference])

  useEffect(() => {
    // La carte suit la recommandation par défaut : c'est le couplage entre la
    // décision et sa représentation visuelle.
    if (donnees?.recommandation && !selection) setSelection(donnees.recommandation.id)
  }, [donnees, selection])

  if (enCours) return <Chargement libelle="Évaluation des itinéraires…" />
  if (erreur) {
    return <MessageErreur message={erreur} action={<Bouton onClick={recharger}>Réessayer</Bouton>} />
  }
  if (!donnees) return null

  const { expedition, recommandation, alternatives, reserves_fr, profil_optimisation } = donnees
  const planActuel = alternatives.find((a) => a.type === 'CURRENT_PLAN')
  const affichee = alternatives.find((a) => a.id === selection) ?? recommandation ?? alternatives[0]

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/expeditions" className="text-sm text-ardoise-500 hover:text-action">
            ← Expéditions
          </Link>
          <h1 className="mt-1 flex items-center gap-3 text-xl font-semibold text-ardoise-900">
            Expédition {expedition.reference}
            {planActuel && (
              <BadgeRisque
                niveau={planActuel.niveau_risque}
                libelle={planActuel.niveau_risque_fr}
              />
            )}
          </h1>
          <p className="mt-1 text-sm text-ardoise-600">
            {expedition.produit} · {tonnes(expedition.volume_tonnes)} ·{' '}
            {expedition.origine} → {expedition.destination} · {expedition.mode_transport}
          </p>
          <p className="mt-0.5 text-sm text-ardoise-500">
            Départ prévu {dateHeure(expedition.depart_prevu)} · échéance{' '}
            {dateHeure(expedition.echeance_service)}
          </p>
        </div>
      </div>

      {reserves_fr.length > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
          <p className="text-sm font-medium text-amber-900">Réserves sur cette analyse</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-amber-800">
            {reserves_fr.map((reserve, i) => <li key={i}>{reserve}</li>)}
          </ul>
        </div>
      )}

      {recommandation && (
        <PanneauRecommandation
          recommandation={recommandation}
          planActuel={planActuel}
          onVoirCarte={() => {
            setSelection(recommandation.id)
            document.getElementById('carte')?.scrollIntoView({ behavior: 'smooth' })
          }}
          onComparer={() =>
            document.getElementById('comparaison')?.scrollIntoView({ behavior: 'smooth' })
          }
        />
      )}

      <div id="carte" className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <CarteOperationnelle
            couches={couches}
            itineraireSelectionne={selection}
            onSelectionItineraire={setSelection}
            hauteur="540px"
          />
          <p className="mt-2 text-xs text-ardoise-500">
            Cliquez sur un itinéraire pour le sélectionner, ou sur une zone colorée
            pour connaître le motif d'exposition et l'heure de passage prévue.
          </p>
        </div>

        <div className="space-y-6">
          <Panneau titre="Combien de temps pour agir ?">
            {affichee?.troncons ? (
              <ChronologieDecision
                troncons={affichee.troncons}
                depart={affichee.depart}
                echeance={expedition.echeance_service}
              />
            ) : (
              <p className="text-sm text-ardoise-500">
                Chronologie indisponible pour cette option.
              </p>
            )}
          </Panneau>

          <Panneau
            titre="Critères de décision"
            sousTitre={profil_optimisation.libelle_fr}
          >
            <p className="text-sm text-ardoise-600">{profil_optimisation.justification_fr}</p>
          </Panneau>
        </div>
      </div>

      <Panneau
        titre="Comparaison des options"
        sousTitre={`${alternatives.length} option(s) respectant les contraintes`}
      >
        <div id="comparaison">
          <TableauComparaison
            alternatives={alternatives}
            selection={selection}
            onSelection={setSelection}
          />
        </div>
        <div className="mt-4">
          <OptionsEcartees options={donnees.alternatives_ecartees} />
        </div>
      </Panneau>

      {affichee && (
        <div className="grid gap-6 lg:grid-cols-2">
          <PanneauPreuves alternative={affichee} />

          <Panneau
            titre="Détail du trajet"
            sousTitre={affichee.libelle_fr}
          >
            {affichee.troncons ? (
              <ul className="divide-y divide-ardoise-100 text-sm">
                {affichee.troncons.map((troncon) => (
                  <li
                    key={troncon.index}
                    className={`flex items-start justify-between gap-3 py-2 ${
                      troncon.expose ? '-mx-2 rounded bg-orange-50 px-2' : ''
                    }`}
                  >
                    <div>
                      <p className="font-medium text-ardoise-800">
                        {troncon.de} → {troncon.vers}
                      </p>
                      <p className="text-xs text-ardoise-500">
                        {troncon.axe} · {troncon.distance_km} km · passage{' '}
                        {dateHeure(troncon.depart_troncon)}
                      </p>
                      {troncon.motifs_fr.map((motif, i) => (
                        <p key={i} className="mt-0.5 text-xs text-orange-800">{motif}</p>
                      ))}
                    </div>
                    <div className="shrink-0 text-right">
                      <BadgeRisque
                        niveau={troncon.niveau_risque}
                        libelle={troncon.niveau_risque_fr}
                        taille="petite"
                      />
                      <p className="mt-1 text-xs tabular-nums text-ardoise-500">
                        +{heures(troncon.heures_apres_depart)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ardoise-500">
                Cette option ne repose pas sur un itinéraire routier.
              </p>
            )}
          </Panneau>
        </div>
      )}
    </div>
  )
}
