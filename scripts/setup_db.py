"""
Database Setup Script
Creates all tables and initializes the database
"""

import asyncio
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.core.config import get_config, init_config
from src.core.logging import get_logger, init_logging
from src.storage.models import Base

logger = init_logging()


def create_database():
    """Create database and all tables"""
    init_config("config.yaml")
    config = get_config()
    
    logger.info("=" * 80)
    logger.info("Trinity Database Setup")
    logger.info("=" * 80)
    
    try:
        # Create engine
        engine = create_engine(
            config.database.dsn,
            echo=config.database.echo_sql
        )
        
        logger.info(f"Connecting to: {config.database.host}:{config.database.port}/{config.database.database}")
        
        # Create all tables
        logger.info("Creating tables...")
        Base.metadata.create_all(engine)
        
        # Create TimescaleDB hypertable if possible
        try:
            with engine.connect() as conn:
                # Check if TimescaleDB extension exists
                result = conn.execute(text(
                    "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')"
                ))
                has_timescale = result.scalar()
                
                if has_timescale:
                    logger.info("TimescaleDB detected, creating hypertable...")
                    
                    # Convert system_metrics to hypertable
                    conn.execute(text(
                        "SELECT create_hypertable('system_metrics', 'timestamp', if_not_exists => TRUE)"
                    ))
                    conn.commit()
                    
                    logger.info("✅ Hypertable created for system_metrics")
                else:
                    logger.warning("TimescaleDB not installed, skipping hypertable creation")
        except Exception as e:
            logger.warning(f"Could not create hypertable: {e}")
        
        # Create indexes
        logger.info("Creating indexes...")
        
        # Verify tables
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ))
            tables = [row[0] for row in result]
            
            logger.info(f"Created tables: {', '.join(tables)}")
        
        logger.info("=" * 80)
        logger.info("✅ Database setup completed successfully")
        logger.info("=" * 80)
        
        return True
        
    except Exception as e:
        logger.error(f"Database setup failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = create_database()
    sys.exit(0 if success else 1)
