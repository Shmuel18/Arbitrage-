# 🚀 Trinity Bot - Quick Start Web Interface

## התקנה מהירה

```powershell
# הרץ סקריפט התקנה אוטומטי
.\setup.ps1
```

זה יתקין:

- ✅ FastAPI Backend dependencies
- ✅ React Frontend dependencies

---

## הפעלה

### שלב 1: וודא ש-Redis רץ

```powershell
# אם התקנת Redis כשירות, הוא אמור לרוץ אוטומטית
# אחרת:
redis-server
```

### שלב 2: הפעל את ה-API (טרמינל 1)

```powershell
.\run_api.ps1
```

### שלב 3: הפעל את הפרונט (טרמינל 2)

```powershell
.\run_frontend.ps1
```

### שלב 4: הפעל את הבוט (טרמינל 3)

```powershell
.\run.ps1
```

---

## גישה לממשק

🌐 **פתח בדפדפן:**

```
http://localhost:3000
```

📚 **API Docs (Swagger):**

```
http://localhost:8000/docs
```

---

## תכונות הממשק

### 📊 Dashboard

- **סטטוס בוט בזמן אמת**
- **סיכום P&L ומדדים**
- **רשימת בורסות מחוברות**

### 💼 Active Positions

- כל הפוזיציות הפתוחות
- P&L בזמן אמת
- סגירה ידנית של פוזיציות

### 📜 Trade History

- היסטוריית כל העסקאות
- סינון לפי זמן (1h, 24h, 7d, All)
- סטטיסטיקות win rate

### 📈 Analytics

- **גרף P&L** - מעקב אחר רווחים לאורך זמן
- **Performance metrics** - Sharpe ratio, drawdown, etc.

### 🎮 Control Panel

- **Start** - התחל מסחר
- **Pause** - עצור זמנית (שמור פוזיציות קיימות)
- **Resume** - חזור למסחר
- **Stop** - עצור וסגור הכל
- **🚨 Emergency Stop** - סגור הכל מיידית!

---

## 🔧 Troubleshooting

### אין חיבור ל-API

```
✅ וודא ש-API רץ על פורט 8000
✅ בדוק firewall/antivirus
```

### אין נתונים בממשק

```
✅ וודא שהבוט Trinity רץ
✅ וודא ש-Redis רץ
✅ בדוק console בדפדפן לשגיאות
```

### Frontend לא עולה

```
✅ וודא ש-Node.js מותקן
✅ הרץ: cd frontend && npm install
✅ נסה פורט אחר אם 3000 תפוס
```

---

## 📱 Screenshots

### Dashboard

![Dashboard with real-time stats and positions]

### Positions Table

![Active positions with P&L tracking]

### Performance Chart

![P&L chart over time]

---

## ⚙️ עריכת הגדרות

### שינוי פורט API

ערוך `run_api.ps1`:

```powershell
--port 8000  # שנה ל-8001 או כל פורט אחר
```

### שינוי כתובת API בפרונט

ערוך `frontend/src/services/api.ts`:

```typescript
const API_BASE_URL = "http://localhost:8000/api";
```

---

## 🎨 התאמה אישית

### צבעים וערכת נושא

ערוך `frontend/tailwind.config.js`

### קומפוננטים

כל הקומפוננטים ב: `frontend/src/components/`

---

## 📄 נוסחת הפעלה מלאה (Copy-Paste)

```powershell
# טרמינל 1 - API
.\run_api.ps1

# טרמינל 2 - Frontend
.\run_frontend.ps1

# טרמינל 3 - Bot
.\run.ps1

# פתח דפדפן:
# http://localhost:3000
```

---

**🎉 מוכן! תהנה מהממשק החדש!**
