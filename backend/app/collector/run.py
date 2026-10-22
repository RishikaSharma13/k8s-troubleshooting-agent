"""Run the memory collector in a loop.

Usage (from the repository root):
    PYTHONPATH=backend python -m app.collector.run --namespace default --interval 15
    PYTHONPATH=backend python -m app.collector.run --namespace default --namespace predict-lab

Writes to the database named by DATABASE_URL. Stop with Ctrl+C.
"""
import argparse
import time

from loguru import logger
from sqlalchemy.engine import make_url

from app.collector.memory_collector import MemoryCollector
from app.collector.signals import SignalCollector
from app.models.database import engine


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect container memory samples from the Metrics API.")
    parser.add_argument("--namespace", action="append", dest="namespaces",
                        help="namespace to collect (repeat for several; default: default)")
    parser.add_argument("--interval", type=float, default=15.0,
                        help="seconds between cycles (default 15, Metrics Server's resolution in this lab)")
    parser.add_argument("--cycles", type=int, default=0, help="stop after N cycles (default: run until Ctrl+C)")
    args = parser.parse_args(argv)
    if args.interval < 1:
        parser.error("--interval must be at least 1 second")
    namespaces = args.namespaces or ["default"]

    logger.info("Memory collector starting: namespaces={} interval={}s database={}",
                namespaces, args.interval, make_url(str(engine.url)).database)
    collector = MemoryCollector()
    signals = SignalCollector()
    cycle = 0
    try:
        while True:
            started = time.monotonic()
            cycle += 1
            for namespace in namespaces:
                collector.collect_once(namespace)
                try:  # the newer signal collection must never stop memory collection
                    signals.collect_once(namespace)
                except Exception as exc:
                    logger.exception("Signal collection crashed for {}: {}", namespace, exc)
            if args.cycles and cycle >= args.cycles:
                break
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        logger.info("Memory collector stopped after {} cycle(s)", cycle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
