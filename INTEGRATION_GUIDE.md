# Trinity Bot - Integration Example

## דוגמה לאינטגרציה של APIPublisher בבוט הקיים

הוסף את הקוד הבא ל-`main.py`:

```python
# בתחילת הקובץ:
from src.api.publisher import APIPublisher

class TrinityEngine:
    def __init__(self, config_path: str = "config.yaml"):
        # ... קוד קיים ...
        self.api_publisher = None
    
    async def start(self):
        # ... קוד קיים ...
        
        # אחרי חיבור Redis:
        self.api_publisher = APIPublisher(self.redis_client)
        logger.info("API Publisher initialized")
        
        # התחל להאזין לפקודות מהממשק
        asyncio.create_task(self._listen_for_commands())
        
        # ... המשך קוד קיים ...
    
    async def _listen_for_commands(self):
        """Listen for commands from web interface"""
        async def handle_command(command):
            action = command.get("action")
            logger.info(f"Received command from web: {action}")
            
            if action == "emergency_stop":
                logger.critical("🚨 EMERGENCY STOP from web interface!")
                await self.stop()
            elif action == "pause":
                # Implement pause logic
                pass
            elif action == "resume":
                # Implement resume logic
                pass
            elif action == "close_position":
                position_id = command.get("position_id")
                # Close specific position
                pass
        
        await self.api_publisher.listen_for_commands(handle_command)
    
    async def _main_loop(self):
        """Main engine loop with API updates"""
        logger.info("Entering main loop...")
        
        while not self._shutdown_event.is_set():
            try:
                # פרסם סטטוס
                exchanges = list(self.exchange_manager.adapters.keys())
                positions_count = len(await self._get_active_positions())
                
                await self.api_publisher.publish_status(
                    running=True,
                    exchanges=exchanges,
                    positions_count=positions_count
                )
                
                # פרסם פוזיציות
                positions = await self._get_active_positions()
                await self.api_publisher.publish_positions(positions)
                
                # פרסם סיכום
                summary = await self._get_summary()
                await self.api_publisher.publish_summary(summary)
                
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(5)
    
    async def _get_active_positions(self):
        """Get current active positions"""
        # דוגמה - התאם לקוד שלך
        positions = []
        
        # Logic to get positions from your execution controller
        # Example:
        # positions = await self.execution_controller.get_positions()
        
        return positions
    
    async def _get_summary(self):
        """Get bot summary statistics"""
        # דוגמה - התאם לקוד שלך
        return {
            "total_pnl": 0.0,
            "total_trades": 0,
            "win_rate": 0.0,
            "active_positions": 0,
            "uptime_hours": (datetime.utcnow() - self.start_time).total_seconds() / 3600
        }
    
    async def _on_trade_closed(self, trade_data):
        """Call this when a trade is closed"""
        if self.api_publisher:
            await self.api_publisher.publish_trade(trade_data)
            await self.api_publisher.publish_pnl(trade_data.get("pnl", 0))
```

## אינטגרציה ב-ExecutionController

הוסף ב-`src/execution/controller.py`:

```python
async def close_position(self, position_id: str):
    """Close a position and notify API"""
    # ... לוגיקה לסגירת פוזיציה ...
    
    trade_data = {
        "id": position_id,
        "symbol": position.symbol,
        "exchanges": {
            "long": position.long_exchange,
            "short": position.short_exchange
        },
        "open_time": position.entry_time,
        "close_time": datetime.utcnow().isoformat(),
        "size": position.size,
        "entry_spread": position.entry_spread,
        "exit_spread": exit_spread,
        "pnl": pnl,
        "pnl_percentage": pnl_percentage,
        "status": "closed"
    }
    
    # Notify API
    if hasattr(self, 'api_publisher') and self.api_publisher:
        await self.api_publisher.publish_trade(trade_data)
        await self.api_publisher.publish_pnl(pnl)
```

## משתנים שצריך לעקוב

ודא שהבוט מעדכן את המידע הבא ב-Redis:

1. **trinity:status** - סטטוס כללי
2. **trinity:positions** - פוזיציות פתוחות
3. **trinity:trades:history** - היסטוריית עסקאות
4. **trinity:pnl:timeseries** - P&L לאורך זמן
5. **trinity:summary** - סיכום כללי
6. **trinity:exchanges** - סטטוס בורסות

כל זה מתבצע אוטומטית דרך `APIPublisher`.

## בדיקה

לאחר האינטגרציה:

1. הפעל את הבוט
2. הפעל את ה-API: `.\run_api.ps1`
3. הפעל את הפרונט: `.\run_frontend.ps1`
4. פתח http://localhost:3000
5. בדוק שאתה רואה נתונים בזמן אמת!
