"""Bound SearXNG requests to configured engines, with per-instance cooldowns."""
import time


class EngineHealth:
    def __init__(self):
        self.states = {}

    def select(self, instance, configured):
        now = time.monotonic()
        engines = list(dict.fromkeys(e.strip() for e in configured.split(",") if e.strip()))
        selected = []
        probing = False
        for engine in engines:
            state = self.states.get((instance, engine))
            if state is None:
                selected.append(engine)
            elif now >= state["until"] and now >= state["probe_until"] and not probing:
                # One recovery probe per request; a lease also bounds concurrent probes.
                state["probe_until"] = now + 20
                selected.append(engine)
                probing = True
        return selected

    def record(self, instance, selected, unresponsive):
        failures = {str(row[0]): str(row[1]) for row in unresponsive
                    if isinstance(row, (list, tuple)) and len(row) >= 2}
        now = time.monotonic()
        for engine in selected:
            key = (instance, engine)
            reason = failures.get(engine)
            if reason is None:
                self.states.pop(key, None)
                continue
            seconds = 300 if any(word in reason.lower() for word in ("captcha", "suspended", "429")) else 60
            self.states[key] = {"until": now + seconds, "probe_until": 0}
        # Config churn must not create an unbounded process cache.
        if len(self.states) > 512:
            self.states = dict(sorted(self.states.items(), key=lambda item: item[1]["until"])[-512:])


engine_health = EngineHealth()
