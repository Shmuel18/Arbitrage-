export type Lang = 'en' | 'he';

export interface Translations {
  // Sidebar / Header
  dashboard: string;
  trinityTitle: string;
  arbitrageEngine: string;
  running: string;
  stopped: string;
  exchanges: string;
  positions: string;
  emergencyStop: string;
  emergencyStopConfirm: string;
  emergencyStopSent: string;
  emergencyStopFailed: string;
  language: string;
  theme: string;
  none: string;

  // Stats
  totalBalance: string;
  dailyPnl: string;
  activeTrades: string;
  systemStatus: string;

  // Positions
  activePositions: string;
  symbol: string;
  longShort: string;
  qtyLS: string;
  entryFunding: string;
  fundingLS: string;
  state: string;
  noOpenPositions: string;

  // Control
  controlPanel: string;
  startBot: string;
  stopBot: string;
  strategyToggle: string;
  mode: string;
  maxConcurrentTrades: string;
  apply: string;
  startSent: string;
  stopSent: string;
  emergencySent: string;
  settingsUpdated: string;
  settingsFailed: string;
  strategySet: string;
  strategyFailed: string;

  // Exchange balances
  exchangePortfolio: string;
  total: string;
  noBalancesYet: string;

  // Opportunities
  liveOpportunities: string;
  scanning: string;
  pair: string;
  long: string;
  short: string;
  fundingL: string;
  fundingS: string;
  netPct: string;
  fundingSpread: string;

  // Analytics
  pnlChart: string;
  waitingPnl: string;

  // Recent trades
  last10Trades: string;
  entryLS: string;
  exitLS: string;
  fundingNet: string;
  fees: string;
  opened: string;
  closed: string;
  noTradesYet: string;
  fundingEstimated: string;

  // Logs
  systemLogs: string;
  totalTradesLabel: string;
  winRate: string;
  waitingLogs: string;
}

const en: Translations = {
  dashboard: 'Dashboard',
  trinityTitle: 'TRINITY',
  arbitrageEngine: 'ARBITRAGE ENGINE',
  running: 'RUNNING',
  stopped: 'STOPPED',
  exchanges: 'EXCHANGES',
  positions: 'POSITIONS',
  emergencyStop: 'EMERGENCY STOP',
  emergencyStopConfirm: '⚠️ Are you sure you want to EMERGENCY STOP? This will close all positions!',
  emergencyStopSent: 'Emergency stop initiated!',
  emergencyStopFailed: 'Failed to send emergency stop command',
  language: 'Language',
  theme: 'Theme',
  none: 'NONE',

  totalBalance: 'TOTAL BALANCE',
  dailyPnl: 'DAILY PNL (24H)',
  activeTrades: 'ACTIVE TRADES',
  systemStatus: 'SYSTEM STATUS',

  activePositions: 'Active Positions',
  symbol: 'Symbol',
  longShort: 'Long / Short',
  qtyLS: 'Qty (L/S)',
  entryFunding: 'Entry Edge %',
  fundingLS: 'Funding L/S',
  state: 'State',
  noOpenPositions: 'No open positions',

  controlPanel: 'Control Panel',
  startBot: 'Start Bot',
  stopBot: 'Stop Bot',
  strategyToggle: 'Strategy',
  mode: 'Mode',
  maxConcurrentTrades: 'Max Concurrent Trades',
  apply: 'Apply',
  startSent: 'Start command sent',
  stopSent: 'Stop command sent',
  emergencySent: 'Emergency stop sent',
  settingsUpdated: 'Settings updated',
  settingsFailed: 'Failed to update settings',
  strategySet: 'Strategy updated',
  strategyFailed: 'Failed to update strategy',

  exchangePortfolio: 'Exchange Portfolio',
  total: 'Total',
  noBalancesYet: 'No balance data available',

  liveOpportunities: 'Live Opportunities',
  scanning: 'Scanning for opportunities...',
  pair: 'PAIR',
  long: 'LONG',
  short: 'SHORT',
  fundingL: 'FUND. L',
  fundingS: 'FUND. S',
  netPct: 'NET %',
  fundingSpread: 'SPREAD %',

  pnlChart: 'PnL Chart (24h)',
  waitingPnl: 'Waiting for PnL data...',

  last10Trades: 'Recent Trades',
  entryLS: 'Entry L/S',
  exitLS: 'Exit L/S',
  fundingNet: 'Funding Net',
  fees: 'Fees',
  opened: 'Opened',
  closed: 'Closed',
  noTradesYet: 'No trades recorded yet',
  fundingEstimated: '* Funding amounts are estimated based on entry rates and notional value',

  systemLogs: 'System Log',
  totalTradesLabel: 'Total Trades',
  winRate: 'Win Rate',
  waitingLogs: 'Waiting for log data...',
};

