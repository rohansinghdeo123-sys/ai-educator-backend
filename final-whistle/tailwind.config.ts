import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Football-night palette
        pitch: {
          950: '#060912',
          900: '#0A0E1A',
          850: '#0D1424',
          800: '#111A30',
          700: '#1A2540',
        },
        neon: {
          DEFAULT: '#39FF8B',
          glow: '#00E676',
          dim: '#1FB867',
        },
        gold: {
          DEFAULT: '#FFD45E',
          deep: '#F5C542',
        },
        danger: '#FF5C7A',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'system-ui', 'sans-serif'],
        display: ['var(--font-display)', 'var(--font-sans)', 'sans-serif'],
      },
      boxShadow: {
        neon: '0 0 20px rgba(57,255,139,0.35)',
        gold: '0 0 24px rgba(255,212,94,0.30)',
        glass: '0 8px 32px rgba(0,0,0,0.45)',
      },
      backgroundImage: {
        'pitch-lines':
          'radial-gradient(circle at 50% 0%, rgba(57,255,139,0.08), transparent 60%)',
      },
      keyframes: {
        'pulse-glow': {
          '0%,100%': { opacity: '1' },
          '50%': { opacity: '0.55' },
        },
        float: {
          '0%,100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-6px)' },
        },
      },
      animation: {
        'pulse-glow': 'pulse-glow 2.2s ease-in-out infinite',
        float: 'float 4s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};

export default config;
