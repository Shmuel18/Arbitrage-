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
  immediateSpread: string;
  normalizedSpread: string;
  currentSpread: string;
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
  immediateSpreadOpp: string;
  priceSpreadPct: string;
  hourlyRate: string;
  nextPayment: string;
  countdown: string;
  belowThreshold: string;
  belowThresholdLabel: string;

  // Analytics
  pnlChart: string;
  pnlChartInterval: string;
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

  // New stats / columns
  avgPnlStat: string;
  allTimePnl: string;
  netPnl: string;
  duration: string;
  sizeUsd: string;
  nextPayout: string;
  lastUpdated: string;
  netImmed: string;

  // Trade Detail Modal
  tradeDetail: string;
  tradeDetailPrices: string;
  tradeDetailPnl: string;
  tradeDetailFunding: string;
  entryPriceLong: string;
  entryPriceShort: string;
  exitPriceLong: string;
  exitPriceShort: string;
  pricePnl: string;
  fundingNetDetail: string;
  feesDetail: string;
  totalNetPnl: string;
  collectionsCount: string;
  fundingCollectedUsd: string;
  entryEdge: string;
  entryBasis: string;
  fundingAtEntry: string;
  exitReasonLabel: string;
  openedAt: string;
  closedAt: string;
  holdDuration: string;
  modeLabel: string;
  statusActive: string;
  statusClosed: string;

  // Mode Labels
  cherry_pick: string;
  pot: string;
  nutcracker: string;
  hold: string;

  // Tier Labels
  tier: string;
  tierTop: string;
  tierMedium: string;
  tierWeak: string;
  tierAdverse: string;

  // StatsCards sub-labels
  subTotalAcross: string;
  subProfitableSession: string;
  subLossSession: string;
  subPositionsOpen: string;
  subNoPositions: string;
  subScanningMarkets: string;
  subBotIdle: string;
  subCumulativePnl: string;
  subPerClosedTrade: string;
  subAllTimeExec: string;

  // PositionsTable column headers
  colExchange: string;
  colEntryPct: string;
  colPnl: string;
  colFundPct: string;

  // RightPanel column headers
  colBridge: string;
  colMode: string;
  colNextFunding: string;

  // RecentTradesPanel hints
  clickRowForDetails: string;

  // PositionDetailCard
  pdPrices: string;
  pdFunding: string;
  pdBasis: string;
  pdEntry: string;
  pdLive: string;
  pdDelta: string;
  pdTarget: string;
  pdEntrySpread: string;
  pdLiveSpread: string;
  pdCollections: string;
  pdTargetReached: string;
  pdToTarget: string;

  // Risk Radar
  rrTitle: string;
  rrMaxDrawdown: string;
  rrMarginUsed: string;
  rrSymbolConc: string;
  rrExchangeConc: string;
  rrLow: string;
  rrModerate: string;
  rrHigh: string;
  rrNoData: string;

  // Error Boundary
  errorBoundaryTitle: string;
  errorBoundaryMessage: string;
  reloadPage: string;
  displayingLastKnownData: string;

  // Dashboard
  viewLogs: string;

  // Header status
  wsPrefix: string;
  wsAge: string;
  staleData: string;

  // Badges / Labels
  live: string;
  sessionLabel: string;
  allTimeLabel: string;
  tradesWord: string;
  positionWord: string;
  positionsWord: string;
  tradeFired: string;
  feedLabel: string;

  // AnalyticsPanel
  allTimePnlSubtitle: string;
  realized: string;
  unrealized: string;
  allLabel: string;

  // Timeline events — PositionDetailCard
  tlPositionOpened: string;
  tlEntrySpreadLabel: string;
  tlBasisLabel: string;
  tlMarkToMarket: string;
  tlCurrentSpreadLabel: string;
  tlPricePnlLabel: string;
  tlFundingCollection: string;
  tlCollectionsNet: string;
  tlNextWindow: string;
  tlProfitTarget: string;
  tlTargetReached: string;
  tlRemaining: string;
  tlTargetUnavailable: string;
  tlExecutionState: string;
  tlActive: string;
  executionTimeline: string;

  // Timeline events — TradeDetailModal
  tlExecutionStarted: string;
  tlPairOpened: string;
  tlSpreadCaptured: string;
  tlFundingSettlement: string;
  tlNoFundingSettlement: string;
  tlExitAttribution: string;
  tlAwaitingExit: string;
  tlNetResult: string;
  tlTotalLabel: string;
  tlHoldLabel: string;
  closeDialog: string;

  // Exit reasons
  exitProfit: string;
  exitRecovery: string;
  exitLowSpread: string;
  exitUpgrade: string;
  exitCherryStop: string;
  exitBasisTimeout: string;
  exitNegFunding: string;
  exitTimeout: string;
  exitLiquidation: string;
  exitManual: string;

  // Countdown
  countdownNow: string;
}

