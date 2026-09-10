import { type FormEvent, useState } from 'react'
import { api, ErreurApi } from '@/lib/api'
import type { Utilisateur } from '@/lib/types'
import { Bouton, MessageErreur } from '@/components/primitives'

export function PageConnexion({ onConnexion }: { onConnexion: (u: Utilisateur) => void }) {
  const [email, setEmail] = useState('supply@souss-primeurs.ma')
  const [motDePasse, setMotDePasse] = useState('')
  const [erreur, setErreur] = useState<string | null>(null)
  const [enCours, setEnCours] = useState(false)

  async function soumettre(evenement: FormEvent) {
    evenement.preventDefault()
    setErreur(null)
    setEnCours(true)
    try {
      onConnexion(await api.connexion(email, motDePasse))
    } catch (e) {
      setErreur(e instanceof ErreurApi ? e.message : 'Connexion impossible.')
    } finally {
      setEnCours(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-ardoise-900 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-bold tracking-tight text-white">AtlasAgri Intelligence</h1>
          <p className="mt-1 text-sm text-ardoise-400">
            Prévoir. Anticiper. Réacheminer. Décider.
          </p>
        </div>

        <form onSubmit={soumettre} className="space-y-4 rounded-lg bg-white p-6 shadow-xl">
          <div>
            <label htmlFor="email" className="mb-1 block text-sm font-medium text-ardoise-700">
              Adresse professionnelle
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border border-ardoise-300 px-3 py-2 text-sm focus:border-action focus:outline-none focus:ring-1 focus:ring-action"
            />
          </div>

          <div>
            <label htmlFor="mdp" className="mb-1 block text-sm font-medium text-ardoise-700">
              Mot de passe
            </label>
            <input
              id="mdp"
              type="password"
              required
              value={motDePasse}
              onChange={(e) => setMotDePasse(e.target.value)}
              className="w-full rounded-md border border-ardoise-300 px-3 py-2 text-sm focus:border-action focus:outline-none focus:ring-1 focus:ring-action"
            />
          </div>

          {erreur && <MessageErreur message={erreur} />}

          <Bouton type="submit" variante="primaire" disabled={enCours} className="w-full">
            {enCours ? 'Connexion…' : 'Se connecter'}
          </Bouton>

          <p className="text-center text-xs text-ardoise-400">
            Environnement privé. Accès réservé aux utilisateurs autorisés.
          </p>
        </form>
      </div>
    </div>
  )
}
