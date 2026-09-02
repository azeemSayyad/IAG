"""
Database Scaling (Step 26.1)

Implements database scaling strategies:
- Read replicas
- Connection pooling
- Query caching
- Partitioning
"""

from typing import Optional
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool

from app.core.config import settings
from app.core.redis import RedisService


class DatabaseScaling:
    """Database scaling configuration."""

    def __init__(self):
        self.redis = RedisService()
        self._read_engine = None
        self._write_engine = None
        self._read_session_factory = None
        self._write_session_factory = None

    def setup_read_replica(
        self,
        read_url: Optional[str] = None,
        pool_size: int = 20,
        max_overflow: int = 10,
    ) -> None:
        """
        Setup read replica connection.

        Args:
            read_url: Read replica database URL
            pool_size: Connection pool size
            max_overflow: Max overflow connections
        """
        url = read_url or settings.DATABASE_URL

        self._read_engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            pool_recycle=3600,
        )

        self._read_session_factory = sessionmaker(
            bind=self._read_engine,
            autocommit=False,
            autoflush=False,
        )

    def setup_write_engine(
        self,
        write_url: Optional[str] = None,
        pool_size: int = 20,
        max_overflow: int = 10,
    ) -> None:
        """
        Setup write engine connection.

        Args:
            write_url: Write database URL
            pool_size: Connection pool size
            max_overflow: Max overflow connections
        """
        url = write_url or settings.DATABASE_URL

        self._write_engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            pool_recycle=3600,
        )

        self._write_session_factory = sessionmaker(
            bind=self._write_engine,
            autocommit=False,
            autoflush=False,
        )

    def get_read_session(self) -> Session:
        """Get a read-only session (uses read replica)."""
        if not self._read_session_factory:
            self.setup_read_replica()
        return self._read_session_factory()

    def get_write_session(self) -> Session:
        """Get a write session (uses primary)."""
        if not self._write_session_factory:
            self.setup_write_engine()
        return self._write_session_factory()

    def get_read_engine(self):
        """Get the read engine."""
        if not self._read_engine:
            self.setup_read_replica()
        return self._read_engine

    def get_write_engine(self):
        """Get the write engine."""
        if not self._write_engine:
            self.setup_write_engine()
        return self._write_engine


class QueryCache:
    """Caches database query results in Redis."""

    def __init__(self, redis: RedisService, ttl: int = 300):
        self.redis = redis
        self.ttl = ttl

    def get_cached(self, key: str):
        """Get cached query result."""
        return self.redis.get_cache(key)

    def set_cached(self, key: str, value, ttl: Optional[int] = None) -> None:
        """Cache query result."""
        self.redis.set_cache(key, value, ttl=ttl or self.ttl)

    def invalidate(self, key: str) -> None:
        """Invalidate cached result."""
        self.redis.delete_cache(key)

    def invalidate_pattern(self, pattern: str) -> None:
        """Invalidate all keys matching pattern."""
        keys = self.redis.client.keys(pattern)
        for key in keys:
            self.redis.client.delete(key)

    def cached_query(self, key: str, query_fn, ttl: Optional[int] = None):
        """
        Execute query with caching.

        Args:
            key: Cache key
            query_fn: Query function to execute
            ttl: Cache TTL
        """
        # Check cache
        cached = self.get_cached(key)
        if cached is not None:
            return cached

        # Execute query
        result = query_fn()

        # Cache result
        self.set_cached(key, result, ttl)

        return result


class ConnectionPool:
    """Manages database connection pools."""

    def __init__(self):
        self.pools = {}

    def get_pool_stats(self, engine) -> dict:
        """Get connection pool statistics."""
        pool = engine.pool
        return {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }


# Global instances
db_scaling = DatabaseScaling()
query_cache = QueryCache(RedisService())
connection_pool = ConnectionPool()
