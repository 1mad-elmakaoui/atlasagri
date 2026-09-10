/**
 * Client HTTP.
 *
 * Le jeton est conservé en mémoire et dans le stockage de session : il
 * disparaît à la fermeture de l'onglet, ce qui limite l'exposition sur un poste
 * partagé — situation courante dans un entrepôt ou un bureau d'exploitation.
 */

import type {
  Alerte, CollecteEnAttente, CoucheCarte, DetailExpedition, EtapeQuestionnaire,
  EtatCopilote, LigneFournisseur, LigneStock, Recommandation, RegionRisque,
  ReponseCopilote, ResultatObserve, ResultatSimulation, ResumeExpedition,
  TableauDeBord, Utilisateur,
} from './types'

const CLE_JETON = 'atlasagri.jeton'
const CLE_UTILISATEUR = 'atlasagri.utilisateur'

export class ErreurApi extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly statut: number,
  ) {
    super(message)
  }
}

function jeton(): string | null {
  return sessionStorage.getItem(CLE_JETON)
}

export function utilisateurCourant(): Utilisateur | null {
  const brut = sessionStorage.getItem(CLE_UTILISATEUR)
  return brut ? (JSON.parse(brut) as Utilisateur) : null
}

export function deconnecter(): void {
  sessionStorage.removeItem(CLE_JETON)
  sessionStorage.removeItem(CLE_UTILISATEUR)
}

async function requete<T>(chemin: string, options: RequestInit = {}): Promise<T> {
  const entetes: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) ?? {}),
  }
  const t = jeton()
  if (t) entetes.Authorization = `Bearer ${t}`

  const reponse = await fetch(`/api${chemin}`, { ...options, headers: entetes })

  if (reponse.status === 401) {
    // Session expirée : on nettoie plutôt que de laisser l'utilisateur devant
    // une suite d'erreurs incompréhensibles.
    deconnecter()
    throw new ErreurApi('authentification_requise', 'Session expirée. Merci de vous reconnecter.', 401)
  }

  if (!reponse.ok) {
    let code = 'erreur_interne'
    let message = 'Une erreur est survenue.'
    try {
      const corps = await reponse.json()
      code = corps.code ?? code
      message = corps.message_fr ?? message
    } catch {
      // Réponse non JSON : on conserve le message générique.
    }
    throw new ErreurApi(code, message, reponse.status)
  }

  return (await reponse.json()) as T
}

export const api = {
  async connexion(email: string, motDePasse: string): Promise<Utilisateur> {
    const reponse = await requete<{ access_token: string; utilisateur: Utilisateur }>(
      '/v1/auth/login',
      { method: 'POST', body: JSON.stringify({ email, password: motDePasse }) },
    )
    sessionStorage.setItem(CLE_JETON, reponse.access_token)
    sessionStorage.setItem(CLE_UTILISATEUR, JSON.stringify(reponse.utilisateur))
    return reponse.utilisateur
  },

  tableauDeBord: () => requete<TableauDeBord>('/v1/tableau-de-bord'),
  regions: (horizon = 24) =>
    requete<{ horizon_heures: number; regions: RegionRisque[] }>(
      `/v1/tableau-de-bord/regions?horizon_hours=${horizon}`,
    ),

  expeditions: () =>
    requete<{ expeditions: ResumeExpedition[]; nombre: number }>('/v1/expeditions'),
  expedition: (reference: string) =>
    requete<DetailExpedition>(`/v1/expeditions/${encodeURIComponent(reference)}`),

  couchesCarte: (reference: string) =>
    requete<CoucheCarte>(`/v1/carte/expedition/${encodeURIComponent(reference)}`),
  sites: () => requete<GeoJSON.FeatureCollection>('/v1/carte/sites'),

  stocks: () => requete<{ stocks: LigneStock[]; nombre: number }>('/v1/stocks'),
  fournisseurs: () =>
    requete<{ fournisseurs: LigneFournisseur[]; nombre: number }>('/v1/fournisseurs'),

  alertes: (seulementOuvertes = false) =>
    requete<{ alertes: Alerte[]; nombre: number }>(
      `/v1/alertes?only_open=${seulementOuvertes}`,
    ),
  traiterAlerte: (id: string) =>
    requete<{ id: string; traitee: boolean }>(`/v1/alertes/${id}/traiter`, {
      method: 'POST',
      body: JSON.stringify({ acknowledged: true }),
    }),

  recommandations: () =>
    requete<{ recommandations: Recommandation[]; nombre: number }>('/v1/recommandations'),
  deciderRecommandation: (id: string, decision: string, note?: string) =>
    requete<{ id: string; statut_fr: string; message_fr: string }>(
      `/v1/recommandations/${id}/decision`,
      { method: 'POST', body: JSON.stringify({ decision, note }) },
    ),

  scenarios: () =>
    requete<{ scenarios: { code: string; libelle_fr: string; parametre_fr: string }[] }>(
      '/v1/simulations/scenarios',
    ),
  simuler: (corps: {
    scenario: string
    shipment_reference: string
    parameter_value?: number
    blocked_node_code?: string | null
  }) =>
    requete<ResultatSimulation>('/v1/simulations', {
      method: 'POST',
      body: JSON.stringify(corps),
    }),

  collectesEnAttente: () =>
    requete<{ en_attente: CollecteEnAttente[]; nombre: number }>('/v1/resultats/en-attente'),
  questionSuivante: (reference: string, reponses: Record<string, unknown>) =>
    requete<EtapeQuestionnaire>('/v1/resultats/question', {
      method: 'POST',
      body: JSON.stringify({ shipment_reference: reference, answers: reponses }),
    }),
  repondre: (reference: string, reponses: Record<string, unknown>, code: string, value: unknown) =>
    requete<EtapeQuestionnaire>('/v1/resultats/reponse', {
      method: 'POST',
      body: JSON.stringify({ shipment_reference: reference, answers: reponses, code, value }),
    }),
  enregistrerResultat: (reference: string, reponses: Record<string, unknown>) =>
    requete<{ id: string; partition: string; message_fr: string }>('/v1/resultats', {
      method: 'POST',
      body: JSON.stringify({ shipment_reference: reference, answers: reponses }),
    }),
  historiqueResultats: () =>
    requete<{ resultats: ResultatObserve[]; nombre: number }>('/v1/resultats/historique'),

  etatCopilote: () => requete<EtatCopilote>('/v1/copilote/etat'),
  demanderCopilote: (question: string, conversationId?: string | null) =>
    requete<ReponseCopilote>('/v1/copilote/question', {
      method: 'POST',
      body: JSON.stringify({ question, conversation_id: conversationId ?? null }),
    }),
}
