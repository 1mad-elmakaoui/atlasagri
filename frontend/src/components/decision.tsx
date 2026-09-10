/**
 * Composants de décision : recommandation, comparaison, preuves, chronologie.
 *
 * Ils appliquent la règle centrale de l'explicabilité : un décideur doit
 * pouvoir répondre à « que se passe-t-il ? », « où ? », « quand ? », « quelles
 * options ? », « laquelle ? » et « pourquoi ? » sans lire une ligne technique.
 *
 * Aucun de ces composants ne calcule quoi que ce soit. Ils affichent ce que les
 * moteurs ont produit — sinon un même chiffre finirait par différer entre
 * l'écran et l'API.
 */

import { useState } from 'react'
import type { Alternative, DetailExpedition, Troncon } from '@/lib/types'
import { dateHeure, heures, heuresSignees, mad, madSigne, points, pourcentage } from '@/lib/format'
import { BadgeRisque, BarreComparaison, Bouton, EtiquetteDonnee, Panneau } from './primitives'

/** Bandeau de recommandation : la conclusion, avant tout le reste. */
export function PanneauRecommandation({
  recommandation, planActuel, onVoirCarte, onComparer,
}: {
  recommandation: Alternative
  planActuel: Alternative | undefined
  onVoirCarte?: () => void
  onComparer?: () => void
}) {
  const identique = planActuel && recommandation.id === planActuel.id

  return (
    <section className="carte-panneau overflow-hidden">
      <div className="border-b border-ardoise-200 bg-gradient-to-r from-blue-50 to-white px-5 py-4">
        <p className="titre-section text-action">Recommandation</p>
        <h2 className="mt-1 text-xl font-semibold text-ardoise-900">
          {recommandation.libelle_fr}
        </h2>
        <p className="mt-1 text-sm text-ardoise-600">{recommandation.description_fr}</p>
      </div>

      <div className="grid grid-cols-2 divide-x divide-ardoise-200 border-b border-ardoise-200 md:grid-cols-4">
        <Metrique
          libelle="Risque de perturbation"
          valeur={pourcentage(recommandation.probabilite_perturbation)}
          ecart={
            identique ? null : recommandation.ecart_risque_points !== null
              ? points(recommandation.ecart_risque_points)
              : null
          }
          ecartFavorable={(recommandation.ecart_risque_points ?? 0) < 0}
        />
        <Metrique
          libelle="Coût estimé"
          valeur={mad(recommandation.cout_mad)}
          ecart={identique ? null : madSigne(recommandation.ecart_cout_mad)}
          ecartFavorable={(recommandation.ecart_cout_mad ?? 0) <= 0}
        />
        <Metrique
          libelle="Durée de trajet"
          valeur={heures(recommandation.duree_h)}
          ecart={identique ? null : heuresSignees(recommandation.ecart_duree_h)}
          ecartFavorable={(recommandation.ecart_duree_h ?? 0) <= 0}
        />
        <Metrique
          libelle="Engagement de service"
          valeur={recommandation.echeance_respectee ? 'Respecté' : 'Non respecté'}
          ecart={
            recommandation.marge_echeance_h !== null
              ? `marge ${heures(recommandation.marge_echeance_h)}`
              : null
          }
          ecartFavorable={recommandation.echeance_respectee}
        />
      </div>

      <div className="grid gap-5 p-5 md:grid-cols-2">
        <div>
          <p className="titre-section mb-2">Pourquoi cette recommandation</p>
          {recommandation.raisons_fr.length > 0 ? (
            <ul className="space-y-1.5">
              {recommandation.raisons_fr.map((raison, i) => (
                <li key={i} className="flex gap-2 text-sm text-ardoise-700">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-600" />
                  {raison}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ardoise-500">Aucune raison particulière enregistrée.</p>
          )}
        </div>

        <div>
          <p className="titre-section mb-2">Ce que cela coûte</p>
          {recommandation.contreparties_fr.length > 0 ? (
            <ul className="space-y-1.5">
              {recommandation.contreparties_fr.map((contrepartie, i) => (
                <li key={i} className="flex gap-2 text-sm text-ardoise-700">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
                  {contrepartie}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ardoise-500">
              Aucune contrepartie identifiée par rapport au plan actuel.
            </p>
          )}
        </div>
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t border-ardoise-200 bg-ardoise-50 px-5 py-3">
        {onVoirCarte && <Bouton onClick={onVoirCarte}>Voir sur la carte</Bouton>}
        {onComparer && <Bouton onClick={onComparer}>Comparer les alternatives</Bouton>}
        <span className="ml-auto flex items-center gap-2 text-xs text-ardoise-500">
          Confiance : {recommandation.confiance}
          <EtiquetteDonnee etat={recommandation.etat_des_donnees} />
        </span>
      </footer>
    </section>
  )
}

function Metrique({
  libelle, valeur, ecart, ecartFavorable,
}: {
  libelle: string
  valeur: string
  ecart?: string | null
  ecartFavorable?: boolean
}) {
  return (
    <div className="px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-ardoise-500">{libelle}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-ardoise-900">{valeur}</p>
      {ecart && (
        <p
          className={`text-xs font-medium tabular-nums ${
            ecartFavorable ? 'text-emerald-700' : 'text-orange-700'
          }`}
        >
          {ecart}
        </p>
      )}
    </div>
  )
}

/** Tableau comparatif des options, recommandation mise en évidence. */
export function TableauComparaison({
  alternatives, selection, onSelection,
}: {
  alternatives: Alternative[]
  selection?: string | null
  onSelection?: (id: string) => void
}) {
  const coutMax = Math.max(...alternatives.map((a) => a.cout_mad), 1)

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-sm">
        <thead>
          <tr className="border-b border-ardoise-200 text-left text-xs uppercase tracking-wide text-ardoise-500">
            <th className="px-3 py-2 font-medium">Option</th>
            <th className="px-3 py-2 font-medium">Risque</th>
            <th className="px-3 py-2 font-medium">Perturbation</th>
            <th className="px-3 py-2 font-medium">Coût</th>
            <th className="px-3 py-2 font-medium">Durée</th>
            <th className="px-3 py-2 font-medium">Échéance</th>
            <th className="px-3 py-2 font-medium">Exposition</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ardoise-100">
          {alternatives.map((a) => (
            <tr
              key={a.id}
              onClick={() => onSelection?.(a.id)}
              className={`cursor-pointer transition ${
                a.recommandee
                  ? 'bg-blue-50/60'
                  : selection === a.id
                    ? 'bg-ardoise-100'
                    : 'hover:bg-ardoise-50'
              }`}
            >
              <td className="px-3 py-2.5">
                <div className="flex items-center gap-2">
                  {a.recommandee && (
                    <span className="rounded bg-action px-1.5 py-0.5 text-[10px] font-semibold uppercase text-white">
                      Recommandé
                    </span>
                  )}
                  <div>
                    <p className="font-medium text-ardoise-900">{a.libelle_fr}</p>
                    <p className="text-xs text-ardoise-500">{a.type_fr}</p>
                  </div>
                </div>
              </td>
              <td className="px-3 py-2.5">
                <BadgeRisque niveau={a.niveau_risque} libelle={a.niveau_risque_fr} taille="petite" />
              </td>
              <td className="px-3 py-2.5 tabular-nums">
                {pourcentage(a.probabilite_perturbation)}
                {a.ecart_risque_points !== null && !a.recommandee && (
                  <span className="ml-1 text-xs text-ardoise-500">
                    ({points(a.ecart_risque_points)})
                  </span>
                )}
              </td>
              <td className="px-3 py-2.5">
                <p className="tabular-nums">{mad(a.cout_mad)}</p>
                <BarreComparaison
                  valeur={a.cout_mad}
                  maximum={coutMax}
                  couleur={a.recommandee ? 'bg-action' : 'bg-ardoise-300'}
                />
              </td>
              <td className="px-3 py-2.5 tabular-nums">{heures(a.duree_h)}</td>
              <td className="px-3 py-2.5">
                {a.echeance_respectee ? (
                  <span className="text-emerald-700">
                    ✓ {a.marge_echeance_h !== null ? heures(a.marge_echeance_h) : ''}
                  </span>
                ) : (
                  <span className="text-red-700">✗ dépassée</span>
                )}
              </td>
              <td className="px-3 py-2.5 tabular-nums text-ardoise-600">
                {pourcentage(a.part_exposee)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Options écartées, avec leur motif : montre ce qui a été examiné puis rejeté. */
export function OptionsEcartees({
  options,
}: {
  options: DetailExpedition['alternatives_ecartees']
}) {
  const [ouvert, setOuvert] = useState(false)
  if (options.length === 0) return null

  return (
    <div className="rounded-lg border border-ardoise-200 bg-ardoise-50">
      <button
        onClick={() => setOuvert(!ouvert)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm font-medium text-ardoise-700"
      >
        <span>{options.length} option(s) examinée(s) puis écartée(s)</span>
        <span className="text-ardoise-400">{ouvert ? '−' : '+'}</span>
      </button>
      {ouvert && (
        <ul className="space-y-2 border-t border-ardoise-200 px-4 py-3">
          {options.map((option) => (
            <li key={option.id}>
              <p className="text-sm font-medium text-ardoise-800">
                {option.libelle_fr}
                <span className="ml-2 text-xs font-normal text-ardoise-500">{option.type_fr}</span>
              </p>
              <ul className="mt-0.5 list-disc pl-5 text-xs text-ardoise-600">
                {option.motifs_rejet_fr.map((motif, i) => (
                  <li key={i}>{motif}</li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * Chronologie de décision.
 *
 * Répond à la question la plus opérationnelle de toutes : combien de temps
 * reste-t-il pour agir ? Elle situe le départ, les fenêtres d'exposition et
 * l'échéance sur un même axe.
 */
export function ChronologieDecision({
  troncons, depart, echeance,
}: {
  troncons: Troncon[]
  depart: string
  echeance: string
}) {
  const debut = Date.now()
  const fin = new Date(echeance).getTime()
  const duree = Math.max(fin - debut, 1)
  const position = (instant: number) => Math.max(0, Math.min(100, ((instant - debut) / duree) * 100))

  const exposes = troncons.filter((t) => t.expose)
  const premiereExposition = exposes[0]

  const departPct = position(new Date(depart).getTime())

  return (
    <div>
      <div className="relative mt-8 h-2 rounded-full bg-ardoise-100">
        {exposes.map((t) => {
          const gauche = position(new Date(t.depart_troncon).getTime())
          const droite = position(new Date(t.arrivee_troncon).getTime())
          return (
            <div
              key={t.index}
              title={`${t.de} → ${t.vers} · ${t.niveau_risque_fr}`}
              className="absolute top-0 h-2 rounded-full"
              style={{
                left: `${gauche}%`,
                width: `${Math.max(1.5, droite - gauche)}%`,
                backgroundColor:
                  t.niveau_risque === 'CRITICAL' ? '#b91c1c'
                  : t.niveau_risque === 'HIGH' ? '#ea580c'
                  : '#ca8a04',
              }}
            />
          )
        })}
        <Reperage position={0} libelle="Maintenant" />
        {/* Le repère de départ est masqué s'il chevauche « Maintenant » :
            deux libellés superposés sont moins lisibles qu'un seul. */}
        {departPct > 12 && departPct < 88 && (
          <Reperage position={departPct} libelle="Départ" accent />
        )}
        <Reperage position={100} libelle="Échéance" aligneDroite />
      </div>

      <div className="mt-12 space-y-2 text-sm">
        {premiereExposition ? (
          <p className="text-ardoise-700">
            Première fenêtre d'exposition&nbsp;: <strong>{premiereExposition.de} → {premiereExposition.vers}</strong>,
            {' '}le {dateHeure(premiereExposition.depart_troncon)}, soit{' '}
            <strong>{heures(premiereExposition.heures_apres_depart)}</strong> après le départ.
          </p>
        ) : (
          <p className="text-emerald-700">
            Aucun tronçon exposé sur la fenêtre de passage prévue.
          </p>
        )}
        <p className="text-ardoise-500">
          Départ prévu {dateHeure(depart)} · échéance {dateHeure(echeance)}
        </p>
      </div>
    </div>
  )
}

function Reperage({
  position, libelle, accent, aligneDroite,
}: {
  position: number
  libelle: string
  accent?: boolean
  aligneDroite?: boolean
}) {
  return (
    <div
      className="absolute -top-1 flex flex-col items-center"
      style={{ left: `${position}%` }}
    >
      <span className={`h-4 w-0.5 ${accent ? 'bg-action' : 'bg-ardoise-400'}`} />
      <span
        className={`mt-1 whitespace-nowrap text-[11px] font-medium ${
          accent ? 'text-action' : 'text-ardoise-500'
        } ${aligneDroite ? '-translate-x-full' : position === 0 ? '' : '-translate-x-1/2'}`}
      >
        {libelle}
      </span>
    </div>
  )
}

/**
 * Panneau de preuves.
 *
 * Traduit en termes métier ce sur quoi repose la décision. Il n'affiche jamais
 * de log ni de sortie brute : un décideur n'a pas à déchiffrer une trace
 * technique pour accorder sa confiance.
 */
export function PanneauPreuves({ alternative }: { alternative: Alternative }) {
  return (
    <Panneau titre="Sources et preuves" sousTitre="Ce sur quoi repose cette analyse">
      <dl className="space-y-3 text-sm">
        {alternative.facteurs_de_risque_fr.length > 0 && (
          <div>
            <dt className="titre-section mb-1">Conditions relevées sur le trajet</dt>
            <dd>
              <ul className="space-y-1 text-ardoise-700">
                {alternative.facteurs_de_risque_fr.map((facteur, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-orange-500" />
                    {facteur}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}

        <div>
          <dt className="titre-section mb-1">Critères de comparaison retenus</dt>
          <dd className="space-y-1.5">
            {alternative.criteres.map((critere) => (
              <div key={critere.libelle_fr} className="flex items-center gap-3">
                <span className="w-52 shrink-0 text-ardoise-600">{critere.libelle_fr}</span>
                <BarreComparaison
                  valeur={critere.poids}
                  maximum={1}
                  couleur="bg-action"
                  libelle={pourcentage(critere.poids)}
                />
              </div>
            ))}
          </dd>
        </div>

        <div>
          <dt className="titre-section mb-1">Origine des données</dt>
          <dd className="flex flex-wrap items-center gap-2">
            {alternative.sources.map((source) => (
              <span
                key={source}
                className="rounded bg-ardoise-100 px-2 py-0.5 text-xs text-ardoise-700"
              >
                {source}
              </span>
            ))}
            <EtiquetteDonnee etat={alternative.etat_des_donnees} />
          </dd>
        </div>
      </dl>
    </Panneau>
  )
}
