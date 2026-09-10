/**
 * Coquille applicative : navigation et identité.
 *
 * Le copilote figure dans la navigation comme une page parmi d'autres, jamais
 * comme la page d'accueil. Le produit est une plateforme de décision ; la
 * conversation en langage naturel n'en est qu'une porte d'entrée.
 */

import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import type { Utilisateur } from '@/lib/types'
import { deconnecter } from '@/lib/api'

const NAVIGATION = [
  { chemin: '/', libelle: 'Vue générale' },
  { chemin: '/risques', libelle: 'Risques' },
  { chemin: '/carte', libelle: 'Carte' },
  { chemin: '/expeditions', libelle: 'Expéditions' },
  { chemin: '/stocks', libelle: 'Stocks' },
  { chemin: '/fournisseurs', libelle: 'Fournisseurs' },
  { chemin: '/simulations', libelle: 'Simulations' },
  { chemin: '/copilote', libelle: 'Copilote IA' },
  { chemin: '/alertes', libelle: 'Alertes' },
  { chemin: '/retour-terrain', libelle: 'Retour terrain' },
]

export function Coquille({
  utilisateur, onDeconnexion, children,
}: {
  utilisateur: Utilisateur
  onDeconnexion: () => void
  children: ReactNode
}) {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-ardoise-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] items-center gap-6 px-6 py-3">
          <div>
            <p className="text-sm font-bold tracking-tight text-ardoise-900">
              AtlasAgri <span className="font-normal text-ardoise-500">Intelligence</span>
            </p>
            <p className="text-[11px] text-ardoise-400">
              Prévoir. Anticiper. Réacheminer. Décider.
            </p>
          </div>

          <nav className="flex flex-1 flex-wrap gap-0.5">
            {NAVIGATION.map((item) => (
              <NavLink
                key={item.chemin}
                to={item.chemin}
                end={item.chemin === '/'}
                className={({ isActive }) =>
                  `rounded-md px-2.5 py-1.5 text-sm font-medium transition ${
                    isActive
                      ? 'bg-ardoise-900 text-white'
                      : 'text-ardoise-600 hover:bg-ardoise-100'
                  }`
                }
              >
                {item.libelle}
              </NavLink>
            ))}
          </nav>

          <div className="text-right">
            <p className="text-sm font-medium text-ardoise-800">{utilisateur.nom}</p>
            <p className="text-[11px] text-ardoise-500">
              {utilisateur.role_fr} · {utilisateur.organisation}
            </p>
          </div>
          <button
            onClick={() => { deconnecter(); onDeconnexion() }}
            className="rounded-md border border-ardoise-300 px-2.5 py-1.5 text-sm text-ardoise-600 hover:bg-ardoise-50"
          >
            Quitter
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-6 py-6">{children}</main>
    </div>
  )
}
