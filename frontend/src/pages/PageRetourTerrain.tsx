/**
 * Retour terrain.
 *
 * C'est la page qui rend le reste du produit corrigible. Sans elle, AtlasAgri
 * annonce des probabilités sans jamais apprendre si elles se vérifient.
 *
 * L'entretien est conduit une question à la fois, l'enchaînement dépendant des
 * réponses précédentes. Le questionnaire complet n'est pas affiché d'un bloc :
 * un formulaire de onze champs dont sept ne s'appliquent pas décourage la
 * personne dont on a justement besoin.
 */

import { useEffect, useState } from 'react'
import { api, ErreurApi } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import type { CollecteEnAttente, EtapeQuestionnaire } from '@/lib/types'
import { dateHeure, heures, mad, pourcentage } from '@/lib/format'
import {
  BadgeRisque, Bouton, Chargement, EtatVide, MessageErreur, Panneau,
} from '@/components/primitives'

export function PageRetourTerrain() {
  const attente = useChargement(() => api.collectesEnAttente())
  const historique = useChargement(() => api.historiqueResultats())
  const [active, setActive] = useState<CollecteEnAttente | null>(null)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ardoise-900">Retour terrain</h1>
        <p className="mt-0.5 max-w-3xl text-sm text-ardoise-500">
          Ce qui s'est réellement passé sur les expéditions livrées. Ces retours
          sont la seule source qui permette de vérifier, puis de corriger, les
          prévisions du système.
        </p>
      </div>

      {active ? (
        <Entretien
          collecte={active}
          onTermine={() => {
            setActive(null)
            attente.recharger()
            historique.recharger()
          }}
          onAnnule={() => setActive(null)}
        />
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Panneau
            titre="En attente de retour"
            sousTitre="Expéditions arrivées à échéance, les plus anciennes d'abord"
          >
            {attente.enCours && <Chargement />}
            {attente.erreur && <MessageErreur message={attente.erreur} />}
            {attente.donnees && (
              attente.donnees.en_attente.length === 0 ? (
                <EtatVide
                  titre="Aucun retour en attente"
                  detail="Les expéditions apparaissent ici quelques heures après leur échéance."
                />
              ) : (
                <ul className="divide-y divide-ardoise-100">
                  {attente.donnees.en_attente.map((item) => (
                    <li key={item.shipment_id} className="flex items-start justify-between gap-3 py-3 first:pt-0">
                      <div>
                        <p className="font-medium text-ardoise-900">{item.reference}</p>
                        <p className="text-sm text-ardoise-600">
                          {item.produit} · {item.origine} → {item.destination}
                        </p>
                        <p className="mt-0.5 text-xs text-ardoise-500">
                          Échéance {dateHeure(item.echeance)} · dépassée de{' '}
                          {heures(item.heures_depuis_echeance)}
                        </p>
                        {item.probabilite_annoncee !== null && (
                          <p className="mt-1 text-xs text-ardoise-500">
                            Le système avait annoncé{' '}
                            <strong className="tabular-nums">
                              {pourcentage(item.probabilite_annoncee)}
                            </strong>{' '}
                            de risque de perturbation.
                          </p>
                        )}
                      </div>
                      <Bouton variante="primaire" onClick={() => setActive(item)}>
                        Renseigner
                      </Bouton>
                    </li>
                  ))}
                </ul>
              )
            )}
          </Panneau>

          <Panneau titre="Retours déjà recueillis">
            {historique.enCours && <Chargement />}
            {historique.donnees && (
              historique.donnees.resultats.length === 0 ? (
                <EtatVide titre="Aucun retour enregistré" />
              ) : (
                <ul className="divide-y divide-ardoise-100">
                  {historique.donnees.resultats.map((r) => (
                    <li key={r.id} className="py-3 first:pt-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-ardoise-900">{r.reference}</span>
                        {r.perturbation ? (
                          <BadgeRisque niveau="HIGH" libelle="Perturbée" taille="petite" />
                        ) : r.livree ? (
                          <BadgeRisque niveau="LOW" libelle="Sans incident" taille="petite" />
                        ) : (
                          <span className="rounded bg-ardoise-100 px-1.5 py-0.5 text-xs text-ardoise-600">
                            Non livrée
                          </span>
                        )}
                        <span
                          title={
                            r.partition === 'evaluation'
                              ? "Réservé à l'évaluation : sert à juger le système, jamais à l'ajuster."
                              : 'Alimente la calibration des seuils.'
                          }
                          className="rounded bg-ardoise-100 px-1.5 py-0.5 text-[11px] text-ardoise-600"
                        >
                          {r.partition}
                        </span>
                        {r.corrige_une_version_anterieure && (
                          <span className="text-[11px] text-ardoise-400">corrigé</span>
                        )}
                      </div>
                      <p className="mt-0.5 text-sm text-ardoise-600">
                        {r.perturbation && r.type_perturbation
                          ? `${r.type_perturbation}${r.lieu ? ` — ${r.lieu}` : ''}`
                          : 'Aucune perturbation signalée'}
                        {r.retard_h ? ` · ${heures(r.retard_h)} de retard` : ''}
                        {r.perte_mad ? ` · perte ${mad(r.perte_mad)}` : ''}
                      </p>
                      <p className="text-xs text-ardoise-400">
                        Recueilli {dateHeure(r.recueilli_le)}
                      </p>
                    </li>
                  ))}
                </ul>
              )
            )}
          </Panneau>
        </div>
      )}
    </div>
  )
}

