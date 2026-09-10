/// <reference types="vite/client" />

/**
 * Variables d'environnement exposées au navigateur.
 *
 * Vite n'expose que les clés préfixées `VITE_`. Tout ce qui est déclaré ici est
 * donc public : ne jamais y placer de secret serveur.
 */
interface ImportMetaEnv {
  /** URL d'un style MapLibre. Vide = OpenFreeMap, gratuit et sans clé. */
  readonly VITE_MAP_STYLE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
