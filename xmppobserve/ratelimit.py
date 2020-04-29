import array
import asyncio
import functools
import logging
import math
import time

from quart import request, abort, current_app


class RateLimiter:
    def __init__(self, max_rps, dt_ceil, alpha, nbuckets):
        init = [float("nan")] * nbuckets
        self._dt_ceil = dt_ceil
        self._max_rps = max_rps
        self._min_dt = 1/max_rps
        self._alpha = alpha
        self._ialpha = 1-alpha
        self._buckets_last_timestamp = array.array("d", init)
        self._buckets_dt = array.array("d", init)
        self._nbuckets = nbuckets

    def _bucket_index(self, hashcode):
        return hashcode % self._nbuckets

    def _advance(self, bucket_index, timestamp):
        prev_ts = self._buckets_last_timestamp[bucket_index]
        if math.isnan(prev_ts):
            return None, None, None
        this_dt = min(timestamp - prev_ts, self._dt_ceil)
        bucket_dt = self._buckets_dt[bucket_index]
        if math.isnan(bucket_dt):
            new_dt = this_dt
        else:
            new_dt = bucket_dt * self._alpha + this_dt * self._ialpha
        return prev_ts, bucket_dt, new_dt

    def preflight(self, hashcode, timestamp):
        index = self._bucket_index(hashcode)
        _, _, dt = self._advance(index, timestamp)
        if dt is None:
            return True
        return dt >= self._min_dt

    def pay(self, hashcode, timestamp):
        index = self._bucket_index(hashcode)
        prev_ts, _, dt = self._advance(index, timestamp)
        if dt is not None:
            self._buckets_dt[index] = dt
        self._buckets_last_timestamp[index] = timestamp
        if dt is None:
            return True
        return dt >= self._min_dt


def _make_coroutine_function(f):
    if not asyncio.iscoroutinefunction(f):
        @functools.wraps(f)
        async def fprime(*args, **kwargs):
            return f(*args, **kwargs)
        return fprime
    return f


def rate_limiter_plugin(*args, **kwargs):
    def decorator(f):
        f = _make_coroutine_function(f)
        return RateLimitPlugin(*args, keyfunc=f, **kwargs)
    return decorator


class RateLimitPlugin:
    APP_ATTR_RATELIMITERS = "xmppobserve_ratelimiters"

    def __init__(self, config_key, keyfunc, *,
                 fixed_buckets=None,
                 default_buckets=2**8,
                 app=None):
        super().__init__()
        self._config_key = config_key
        self._keyfunc = keyfunc
        self._fixed_buckets = fixed_buckets
        self._default_buckets = default_buckets
        if app is not None:
            self.init_app(app)

    def __str__(self):
        return "<{}.{} keyfunc={}>".format(
            type(self).__module__,
            type(self).__qualname__,
            self._keyfunc,
        )

    def init_app(self, app):
        app_ratelimiters = getattr(app, self.APP_ATTR_RATELIMITERS, {})
        setattr(app, self.APP_ATTR_RATELIMITERS, app_ratelimiters)
        cfg = app.config.get(self._config_key, {})
        max_rps = cfg.get("MAX_REQUESTS_PER_SECOND", 1.0)
        dt_ceil = cfg.get("MAX_INTERVAL", 60)
        alpha = cfg.get("ALPHA", 0.9)

        if self._fixed_buckets is not None:
            nbuckets = self._fixed_buckets
        else:
            nbuckets = cfg.get("BUCKETS", self._default_buckets)

        app_ratelimiters[self] = RateLimiter(max_rps, dt_ceil, alpha, nbuckets)

    def get_ratelimiter(self):
        return getattr(current_app, self.APP_ATTR_RATELIMITERS)[self]

    def ratelimited(self, f):
        f = _make_coroutine_function(f)

        @functools.wraps(f)
        async def wrapped(*args, **kwargs):
            k = self.get_current_key()
            khash = hash(k)
            ratelimiter = self.get_ratelimiter()
            timestamp = time.monotonic()
            if not ratelimiter.preflight(khash):
                return abort(429)
            if not ratelimiter.pay(khash):
                return abort(429)
            return await f(*args, **kwargs)

        return wrapped

    async def get_current_key(self):
        k = await self._keyfunc()
        # we restrict the input here to prevent stupid mistakes
        assert isinstance(k, (bytes, str, int))
        return k

    def preflight(self, key, timestamp):
        return self.get_ratelimiter().preflight(hash(key), timestamp)

    def pay(self, key, timestamp):
        return self.get_ratelimiter().pay(hash(key), timestamp)


def multi_rate_limit(*plugins):
    def decorator(f):
        f = _make_coroutine_function(f)

        @functools.wraps(f)
        async def wrapped(*args, **kwargs):
            timestamp = time.monotonic()
            plugin_keys = [await plugin.get_current_key()
                           for plugin in plugins]
            for key, plugin in zip(plugin_keys, plugins):
                if not plugin.preflight(key, timestamp):
                    return abort(429)

            def pay():
                ok = True
                for key, plugin in zip(plugin_keys, plugins):
                    if not plugin.pay(key, timestamp):
                        logging.debug(
                            "rate-limit tripped despite preflight, ignoring"
                        )
                        ok = False
                return ok

            return await f(*args, ratelimit_pay_func=pay, **kwargs)

        return wrapped
    return decorator
