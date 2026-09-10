/**
 * Carte opérationnelle.
 *
 * Ce n'est pas une illustration : c'est la surface sur laquelle se prend la
 * décision. Trois règles la gouvernent.
 *
 * 1. **L'état de la carte suit la recommandation.** L'itinéraire retenu est
 *    tracé en bleu et au premier plan ; les options moins bien classées sont
 *    atténuées. L'utilisateur voit le choix, il n'a pas à le déduire.
 * 2. **La zone qui motive la décision est visible.** Les tronçons exposés sont
 *    une couche distincte, superposée à l'itinéraire, avec l'heure de passage
 *    prévue. C'est ce qui rend la recommandation compréhensible.
 * 3. **Aucune géométrie n'est inventée côté client.** Les tracés viennent du
 *    service de routage via l'API.
 */

import { useEffect, useRef, useState } from 'react'
import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { CoucheCarte } from '@/lib/types'

const COULEURS: Record<string, string> = {
  LOW: '#15803d',
  MODERATE: '#ca8a04',
  HIGH: '#ea580c',
  CRITICAL: '#b91c1c',
  RECOMMANDE: '#1d4ed8',
  ACTUEL: '#64748b',
}

/**
 * Fond de carte.
 *
 * Par défaut, OpenFreeMap : tuiles vectorielles OpenStreetMap, gratuites et
 * sans clé d'API. C'est le seul fond réel utilisable immédiatement, sans
 * inscription ni quota, ce qui évite qu'une démonstration dépende d'un compte
 * à créer.
 *
 * `VITE_MAP_STYLE_URL` permet de le remplacer par un fournisseur commercial
 * (MapTiler, Stadia) lorsqu'un engagement de service est nécessaire.
 *
 * Si le fond ne se charge pas — réseau coupé, proxy d'entreprise, quota
 * dépassé — la carte bascule sur un fond neutre plutôt que de rester blanche.
 * Les itinéraires et les zones de risque, eux, ne dépendent d'aucun service
 * externe : ils viennent de notre API et restent lisibles dans tous les cas.
 */
const STYLE_DISTANT =
  import.meta.env.VITE_MAP_STYLE_URL ?? 'https://tiles.openfreemap.org/styles/liberty'

const STYLE_NEUTRE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'fond', type: 'background', paint: { 'background-color': '#eef2f6' } }],
}

interface Props {
  couches: CoucheCarte | null
  itineraireSelectionne?: string | null
  onSelectionItineraire?: (id: string) => void
  hauteur?: string
}

