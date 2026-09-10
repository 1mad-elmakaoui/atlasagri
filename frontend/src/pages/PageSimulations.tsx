/**
 * Simulations « et si ? ».
 *
 * Compare la situation actuelle à la situation simulée. L'indicateur le plus
 * parlant n'est pas le score : c'est le **nombre d'options restantes**. Perdre
 * onze options sur douze dit mieux qu'un score que la marge de manœuvre s'est
 * effondrée.
 */

import { useEffect, useState } from 'react'
import { api, ErreurApi } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import type { InstantaneSimulation, ResultatSimulation } from '@/lib/types'
import { heures, mad, pourcentage } from '@/lib/format'
import {
  BadgeRisque, Bouton, Chargement, MessageErreur, Panneau,
} from '@/components/primitives'

export function PageSimulations() {
  const { donnees: scenarios } = useChargement(() => api.scenarios())
  const { donnees: expeditions } = useChargement(() => api.expeditions())

  const [scenario, setScenario] = useState('route_indisponible')
  const [reference, setReference] = useState('')
  const [valeur, setValeur] = useState(48)
  const [resultat, setResultat] = useState<ResultatSimulation | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [enCours, setEnCours] = useState(false)

  useEffect(() => {
    if (!reference && expeditions?.expeditions.length) {
      setReference(expeditions.expeditions[0].reference)
    }
  }, [expeditions, reference])

  async function lancer() {
    setEnCours(true)
    setErreur(null)
    try {
      setResultat(
        await api.simuler({
          scenario,
          shipment_reference: reference,
          parameter_value: valeur,
        }),
      )
    } catch (e) {
      setErreur(e instanceof ErreurApi ? e.message : 'Simulation impossible.')
      setResultat(null)
    } finally {
      setEnCours(false)
    }
  }

  const besoinValeur = scenario !== 'route_indisponible'

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ardoise-900">Simulations</h1>
        <p className="mt-0.5 text-sm text-ardoise-500">
          Éprouvez un plan avant de vous y engager. Aucune donnée n'est modifiée.
        </p>
      </div>

      <Panneau titre="Paramètres du scénario">
        <div className="flex flex-wrap items-end gap-4">
          <label className="text-sm">
            <span className="mb-1 block text-ardoise-600">Scénario</span>
            <select
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              className="rounded-md border border-ardoise-300 bg-white px-3 py-1.5 text-sm focus:border-action focus:outline-none"
            >
              {scenarios?.scenarios.map((s) => (
                <option key={s.code} value={s.code}>{s.libelle_fr}</option>
              ))}
            </select>
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-ardoise-600">Expédition</span>
            <select
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              className="rounded-md border border-ardoise-300 bg-white px-3 py-1.5 text-sm focus:border-action focus:outline-none"
            >
              {expeditions?.expeditions.map((e) => (
                <option key={e.id} value={e.reference}>
                  {e.reference} — {e.produit}
                </option>
              ))}
            </select>
          </label>

          {besoinValeur && (
            <label className="text-sm">
              <span className="mb-1 block text-ardoise-600">Ampleur (heures)</span>
              <input
                type="number"
                min={0}
                max={720}
                value={valeur}
                onChange={(e) => setValeur(Number(e.target.value))}
                className="w-28 rounded-md border border-ardoise-300 px-3 py-1.5 text-sm focus:border-action focus:outline-none"
              />
            </label>
          )}

          <Bouton variante="primaire" onClick={lancer} disabled={enCours || !reference}>
            {enCours ? 'Simulation…' : 'Lancer la simulation'}
          </Bouton>
        </div>
      </Panneau>

      {enCours && <Chargement libelle="Recalcul des options…" />}
      {erreur && <MessageErreur message={erreur} />}

      {resultat && !enCours && (
        <div className="space-y-4">
          <Panneau titre={resultat.scenario} sousTitre={resultat.hypothese_fr}>
            <div className="grid gap-4 md:grid-cols-2">
              <Instantane titre="Situation actuelle" instantane={resultat.situation_actuelle} />
              <Instantane
                titre="Situation simulée"
                instantane={resultat.situation_simulee}
                accentue
              />
            </div>

            <div className="mt-4 rounded-lg bg-ardoise-50 px-4 py-3">
              <p className="titre-section mb-1.5">Ce qui change</p>
              <ul className="space-y-1 text-sm text-ardoise-700">
                {resultat.evolution_fr.map((evolution, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ardoise-400" />
                    {evolution}
                  </li>
                ))}
              </ul>
            </div>

            <p className="mt-3 text-xs text-ardoise-500">{resultat.avertissement_fr}</p>
          </Panneau>
        </div>
      )}
    </div>
  )
}

function Instantane({
  titre, instantane, accentue,
}: {
  titre: string
  instantane: InstantaneSimulation
  accentue?: boolean
}) {
  return (
    <div
      className={`rounded-lg border p-4 ${
        accentue ? 'border-orange-300 bg-orange-50/50' : 'border-ardoise-200'
      }`}
    >
      <p className="titre-section">{titre}</p>

      <div className="mt-2 flex items-center gap-2">
        <BadgeRisque niveau={instantane.niveau_risque} libelle={instantane.niveau_risque} />
        {instantane.probabilite_perturbation !== null && (
          <span className="text-sm tabular-nums text-ardoise-600">
            {pourcentage(instantane.probabilite_perturbation)} de perturbation
          </span>
        )}
      </div>

      <p className="mt-2 text-sm text-ardoise-700">
        Meilleure option : <strong>{instantane.meilleure_option ?? 'aucune'}</strong>
      </p>

      <dl className="mt-2 space-y-1 text-sm">
        <Ligne libelle="Options faisables" valeur={String(instantane.options_faisables)} />
        <Ligne libelle="Options écartées" valeur={String(instantane.options_ecartees)} />
        <Ligne libelle="Coût" valeur={mad(instantane.cout_mad)} />
        <Ligne
          libelle="Marge d'échéance"
          valeur={instantane.marge_echeance_h !== null ? heures(instantane.marge_echeance_h) : '—'}
        />
      </dl>
    </div>
  )
}

function Ligne({ libelle, valeur }: { libelle: string; valeur: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-ardoise-500">{libelle}</dt>
      <dd className="tabular-nums text-ardoise-800">{valeur}</dd>
    </div>
  )
}
