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

The API client uses a relative base URL (`/api`) so it works on localhost and tunneled hosts.
If needed, adjust `src/services/api.ts`.

```typescript
const API_BASE_URL = "/api";
```

WebSocket auth (required by backend fail-closed policy):

```bash
# must match backend ADMIN_TOKEN
VITE_WS_TOKEN=change-me-strong-token

# optional dedicated read token (fallback: VITE_WS_TOKEN)
VITE_READ_TOKEN=change-me-read-token

# optional scoped control tokens (fallback: VITE_ADMIN_TOKEN or VITE_WS_TOKEN)
VITE_ADMIN_TOKEN=change-me-admin-token
VITE_COMMAND_TOKEN=change-me-command-token
VITE_CONFIG_TOKEN=change-me-config-token
VITE_EMERGENCY_TOKEN=change-me-emergency-token
VITE_TRADE_TOKEN=change-me-trade-token
```

## Components

- `Dashboard` - Main dashboard
- `Header` - Top navigation
- `StatsCards` - Summary statistics
- `PositionsTable` - Active positions
- `TradesHistory` - Trade history
- `PnLChart` - Performance chart
- `ControlPanel` - Bot controls
