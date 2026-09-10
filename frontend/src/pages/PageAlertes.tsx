/** Alertes structurées : QUOI / OÙ / QUAND / IMPACT / ACTION. */

import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import { dateHeure } from '@/lib/format'
import {
  BadgeRisque, Bouton, Chargement, EtatVide, MessageErreur, Panneau,
} from '@/components/primitives'

export function PageAlertes() {
  const { donnees, erreur, enCours, recharger } = useChargement(() => api.alertes())

  if (enCours) return <Chargement />
  if (erreur) return <MessageErreur message={erreur} action={<Bouton onClick={recharger}>Réessayer</Bouton>} />
  if (!donnees) return null

  async function traiter(id: string) {
    await api.traiterAlerte(id)
    recharger()
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ardoise-900">Alertes</h1>
        <p className="mt-0.5 text-sm text-ardoise-500">
          Chaque alerte indique ce qui se passe, où, quand, l'impact attendu et
          l'action recommandée.
        </p>
      </div>

      {donnees.alertes.length === 0 ? (
        <Panneau>
          <EtatVide
            titre="Aucune alerte"
            detail="Les alertes sont créées depuis le copilote ou par les responsables d'exploitation."
          />
        </Panneau>
      ) : (
        <div className="space-y-4">
          {donnees.alertes.map((alerte) => (
            <Panneau key={alerte.id} className={alerte.traitee ? 'opacity-60' : ''}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex items-center gap-2">
                  <BadgeRisque niveau={alerte.niveau} />
                  <span className="text-xs text-ardoise-400">{dateHeure(alerte.cree_le)}</span>
                </div>
                {!alerte.traitee && (
                  <Bouton onClick={() => traiter(alerte.id)}>Marquer comme traitée</Bouton>
                )}
              </div>

              <dl className="mt-3 grid gap-3 md:grid-cols-5">
                <Champ libelle="Quoi" valeur={alerte.quoi_fr} large />
                <Champ libelle="Où" valeur={alerte.ou_fr} />
                <Champ libelle="Quand" valeur={alerte.quand_fr} />
                <Champ libelle="Impact" valeur={alerte.impact_fr} large />
                <Champ libelle="Action" valeur={alerte.action_fr} large accent />
              </dl>

              {alerte.sujet_type === 'SHIPMENT' && alerte.sujet_id && (
                <Link
                  to="/expeditions"
                  className="mt-3 inline-block text-sm font-medium text-action hover:underline"
                >
                  Voir l'expédition concernée →
                </Link>
              )}
            </Panneau>
          ))}
        </div>
      )}
    </div>
  )
}

function Champ({
  libelle, valeur, large, accent,
}: {
  libelle: string
  valeur: string
  large?: boolean
  accent?: boolean
}) {
  return (
    <div className={large ? 'md:col-span-2' : ''}>
      <dt className="titre-section">{libelle}</dt>
      <dd className={`mt-0.5 text-sm ${accent ? 'font-medium text-action' : 'text-ardoise-700'}`}>
        {valeur}
      </dd>
    </div>
  )
}