const he: Translations = {
  dashboard: 'לוח מחוונים',
  trinityTitle: 'TRINITY',
  arbitrageEngine: 'מנוע ארביטראז׳',
  running: 'פועל',
  stopped: 'מופסק',
  exchanges: 'בורסאות',
  positions: 'פוזיציות',
  emergencyStop: 'עצירת חירום',
  emergencyStopConfirm: '⚠️ האם לבצע עצירת חירום? פעולה זו תסגור את כל הפוזיציות הפתוחות.',
  emergencyStopSent: 'פקודת עצירת חירום נשלחה בהצלחה',
  emergencyStopFailed: 'שגיאה בשליחת פקודת עצירת חירום',
  language: 'שפה',
  theme: 'מראה',
  none: 'אין',

  totalBalance: 'יתרה כוללת',
  dailyPnl: 'רווח והפסד יומי',
  activeTrades: 'עסקאות פתוחות',
  systemStatus: 'מצב המערכת',

  activePositions: 'פוזיציות פתוחות',
  symbol: 'מטבע',
  longShort: 'לונג / שורט',
  qtyLS: 'כמות (ל/ש)',
  entryFunding: '% תשואה בכניסה',
  fundingLS: 'מימון ל/ש',
  state: 'סטטוס',
  noOpenPositions: 'אין פוזיציות פתוחות כרגע',

  controlPanel: 'לוח בקרה',
  startBot: 'הפעלה',
  stopBot: 'עצירה',
  strategyToggle: 'אסטרטגיה',
  mode: 'מצב פעולה',
  maxConcurrentTrades: 'עסקאות מקביליות',
  apply: 'עדכן',
  startSent: 'פקודת הפעלה נשלחה',
  stopSent: 'פקודת עצירה נשלחה',
  emergencySent: 'עצירת חירום נשלחה',
  settingsUpdated: 'ההגדרות עודכנו',
  settingsFailed: 'שגיאה בעדכון ההגדרות',
  strategySet: 'האסטרטגיה עודכנה',
  strategyFailed: 'שגיאה בעדכון האסטרטגיה',

  exchangePortfolio: 'תיק השקעות',
  total: 'סה״כ',
  noBalancesYet: 'אין נתוני יתרות זמינים',

  liveOpportunities: 'הזדמנויות בזמן אמת',
  scanning: 'מחפש הזדמנויות ארביטראז׳...',
  pair: 'צמד',
  long: 'לונג',
  short: 'שורט',
  fundingL: 'מימון ל׳',
  fundingS: 'מימון ש׳',
  netPct: '% נטו',
  fundingSpread: '% ספרד',

  pnlChart: 'גרף רווח והפסד (24 שעות)',
  waitingPnl: 'ממתין לנתוני רווח והפסד...',

  last10Trades: 'עסקאות אחרונות',
  entryLS: 'מחיר כניסה',
  exitLS: 'מחיר יציאה',
  fundingNet: 'מימון נטו',
  fees: 'עמלות',
  opened: 'נפתחה',
  closed: 'נסגרה',
  noTradesYet: 'טרם בוצעו עסקאות',
  fundingEstimated: '* סכומי המימון מחושבים לפי שיעורי הכניסה והנפח הנומינלי',

  systemLogs: 'יומן מערכת',
  totalTradesLabel: 'סה״כ עסקאות',
  winRate: 'אחוז הצלחה',
  waitingLogs: 'ממתין לנתוני יומן...',
};

export const translations: Record<Lang, Translations> = { en, he };
