/**
 * Mise en forme française.
 *
 * Centralisée pour que les mêmes conventions s'appliquent partout : un coût
 * affiché « 43 786 MAD » ici et « 43786.0 » ailleurs donnerait l'impression de
 * deux systèmes différents.
 */

/**
 * Séparateur de milliers à l'espace, conformément à l'usage français.
 *
 * La locale `fr-MA` regroupe avec un point : « 43.786 MAD » se lit alors comme
 * un montant décimal, ce qui est trompeur sur un écran de décision financière.
 * On force donc `fr-FR`, dont le regroupement par espace est sans ambiguïté.
 */
const DEVISE = new Intl.NumberFormat('fr-FR', {
  style: 'decimal',
  maximumFractionDigits: 0,
})

export function mad(valeur: number | null | undefined): string {
  if (valeur === null || valeur === undefined) return '—'
  return `${DEVISE.format(valeur)} MAD`
}

export function madSigne(valeur: number | null | undefined): string {
  if (valeur === null || valeur === undefined) return '—'
  const signe = valeur > 0 ? '+' : valeur < 0 ? '−' : ''
  return `${signe}${DEVISE.format(Math.abs(valeur))} MAD`
}

export function heures(valeur: number | null | undefined): string {
  if (valeur === null || valeur === undefined) return '—'
  const total = Math.abs(valeur)
  const h = Math.floor(total)
  const m = Math.round((total - h) * 60)
  const signe = valeur < 0 ? '−' : ''
  return m === 0 ? `${signe}${h} h` : `${signe}${h} h ${String(m).padStart(2, '0')}`
}

export function heuresSignees(valeur: number | null | undefined): string {
  if (valeur === null || valeur === undefined) return '—'
  return `${valeur > 0 ? '+' : ''}${heures(valeur)}`
}

export function pourcentage(valeur: number | null | undefined, decimales = 0): string {
  if (valeur === null || valeur === undefined) return '—'
  return `${(valeur * 100).toFixed(decimales)} %`
}

export function points(valeur: number | null | undefined): string {
  if (valeur === null || valeur === undefined) return '—'
  const signe = valeur > 0 ? '+' : valeur < 0 ? '−' : ''
  return `${signe}${Math.abs(valeur).toFixed(0)} pts`
}

export function jours(valeur: number | null | undefined): string {
  if (valeur === null || valeur === undefined) return '—'
  return `${valeur.toFixed(1)} j`
}

export function tonnes(valeur: number | null | undefined): string {
  if (valeur === null || valeur === undefined) return '—'
  return `${DEVISE.format(valeur)} t`
}

const DATE_COURTE = new Intl.DateTimeFormat('fr-FR', {
  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
})

export function dateHeure(valeur: string | null | undefined): string {
  if (!valeur) return '—'
  return DATE_COURTE.format(new Date(valeur))
}

export function depuisMaintenant(valeur: string | null | undefined): string {
  if (!valeur) return '—'
  const delta = (new Date(valeur).getTime() - Date.now()) / 3_600_000
  if (Math.abs(delta) < 1) return "moins d'une heure"
  if (delta > 0) return `dans ${heures(delta)}`
  return `il y a ${heures(-delta)}`
}