const en: Translations = {
  dashboard: 'Dashboard',
  trinityTitle: 'RATEBRIDGE',
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
  immediateSpread: 'Funding Spread',
  normalizedSpread: 'Spread (8h)',
  currentSpread: 'Live Spread %',
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
  immediateSpreadOpp: 'FUND. SPREAD %',
  priceSpreadPct: 'PRICE SPREAD %',
  hourlyRate: '/HOUR %',
  nextPayment: 'NEXT CYC',
  countdown: 'PAYOUT',
  belowThreshold: 'Below threshold (0.3%)',
  belowThresholdLabel: 'Below threshold',

  pnlChart: 'PnL Chart',
  pnlChartInterval: 'Interval',
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

  avgPnlStat: 'AVG P&L / TRADE',
  allTimePnl: 'ALL-TIME PNL',
  netPnl: 'Net P&L',
  duration: 'Duration',
  sizeUsd: 'Size $',
  nextPayout: 'Next ⏱',
  lastUpdated: 'LAST UPDATE',
  netImmed: 'NET PROFIT',

  // Trade Detail Modal
  tradeDetail: 'Trade Detail',
  tradeDetailPrices: 'Prices',
  tradeDetailPnl: 'P&L Breakdown',
  tradeDetailFunding: 'Funding Collections',
  entryPriceLong: 'Entry Long',
  entryPriceShort: 'Entry Short',
  exitPriceLong: 'Exit Long',
  exitPriceShort: 'Exit Short',
  pricePnl: 'Price P&L',
  fundingNetDetail: 'Funding Net',
  feesDetail: 'Fees',
  totalNetPnl: 'Total Net P&L',
  collectionsCount: 'Collections',
  fundingCollectedUsd: 'Collected (USD)',
  entryEdge: 'Entry Edge',
  entryBasis: 'Price Spread',
  fundingAtEntry: 'Funding at Entry',
  exitReasonLabel: 'Exit Reason',
  openedAt: 'Opened',
  closedAt: 'Closed',
  holdDuration: 'Duration',
  modeLabel: 'Mode',
  statusActive: 'ACTIVE',
  statusClosed: 'CLOSED',
  cherry_pick: 'CHERRY PICK',
  pot: 'POT',
  nutcracker: 'NUTCRACKER',
  hold: 'HOLD',

  // Tier Labels
  tier: 'Tier',
  tierTop: 'TOP',
  tierMedium: 'MEDIUM',
  tierWeak: 'WEAK',
  tierAdverse: 'PRICE ⛔',

  // StatsCards sub-labels
  subTotalAcross: 'Total across all exchanges',
  subProfitableSession: '▲ Profitable session',
  subLossSession: '▼ Loss session',
  subPositionsOpen: 'open',
  subNoPositions: 'No open positions',
  subScanningMarkets: 'Scanning markets',
  subBotIdle: 'Bot is idle',
  subCumulativePnl: 'Cumulative P&L',
  subPerClosedTrade: 'Per closed trade',
  subAllTimeExec: 'All-time executions',

  // PositionsTable column headers
  colExchange: 'Ex',
  colEntryPct: 'Entry%',
  colPnl: 'PnL%',
  colFundPct: 'Fund%',

  // RightPanel column headers
  colBridge: 'EXCHANGES',
  colMode: 'MODE',
  colNextFunding: 'NEXT FUNDING',

  // RecentTradesPanel hints
  clickRowForDetails: 'Click row for details',

  // PositionDetailCard
  pdPrices: 'Prices',
  pdFunding: 'Funding',
  pdBasis: 'Basis',
  pdEntry: 'Entry',
  pdLive: 'Live',
  pdDelta: 'Delta',
  pdTarget: 'Target',
  pdEntrySpread: 'Entry Spread',
  pdLiveSpread: 'Live Spread',
  pdCollections: 'Collections',
  pdTargetReached: 'Reached!',
  pdToTarget: 'to target',

  // Risk Radar
  rrTitle: 'RISK RADAR',
  rrMaxDrawdown: 'MAX DRAWDOWN',
  rrMarginUsed: 'MARGIN UTILIZATION',
  rrSymbolConc: 'SYMBOL CONCENTRATION',
  rrExchangeConc: 'EXCHANGE CONCENTRATION',
  rrLow: 'Low risk',
  rrModerate: 'Moderate',
  rrHigh: 'High risk',
  rrNoData: 'No data yet',

  // Error Boundary
  errorBoundaryTitle: 'Something went wrong',
  errorBoundaryMessage: 'An unexpected error occurred in the UI. The bot process is unaffected.',
  reloadPage: 'Reload Page',
  displayingLastKnownData: 'displaying last known data',

  // Dashboard
  viewLogs: 'View Logs ↓',

  // Header status
  wsPrefix: 'WS',
  wsAge: 'WS age:',
  staleData: 'STALE DATA',

  // Badges / Labels
  live: 'LIVE',
  sessionLabel: 'SESSION',
  allTimeLabel: 'ALL-TIME',
  tradesWord: 'trades',
  positionWord: 'position',
  positionsWord: 'positions',
  tradeFired: 'TRADE FIRED',
  feedLabel: 'FEED',

  // AnalyticsPanel
  allTimePnlSubtitle: 'All-time P&L',
  realized: 'Realized',
  unrealized: 'Unrealized',
  allLabel: 'All',

  // Timeline events — PositionDetailCard
  tlPositionOpened: 'Position Opened',
  tlEntrySpreadLabel: 'Entry spread',
  tlBasisLabel: 'Basis',
  tlMarkToMarket: 'Mark-to-Market',
  tlCurrentSpreadLabel: 'Current spread',
  tlPricePnlLabel: 'Price PnL',
  tlFundingCollection: 'Funding Collection',
  tlCollectionsNet: 'collection(s), net',
  tlNextWindow: 'Next window',
  tlProfitTarget: 'Profit Target',
  tlTargetReached: 'Target reached',
  tlRemaining: 'remaining',
  tlTargetUnavailable: 'Target tracking unavailable',
  tlExecutionState: 'Execution State',
  tlActive: 'ACTIVE',
  executionTimeline: 'Execution Confidence Timeline',

  // Timeline events — TradeDetailModal
  tlExecutionStarted: 'Execution Started',
  tlPairOpened: 'pair opened',
  tlSpreadCaptured: 'Spread Captured',
  tlFundingSettlement: 'Funding Settlement',
  tlNoFundingSettlement: 'No funding settlement recorded',
  tlExitAttribution: 'Exit & Attribution',
  tlAwaitingExit: 'Awaiting exit trigger',
  tlNetResult: 'Net Result',
  tlTotalLabel: 'total',
  tlHoldLabel: 'hold',
  closeDialog: 'Close dialog',

  // Exit reasons
  exitProfit: 'Profit',
  exitRecovery: 'Recovery',
  exitLowSpread: 'Low Spread',
  exitUpgrade: 'Upgrade',
  exitCherryStop: 'Cherry Stop',
  exitBasisTimeout: 'Basis Timeout',
  exitNegFunding: 'Neg. Funding',
  exitTimeout: 'Timeout',
  exitLiquidation: 'Liquidation',
  exitManual: 'Manual',

  // Countdown
  countdownNow: '⚡ NOW',
};

