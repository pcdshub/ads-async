import datetime
import threading


class ThreadsafeCounter:
    """
    A thread-safe counter with rollover and clash-prevention.

    Note: if necessary, use the counter lock to keep the dont_clash_with object
    in sync.
    """

    # Borrowed from caproto

    def __init__(self, *, initial_value=-1, dont_clash_with=None, max_count=2**32):
        if dont_clash_with is None:
            dont_clash_with = set()

        self.value = initial_value
        self.lock = threading.RLock()
        self.dont_clash_with = dont_clash_with
        self._max_count = max_count

    def __call__(self):
        "Get next ID, wrapping around at 2**16, ensuring no clashes"
        with self.lock:
            value = self.value + 1

            while value in self.dont_clash_with or value >= self._max_count:
                value = 0 if value >= self._max_count else value + 1

            self.value = value
            return self.value


UNIX_EPOCH_START = 116444736000000000  # January 1, 1970


def get_datetime_from_timestamp(timestamp: int) -> datetime.datetime:
    """Get a datetime.datetime instance from a TwinCAT timestamp."""
    return datetime.datetime.utcfromtimestamp((timestamp - UNIX_EPOCH_START) * 1e-7)
