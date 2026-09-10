/** Stocks : couverture, marge utilisable et besoin de réapprovisionnement. */

import { api } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import { jours, tonnes } from '@/lib/format'
import {
  BarreComparaison, Bouton, Chargement, EtatVide, MessageErreur, Panneau,
} from '@/components/primitives'

export function PageStocks() {
  const { donnees, erreur, enCours, recharger } = useChargement(() => api.stocks())

  if (enCours) return <Chargement />
  if (erreur) return <MessageErreur message={erreur} action={<Bouton onClick={recharger}>Réessayer</Bouton>} />
  if (!donnees) return null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ardoise-900">Stocks</h1>
        <p className="mt-0.5 text-sm text-ardoise-500">
          La couverture utilisable exclut le stock de sécurité : c'est la marge réelle
          face à une perturbation.
        </p>
      </div>

      <Panneau>
        {donnees.stocks.length === 0 ? (
          <EtatVide titre="Aucun stock enregistré" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[960px] text-sm">
              <thead>
                <tr className="border-b border-ardoise-200 text-left text-xs uppercase tracking-wide text-ardoise-500">
                  <th className="px-3 py-2 font-medium">Produit</th>
                  <th className="px-3 py-2 font-medium">Site</th>
                  <th className="px-3 py-2 font-medium">Quantité</th>
                  <th className="px-3 py-2 font-medium">Couverture utilisable</th>
                  <th className="px-3 py-2 font-medium">Délai fournisseur</th>
                  <th className="px-3 py-2 font-medium">À commander</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ardoise-100">
                {donnees.stocks.map((ligne) => {
                  const insuffisant =
                    ligne.delai_fournisseur_jours !== null &&
                    ligne.couverture_utilisable_jours < ligne.delai_fournisseur_jours
                  return (
                    <tr key={ligne.id} className={insuffisant ? 'bg-red-50/60' : ''}>
                      <td className="px-3 py-2.5">
                        <p className="font-medium text-ardoise-900">{ligne.produit}</p>
                        {ligne.sous_stock_securite && (
                          <span className="text-xs font-medium text-red-700">
                            Sous le stock de sécurité
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-ardoise-600">{ligne.site}</td>
                      <td className="px-3 py-2.5 tabular-nums text-ardoise-700">
                        {tonnes(ligne.quantite_tonnes)}
                        <p className="text-xs text-ardoise-400">
                          dont {tonnes(ligne.stock_securite_tonnes)} de sécurité
                        </p>
                      </td>
                      <td className="px-3 py-2.5">
                        <p
                          className={`tabular-nums font-medium ${
                            insuffisant ? 'text-red-700' : 'text-ardoise-800'
                          }`}
                        >
                          {jours(ligne.couverture_utilisable_jours)}
                        </p>
                        <BarreComparaison
                          valeur={ligne.couverture_utilisable_jours}
                          maximum={Math.max(ligne.couverture_jours, ligne.delai_fournisseur_jours ?? 1)}
                          couleur={insuffisant ? 'bg-red-500' : 'bg-emerald-600'}
                        />
                      </td>
                      <td className="px-3 py-2.5 tabular-nums text-ardoise-700">
                        {jours(ligne.delai_fournisseur_jours)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {ligne.commande_necessaire ? (
                          <span className="font-medium text-orange-700">
                            {tonnes(ligne.quantite_a_commander_tonnes ?? 0)}
                          </span>
                        ) : (
                          <span className="text-ardoise-400">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panneau>
    </div>
  )
}