export function CarteOperationnelle({
  couches, itineraireSelectionne, onSelectionItineraire, hauteur = '520px',
}: Props) {
  const conteneur = useRef<HTMLDivElement>(null)
  const carte = useRef<MapLibreMap | null>(null)
  const marqueurs = useRef<maplibregl.Marker[]>([])
  const fondDegrade = useRef(false)
  // Incrémenté chaque fois qu'un style devient exploitable. Sert de dépendance
  // au dessin : changer de fond vide les sources, il faut donc tout redessiner.
  const [styleGeneration, setStyleGeneration] = useState(0)

  useEffect(() => {
    if (!conteneur.current || carte.current) return

    const instance = new maplibregl.Map({
      container: conteneur.current,
      style: STYLE_DISTANT,
      center: [-8.0, 32.0],
      zoom: 5.2,
      // L'attribution OpenStreetMap est obligatoire dès qu'on affiche ses tuiles.
      attributionControl: { compact: true },
    })
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    instance.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')

    /**
     * Repli sur un fond neutre.
     *
     * Déclenché par un délai plutôt que par le message d'erreur de MapLibre :
     * un réseau bloqué, un DNS muet ou un quota dépassé produisent des erreurs
     * différentes, et filtrer sur leur libellé laisse forcément passer un cas.
     * Le seul critère fiable est « le fond n'est toujours pas chargé ».
     *
     * Les itinéraires et les zones de risque viennent de notre API : ils
     * doivent rester visibles même sans fond de carte.
     */
    const replier = () => {
      if (fondDegrade.current) return
      fondDegrade.current = true
      console.warn(
        'Fond de carte distant indisponible, repli sur un fond neutre. ' +
        'Les itinéraires et zones de risque restent affichés.',
      )
      instance.setStyle(STYLE_NEUTRE)
    }

    const minuterie = window.setTimeout(replier, 6000)

    instance.on('error', (evenement) => {
      // Une erreur de tuile isolée n'est pas fatale ; une erreur avant le
      // premier chargement du style l'est.
      if (!instance.isStyleLoaded()) replier()
      else console.warn('Erreur de carte non bloquante :', evenement?.error?.message)
    })

    // `styledata` couvre le chargement initial comme les changements de style.
    instance.on('styledata', () => {
      if (!instance.isStyleLoaded()) return
      window.clearTimeout(minuterie)
      setStyleGeneration((generation) => generation + 1)
    })

    carte.current = instance

    return () => {
      window.clearTimeout(minuterie)
      for (const marqueur of marqueurs.current) marqueur.remove()
      marqueurs.current = []
      instance.remove()
      carte.current = null
      fondDegrade.current = false
    }
  }, [])

  useEffect(() => {
    const instance = carte.current
    if (!instance || !couches || styleGeneration === 0) return

    const dessiner = () => {
      if (!instance.isStyleLoaded()) return
      // On repart d'une carte propre à chaque mise à jour : gérer des mises à
      // jour incrémentales de sources MapLibre pour quelques dizaines de
      // features ajouterait de la complexité sans gain perceptible.
      for (const id of ['itineraires-fond', 'itineraires', 'itineraire-actuel', 'troncons', 'points']) {
        if (instance.getLayer(id)) instance.removeLayer(id)
      }
      for (const id of ['src-itineraires', 'src-troncons', 'src-points']) {
        if (instance.getSource(id)) instance.removeSource(id)
      }

      instance.addSource('src-itineraires', { type: 'geojson', data: couches.itineraires })
      instance.addSource('src-troncons', { type: 'geojson', data: couches.troncons_exposes })
      instance.addSource('src-points', { type: 'geojson', data: couches.points })

      // Halo blanc sous les tracés : sans lui, deux itinéraires proches
      // deviennent illisibles là où ils se superposent.
      instance.addLayer({
        id: 'itineraires-fond',
        type: 'line',
        source: 'src-itineraires',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#ffffff', 'line-width': 7, 'line-opacity': 0.9 },
      })

      instance.addLayer({
        id: 'itineraires',
        type: 'line',
        source: 'src-itineraires',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': [
            'case',
            ['==', ['get', 'id'], itineraireSelectionne ?? ''], COULEURS.RECOMMANDE,
            ['get', 'recommandee'], COULEURS.RECOMMANDE,
            ['get', 'plan_actuel'], COULEURS.ACTUEL,
            '#94a3b8',
          ],
          'line-width': [
            'case',
            ['any', ['get', 'recommandee'], ['==', ['get', 'id'], itineraireSelectionne ?? '']], 4.5,
            ['get', 'plan_actuel'], 3.5,
            2.5,
          ],
          // Les options moins bien classées sont atténuées plutôt que masquées :
          // l'utilisateur doit pouvoir constater qu'elles ont été envisagées.
          'line-opacity': [
            'case',
            ['any', ['get', 'recommandee'], ['get', 'plan_actuel'],
             ['==', ['get', 'id'], itineraireSelectionne ?? '']], 1,
            0.35,
          ],
        },
      })

      // Le plan actuel est tireté pour se distinguer de la recommandation.
      // `line-dasharray` n'accepte pas d'expression pilotée par la donnée : il
      // faut donc une couche dédiée, filtrée sur les seuls itinéraires actuels.
      instance.addLayer({
        id: 'itineraire-actuel',
        type: 'line',
        source: 'src-itineraires',
        filter: ['==', ['get', 'plan_actuel'], true],
        layout: { 'line-cap': 'butt', 'line-join': 'round' },
        paint: {
          'line-color': '#ffffff',
          'line-width': 1.6,
          'line-dasharray': [3, 3],
        },
      })

      instance.addLayer({
        id: 'troncons',
        type: 'line',
        source: 'src-troncons',
        layout: { 'line-cap': 'round' },
        paint: {
          'line-color': [
            'match', ['get', 'niveau_risque'],
            'CRITICAL', COULEURS.CRITICAL,
            'HIGH', COULEURS.HIGH,
            'MODERATE', COULEURS.MODERATE,
            COULEURS.LOW,
          ],
          'line-width': 9,
          'line-opacity': 0.55,
          'line-blur': 2,
        },
      }, 'itineraires-fond')

      instance.addLayer({
        id: 'points',
        type: 'circle',
        source: 'src-points',
        paint: {
          'circle-radius': 7,
          'circle-color': '#ffffff',
          'circle-stroke-color': '#0f172a',
          'circle-stroke-width': 2.5,
        },
      })

      // Les libellés passent par des marqueurs HTML plutôt que par une couche
      // `symbol` : celle-ci exigerait une source de glyphes hébergée, donc une
      // dépendance réseau externe que ce fond de carte neutre évite justement.
      for (const marqueur of marqueurs.current) marqueur.remove()
      marqueurs.current = []

      for (const point of couches.points.features) {
        if (point.geometry.type !== 'Point') continue
        const p = point.properties ?? {}
        const element = document.createElement('div')
        element.className =
          'rounded bg-white/95 px-1.5 py-0.5 text-[11px] font-semibold text-ardoise-900 ' +
          'shadow ring-1 ring-ardoise-300 whitespace-nowrap'
        element.textContent = String(p.nom ?? '')
        element.title = String(p.role_fr ?? '')
        marqueurs.current.push(
          new maplibregl.Marker({ element, anchor: 'top', offset: [0, 10] })
            .setLngLat(point.geometry.coordinates as [number, number])
            .addTo(instance),
        )
      }

      // Cadrage sur l'ensemble des tracés : l'utilisateur ne doit jamais avoir
      // à chercher où se passe l'action.
      const coords = couches.itineraires.features.flatMap((f) =>
        f.geometry.type === 'LineString' ? (f.geometry.coordinates as [number, number][]) : [],
      )
      if (coords.length > 0) {
        const bounds = coords.reduce(
          (b, c) => b.extend(c),
          new maplibregl.LngLatBounds(coords[0], coords[0]),
        )
        instance.fitBounds(bounds, { padding: 70, duration: 600, maxZoom: 8 })
      }
    }

    dessiner()
  }, [couches, itineraireSelectionne, styleGeneration])

  // Interactions : sélection d'un itinéraire et détail d'un tronçon exposé.
  useEffect(() => {
    const instance = carte.current
    if (!instance) return

    const surClicItineraire = (e: maplibregl.MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.id
      if (id && onSelectionItineraire) onSelectionItineraire(String(id))
    }

    const surClicTroncon = (e: maplibregl.MapLayerMouseEvent) => {
      const p = e.features?.[0]?.properties
      if (!p) return
      const motifs = typeof p.motifs_fr === 'string' ? JSON.parse(p.motifs_fr) : p.motifs_fr
      new maplibregl.Popup({ closeButton: true, maxWidth: '320px' })
        .setLngLat(e.lngLat)
        .setHTML(
          `<div class="text-ardoise-800">
             <p class="font-semibold">${p.de} → ${p.vers}</p>
             <p class="text-xs text-ardoise-500">${p.axe} · passage ${p.passage_prevu_fr}</p>
             <p class="mt-1 text-xs font-medium">Risque ${p.niveau_risque_fr}</p>
             <ul class="mt-1 list-disc pl-4 text-xs">
               ${(Array.isArray(motifs) ? motifs : []).map((m: string) => `<li>${m}</li>`).join('')}
             </ul>
           </div>`,
        )
        .addTo(instance)
    }

    const pointeur = () => { instance.getCanvas().style.cursor = 'pointer' }
    const defaut = () => { instance.getCanvas().style.cursor = '' }

    instance.on('click', 'itineraires', surClicItineraire)
    instance.on('click', 'troncons', surClicTroncon)
    instance.on('mouseenter', 'itineraires', pointeur)
    instance.on('mouseleave', 'itineraires', defaut)
    instance.on('mouseenter', 'troncons', pointeur)
    instance.on('mouseleave', 'troncons', defaut)

    return () => {
      instance.off('click', 'itineraires', surClicItineraire)
      instance.off('click', 'troncons', surClicTroncon)
      instance.off('mouseenter', 'itineraires', pointeur)
      instance.off('mouseleave', 'itineraires', defaut)
      instance.off('mouseenter', 'troncons', pointeur)
      instance.off('mouseleave', 'troncons', defaut)
    }
  }, [onSelectionItineraire])

  return (
    <div className="relative overflow-hidden rounded-lg border border-ardoise-200">
      <div ref={conteneur} style={{ height: hauteur }} />
      {couches && <LegendeCarte legende={couches.legende} />}
    </div>
  )
}

function LegendeCarte({ legende }: { legende: CoucheCarte['legende'] }) {
  return (
    <div className="absolute bottom-3 left-3 rounded-lg border border-ardoise-200 bg-white/95 px-3 py-2 shadow-sm backdrop-blur">
      <p className="titre-section mb-1.5">Légende</p>
      <ul className="space-y-1">
        {legende.map((item) => (
          <li key={item.code} className="flex items-center gap-2 text-xs text-ardoise-700">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: item.couleur }} />
            {item.libelle_fr}
          </li>
        ))}
      </ul>
    </div>
  )
}
