"""Memory load generator for the prediction scenarios (predict-lab namespace).

MODE=ramp: allocate STEP_MIB every INTERVAL_S seconds until the container is
           OOMKilled (a leak-like workload; ground truth = the OOMKill time).
MODE=flat: hold BASE_MIB, toggling an extra JITTER_MIB every JITTER_PERIOD_S
           seconds (a steady, hot workload that never OOMs; false-alarm test).

Memory is written, not just reserved, so it counts toward the container's
working set. Every change is logged with a UTC timestamp.
"""
import os
import time

MiB = 1024 * 1024


def allocate(mib: int) -> bytearray:
    return bytearray(b"\x01") * (mib * MiB)  # writes every byte, so pages are resident


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), message, flush=True)


def ramp() -> None:
    step = int(os.environ.get("STEP_MIB", "2"))
    interval = float(os.environ.get("INTERVAL_S", "10"))
    held, total = [], 0
    log(f"mode=ramp step_mib={step} interval_s={interval}")
    while True:
        time.sleep(interval)
        held.append(allocate(step))
        total += step
        log(f"allocated_mib={total}")


def flat() -> None:
    base = int(os.environ.get("BASE_MIB", "195"))
    jitter = int(os.environ.get("JITTER_MIB", "8"))
    period = float(os.environ.get("JITTER_PERIOD_S", "30"))
    held = allocate(base)
    extra = None
    log(f"mode=flat base_mib={base} jitter_mib={jitter} period_s={period}")
    while True:
        time.sleep(period)
        extra = None if extra is not None else allocate(jitter)
        log(f"allocated_mib={base + (jitter if extra is not None else 0)}")


if __name__ == "__main__":
    {"ramp": ramp, "flat": flat}[os.environ.get("MODE", "ramp")]()
