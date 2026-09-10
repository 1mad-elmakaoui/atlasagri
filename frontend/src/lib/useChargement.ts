/**
 * Chargement de données avec ses trois états explicites.
 *
 * L'état d'erreur est traité au même rang que le succès : une source externe
 * peut tomber, et l'utilisateur doit le voir plutôt que de contempler un écran
 * vide qu'il interpréterait comme « aucun risque ».
 */

import { useCallback, useEffect, useState } from 'react'
import { ErreurApi } from './api'

export function useChargement<T>(
  charger: () => Promise<T>,
  dependances: unknown[] = [],
): { donnees: T | null; erreur: string | null; enCours: boolean; recharger: () => void } {
  const [donnees, setDonnees] = useState<T | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [enCours, setEnCours] = useState(true)
  const [compteur, setCompteur] = useState(0)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const executer = useCallback(charger, dependances)

  useEffect(() => {
    let annule = false
    setEnCours(true)
    setErreur(null)

    executer()
      .then((resultat) => { if (!annule) setDonnees(resultat) })
      .catch((e) => {
        if (annule) return
        setErreur(e instanceof ErreurApi ? e.message : 'Chargement impossible.')
      })
      .finally(() => { if (!annule) setEnCours(false) })

    return () => { annule = true }
  }, [executer, compteur])

  return { donnees, erreur, enCours, recharger: () => setCompteur((c) => c + 1) }
}
