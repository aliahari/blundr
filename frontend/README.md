# Blundr Frontend

A React frontend for the Chess Blunder Analyzer, built with Vite and TypeScript.

## Features

- Enter Lichess username
- Select time period (1 month, 3 months, 1 year, all time)
- Select game type (Bullet, Blitz, Rapid)
- Fetch and display games from the backend
- View game statistics (wins, losses, draws, win rate)
- Responsive design

## Prerequisites

- Node.js 18+ 
- npm or yarn
- Backend server running (see main README)

## Installation

```bash
cd frontend
npm install
```

## Development

```bash
npm run dev
```

This will start the development server on `http://localhost:3000` with hot module replacement.

## Configuration

The frontend automatically proxies API requests to `http://localhost:8000`. 
To change this, modify `vite.config.ts`:

```typescript
server: {
  proxy: {
    '/api': {
      target: 'http://your-backend-url',
      changeOrigin: true,
    },
  },
}
```

## Build

```bash
npm run build
```

Creates a production build in the `dist/` directory.

## Preview

```bash
npm run preview
```

Locally preview the production build.

## Project Structure

```
frontend/
├── src/
│   ├── App.tsx           # Main application component
│   ├── main.tsx          # Entry point
│   ├── types/
│   │   └── index.ts      # TypeScript types
│   ├── services/
│   │   └── api.ts        # API service functions
│   ├── utils/
│   │   └── index.ts      # Utility functions
│   └── styles/
│       └── index.css     # Global styles
├── package.json
├── vite.config.ts
└── tsconfig.json
```
