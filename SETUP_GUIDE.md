# 🚀 Trinity Bot - Setup Guide (Windows)

## שלב 1: התקנת תוכנות בסיס

### Python 3.10+
```powershell
# בדיקה אם Python מותקן
python --version

# אם לא מותקן - הורד מ:
# https://www.python.org/downloads/
# ✅ סמן בהתקנה: "Add Python to PATH"
```

### Node.js 16+
```powershell
# בדיקה אם Node מותקן
node --version
npm --version

# אם לא מותקן - הורד מ:
# https://nodejs.org/
```

### Docker Desktop
```powershell
# בדיקה אם Docker מותקן
docker --version
docker-compose --version

# אם לא מותקן - הורד מ:
# https://www.docker.com/products/docker-desktop/
```

### Git
```powershell
# בדיקה אם Git מותקן
git --version

# אם לא מותקן - הורד מ:
# https://git-scm.com/download/win
```

---

## שלב 2: הורדת הפרויקט

```powershell
# פתח PowerShell במיקום שבו אתה רוצה להוריד
cd C:\Users\YourUsername\Documents

# שכפול הקוד (או העתק ידנית את התיקייה)
# אם יש לך Git repository:
# git clone <repository-url> Arbitrage

# היכנס לתיקיית הפרויקט
cd Arbitrage
```

---

## שלב 3: Backend (Python) - התקנת תלויות

```powershell
# יצירת סביבה וירטואלית
python -m venv venv

# הפעלת הסביבה הוירטואלית
.\venv\Scripts\Activate.ps1

# ⚠️ אם מקבל שגיאת הרשאות, הרץ קודם:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# עדכון pip
python -m pip install --upgrade pip

# התקנת כל החבילות
pip install -r requirements.txt

# בדיקה שהכל עבד
pip list
```

**תלויות שיותקנו:**
- ccxt (חיבור לבורסות)
- aiohttp (בקשות async)
- redis (מסד נתונים)
- pydantic (ולידציות)
- pyyaml (קריאת config)
- pytest (טסטים)

---

## שלב 4: Frontend (React) - התקנת תלויות

```powershell
# פתח טרמינל חדש או המשך באותו טרמינל

# היכנס לתיקיית Frontend
cd frontend

# התקנת כל החבילות
npm install

# בדיקה שהכל עבד
npm list --depth=0

# חזרה לתיקיית הראשית
cd ..
```

**תלויות שיותקנו:**
- react + react-dom
- typescript
- axios
- chart.js (גרפים)
- tailwindcss (עיצוב)

---

## שלב 5: Redis Database

### אופציה 1: Docker (מומלץ)

```powershell
# הרצת Redis דרך Docker
docker-compose up -d redis

# בדיקה שרץ
docker ps

# צריך לראות: trinity-redis
```

### אופציה 2: התקנה ישירה (Windows)

```powershell
# הורד Redis for Windows:
# https://github.com/tporadowski/redis/releases

# או דרך Chocolatey:
choco install redis-64

# הפעלה:
redis-server

# בדיקה (בטרמינל נוסף):
redis-cli ping
# צריך לקבל: PONG
```

---

## שלב 6: קובץ הגדרות (.env)

```powershell
# צור קובץ .env בתיקיית הראשית
# העתק את התבנית הזו:
```

### תוכן קובץ `.env`:

```env
# ========================================
# Trinity Bot - Environment Configuration
# ========================================

# OKX Exchange
OKX_API_KEY=your_okx_api_key_here
OKX_API_SECRET=your_okx_secret_here
OKX_API_PASSPHRASE=your_okx_passphrase_here

# Bybit Exchange
BYBIT_API_KEY=your_bybit_api_key_here
BYBIT_API_SECRET=your_bybit_secret_here

# Binance Exchange
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_API_SECRET=your_binance_secret_here

# Gate.io Exchange
GATEIO_API_KEY=your_gateio_api_key_here
GATEIO_API_SECRET=your_gateio_secret_here

# KuCoin Exchange
KUCOIN_API_KEY=your_kucoin_api_key_here
KUCOIN_API_SECRET=your_kucoin_secret_here
KUCOIN_API_PASSPHRASE=your_kucoin_passphrase_here

# Kraken (optional)
KRAKEN_API_KEY=
KRAKEN_API_SECRET=
```

