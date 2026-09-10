import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import { dateHeure, depuisMaintenant, pourcentage, tonnes } from '@/lib/format'
import { BadgeRisque, Bouton, Chargement, EtatVide, MessageErreur, Panneau } from '@/components/primitives'

export function PageExpeditions() {
  const { donnees, erreur, enCours, recharger } = useChargement(() => api.expeditions())

  if (enCours) return <Chargement libelle="Évaluation des expéditions…" />
  if (erreur) return <MessageErreur message={erreur} action={<Bouton onClick={recharger}>Réessayer</Bouton>} />
  if (!donnees) return null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ardoise-900">Expéditions</h1>
        <p className="mt-0.5 text-sm text-ardoise-500">
          {donnees.nombre} expédition(s) suivie(s), les plus exposées en premier.
        </p>
      </div>

      <Panneau>
        {donnees.expeditions.length === 0 ? (
          <EtatVide titre="Aucune expédition" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-sm">
              <thead>
                <tr className="border-b border-ardoise-200 text-left text-xs uppercase tracking-wide text-ardoise-500">
                  <th className="px-3 py-2 font-medium">Référence</th>
                  <th className="px-3 py-2 font-medium">Produit</th>
                  <th className="px-3 py-2 font-medium">Trajet</th>
                  <th className="px-3 py-2 font-medium">Volume</th>
                  <th className="px-3 py-2 font-medium">Départ</th>
                  <th className="px-3 py-2 font-medium">Risque</th>
                  <th className="px-3 py-2 font-medium">Perturbation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ardoise-100">
                {donnees.expeditions.map((expedition) => (
                  <tr key={expedition.id} className="hover:bg-ardoise-50">
                    <td className="px-3 py-2.5">
                      <Link
                        to={`/expeditions/${expedition.reference}`}
                        className="font-medium text-ardoise-900 hover:text-action"
                      >
                        {expedition.reference}
                      </Link>
                    </td>
                    <td className="px-3 py-2.5 text-ardoise-700">{expedition.produit}</td>
                    <td className="px-3 py-2.5 text-ardoise-600">
                      {expedition.origine} → {expedition.destination}
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-ardoise-700">
                      {tonnes(expedition.volume_tonnes)}
                    </td>
                    <td className="px-3 py-2.5 text-ardoise-600">
                      <p>{dateHeure(expedition.depart_prevu)}</p>
                      <p className="text-xs text-ardoise-400">
                        {depuisMaintenant(expedition.depart_prevu)}
                      </p>
                    </td>
                    <td className="px-3 py-2.5">
                      {expedition.niveau_risque ? (
                        <BadgeRisque niveau={expedition.niveau_risque} taille="petite" />
                      ) : (
                        <span className="text-xs text-ardoise-400">non évalué</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-ardoise-700">
                      {pourcentage(expedition.probabilite_perturbation)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panneau>
    </div>
  )
}
