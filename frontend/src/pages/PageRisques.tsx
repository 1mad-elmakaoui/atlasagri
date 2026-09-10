/** Registre des risques régionaux, par horizon. */

import { useState } from 'react'
import { api } from '@/lib/api'
import { useChargement } from '@/lib/useChargement'
import {
  BadgeRisque, Bouton, Chargement, EtatVide, EtiquetteDonnee, MessageErreur, Panneau,
} from '@/components/primitives'

const HORIZONS = [6, 12, 24, 48]

export function PageRisques() {
  const [horizon, setHorizon] = useState(24)
  const { donnees, erreur, enCours, recharger } = useChargement(
    () => api.regions(horizon), [horizon],
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ardoise-900">Risques régionaux</h1>
          <p className="mt-0.5 text-sm text-ardoise-500">
            Exposition agro-climatique des régions où vous opérez.
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-md border border-ardoise-300 bg-white p-0.5">
          {HORIZONS.map((valeur) => (
            <button
              key={valeur}
              onClick={() => setHorizon(valeur)}
              className={`rounded px-3 py-1 text-sm font-medium transition ${
                horizon === valeur ? 'bg-ardoise-900 text-white' : 'text-ardoise-600 hover:bg-ardoise-100'
              }`}
            >
              {valeur} h
            </button>
          ))}
        </div>
      </div>

      {enCours && <Chargement libelle="Évaluation des régions…" />}
      {erreur && <MessageErreur message={erreur} action={<Bouton onClick={recharger}>Réessayer</Bouton>} />}

      {donnees && (
        donnees.regions.length === 0 ? (
          <EtatVide titre="Aucune région à afficher" detail="Aucun site n'est enregistré." />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {donnees.regions.map((region) => (
              <Panneau key={region.code}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2 className="font-semibold text-ardoise-900">{region.nom_fr}</h2>
                    <p className="mt-0.5 text-xs text-ardoise-500">
                      {region.cultures.join(' · ')}
                    </p>
                  </div>
                  {region.niveau_risque && (
                    <BadgeRisque
                      niveau={region.code_risque ?? 'LOW'}
                      libelle={region.niveau_risque}
                      taille="petite"
                    />
                  )}
                </div>

                <p className="mt-3 text-sm text-ardoise-600">{region.profil_agricole_fr}</p>

                {region.facteur_principal_fr && (
                  <p className="mt-2 text-sm">
                    <span className="text-ardoise-500">Facteur dominant : </span>
                    <span className="font-medium text-ardoise-800">
                      {region.facteur_principal_fr}
                    </span>
                    {region.culture_exposee && (
                      <span className="text-ardoise-500"> sur {region.culture_exposee.toLowerCase()}</span>
                    )}
                  </p>
                )}

                <div className="mt-3 flex items-center gap-2 border-t border-ardoise-100 pt-2 text-xs text-ardoise-500">
                  <EtiquetteDonnee etat={region.etat_des_donnees} />
                  {region.confiance && <span>Confiance : {region.confiance}</span>}
                </div>
              </Panneau>
            ))}
          </div>
        )
      )}
    </div>
  )
}
