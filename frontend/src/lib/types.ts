/**
 * Contrats partagés avec l'API.
 *
 * Les noms de champs sont en français parce qu'ils le sont côté serveur : une
 * traduction intermédiaire créerait deux vocabulaires du domaine et des écarts
 * silencieux entre l'API et l'écran.
 */

export type NiveauRisque = 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL'

export interface Utilisateur {
  id: string
  nom: string
  email: string
  role: string
  role_fr: string
  organisation: string | null
  organisation_slug: string | null
}

export interface Critere {
  libelle_fr: string
  valeur: number
  unite_fr: string
  normalise: number
  poids: number
}

export interface Troncon {
  index: number
  de: string
  vers: string
  axe: string
  distance_km: number
  depart_troncon: string
  arrivee_troncon: string
  heures_apres_depart: number
  niveau_risque: NiveauRisque
  niveau_risque_fr: string
  severite: number
  expose: boolean
  intensite_pluie_mm_h: number | null
  cumul_24h_avant_mm: number | null
  rafale_kmh: number | null
  motifs_fr: string[]
  trace: [number, number][]
}

export interface Alternative {
  id: string
  rang: number
  type: string
  type_fr: string
  libelle_fr: string
  description_fr: string
  recommandee: boolean
  score_global: number
  niveau_risque: NiveauRisque
  niveau_risque_fr: string
  score_risque: number
  probabilite_perturbation: number
  part_exposee: number
  fiabilite: number
  distance_km: number
  duree_h: number
  cout_mad: number
  depart: string
  arrivee_estimee: string
  echeance_respectee: boolean
  marge_echeance_h: number | null
  confiance: string
  etat_des_donnees: string
  sources: string[]
  facteurs_de_risque_fr: string[]
  raisons_fr: string[]
  contreparties_fr: string[]
  ecart_risque_points: number | null
  ecart_cout_mad: number | null
  ecart_cout_pct: number | null
  ecart_duree_h: number | null
  criteres: Critere[]
  trace?: [number, number][]
  villes?: string[]
  troncons?: Troncon[]
}

export interface DetailExpedition {
  expedition: {
    id: string
    reference: string
    produit: string
    culture: string
    origine: string
    destination: string
    volume_tonnes: number
    mode_transport: string
    statut: string
    depart_prevu: string
    echeance_service: string
  }
  profil_optimisation: {
    code: string
    libelle_fr: string
    justification_fr: string
    ponderations: Record<string, number>
  }
  confiance: string
  reserves_fr: string[]
  recommandation: Alternative | null
  alternatives: Alternative[]
  alternatives_ecartees: {
    id: string
    libelle_fr: string
    type_fr: string
    motifs_rejet_fr: string[]
  }[]
}

export interface ResumeExpedition {
  id: string
  reference: string
  produit: string
  culture: string
  origine: string
  destination: string
  volume_tonnes: number
  statut: string
  depart_prevu: string
  echeance_service: string
  niveau_risque: string | null
  rang_risque?: number
  probabilite_perturbation: number | null
  echeance_respectee?: boolean
}

export interface EtatSource {
  fournisseur: string
  disponible: boolean
  message_fr: string
}

export interface TableauDeBord {
  risque_global: { niveau: string; code: NiveauRisque; explication_fr: string }
  indicateurs: Record<string, number>
  expeditions_exposees: {
    reference: string
    produit: string
    origine: string
    destination: string
    volume_tonnes: number
    niveau_risque: string
    rang: number
    probabilite_perturbation: number
    depart_prevu: string
    echeance_respectee: boolean
    facteurs_fr: string[]
    action_possible_fr: string | null
    gain_risque_points: number | null
  }[]
  stocks_critiques: {
    site: string
    produit: string
    couverture_jours: number
    couverture_utilisable_jours: number
    sous_stock_securite: boolean
    resume_fr: string
  }[]
  alertes: Alerte[]
  opportunites_action: {
    reference: string
    action_fr: string | null
    gain_risque_points: number | null
  }[]
  sources: EtatSource[]
}

export interface Alerte {
  id: string
  niveau: string
  type_risque?: string
  quoi_fr: string
  ou_fr: string
  quand_fr: string
  impact_fr: string
  action_fr: string
  sujet_type?: string | null
  sujet_id?: string | null
  traitee?: boolean
  cree_le: string
}

