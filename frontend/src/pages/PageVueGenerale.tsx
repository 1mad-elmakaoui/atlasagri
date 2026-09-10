/**
 * Vue générale.
 *
 * Répond dans l'ordre aux six questions d'un responsable qui ouvre
 * l'application : qu'est-ce qui ne va pas, où, à quel point, quand, quelles
 * options, que faire. La hiérarchie visuelle suit cet ordre — l'utilisateur ne
 * doit pas avoir à chercher.
 */

import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import { dateHeure, depuisMaintenant, jours, points, pourcentage } from '@/lib/format'
import {
  BadgeRisque, Bouton, CarteKpi, Chargement, EtatVide, MessageErreur, Panneau,
} from '@/components/primitives'

export function PageVueGenerale() {
  const { donnees, erreur, enCours, recharger } = useChargement(() => api.tableauDeBord())

  if (enCours) return <Chargement libelle="Analyse des expéditions en cours…" />
  if (erreur) {
    return <MessageErreur message={erreur} action={<Bouton onClick={recharger}>Réessayer</Bouton>} />
  }
  if (!donnees) return null

  const sourceSimulee = donnees.sources.find((s) => s.message_fr.toLowerCase().includes('simul'))

  return (
    <div className="space-y-6">
      {sourceSimulee && (
        <div className="rounded-lg border border-purple-300 bg-purple-50 px-4 py-3">
          <p className="text-sm font-medium text-purple-900">
            Mode démonstration — données météo simulées
          </p>
          <p className="mt-0.5 text-sm text-purple-800">
            Les valeurs météo proviennent d'un jeu local et sont signalées comme
            simulées dans toute l'interface. Elles ne doivent pas fonder une
            décision réelle.
          </p>
        </div>
      )}

      <div>
        <h1 className="text-xl font-semibold text-ardoise-900">Vue générale</h1>
        <p className="mt-0.5 text-sm text-ardoise-500">
          Situation opérationnelle et actions possibles maintenant.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <CarteKpi
          libelle="Risque global"
          valeur={donnees.risque_global.niveau}
          detail={donnees.risque_global.explication_fr}
          accent={donnees.risque_global.code}
        />
        <CarteKpi
          libelle="Expéditions exposées"
          valeur={`${donnees.indicateurs.expeditions_exposees} / ${donnees.indicateurs.expeditions_suivies}`}
          detail="Niveau modéré ou supérieur"
        />
        <CarteKpi
          libelle="Stocks critiques"
          valeur={donnees.indicateurs.stocks_critiques}
          detail="Moins de 3 jours de marge utilisable"
        />
        <CarteKpi
          libelle="Décisions en attente"
          valeur={donnees.indicateurs.recommandations_en_attente}
          detail={`${donnees.indicateurs.alertes_non_traitees} alerte(s) non traitée(s)`}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Panneau
          className="lg:col-span-2"
          titre="Expéditions exposées"
          sousTitre="Classées par niveau de risque, les plus urgentes d'abord"
        >
          {donnees.expeditions_exposees.length === 0 ? (
            <EtatVide
              titre="Aucune expédition exposée"
              detail="Toutes les expéditions suivies présentent un risque faible sur leur fenêtre de passage."
            />
          ) : (
            <ul className="divide-y divide-ardoise-100">
              {donnees.expeditions_exposees.map((expedition) => (
                <li key={expedition.reference} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/expeditions/${expedition.reference}`}
                          className="font-semibold text-ardoise-900 hover:text-action"
                        >
                          {expedition.reference}
                        </Link>
                        <BadgeRisque niveau={expedition.niveau_risque} taille="petite" />
                        {!expedition.echeance_respectee && (
                          <span className="rounded bg-red-100 px-1.5 py-0.5 text-[11px] font-medium text-red-800">
                            Échéance menacée
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 text-sm text-ardoise-600">
                        {expedition.produit} · {expedition.volume_tonnes} t ·{' '}
                        {expedition.origine} → {expedition.destination}
                      </p>
                      <p className="mt-0.5 text-xs text-ardoise-500">
                        Départ {dateHeure(expedition.depart_prevu)} (
                        {depuisMaintenant(expedition.depart_prevu)})
                      </p>
                      {expedition.facteurs_fr.length > 0 && (
                        <p className="mt-1 text-xs text-ardoise-500">
                          {expedition.facteurs_fr[0]}
                        </p>
                      )}
                    </div>

                    <div className="text-right">
                      <p className="text-lg font-semibold tabular-nums text-ardoise-900">
                        {pourcentage(expedition.probabilite_perturbation)}
                      </p>
                      <p className="text-[11px] text-ardoise-500">risque de perturbation</p>
                      {expedition.action_possible_fr && (
                        <p className="mt-1.5 text-xs font-medium text-emerald-700">
                          {expedition.action_possible_fr}
                          {expedition.gain_risque_points !== null && (
                            <span className="ml-1 tabular-nums">
                              ({points(-expedition.gain_risque_points)})
                            </span>
                          )}
                        </p>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panneau>

        <div className="space-y-6">
          <Panneau titre="Opportunités d'action" sousTitre="Ce que vous pouvez faire maintenant">
            {donnees.opportunites_action.length === 0 ? (
              <EtatVide titre="Aucune action à engager" />
            ) : (
              <ul className="space-y-2.5">
                {donnees.opportunites_action.map((opportunite) => (
                  <li key={opportunite.reference} className="rounded-md bg-emerald-50 px-3 py-2">
                    <Link
                      to={`/expeditions/${opportunite.reference}`}
                      className="text-sm font-medium text-emerald-900 hover:underline"
                    >
                      {opportunite.reference}
                    </Link>
                    <p className="text-sm text-emerald-800">{opportunite.action_fr}</p>
                    {opportunite.gain_risque_points !== null && (
                      <p className="text-xs tabular-nums text-emerald-700">
                        Réduction estimée : {points(-opportunite.gain_risque_points)}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Panneau>

          <Panneau titre="Stocks critiques">
            {donnees.stocks_critiques.length === 0 ? (
              <EtatVide titre="Aucun stock critique" />
            ) : (
              <ul className="space-y-2">
                {donnees.stocks_critiques.map((stock, i) => (
                  <li key={i} className="text-sm">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-medium text-ardoise-800">{stock.produit}</span>
                      <span
                        className={`tabular-nums ${
                          stock.sous_stock_securite ? 'text-red-700' : 'text-amber-700'
                        }`}
                      >
                        {jours(stock.couverture_utilisable_jours)}
                      </span>
                    </div>
                    <p className="text-xs text-ardoise-500">{stock.site}</p>
                  </li>
                ))}
              </ul>
            )}
          </Panneau>

          <Panneau titre="Sources de données" sousTitre="État des fournisseurs externes">
            <ul className="space-y-2">
              {donnees.sources.map((source) => (
                <li key={source.fournisseur} className="text-sm">
                  <div className="flex items-center gap-2">
                    <span
                      className={`h-2 w-2 rounded-full ${
                        source.disponible ? 'bg-emerald-600' : 'bg-ardoise-400'
                      }`}
                    />
                    <span className="font-medium text-ardoise-800">{source.fournisseur}</span>
                  </div>
                  <p className="ml-4 text-xs text-ardoise-500">{source.message_fr}</p>
                </li>
              ))}
            </ul>
          </Panneau>
        </div>
      </div>
    </div>
  )
}
