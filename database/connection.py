"""
Database connection manager.

Handles connections to SQLite or PostgreSQL databases with
connection pooling, retry logic, and health checks.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """
    Manages database connections and sessions.
    
    Supports both SQLite and PostgreSQL with automatic provider selection
    based on configuration.
    """

    def __init__(self) -> None:
        self.config = settings.db
        self.engine: Optional[Engine] = None
        self.session_factory: Optional[sessionmaker] = None
        self._initialize()

    def _initialize(self) -> None:
        """Initialize the database engine and session factory."""
        try:
            # Create engine with appropriate settings
            if self.config.provider == "postgresql":
                self.engine = create_engine(
                    self.config.connection_string,
                    pool_size=10,
                    max_overflow=20,
                    pool_pre_ping=True,
                    pool_recycle=3600,
                    echo=settings.debug,
                )
            else:
                # SQLite settings
                self.engine = create_engine(
                    self.config.connection_string,
                    connect_args={"check_same_thread": False},
                    pool_pre_ping=True,
                    echo=settings.debug,
                )

            self.session_factory = sessionmaker(
                bind=self.engine,
                autocommit=False,
                autoflush=False,
            )

            # SQLite optimizations
            if self.config.provider == "sqlite":
                self._optimize_sqlite()

            logger.info(
                f"Database initialized: {self.config.provider} @ "
                f"{self.config.sqlite_path if self.config.provider == 'sqlite' else self.config.postgres_db}"
            )

        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            raise

    def _optimize_sqlite(self) -> None:
        """Apply SQLite performance optimizations."""
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
            cursor.close()

    def create_tables(self) -> None:
        """Create all database tables."""
        from database.models import Base

        try:
            Base.metadata.create_all(self.engine)
            logger.info("Database tables created successfully")
        except SQLAlchemyError as e:
            logger.error(f"Error creating tables: {e}")
            raise

    def drop_tables(self) -> None:
        """Drop all database tables. Use with caution."""
        from database.models import Base

        try:
            Base.metadata.drop_all(self.engine)
            logger.warning("All database tables dropped")
        except SQLAlchemyError as e:
            logger.error(f"Error dropping tables: {e}")
            raise

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Provide a transactional scope around a series of operations.
        
        Usage:
            with db.session() as session:
                session.add(obj)
                session.commit()
        """
        if not self.session_factory:
            raise RuntimeError("Database not initialized")

        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Session rollback due to error: {e}")
            raise
        finally:
            session.close()

    def health_check(self) -> bool:
        """Check database connectivity."""
        try:
            with self.session() as session:
                session.execute(text("SELECT 1"))
                return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    def get_stats(self) -> dict:
        """Get database statistics."""
        from database.models import PipelineLog, TrackedVideo

        stats = {
            "provider": self.config.provider,
            "connection_string": self.config.connection_string.replace(
                self.config.postgres_password, "***"
            ) if self.config.provider == "postgresql" else self.config.connection_string,
        }

        try:
            with self.session() as session:
                stats["tracked_videos_count"] = session.query(TrackedVideo).count()
                stats["logs_count"] = session.query(PipelineLog).count()

                # Get success rate
                total_logs = session.query(PipelineLog).count()
                if total_logs > 0:
                    success_logs = (
                        session.query(PipelineLog)
                        .filter(PipelineLog.success == True)
                        .count()
                    )
                    stats["success_rate"] = f"{(success_logs / total_logs * 100):.1f}%"
                else:
                    stats["success_rate"] = "N/A"

        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            stats["error"] = str(e)

        return stats

    def close(self) -> None:
        """Close database connections."""
        if self.engine:
            self.engine.dispose()
            logger.info("Database connections closed")

    def __enter__(self) -> DatabaseManager:
        return self

    def __exit__(self, *args) -> None:
        self.close()


# Global database manager instance
db_manager = DatabaseManager()