const he: Translations = {
  dashboard: 'לוח מחוונים',
  trinityTitle: 'RATEBRIDGE',
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
  immediateSpread: 'הפרש פנדינג',
  normalizedSpread: 'ספרד (8 שעות)',
  currentSpread: '% ספרד נוכחי',
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
  immediateSpreadOpp: '% הפרש פנדינג',
  priceSpreadPct: '% הפרש מחירים',
  hourlyRate: '% לשעה',
  nextPayment: 'פעימה הבאה',
  countdown: 'פעימה',
  belowThreshold: 'מתחת לסף (0.3%)',
  belowThresholdLabel: 'מתחת לסף',

  pnlChart: 'גרף רווח והפסד',
  pnlChartInterval: 'טווח',
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

  avgPnlStat: 'ממוצע לעסקה',
  allTimePnl: 'רווח כולל',
  netPnl: 'רווח נטו',
  duration: 'משך',
  sizeUsd: 'גודל $',
  nextPayout: 'תשלום הבא',
  lastUpdated: 'עדכון אחרון',
  netImmed: 'רווח נטו',

  // Trade Detail Modal
  tradeDetail: 'פרטי עסקה',
  tradeDetailPrices: 'מחירים',
  tradeDetailPnl: 'פירוט רווח/הפסד',
  tradeDetailFunding: 'גביות מימון',
  entryPriceLong: 'כניסה לונג',
  entryPriceShort: 'כניסה שורט',
  exitPriceLong: 'יציאה לונג',
  exitPriceShort: 'יציאה שורט',
  pricePnl: 'רווח מחיר',
  fundingNetDetail: 'מימון נטו',
  feesDetail: 'עמלות',
  totalNetPnl: 'רווח נטו כולל',
  collectionsCount: 'גביות',
  fundingCollectedUsd: 'מימון שנגבה (USD)',
  entryEdge: 'יתרון כניסה',
  entryBasis: 'מרווח מחירים בכניסה',
  fundingAtEntry: 'מימון בכניסה',
  exitReasonLabel: 'סיבת יציאה',
  openedAt: 'נפתחה',
  closedAt: 'נסגרה',
  holdDuration: 'משך',
  modeLabel: 'מצב',
  statusActive: 'פעילה',
  statusClosed: 'סגורה',
  cherry_pick: 'צ׳רי פיק',
  pot: 'סיר דבש',
  nutcracker: 'מפצח אגוזים',
  hold: 'החזקה',

  // Tier Labels
  tier: 'דרגה',
  tierTop: 'מוביל',
  tierMedium: 'בינוני',
  tierWeak: 'חלש',
  tierAdverse: 'מחיר ⛔',

  // StatsCards sub-labels
  subTotalAcross: 'סה״כ בכל הבורסאות',
  subProfitableSession: '▲ סשן רווחי',
  subLossSession: '▼ סשן הפסדי',
  subPositionsOpen: 'פתוחות',
  subNoPositions: 'אין פוזיציות פתוחות',
  subScanningMarkets: 'סורק שווקים',
  subBotIdle: 'הבוט לא פעיל',
  subCumulativePnl: 'רווח מצטבר',
  subPerClosedTrade: 'ממוצע לעסקה',
  subAllTimeExec: 'סה״כ ביצועים',

  // PositionsTable column headers
  colExchange: 'בורסה',
  colEntryPct: '% כניסה',
  colPnl: '% רוו"ה',
  colFundPct: '% מימון',

  // RightPanel column headers
  colBridge: 'בורסאות',
  colMode: 'מצב',
  colNextFunding: 'מימון הבא',

  // RecentTradesPanel hints
  clickRowForDetails: 'לחץ על שורה לפרטים',

  // PositionDetailCard
  pdPrices: 'מחירים',
  pdFunding: 'מימון',
  pdBasis: 'בסיס',
  pdEntry: 'כניסה',
  pdLive: 'עכשווי',
  pdDelta: 'שינוי',
  pdTarget: 'יעד',
  pdEntrySpread: 'ספרד כניסה',
  pdLiveSpread: 'ספרד נוכחי',
  pdCollections: 'גביות',
  pdTargetReached: 'הושג!',
  pdToTarget: 'ליעד',

  // Risk Radar
  rrTitle: 'ראדר סיכון',
  rrMaxDrawdown: 'ירידה מקסימלית',
  rrMarginUsed: 'ניצולת מרג׳ין',
  rrSymbolConc: 'ריכוז סימבול',
  rrExchangeConc: 'ריכוז בורסה',
  rrLow: 'סיכון נמוך',
  rrModerate: 'בינוני',
  rrHigh: 'סיכון גבוה',
  rrNoData: 'אין נתונים עדיין',

  // Error Boundary
  errorBoundaryTitle: 'משהו השתבש',
  errorBoundaryMessage: 'אירעה שגיאה בלתי צפויה בממשק. תהליך הבוט לא מושפע.',
  reloadPage: 'טען מחדש',
  displayingLastKnownData: 'מציג נתונים אחרונים ידועים',

  // Dashboard
  viewLogs: 'צפה ביומן ↓',

  // Header status
  wsPrefix: 'WS',
  wsAge: 'גיל WS:',
  staleData: 'נתונים ישנים',

  // Badges / Labels
  live: 'פעיל',
  sessionLabel: 'סשן',
  allTimeLabel: 'כל הזמן',
  tradesWord: 'עסקאות',
  positionWord: 'פוזיציה',
  positionsWord: 'פוזיציות',
  tradeFired: 'עסקה בוצעה',
  feedLabel: 'פיד',

  // AnalyticsPanel
  allTimePnlSubtitle: 'רווח והפסד מצטבר',
  realized: 'ממומש',
  unrealized: 'לא ממומש',
  allLabel: 'הכל',

  // Timeline events — PositionDetailCard
  tlPositionOpened: 'פוזיציה נפתחה',
  tlEntrySpreadLabel: 'ספרד כניסה',
  tlBasisLabel: 'בסיס',
  tlMarkToMarket: 'שערוך שוק',
  tlCurrentSpreadLabel: 'ספרד נוכחי',
  tlPricePnlLabel: 'רווח מחיר',
  tlFundingCollection: 'גביית מימון',
  tlCollectionsNet: 'גבייה(ות), נטו',
  tlNextWindow: 'חלון הבא',
  tlProfitTarget: 'יעד רווח',
  tlTargetReached: 'היעד הושג',
  tlRemaining: 'נותר',
  tlTargetUnavailable: 'מעקב יעד לא זמין',
  tlExecutionState: 'מצב ביצוע',
  tlActive: 'פעיל',
  executionTimeline: 'ציר זמן ביצוע',

  // Timeline events — TradeDetailModal
  tlExecutionStarted: 'ביצוע התחיל',
  tlPairOpened: 'צמד נפתח',
  tlSpreadCaptured: 'ספרד נלכד',
  tlFundingSettlement: 'סליקת מימון',
  tlNoFundingSettlement: 'לא נרשמה סליקת מימון',
  tlExitAttribution: 'יציאה וייחוס',
  tlAwaitingExit: 'ממתין לטריגר יציאה',
  tlNetResult: 'תוצאה נטו',
  tlTotalLabel: 'סה״כ',
  tlHoldLabel: 'החזקה',
  closeDialog: 'סגור חלון',

  // Exit reasons
  exitProfit: 'רווח',
  exitRecovery: 'התאוששות',
  exitLowSpread: 'ספרד נמוך',
  exitUpgrade: 'שדרוג',
  exitCherryStop: 'סטופ צ׳רי',
  exitBasisTimeout: 'טיימאאוט בסיס',
  exitNegFunding: 'מימון שלילי',
  exitTimeout: 'טיימאאוט',
  exitLiquidation: 'חיסול',
  exitManual: 'ידני',

  // Countdown
  countdownNow: '⚡ עכשיו',
};

export const translations: Record<Lang, Translations> = { en, he };
