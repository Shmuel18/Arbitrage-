# 🚀 התקנה מהירה - פקודות להעתקה

## אופציה 1️⃣: התקנה אוטומטית (מומלץ)

```powershell
# פתח PowerShell בתיקיית הפרויקט והרץ:
.\install.ps1
```

---

## אופציה 2️⃣: התקנה ידנית - צעד אחר צעד

### 1. בדיקת תוכנות בסיס

```powershell
# בדוק שהכל מותקן
python --version    # צריך 3.10+
node --version      # צריך 16+
npm --version
docker --version
git --version
```

**אם חסר משהו:**

- Python: https://www.python.org/downloads/
- Node.js: https://nodejs.org/
- Docker: https://www.docker.com/products/docker-desktop/

---

### 2. Backend - Python

```powershell
# יצירת סביבה וירטואלית
python -m venv venv

# הפעלה (אם יש שגיאת הרשאות, קודם הרץ את השורה למטה)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# הפעלת הסביבה
.\venv\Scripts\Activate.ps1

# עדכון pip
python -m pip install --upgrade pip

# התקנת תלויות (לוקח 1-2 דקות)
pip install -r requirements.txt

# בדיקה
pip list
```

---

### 3. Frontend - React

```powershell
# כניסה לתיקייה
cd frontend

# התקנת תלויות (לוקח 2-3 דקות)
npm install

# חזרה לתיקיית הראשית
cd ..
```

---

### 4. Redis Database

**דרך Docker (מומלץ):**

```powershell
# הפעלת Redis
docker-compose up -d redis

# בדיקה שרץ
docker ps
```

**דרך התקנה ישירה:**

```powershell
# הורד מ: https://github.com/tporadowski/redis/releases
# או:
choco install redis-64

# הרצה
redis-server

# בדיקה (בטרמינל נוסף)
redis-cli ping
# צריך לקבל: PONG
```

---

### 5. קובץ הגדרות

```powershell
# צור קובץ .env בתיקיית הראשית
notepad .env
```

**העתק את זה לתוך הקובץ:**

```env
# OKX
OKX_API_KEY=your_key_here
OKX_API_SECRET=your_secret_here
OKX_API_PASSPHRASE=your_passphrase_here

# Bybit
BYBIT_API_KEY=your_key_here
BYBIT_API_SECRET=your_secret_here

# Binance
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here

# Gate.io
GATEIO_API_KEY=your_key_here
GATEIO_API_SECRET=your_secret_here

# KuCoin
KUCOIN_API_KEY=your_key_here
KUCOIN_API_SECRET=your_secret_here
KUCOIN_API_PASSPHRASE=your_passphrase_here
```

**שמור (Ctrl+S) וסגור**

---

### 6. הרצת הבוט

**טרמינל 1 - Backend:**

```powershell
# הפעל את הסביבה הוירטואלית
.\venv\Scripts\Activate.ps1

# הרץ את הבוט
python main.py

# או:
.\run.ps1
```

**טרמינל 2 - Frontend:**

```powershell
# כניסה לתיקייה
cd frontend

# הרצת שרת הפיתוח
npm start
```

הדפדפן יפתח אוטומטית ל: **http://localhost:3000**

---

### 7. בדיקת תקינות

```powershell
# בדיקת Redis
redis-cli ping

# הרצת טסטים
.\venv\Scripts\Activate.ps1
pytest tests/ -v

# צריך לראות: 53 passed
```

---

## 🆘 פתרון בעיות

### Python לא נמצא

```powershell
# הוסף ל-PATH:
# הגדרות → מערכת → משתני סביבה → Path → עריכה
# הוסף: C:\Users\YourName\AppData\Local\Programs\Python\Python3XX
```

### שגיאת ExecutionPolicy

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### npm install נכשל

```powershell
# נקה cache
npm cache clean --force
cd frontend
npm install
```

### Redis לא עובד

```powershell
# בדוק ש-Docker Desktop רץ
# או הרץ Redis ידנית:
redis-server
```

### Port כבר בשימוש

```powershell
# מצא את התהליך
netstat -ano | findstr :3000

# הרוג אותו
taskkill /PID <מספר_התהליך> /F
```

---

## ✅ צ'קליסט התקנה

- [ ] Python 3.10+ מותקן
- [ ] Node.js 16+ מותקן
- [ ] Docker Desktop מותקן
- [ ] `pip install -r requirements.txt` עבד
- [ ] `npm install` בתיקיית frontend עבד
- [ ] Redis רץ
- [ ] קובץ `.env` קיים עם מפתחות API
- [ ] `python main.py` רץ ללא שגיאות
- [ ] `npm start` פותח את הדאשבורד
- [ ] כל 53 הטסטים עוברים

---

## 🎯 פקודות שימוש יומיומי

```powershell
# התחלת הבוט
.\venv\Scripts\Activate.ps1
python main.py

# התחלת Frontend
cd frontend
npm start

# הרצת טסטים
pytest tests/ -v

# עצירת Redis (Docker)
docker-compose down

# הצגת לוגים
Get-Content logs\trinity.log -Tail 50

# ניקוי Redis
redis-cli FLUSHALL
```

---

## 📋 קבצים חשובים

| קובץ                    | תיאור                              |
| ----------------------- | ---------------------------------- |
| `config.yaml`           | הגדרות הבוט (בורסות, סיכון, מינוף) |
| `.env`                  | מפתחות API (אל תשתף!)              |
| `main.py`               | נקודת כניסה ראשית                  |
| `requirements.txt`      | תלויות Python                      |
| `frontend/package.json` | תלויות React                       |
| `docker-compose.yml`    | הגדרות Redis                       |
| `logs/`                 | קבצי לוג                           |

---

## 🔐 אבטחה

⚠️ **חשוב מאוד:**

- אל **תשתף** את קובץ `.env`
- אל **תעלה** ל-GitHub ציבורי
- השתמש **רק** ב-API keys עם הרשאות Trade + Read
- הפעל **2FA** בכל הבורסות
- התחל עם **סכומים קטנים**

---

## 📞 לעזרה

1. בדוק את `logs/trinity.log`
2. הרץ `pytest tests/ -v` לאבחון
3. ודא ש-`.env` ו-`config.yaml` נכונים
4. בדוק ש-Redis רץ
5. בדוק שיש אינטרנט ויכולת גישה לבורסות

---

**גרסה:** 3.0.0  
**תאריך:** פברואר 2026
