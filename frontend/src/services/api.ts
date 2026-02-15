import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const getPositions = async () => {
  const response = await api.get('/positions');
  return response.data;
};

export const closePosition = async (positionId: string) => {
  const response = await api.delete(`/positions/${positionId}`);
  return response.data;
};

export const getTrades = async (limit = 100, hours?: number) => {
  const params: any = { limit };
  if (hours) params.hours = hours;
  const response = await api.get('/trades', { params });
  return response.data;
};

export const getTradeStats = async () => {
  const response = await api.get('/trades/stats');
  return response.data;
};

export const sendBotCommand = async (action: string) => {
  const response = await api.post('/controls/command', { action });
  return response.data;
};

export const emergencyStop = async () => {
  const response = await api.post('/controls/emergency_stop');
  return response.data;
};

export const updateConfig = async (key: string, value: any) => {
  const response = await api.post('/controls/config', { key, value });
  return response.data;
};

export const getExchanges = async () => {
  const response = await api.get('/controls/exchanges');
  return response.data;
};

export const getPerformance = async (hours = 24) => {
  const response = await api.get('/analytics/performance', { params: { hours } });
  return response.data;
};

export const getPnL = async (hours = 24) => {
  const response = await api.get('/analytics/pnl', { params: { hours } });
  return response.data;
};

export const getSummary = async () => {
  const response = await api.get('/analytics/summary');
  return response.data;
};

export const getOpportunities = async () => {
  const response = await api.get('/opportunities');
  return response.data;
};

export const getBalances = async () => {
  const response = await api.get('/balances');
  return response.data;
};

export const getLogs = async (limit = 50) => {
  const response = await api.get('/logs', { params: { limit } });
  return response.data;
};

export default api;
