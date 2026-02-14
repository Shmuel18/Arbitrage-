# Trinity Bot Frontend

Modern React + TypeScript frontend for Trinity Arbitrage Bot.

## Features

- Real-time WebSocket updates
- Interactive dashboard
- Position monitoring
- Trade history
- Performance analytics
- Bot controls

## Tech Stack

- React 18
- TypeScript
- Tailwind CSS
- Chart.js
- Axios
- WebSocket

## Getting Started

```bash
# Install dependencies
npm install

# Start development server
npm start

# Build for production
npm run build
```

## Available Scripts

- `npm start` - Development server (port 3000)
- `npm build` - Production build
- `npm test` - Run tests
- `npm eject` - Eject from Create React App

## Configuration

Edit `src/services/api.ts` to change API endpoint:

```typescript
const API_BASE_URL = "http://localhost:8000/api";
```

## Components

- `Dashboard` - Main dashboard
- `Header` - Top navigation
- `StatsCards` - Summary statistics
- `PositionsTable` - Active positions
- `TradesHistory` - Trade history
- `PnLChart` - Performance chart
- `ControlPanel` - Bot controls
