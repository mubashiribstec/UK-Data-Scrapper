import time
import random
from collections import defaultdict


class RateLimiter:
    def __init__(self, domain_delays: dict):
        self.delays = domain_delays
        self.last_request = defaultdict(float)

    def wait(self, domain: str):
        delay = self.delays.get(domain, 2.0)
        jitter = random.uniform(0, delay * 0.3)
        elapsed = time.time() - self.last_request[domain]
        sleep_time = delay + jitter - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)
        self.last_request[domain] = time.time()
