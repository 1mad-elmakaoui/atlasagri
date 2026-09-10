/** Page carte : choix d'une expédition puis lecture de ses couches. */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import type { CoucheCarte } from '@/lib/types'
import { heures, mad, pourcentage } from '@/lib/format'
import { BadgeRisque, Chargement, EtatVide, MessageErreur, Panneau } from '@/components/primitives'
import { CarteOperationnelle } from '@/components/CarteOperationnelle'

export function PageCarte() {
  const { donnees: liste, erreur } = useChargement(() => api.expeditions())
  const [reference, setReference] = useState<string | null>(null)
  const [couches, setCouches] = useState<CoucheCarte | null>(null)
  const [selection, setSelection] = useState<string | null>(null)
  const [chargementCouches, setChargementCouches] = useState(false)

  useEffect(() => {
    if (!reference && liste?.expeditions.length) setReference(liste.expeditions[0].reference)
  }, [liste, reference])

  useEffect(() => {
    if (!reference) return
    let annule = false
    setChargementCouches(true)
    api.couchesCarte(reference)
      .then((c) => {
        if (annule) return
        setCouches(c)
        setSelection(c.option_recommandee)
      })
      .catch(() => { if (!annule) setCouches(null) })
      .finally(() => { if (!annule) setChargementCouches(false) })
    return () => { annule = true }
  }, [reference])

  if (erreur) return <MessageErreur message={erreur} />

  const itineraires = (couches?.itineraires.features ?? []) as GeoJSON.Feature[]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ardoise-900">Carte opérationnelle</h1>
          <p className="mt-0.5 text-sm text-ardoise-500">
            Itinéraires, zones exposées et options pour une expédition.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-ardoise-600">Expédition</span>
          <select
            value={reference ?? ''}
            onChange={(e) => setReference(e.target.value)}
            className="rounded-md border border-ardoise-300 bg-white px-3 py-1.5 text-sm focus:border-action focus:outline-none"
          >
            {liste?.expeditions.map((expedition) => (
              <option key={expedition.id} value={expedition.reference}>
                {expedition.reference} — {expedition.produit}
              </option>
            ))}
          </select>
        </label>
      </div>

      {chargementCouches && <Chargement libelle="Calcul des itinéraires…" />}

      {!chargementCouches && couches && (
        <div className="grid gap-4 lg:grid-cols-4">
          <div className="lg:col-span-3">
            <CarteOperationnelle
              couches={couches}
              itineraireSelectionne={selection}
              onSelectionItineraire={setSelection}
              hauteur="640px"
            />
          </div>

          <Panneau titre="Options" sousTitre={couches.expedition.produit}>
            {itineraires.length === 0 ? (
              <EtatVide titre="Aucun itinéraire" />
            ) : (
              <ul className="space-y-2">
                {itineraires.map((feature) => {
                  const p = feature.properties as Record<string, unknown>
                  const id = String(p.id)
                  return (
                    <li key={id}>
                      <button
                        onClick={() => setSelection(id)}
                        className={`w-full rounded-md border p-2.5 text-left transition ${
                          selection === id
                            ? 'border-action bg-blue-50'
                            : 'border-ardoise-200 hover:bg-ardoise-50'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-medium text-ardoise-900">
                            {String(p.libelle)}
                          </span>
                          {Boolean(p.recommandee) && (
                            <span className="rounded bg-action px-1.5 py-0.5 text-[10px] font-semibold uppercase text-white">
                              Recommandé
                            </span>
                          )}
                        </div>
                        <div className="mt-1 flex items-center gap-2">
                          <BadgeRisque
                            niveau={String(p.niveau_risque)}
                            libelle={String(p.niveau_risque_fr)}
                            taille="petite"
                          />
                          <span className="text-xs tabular-nums text-ardoise-500">
                            {pourcentage(Number(p.probabilite_perturbation))}
                          </span>
                        </div>
                        <p className="mt-1 text-xs tabular-nums text-ardoise-500">
                          {Number(p.distance_km)} km · {heures(Number(p.duree_h))} ·{' '}
                          {mad(Number(p.cout_mad))}
                        </p>
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
            {reference && (
              <Link
                to={`/expeditions/${reference}`}
                className="mt-3 block text-center text-sm font-medium text-action hover:underline"
              >
                Voir l'analyse complète →
              </Link>
            )}
          </Panneau>
        </div>
      )}
    </div>
  )
}
