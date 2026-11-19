"""Memory patterns for the realistic-workload tests (pattern-lab namespace).

Each mode is a pure function "how many MiB should the container hold at time t" (target_mib); the loop
allocates or frees 1 MiB blocks to match it, so memory can rise, fall and jump. Blocks are written, not
just reserved, so they count toward the working set; freed blocks are returned to the OS (large
allocations are separate mappings), so memory really goes down. The container is OOMKilled when the
pattern crosses its limit: that is the ground truth.

MODE=cycle: BASE + LEAK per minute + AMP * sin(2*pi*t/PERIOD)   (a daily-like cycle, compressed)
MODE=noisy: BASE + LEAK per minute + Gaussian noise (SD) + rare SPIKE for SPIKE_S seconds
MODE=step:  BASE + STEP every STEP_PERIOD seconds, flat in between   (a cache filling in batches)
"""
import math
import os
import random
import time

MiB = 1024 * 1024


def env(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def target_mib(mode: str, t: float, rng: random.Random, state: dict) -> float:
    if mode == "cycle":
        return env("BASE_MIB", 40) + env("LEAK_MIB_PER_MIN", 2) * t / 60 + \
            env("AMP_MIB", 30) * math.sin(2 * math.pi * t / env("PERIOD_S", 1200))
    if mode == "noisy":
        base = env("BASE_MIB", 30) + env("LEAK_MIB_PER_MIN", 3) * t / 60 + rng.gauss(0, env("SD_MIB", 6))
        if t >= state.get("spike_until", -1) and rng.random() < env("SPIKE_PROB", 0.02):
            state["spike_until"] = t + env("SPIKE_S", 30)
        return base + (env("SPIKE_MIB", 40) if t < state.get("spike_until", -1) else 0)
    if mode == "step":
        return env("BASE_MIB", 20) + env("STEP_MIB", 25) * math.floor(t / env("STEP_PERIOD_S", 300))
    raise ValueError(f"unknown MODE {mode!r}")


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), message, flush=True)


def main() -> None:
    mode, tick = os.environ.get("MODE", "cycle"), env("INTERVAL_S", 10)
    rng, state, blocks = random.Random(), {}, []
    start, last_log = time.monotonic(), -60.0
    log(f"mode={mode} interval_s={tick}")
    while True:
        t = time.monotonic() - start
        want = max(0, int(round(target_mib(mode, t, rng, state))))
        while len(blocks) < want:
            blocks.append(bytearray(b"\x01") * MiB)       # write every byte: resident memory
        while len(blocks) > want:
            blocks.pop()                                   # freed and returned to the OS
        if t - last_log >= 60:
            log(f"t={t:.0f}s held_mib={len(blocks)}")
            last_log = t
        time.sleep(tick)


if __name__ == "__main__":
    main()
