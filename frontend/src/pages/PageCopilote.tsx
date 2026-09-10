/**
 * Copilote IA.
 *
 * La réponse n'est jamais affichée comme un simple paragraphe : la conclusion
 * structurée devient des cartes, et les outils mobilisés sont listés pour que
 * l'utilisateur voie sur quoi repose la réponse.
 *
 * Quand le copilote n'est pas configuré, la page le dit et renvoie vers les
 * fonctions déterministes, qui restent entièrement opérationnelles.
 */

import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ErreurApi } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import type { ReponseCopilote } from '@/lib/types'
import { BadgeRisque, Bouton, Chargement, MessageErreur, Panneau } from '@/components/primitives'

const EXEMPLES = [
  'Mon transport de tomates vers Casablanca est-il à risque ?',
  'Quelles alternatives pour EXP-1842 et laquelle recommandez-vous ?',
  'Quels stocks risquent la rupture si un fournisseur est retardé de 48 heures ?',
  'Quelles régions sont les plus exposées dans les 24 prochaines heures ?',
]

export function PageCopilote() {
  const { donnees: etat, enCours: chargementEtat } = useChargement(() => api.etatCopilote())
  const [question, setQuestion] = useState('')
  const [reponse, setReponse] = useState<ReponseCopilote | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [enCours, setEnCours] = useState(false)

  async function demander(texte: string) {
    if (!texte.trim()) return
    setEnCours(true)
    setErreur(null)
    try {
      setReponse(await api.demanderCopilote(texte, reponse?.conversation_id))
    } catch (e) {
      setErreur(e instanceof ErreurApi ? e.message : 'Le copilote est indisponible.')
    } finally {
      setEnCours(false)
    }
  }

  function soumettre(evenement: FormEvent) {
    evenement.preventDefault()
    demander(question)
  }

  if (chargementEtat) return <Chargement />

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ardoise-900">Copilote IA</h1>
        <p className="mt-0.5 text-sm text-ardoise-500">
          Posez une question métier. Le copilote interroge les moteurs d'analyse et
          explique le résultat — il ne calcule rien lui-même.
        </p>
      </div>

      {etat && !etat.disponible && (
        <Panneau>
          <p className="font-medium text-ardoise-800">Copilote non configuré</p>
          <p className="mt-1 text-sm text-ardoise-600">{etat.message_fr}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link to="/expeditions">
              <Bouton>Analyser une expédition</Bouton>
            </Link>
            <Link to="/simulations">
              <Bouton>Lancer une simulation</Bouton>
            </Link>
          </div>
        </Panneau>
      )}

      {etat?.disponible && (
        <>
          <form onSubmit={soumettre} className="carte-panneau p-4">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={3}
              placeholder="Exemple : mon transport de tomates vers Casablanca est-il à risque ?"
              className="w-full resize-none rounded-md border border-ardoise-300 px-3 py-2 text-sm focus:border-action focus:outline-none focus:ring-1 focus:ring-action"
            />
            <div className="mt-3 flex items-center justify-between gap-3">
              <p className="text-xs text-ardoise-400">
                Modèle : {etat.modele}
              </p>
              <Bouton type="submit" variante="primaire" disabled={enCours || !question.trim()}>
                {enCours ? 'Analyse en cours…' : 'Poser la question'}
              </Bouton>
            </div>
          </form>

          <div className="flex flex-wrap gap-2">
            {EXEMPLES.map((exemple) => (
              <button
                key={exemple}
                onClick={() => { setQuestion(exemple); demander(exemple) }}
                disabled={enCours}
                className="rounded-full border border-ardoise-300 bg-white px-3 py-1 text-xs text-ardoise-600 hover:bg-ardoise-50 disabled:opacity-50"
              >
                {exemple}
              </button>
            ))}
          </div>
        </>
      )}

      {enCours && <Chargement libelle="Le copilote consulte les moteurs d'analyse…" />}
      {erreur && <MessageErreur message={erreur} />}

      {reponse && !enCours && (
        <div className="space-y-4">
          {reponse.conclusion && (
            <Panneau titre="Conclusion">
              {reponse.conclusion.decision_fr && (
                <div className="flex flex-wrap items-center gap-3">
                  <p className="text-lg font-semibold text-ardoise-900">
                    {reponse.conclusion.decision_fr}
                  </p>
                  {reponse.conclusion.niveau_risque && (
                    <BadgeRisque niveau={reponse.conclusion.niveau_risque} />
                  )}
                </div>
              )}

              <div className="mt-4 grid gap-4 md:grid-cols-2">
                {reponse.conclusion.raisons_fr && reponse.conclusion.raisons_fr.length > 0 && (
                  <div>
                    <p className="titre-section mb-1.5">Raisons</p>
                    <ul className="space-y-1 text-sm text-ardoise-700">
                      {reponse.conclusion.raisons_fr.map((raison, i) => (
                        <li key={i} className="flex gap-2">
                          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-600" />
                          {raison}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {reponse.conclusion.contreparties_fr &&
                  reponse.conclusion.contreparties_fr.length > 0 && (
                  <div>
                    <p className="titre-section mb-1.5">Contreparties</p>
                    <ul className="space-y-1 text-sm text-ardoise-700">
                      {reponse.conclusion.contreparties_fr.map((contrepartie, i) => (
                        <li key={i} className="flex gap-2">
                          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
                          {contrepartie}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-ardoise-100 pt-3">
                {reponse.conclusion.confiance && (
                  <span className="text-xs text-ardoise-500">
                    Confiance : {reponse.conclusion.confiance}
                  </span>
                )}
                {reponse.conclusion.expedition_reference && (
                  <Link
                    to={`/expeditions/${reponse.conclusion.expedition_reference}`}
                    className="text-sm font-medium text-action hover:underline"
                  >
                    Ouvrir {reponse.conclusion.expedition_reference} sur la carte →
                  </Link>
                )}
              </div>
            </Panneau>
          )}

          <Panneau titre="Réponse détaillée">
            <div className="whitespace-pre-wrap text-sm leading-relaxed text-ardoise-700">
              {reponse.reponse_fr.replace(/```json[\s\S]*?```/g, '').trim()}
            </div>
          </Panneau>

          {reponse.outils_utilises.length > 0 && (
            <Panneau
              titre="Données consultées"
              sousTitre="Capacités mobilisées pour construire cette réponse"
            >
              <ul className="space-y-1.5 text-sm">
                {reponse.outils_utilises.map((trace, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span
                      className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                        trace.reussi ? 'bg-emerald-600' : 'bg-red-600'
                      }`}
                    />
                    <div>
                      <span className="font-mono text-xs text-ardoise-700">{trace.outil}</span>
                      {trace.erreur_fr && (
                        <p className="text-xs text-red-700">{trace.erreur_fr}</p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </Panneau>
          )}
        </div>
      )}
    </div>
  )
}
