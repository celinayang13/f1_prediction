"""
One-off migration: re-tag OpenF1 sprint sessions stored with the coarse
OpenF1 session_type ('Race' / 'Qualifying') to the canonical 'Sprint' /
'Sprint Shootout', matching ingest/openf1_loader.py::_canonical_session_type
and the 2022 FastF1 rows. Also drops feature and prediction rows that were
built for sprints as if they were Grands Prix.

Idempotent. Back up the database first; then rerun scripts/build_features.py
(cross-race aggregates included sprint results).

Usage:
    python scripts/migrate_sprint_session_types.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from f1_prediction.db.engine import get_engine  # noqa: E402

_NOT_GP = "(SELECT session_key FROM raw_sessions WHERE session_type != 'Race')"


def main() -> None:
    with get_engine().begin() as conn:
        for label, sql in [
            ("sprints re-tagged", "UPDATE raw_sessions SET session_type = 'Sprint' "
                "WHERE session_type = 'Race' AND session_name = 'Sprint'"),
            ("sprint qualifying re-tagged", "UPDATE raw_sessions SET session_type = 'Sprint Shootout' "
                "WHERE session_type = 'Qualifying' "
                "AND session_name IN ('Sprint Qualifying', 'Sprint Shootout')"),
            ("features_prerace rows dropped", f"DELETE FROM features_prerace WHERE session_key IN {_NOT_GP}"),
            ("features_inrace rows dropped", f"DELETE FROM features_inrace WHERE session_key IN {_NOT_GP}"),
            ("predictions rows dropped", f"DELETE FROM predictions WHERE session_key IN {_NOT_GP}"),
        ]:
            print(f"{label}: {conn.execute(text(sql)).rowcount}")


if __name__ == "__main__":
    main()