function Entretien({
  collecte, onTermine, onAnnule,
}: {
  collecte: CollecteEnAttente
  onTermine: () => void
  onAnnule: () => void
}) {
  const [etape, setEtape] = useState<EtapeQuestionnaire | null>(null)
  const [saisie, setSaisie] = useState<string>('')
  const [erreur, setErreur] = useState<string | null>(null)
  const [enCours, setEnCours] = useState(false)
  const [confirmation, setConfirmation] = useState<string | null>(null)

  useEffect(() => {
    api.questionSuivante(collecte.reference, {})
      .then(setEtape)
      .catch((e) => setErreur(e instanceof ErreurApi ? e.message : 'Ouverture impossible.'))
  }, [collecte.reference])

  async function repondre(valeur: unknown) {
    if (!etape?.question) return
    setEnCours(true)
    setErreur(null)
    try {
      const suivant = await api.repondre(
        collecte.reference, etape.reponses, etape.question.code, valeur,
      )
      setEtape(suivant)
      setSaisie('')
    } catch (e) {
      setErreur(e instanceof ErreurApi ? e.message : 'Réponse refusée.')
    } finally {
      setEnCours(false)
    }
  }

  async function enregistrer() {
    if (!etape) return
    setEnCours(true)
    setErreur(null)
    try {
      const resultat = await api.enregistrerResultat(collecte.reference, etape.reponses)
      setConfirmation(resultat.message_fr)
      setTimeout(onTermine, 1800)
    } catch (e) {
      setErreur(e instanceof ErreurApi ? e.message : 'Enregistrement impossible.')
      setEnCours(false)
    }
  }

  if (!etape) return <Chargement libelle="Ouverture du questionnaire…" />

  const { question, progression } = etape
  const avancement = progression.total > 0
    ? Math.round((progression.repondues / progression.total) * 100)
    : 0

  return (
    <Panneau
      titre={`Expédition ${collecte.reference}`}
      sousTitre={`${collecte.produit} · ${collecte.origine} → ${collecte.destination}`}
      actions={<Bouton variante="discret" onClick={onAnnule}>Fermer</Bouton>}
      className="mx-auto max-w-2xl"
    >
      <div className="mb-4">
        <div className="h-1.5 overflow-hidden rounded-full bg-ardoise-100">
          <div className="h-full rounded-full bg-action transition-all" style={{ width: `${avancement}%` }} />
        </div>
        <p className="mt-1 text-xs text-ardoise-500">
          {progression.repondues} / {progression.total} questions
        </p>
      </div>

      {confirmation ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3">
          <p className="text-sm font-medium text-emerald-900">{confirmation}</p>
        </div>
      ) : question ? (
        <div>
          <p className="text-base font-medium text-ardoise-900">{question.texte_fr}</p>
          {question.aide_fr && (
            <p className="mt-1 text-sm text-ardoise-500">{question.aide_fr}</p>
          )}

          <div className="mt-4">
            {question.type === 'boolean' && (
              <div className="flex gap-2">
                <Bouton variante="primaire" onClick={() => repondre(true)} disabled={enCours}>
                  Oui
                </Bouton>
                <Bouton onClick={() => repondre(false)} disabled={enCours}>
                  Non
                </Bouton>
              </div>
            )}

            {question.type === 'choice' && (
              <div className="flex flex-wrap gap-2">
                {question.options.map((option) => (
                  <button
                    key={option.code}
                    onClick={() => repondre(option.code)}
                    disabled={enCours}
                    className="rounded-md border border-ardoise-300 bg-white px-3 py-1.5 text-sm text-ardoise-700 hover:border-action hover:bg-blue-50 disabled:opacity-50"
                  >
                    {option.libelle_fr}
                  </button>
                ))}
              </div>
            )}

            {(question.type === 'number' || question.type === 'text') && (
              <form
                onSubmit={(e) => { e.preventDefault(); repondre(saisie) }}
                className="flex gap-2"
              >
                <input
                  autoFocus
                  type={question.type === 'number' ? 'number' : 'text'}
                  step="any"
                  value={saisie}
                  onChange={(e) => setSaisie(e.target.value)}
                  className="flex-1 rounded-md border border-ardoise-300 px-3 py-2 text-sm focus:border-action focus:outline-none focus:ring-1 focus:ring-action"
                />
                <Bouton type="submit" variante="primaire" disabled={enCours}>
                  Valider
                </Bouton>
                {!question.obligatoire && (
                  <Bouton onClick={() => repondre('')} disabled={enCours}>
                    Passer
                  </Bouton>
                )}
              </form>
            )}
          </div>
        </div>
      ) : (
        <div>
          <p className="font-medium text-ardoise-900">Questionnaire terminé</p>
          <p className="mt-1 text-sm text-ardoise-600">
            Une fois enregistré, ce retour ne sera plus modifiable : une correction
            créera une nouvelle version sans effacer celle-ci.
          </p>
          <div className="mt-4">
            <Bouton variante="primaire" onClick={enregistrer} disabled={enCours}>
              {enCours ? 'Enregistrement…' : 'Enregistrer le retour'}
            </Bouton>
          </div>
        </div>
      )}

      {erreur && <div className="mt-4"><MessageErreur message={erreur} /></div>}
    </Panneau>
  )
}
