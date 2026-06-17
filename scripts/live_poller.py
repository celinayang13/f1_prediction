"""
Live poller CLI — starts the APScheduler live polling loop for an active race session.

Usage:
    # Poll the current race session (session key from OpenF1 website or API):
    python scripts/live_poller.py --session-key 9158

    # Custom poll interval (default 30 seconds):
    python scripts/live_poller.py --session-key 9158 --interval 15

    # Stop: Ctrl+C (SIGINT) triggers a clean shutdown

The poller will:
  1. Call OpenF1 every --interval seconds for laps, stints, positions, and pit stops.
  2. Dedupe against existing rows in raw_laps by (session_key, driver_number, lap_number).
  3. No-op if the session is outside its active window (±30 minutes around date_start/end).
  4. Continue until Ctrl+C (SIGINT) or SIGTERM.

Prerequisites:
    python scripts/backfill.py --years <year> --rounds <round> --identifiers FP2 FP3 Q
    (backfill qualifying and practice data before starting the race)

Exit codes:
  0: clean exit (Ctrl+C or SIGTERM)
  1: startup error
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

# Ensure src/ is on the path for non-installed runs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from f1_prediction.live.poller import start_poller, stop_poller  # noqa: E402


def main() -> None:
    """CLI entrypoint for the live poller."""
    parser = argparse.ArgumentParser(
        description=(
            "Start the live race poller. Polls OpenF1 every --interval seconds "
            "for an active race session, inserting new laps and positions into "
            "the local SQLite database."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--session-key",
        type=int,
        required=True,
        help=(
            "OpenF1 session_key for the race to poll. "
            "Find this on openf1.org or via: "
            "python -c \"from f1_prediction.live.session_window import find_current_session; "
            "print(find_current_session())\""
        ),
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help=(
            "Poll interval in seconds. "
            "Default 30s is sufficient for live races "
            "(OpenF1 data lag is ~3s; polling more often wastes rate budget)."
        ),
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    logger = logging.getLogger(__name__)
    logger.info(
        "Starting live poller for session %d (interval=%ds)",
        args.session_key, args.interval,
    )

    try:
        scheduler = start_poller(args.session_key, interval_seconds=args.interval)
    except Exception as exc:
        logger.error("Failed to start poller: %s", exc, exc_info=True)
        sys.exit(1)

    # Install SIGINT (Ctrl+C) and SIGTERM handlers for clean shutdown
    def _shutdown(signum, frame):
        logger.info("Signal %d received — stopping poller...", signum)
        stop_poller(scheduler)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (AttributeError, OSError):
        # SIGTERM not available on Windows (expected — project is local-first on macOS/Linux)
        logger.debug("SIGTERM not available on this platform")

    logger.info(
        "Poller running. Press Ctrl+C to stop. "
        "Session %d will be polled every %ds.",
        args.session_key, args.interval,
    )

    # Block main thread until SIGINT/SIGTERM
    # Fallback for platforms without signal.pause() (e.g., Windows)
    try:
        signal.pause()  # Unix: sleep until any signal
    except AttributeError:
        # Windows fallback (unlikely per project constraints, but documented)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            _shutdown(signal.SIGINT, None)


if __name__ == "__main__":
    main()
