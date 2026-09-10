/**
 * Composants de base de l'interface.
 *
 * Ils portent la sémantique visuelle du produit : un niveau de risque a
 * toujours la même couleur, un état de donnée est toujours signalé de la même
 * manière. Sans ces primitives, chaque page finirait par inventer ses propres
 * conventions et l'utilisateur devrait les réapprendre à chaque écran.
 */

import type { ReactNode } from 'react'
import type { NiveauRisque } from '@/lib/types'

const COULEURS_RISQUE: Record<string, string> = {
  LOW: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  MODERATE: 'bg-amber-50 text-amber-800 border-amber-200',
  HIGH: 'bg-orange-50 text-orange-800 border-orange-200',
  CRITICAL: 'bg-red-50 text-red-800 border-red-200',
}

const PASTILLES_RISQUE: Record<string, string> = {
  LOW: 'bg-emerald-600',
  MODERATE: 'bg-amber-500',
  HIGH: 'bg-orange-600',
  CRITICAL: 'bg-red-700',
}

/** Traduit un libellé français en code, pour les endpoints qui renvoient le libellé. */
export function codeDepuisLibelle(libelle: string | null | undefined): NiveauRisque {
  switch ((libelle ?? '').toLowerCase()) {
    case 'critique': return 'CRITICAL'
    case 'élevé': case 'eleve': return 'HIGH'
    case 'modéré': case 'modere': return 'MODERATE'
    default: return 'LOW'
  }
}

export function BadgeRisque({
  niveau, libelle, taille = 'normale',
}: {
  niveau: NiveauRisque | string
  libelle?: string
  taille?: 'petite' | 'normale'
}) {
  const code = niveau in COULEURS_RISQUE ? niveau : codeDepuisLibelle(niveau)
  const classes = COULEURS_RISQUE[code] ?? COULEURS_RISQUE.LOW
  const texte = libelle ?? { LOW: 'Faible', MODERATE: 'Modéré', HIGH: 'Élevé', CRITICAL: 'Critique' }[code as string]
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-medium ${classes} ${
        taille === 'petite' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm'
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${PASTILLES_RISQUE[code as string] ?? PASTILLES_RISQUE.LOW}`} />
      {texte}
    </span>
  )
}

/**
 * Signale l'origine épistémique d'une valeur.
 *
 * C'est le composant le plus important pour la crédibilité du produit : une
 * valeur simulée doit être impossible à confondre avec une mesure.
 */
export function EtiquetteDonnee({ etat }: { etat: string | null | undefined }) {
  if (!etat) return null
  const simule = etat.toLowerCase().startsWith('simul')
  return (
    <span
      title={
        simule
          ? "Donnée de démonstration : ne doit pas fonder une décision réelle."
          : `Origine de la donnée : ${etat.toLowerCase()}.`
      }
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ${
        simule
          ? 'bg-purple-100 text-purple-900 ring-1 ring-purple-300'
          : 'bg-ardoise-100 text-ardoise-600'
      }`}
    >
      {simule ? '⚠ ' : ''}{etat}
    </span>
  )
}

export function CarteKpi({
  libelle, valeur, detail, accent, children,
}: {
  libelle: string
  valeur: ReactNode
  detail?: ReactNode
  accent?: NiveauRisque | string
  children?: ReactNode
}) {
  const bordure = accent
    ? (COULEURS_RISQUE[accent in COULEURS_RISQUE ? accent : codeDepuisLibelle(accent)] ?? '').split(' ').pop()
    : 'border-ardoise-200'
  return (
    <div className={`carte-panneau border-l-4 p-4 ${bordure}`}>
      <p className="titre-section">{libelle}</p>
      <p className="mt-1.5 text-2xl font-semibold tabular-nums text-ardoise-900">{valeur}</p>
      {detail && <p className="mt-1 text-sm text-ardoise-500">{detail}</p>}
      {children}
    </div>
  )
}

export function Panneau({
  titre, sousTitre, actions, children, className = '',
}: {
  titre?: string
  sousTitre?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`carte-panneau ${className}`}>
      {(titre || actions) && (
        <header className="flex items-start justify-between gap-4 border-b border-ardoise-200 px-4 py-3">
          <div>
            {titre && <h2 className="font-semibold text-ardoise-900">{titre}</h2>}
            {sousTitre && <p className="mt-0.5 text-sm text-ardoise-500">{sousTitre}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

/** Barre de comparaison : rend un écart lisible d'un coup d'œil. */
export function BarreComparaison({
  valeur, maximum, couleur = 'bg-ardoise-400', libelle,
}: {
  valeur: number
  maximum: number
  couleur?: string
  libelle?: string
}) {
  const largeur = maximum > 0 ? Math.min(100, (valeur / maximum) * 100) : 0
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-ardoise-100">
        <div className={`h-full rounded-full ${couleur}`} style={{ width: `${largeur}%` }} />
      </div>
      {libelle && <span className="w-16 text-right text-xs tabular-nums text-ardoise-600">{libelle}</span>}
    </div>
  )
}

export function Bouton({
  children, variante = 'secondaire', onClick, disabled, type = 'button', className = '',
}: {
  children: ReactNode
  variante?: 'primaire' | 'secondaire' | 'discret' | 'danger'
  onClick?: () => void
  disabled?: boolean
  type?: 'button' | 'submit'
  className?: string
}) {
  const styles = {
    primaire: 'bg-action text-white hover:bg-blue-800 disabled:bg-ardoise-300',
    secondaire: 'border border-ardoise-300 bg-white text-ardoise-700 hover:bg-ardoise-50',
    discret: 'text-ardoise-600 hover:bg-ardoise-100',
    danger: 'border border-red-300 bg-white text-red-700 hover:bg-red-50',
  }[variante]
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60 ${styles} ${className}`}
    >
      {children}
    </button>
  )
}

export function Chargement({ libelle = 'Chargement…' }: { libelle?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-12 text-sm text-ardoise-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ardoise-300 border-t-action" />
      {libelle}
    </div>
  )
}

export function MessageErreur({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3">
      <p className="text-sm font-medium text-red-800">{message}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function EtatVide({ titre, detail }: { titre: string; detail?: string }) {
  return (
    <div className="py-10 text-center">
      <p className="font-medium text-ardoise-700">{titre}</p>
      {detail && <p className="mt-1 text-sm text-ardoise-500">{detail}</p>}
    </div>
  )
}
