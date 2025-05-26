"""Rate limiting functionality for MCP server."""

import asyncio
import time
from collections import defaultdict, deque
from typing import Dict, Optional


class RateLimiter:
    """Token bucket rate limiter for MCP requests."""
    
    def __init__(self, requests_per_minute: int = 60, burst_size: int = 10):
        """
        Initialize rate limiter.
        
        Args:
            requests_per_minute: Maximum requests per minute per client
            burst_size: Maximum burst requests allowed
        """
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self.refill_rate = requests_per_minute / 60.0  # tokens per second
        
        # Track tokens and last refill time per client
        self.client_tokens: Dict[str, float] = defaultdict(lambda: burst_size)
        self.last_refill: Dict[str, float] = defaultdict(time.time)
        
        # Track request history for monitoring
        self.request_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        
        self._lock = asyncio.Lock()
    
    async def is_allowed(self, client_id: str = "default") -> bool:
        """
        Check if a request is allowed for the given client.
        
        Args:
            client_id: Identifier for the client (e.g., IP address, session ID)
            
        Returns:
            True if request is allowed, False if rate limited
        """
        async with self._lock:
            now = time.time()
            
            # Refill tokens based on time elapsed
            time_elapsed = now - self.last_refill[client_id]
            tokens_to_add = time_elapsed * self.refill_rate
            
            self.client_tokens[client_id] = min(
                self.burst_size,
                self.client_tokens[client_id] + tokens_to_add
            )
            self.last_refill[client_id] = now
            
            # Check if we have tokens available
            if self.client_tokens[client_id] >= 1.0:
                self.client_tokens[client_id] -= 1.0
                self.request_history[client_id].append(now)
                return True
            
            return False
    
    def get_client_stats(self, client_id: str = "default") -> Dict:
        """Get statistics for a client."""
        now = time.time()
        history = self.request_history[client_id]
        
        # Count requests in last minute
        recent_requests = sum(1 for req_time in history if now - req_time <= 60)
        
        return {
            "client_id": client_id,
            "tokens_remaining": self.client_tokens[client_id],
            "requests_last_minute": recent_requests,
            "total_requests": len(history),
            "rate_limit": self.requests_per_minute,
            "burst_size": self.burst_size
        }
    
    def reset_client(self, client_id: str):
        """Reset rate limiting for a specific client."""
        self.client_tokens[client_id] = self.burst_size
        self.last_refill[client_id] = time.time()
        self.request_history[client_id].clear()


class RateLimitExceeded(Exception):
    """Exception raised when rate limit is exceeded."""
    
    def __init__(self, retry_after: float, client_stats: Dict):
        self.retry_after = retry_after
        self.client_stats = client_stats
        super().__init__(f"Rate limit exceeded. Retry after {retry_after:.1f} seconds") 