export interface RegionRisque {
  code: string
  nom_fr: string
  latitude: number
  longitude: number
  profil_agricole_fr: string
  cultures: string[]
  niveau_risque: string | null
  code_risque: NiveauRisque | null
  score_risque: number | null
  culture_exposee: string | null
  facteur_principal_fr: string | null
  etat_des_donnees: string | null
  confiance: string | null
}

export interface LigneStock {
  id: string
  site: string
  region: string
  produit: string
  culture: string
  quantite_tonnes: number
  stock_securite_tonnes: number
  demande_quotidienne_tonnes: number
  couverture_jours: number
  couverture_utilisable_jours: number
  sous_stock_securite: boolean
  delai_fournisseur_jours: number | null
  resume_fr: string
  point_de_commande_tonnes?: number
  quantite_a_commander_tonnes?: number
  commande_necessaire?: boolean
}

export interface LigneFournisseur {
  id: string
  nom: string
  code: string
  site: string
  region: string
  zone_fr: string
  culture: string
  delai_jours: number
  fiabilite: number
  capacite_quotidienne_tonnes: number
  prix_mad_par_tonne: number
  fournisseur_de_secours: boolean
  niveau_exposition: string
  code_exposition: NiveauRisque
  score_exposition: number
  implications_fr: string[]
  latitude: number
  longitude: number
}

export interface CoucheCarte {
  expedition: { reference: string; produit: string; volume_tonnes: number }
  points: GeoJSON.FeatureCollection
  itineraires: GeoJSON.FeatureCollection
  troncons_exposes: GeoJSON.FeatureCollection
  option_recommandee: string | null
  legende: { code: string; libelle_fr: string; couleur: string }[]
}

export interface Recommandation {
  id: string
  titre: string
  sujet_type: string
  sujet_id: string
  option_retenue: string
  statut: string
  statut_fr: string
  resume_fr: string
  raisons_fr: string[]
  contreparties_fr: string[]
  impact_attendu: Record<string, unknown>
  confiance: string
  entrees: Record<string, unknown>
  alternatives_considerees: Record<string, unknown>[]
  sources: string[]
  decidee_par: string | null
  decidee_le: string | null
  note_decision: string | null
  cree_le: string
}

export interface ResultatSimulation {
  scenario: string
  reference: string
  hypothese_fr: string
  situation_actuelle: InstantaneSimulation
  situation_simulee: InstantaneSimulation
  evolution_fr: string[]
  avertissement_fr: string
  simulation_id?: string
}

export interface InstantaneSimulation {
  options_faisables: number
  options_ecartees: number
  meilleure_option: string | null
  niveau_risque: string
  probabilite_perturbation: number | null
  cout_mad: number | null
  marge_echeance_h: number | null
  confiance: string
}

export interface EtatCopilote {
  disponible: boolean
  modele: string | null
  message_fr: string
}

export interface ReponseCopilote {
  reponse_fr: string
  conclusion: {
    decision_fr?: string
    niveau_risque?: string
    raisons_fr?: string[]
    contreparties_fr?: string[]
    confiance?: string
    expedition_reference?: string | null
    option_recommandee_id?: string | null
    focus_carte?: { itineraire_id?: string | null; troncons_exposes?: string[] }
  } | null
  outils_utilises: {
    outil: string
    parametres: Record<string, unknown>
    reussi: boolean
    erreur_fr: string | null
  }[]
  iterations: number
  modele: string
  conversation_id: string | null
}

// --- Retour terrain -------------------------------------------------------

export interface QuestionPayload {
  code: string
  texte_fr: string
  aide_fr: string
  type: 'boolean' | 'choice' | 'number' | 'text'
  obligatoire: boolean
  options: { code: string; libelle_fr: string }[]
  minimum: number | null
  maximum: number | null
}

export interface EtapeQuestionnaire {
  reference: string
  termine: boolean
  question: QuestionPayload | null
  progression: { repondues: number; total: number }
  reponses: Record<string, unknown>
}

export interface CollecteEnAttente {
  shipment_id: string
  reference: string
  produit: string
  origine: string
  destination: string
  echeance: string
  heures_depuis_echeance: number
  probabilite_annoncee: number | null
  niveau_annonce: string | null
}

export interface ResultatObserve {
  id: string
  reference: string
  recueilli_le: string
  partition: string
  livree: boolean
  ecart_arrivee_h: number | null
  perturbation: boolean | null
  type_perturbation: string | null
  lieu: string | null
  retard_h: number | null
  qualite_affectee: boolean | null
  perte_mad: number | null
  commentaire: string | null
  corrige_une_version_anterieure: boolean
}
