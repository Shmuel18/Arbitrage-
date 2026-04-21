# Trinity Bot - Web Interface Setup

Este proyecto incluye una interfaz web moderna para monitorear y controlar el bot Trinity en tiempo real.

## 🚀 Características

- **Dashboard en tiempo real** con WebSocket
- **Monitoreo de posiciones** activas
- **Historial de trades** y estadísticas
- **Gráficos de rendimiento** y P&L
- **Panel de control** del bot
- **Alertas y notificaciones**

## 📦 Estructura

```
Arbitrage/
├── api/                    # FastAPI Backend
│   ├── main.py
│   ├── routes/
│   └── websocket_manager.py
├── frontend/               # React Frontend
│   ├── src/
│   │   ├── components/
│   │   ├── services/
│   │   └── types.ts
│   ├── public/
│   └── package.json
└── src/
    └── api/
        └── publisher.py    # Integration with Trinity bot
```

## 🛠️ Instalación

### Backend API

```bash
# Instalar dependencias de la API
pip install -r api/requirements.txt
```

### Frontend

```bash
# Navegar a la carpeta frontend
cd frontend

# Instalar dependencias
npm install
```

## ▶️ Ejecución

### 1. Iniciar Redis (requerido)

Asegúrate de que Redis esté corriendo en `localhost:6379`

### 2. Iniciar Backend API

```bash
# Desde la raíz del proyecto
python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

O usa el script:

```bash
.\run_api.ps1
```

### 3. Iniciar Frontend

```bash
# Desde la carpeta frontend
cd frontend
npm start
```

O usa el script:

```bash
.\run_frontend.ps1
```

### 4. Iniciar Bot Trinity (con integración API)

```bash
.\venv\Scripts\python.exe main.py
```

## 🌐 Acceso

- **Frontend**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs
- **WebSocket**: `ws://localhost:8000/ws` (auth via cookie `trinity_ws_token`)

> Seguridad: el endpoint `/ws` es fail-closed. Si `ADMIN_TOKEN` no está
> configurado (o el token no coincide), la conexión WebSocket se rechaza.

## 📊 API Endpoints

### Status

- `GET /api/status` - Bot status

### Positions

- `GET /api/positions` - Lista de posiciones activas
- `DELETE /api/positions/{id}` - Cerrar posición

### Trades

- `GET /api/trades` - Historial de trades
- `GET /api/trades/stats` - Estadísticas

### Controls

- `POST /api/controls/command` - Enviar comando al bot
- `POST /api/controls/emergency_stop` - Parada de emergencia
- `GET /api/controls/exchanges` - Status de exchanges

### Analytics

- `GET /api/analytics/performance` - Métricas de rendimiento
- `GET /api/analytics/pnl` - P&L histórico
- `GET /api/analytics/summary` - Resumen general

## 🔧 Configuración

### Backend

Edita `api/main.py` para cambiar:

- Puerto (default: 8000)
- CORS origins
- Redis connection

### Frontend

Edita `frontend/src/services/api.ts` para cambiar:

- API base URL (default: http://localhost:8000)

## 🎨 Personalización

### Colores y Tema

Edita `frontend/tailwind.config.js` y `frontend/src/index.css`

### Componentes

Todos los componentes están en `frontend/src/components/`

## 📝 Notas

- Asegúrate de tener Redis corriendo
- El bot Trinity debe estar corriendo para ver datos en tiempo real
- Para producción, configura CORS correctamente y usa HTTPS

## 🐛 Troubleshooting

**Error: Cannot connect to API**

- Verifica que la API esté corriendo en puerto 8000
- Revisa la URL en `api.ts`

**Error: WebSocket connection failed**

- Verifica que la API esté corriendo
- Verifica que `ADMIN_TOKEN` (backend) y `VITE_WS_TOKEN` (frontend) estén configurados y sean iguales
- Si usas tokens separados, verifica también `COMMAND_TOKEN`/`CONFIG_TOKEN`/`EMERGENCY_TOKEN`/`TRADE_TOKEN` y sus equivalentes `VITE_*`
- Revisa firewall/antivirus

**Error: No data showing**

- Asegúrate de que el bot Trinity esté corriendo
- Verifica conexión a Redis

## 📄 Licencia

Proprietary - Trinity Bot © 2026
