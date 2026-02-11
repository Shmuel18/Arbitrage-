# 🐳 התקנת Docker Desktop - מדריך מהיר

## 📥 הורדה והתקנה

### שלב 1: הורד Docker Desktop

https://www.docker.com/products/docker-desktop/

### שלב 2: התקן

1. הרץ את הקובץ שהורדת
2. בחר בהגדרות ברירת המחדל
3. **אתחל את המחשב** לאחר ההתקנה

### שלב 3: הפעל Docker Desktop

1. פתח Docker Desktop מתפריט ההתחלה
2. המתן עד שהאייקון למטה משתנה לירוק ✅
3. זה לוקח בערך 1-2 דקות בפעם הראשונה

### שלב 4: הרץ Redis

```bash
cd C:\Users\shh92\Documents\Arbitrage
docker-compose up -d redis
```

### שלב 5: בדיקה

```bash
docker ps
```

אמור להראות:

```
CONTAINER ID   IMAGE         PORTS                    STATUS
xxxxx          redis:7       0.0.0.0:6379->6379/tcp   Up
```

---

## ✅ לאחר מכן הרץ את הבוט:

```bash
python main.py
```

---

## ⚠️ אם Docker Desktop איטי/כבד

Docker Desktop צורך הרבה משאבים. אם המחשב שלך לא חזק מספיק, עבור לאפשרות 2.