**💡 איך ליצור API Keys:**
- OKX: Account → API → Create API Key (הרשאות: Trade + Read)
- Bybit: Account → API Management → Create New Key
- Binance: Profile → API Management → Create API
- Gate.io: Account → API Keys → Create
- KuCoin: Account → API Management → Create API

⚠️ **חשוב:** אל תשתף את מפתחות ה-API עם אף אחד!

---

## שלב 7: הרצת הבוט

### טרמינל 1 - Backend (Python Bot)

```powershell
# ודא שהסביבה הוירטואלית פעילה
.\venv\Scripts\Activate.ps1

# ודא ש-Redis רץ
# אם דרך Docker:
docker ps | Select-String redis

# הרצת הבוט
python main.py

# או דרך הסקריפט:
.\run.ps1
```

### טרמינל 2 - Frontend (React Dashboard)

```powershell
# היכנס לתיקיית Frontend
cd frontend

# הרצת שרת הפיתוח
npm start

# הדפדפן יפתח אוטומטית ל:
# http://localhost:3000
```

---

## שלב 8: בדיקת תקינות

```powershell
# בדיקה ש-Redis עובד
redis-cli ping

# הרצת טסטים
.\venv\Scripts\Activate.ps1
pytest tests/ -v

# בדיקת API (בטרמינל נוסף)
curl http://localhost:8000/health

# בדיקת Frontend
# פתח דפדפן: http://localhost:3000
```

---

## 🔧 פתרון בעיות נפוצות

### Python לא מזוהה
```powershell
# הוסף Python ל-PATH ידנית:
# Control Panel → System → Advanced → Environment Variables
# הוסף: C:\Users\YourName\AppData\Local\Programs\Python\Python3XX
```

### שגיאת ExecutionPolicy
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Redis לא מתחבר
```powershell
# בדוק ש-Docker Desktop רץ
# או הרץ Redis ידנית:
redis-server
```

### npm install נכשל
```powershell
# נקה cache
npm cache clean --force
npm install
```

### Port 3000 תפוס
```powershell
# הרוג תהליך על Port 3000
netstat -ano | findstr :3000
taskkill /PID <PID> /F

# או שנה את הפורט ב-frontend:
# ערוך package.json → "start": "PORT=3001 react-scripts start"
```

---

## 📋 צ'קליסט סופי

- [ ] Python 3.10+ מותקן ועובד
- [ ] Node.js 16+ מותקן ועובד
- [ ] Docker Desktop מותקן ורץ
- [ ] `pip install -r requirements.txt` עבר בהצלחה
- [ ] `npm install` בתיקיית frontend עבר בהצלחה
- [ ] Redis רץ (Docker או standalone)
- [ ] קובץ `.env` נוצר עם מפתחות API
- [ ] `python main.py` רץ ללא שגיאות
- [ ] `npm start` רץ והדאשבורד פתוח
- [ ] `pytest tests/ -v` עובר (53/53 tests)

---

## 🎉 סיימת!

הבוט אמור לרוץ עכשיו:
- **Backend API:** http://localhost:8000
- **Frontend Dashboard:** http://localhost:3000
- **Redis:** localhost:6379

---

## 📞 תמיכה

אם יש בעיה:
1. בדוק שכל התוכנות מותקנות (`python --version`, `node --version`, `docker --version`)
2. בדוק שהקבצים `.env` ו-`config.yaml` נכונים
3. הסתכל על השגיאות ב-`logs/` directory
4. הרץ `pytest tests/ -v` לאבחון בעיות

---

**גרסה:** 3.0.0  
**עודכן:** פברואר 2026
