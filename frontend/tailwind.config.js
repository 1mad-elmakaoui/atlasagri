/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Palette d'entreprise volontairement restreinte. Les couleurs vives
        // sont réservées aux niveaux de risque : si tout est coloré, plus rien
        // n'attire l'œil là où il faut.
        ardoise: {
          50: '#f8fafc', 100: '#f1f5f9', 200: '#e2e8f0', 300: '#cbd5e1',
          400: '#94a3b8', 500: '#64748b', 600: '#475569', 700: '#334155',
          800: '#1e293b', 900: '#0f172a', 950: '#020617',
        },
        risque: {
          faible: '#15803d',
          modere: '#ca8a04',
          eleve: '#ea580c',
          critique: '#b91c1c',
        },
        action: '#1d4ed8',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
