import { useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import type { Utilisateur } from '@/lib/types'
import { utilisateurCourant } from '@/lib/api'
import { Coquille } from '@/components/Coquille'
import { PageConnexion } from '@/pages/PageConnexion'
import { PageVueGenerale } from '@/pages/PageVueGenerale'
import { PageRisques } from '@/pages/PageRisques'
import { PageCarte } from '@/pages/PageCarte'
import { PageExpeditions } from '@/pages/PageExpeditions'
import { PageExpedition } from '@/pages/PageExpedition'
import { PageStocks } from '@/pages/PageStocks'
import { PageFournisseurs } from '@/pages/PageFournisseurs'
import { PageSimulations } from '@/pages/PageSimulations'
import { PageCopilote } from '@/pages/PageCopilote'
import { PageAlertes } from '@/pages/PageAlertes'
import { PageRetourTerrain } from '@/pages/PageRetourTerrain'

export function App() {
  const [utilisateur, setUtilisateur] = useState<Utilisateur | null>(utilisateurCourant)

  if (!utilisateur) {
    return <PageConnexion onConnexion={setUtilisateur} />
  }

  return (
    <BrowserRouter>
      <Coquille utilisateur={utilisateur} onDeconnexion={() => setUtilisateur(null)}>
        <Routes>
          <Route path="/" element={<PageVueGenerale />} />
          <Route path="/risques" element={<PageRisques />} />
          <Route path="/carte" element={<PageCarte />} />
          <Route path="/expeditions" element={<PageExpeditions />} />
          <Route path="/expeditions/:reference" element={<PageExpedition />} />
          <Route path="/stocks" element={<PageStocks />} />
          <Route path="/fournisseurs" element={<PageFournisseurs />} />
          <Route path="/simulations" element={<PageSimulations />} />
          <Route path="/copilote" element={<PageCopilote />} />
          <Route path="/alertes" element={<PageAlertes />} />
          <Route path="/retour-terrain" element={<PageRetourTerrain />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Coquille>
    </BrowserRouter>
  )
}
