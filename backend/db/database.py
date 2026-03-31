import aiosqlite
import json
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "openclaw.db"


async def _migrate_signals_columns(db):
    cur = await db.execute("PRAGMA table_info(signals)")
    rows = await cur.fetchall()
    colnames = {r[1] for r in rows}

    # Old schema used instrument_name TEXT NOT NULL (single-expiry design).
    # New schema uses instrument_t1 / instrument_t2 (two-bracket interpolation).
    # If the old column exists we rebuild the table — SQLite can't DROP NOT NULL in-place.
    if "instrument_name" in colnames:
        await db.execute("ALTER TABLE signals RENAME TO signals_v1_backup")
        await db.execute("""
            CREATE TABLE signals (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_t1           TEXT,
                instrument_t2           TEXT,
                interp_method           TEXT,
                interp_weight_w         REAL,
                polymarket_market_id    TEXT NOT NULL,
                polymarket_question     TEXT,
                option_type             TEXT,
                spot_price              REAL,
                strike                  REAL,
                t_poly_days             REAL,
                T1_days                 REAL,
                T2_days                 REAL,
                sigma_t1                REAL,
                sigma_t2                REAL,
                sigma_interp            REAL,
                delta                   REAL,
                gamma                   REAL,
                vega                    REAL,
                theta                   REAL,
                rho                     REAL,
                deribit_prob            REAL,
                polymarket_price        REAL,
                edge_pct                REAL,
                abs_edge_pct            REAL,
                direction               TEXT,
                payout_ratio            REAL,
                asymmetric_payout       INTEGER,
                has_alpha               INTEGER,
                liquidity_usd           REAL,
                reasoning               TEXT,
                scanned_at              TEXT,
                raw_json                TEXT
            )
        """)
        # Migrate compatible rows (instrument_name → instrument_t1, no T2)
        old_cols = {r[1] for r in rows}
        shared = [
            c for c in [
                "polymarket_market_id", "polymarket_question", "option_type",
                "spot_price", "strike", "deribit_prob", "polymarket_price",
                "edge_pct", "abs_edge_pct", "direction", "payout_ratio",
                "asymmetric_payout", "has_alpha", "liquidity_usd",
                "reasoning", "scanned_at", "raw_json",
            ] if c in old_cols
        ]
        if shared:
            cols_str = ", ".join(shared)
            await db.execute(f"""
                INSERT INTO signals (instrument_t1, {cols_str})
                SELECT instrument_name, {cols_str} FROM signals_v1_backup
            """)
        await db.execute("DROP TABLE signals_v1_backup")
        return  # indexes will be recreated by init_db after this call

    if "option_type" not in colnames:
        await db.execute("ALTER TABLE signals ADD COLUMN option_type TEXT")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                -- Instruments
                instrument_t1           TEXT,
                instrument_t2           TEXT,
                interp_method           TEXT,
                interp_weight_w         REAL,
                -- Market
                polymarket_market_id    TEXT NOT NULL,
                polymarket_question     TEXT,
                option_type             TEXT,
                -- Prices
                spot_price              REAL,
                strike                  REAL,
                -- Time
                t_poly_days             REAL,
                T1_days                 REAL,
                T2_days                 REAL,
                -- Volatility
                sigma_t1                REAL,
                sigma_t2                REAL,
                sigma_interp            REAL,
                -- Greeks (interpolated at t_poly)
                delta                   REAL,
                gamma                   REAL,
                vega                    REAL,
                theta                   REAL,
                rho                     REAL,
                -- Edge
                deribit_prob            REAL,
                polymarket_price        REAL,
                edge_pct                REAL,
                abs_edge_pct            REAL,
                direction               TEXT,
                payout_ratio            REAL,
                asymmetric_payout       INTEGER,
                has_alpha               INTEGER,
                -- Liquidity
                liquidity_usd           REAL,
                -- Meta
                reasoning               TEXT,
                scanned_at              TEXT,
                raw_json                TEXT
            )
        """)
        await _migrate_signals_columns(db)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_scanned_at ON signals (scanned_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_abs_edge   ON signals (abs_edge_pct DESC)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_leaderboard_cache (
                rank                    INTEGER PRIMARY KEY CHECK (rank >= 1 AND rank <= 20),
                polymarket_market_id    TEXT,
                polymarket_question     TEXT,
                instrument_t1           TEXT,
                instrument_t2           TEXT,
                option_type             TEXT,
                abs_edge_pct            REAL,
                edge_pct                REAL,
                direction               TEXT,
                payout_ratio            REAL,
                liquidity_usd           REAL,
                deribit_prob            REAL,
                polymarket_price        REAL,
                reasoning               TEXT,
                scanned_at              TEXT,
                refreshed_at            TEXT NOT NULL
            )
        """)
        await db.commit()


async def cleanup_old_signals(retain_days: int = 30):
    cutoff = (datetime.utcnow() - timedelta(days=retain_days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM signals WHERE scanned_at < ?", (cutoff,))
        await db.commit()

    # SQLite maintenance commands (checkpoint/VACUUM) must run outside transactions.
    # Also ensure checkpoint cursor is fully consumed/closed before VACUUM.
    async with aiosqlite.connect(DB_PATH, isolation_level=None) as db:
        chk = await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await chk.fetchall()
        await chk.close()

    async with aiosqlite.connect(DB_PATH, isolation_level=None) as db:
        await db.execute("VACUUM")


async def save_signal(signal: dict):
    _ph = ", ".join(["?"] * 32)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""
            INSERT INTO signals (
                instrument_t1, instrument_t2, interp_method, interp_weight_w,
                polymarket_market_id, polymarket_question, option_type,
                spot_price, strike,
                t_poly_days, T1_days, T2_days,
                sigma_t1, sigma_t2, sigma_interp,
                delta, gamma, vega, theta, rho,
                deribit_prob, polymarket_price,
                edge_pct, abs_edge_pct, direction,
                payout_ratio, asymmetric_payout, has_alpha,
                liquidity_usd, reasoning, scanned_at, raw_json
            ) VALUES ({_ph})
        """, (
            signal.get("instrument_t1"),
            signal.get("instrument_t2"),
            signal.get("interp_method"),
            signal.get("interp_weight_w"),
            signal.get("polymarket_market_id"),
            signal.get("polymarket_question"),
            signal.get("option_type"),
            signal.get("spot_price"),
            signal.get("strike"),
            signal.get("t_poly_days"),
            signal.get("T1_days"),
            signal.get("T2_days"),
            signal.get("sigma_t1"),
            signal.get("sigma_t2"),
            signal.get("sigma_interp"),
            signal.get("delta"),
            signal.get("gamma"),
            signal.get("vega"),
            signal.get("theta"),
            signal.get("rho"),
            signal.get("deribit_prob"),
            signal.get("polymarket_price"),
            signal.get("edge_pct"),
            signal.get("abs_edge_pct"),
            signal.get("direction"),
            signal.get("payout_ratio"),
            1 if signal.get("asymmetric_payout") else 0,
            1 if signal.get("has_alpha") else 0,
            signal.get("liquidity_usd"),
            signal.get("reasoning"),
            signal.get("scanned_at") or datetime.utcnow().isoformat(),
            json.dumps(signal),
        ))
        await db.commit()


async def _compute_leaderboard_rows(hours: int, top_n: int) -> list[dict]:
    """Rolling window: best row per (T1,T2) bracket, ranked by abs edge (spec §7)."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM signals
            WHERE has_alpha = 1
              AND scanned_at >= ?
              AND id IN (
                  SELECT MAX(id)
                  FROM signals
                  WHERE has_alpha = 1 AND scanned_at >= ?
                  GROUP BY instrument_t1, instrument_t2
              )
            ORDER BY abs_edge_pct DESC
            LIMIT ?
        """, (cutoff, cutoff, top_n))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def refresh_alpha_leaderboard_cache(hours: int = 24, top_n: int = 5):
    """
    After each scan: recompute top-N from the rolling window and replace the cache
    (spec §7 — persistent daily-best style leaderboard).
    """
    rows = await _compute_leaderboard_rows(hours, top_n)
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM alpha_leaderboard_cache")
        for i, r in enumerate(rows, start=1):
            await db.execute("""
                INSERT INTO alpha_leaderboard_cache (
                    rank, polymarket_market_id, polymarket_question,
                    instrument_t1, instrument_t2, option_type,
                    abs_edge_pct, edge_pct, direction, payout_ratio,
                    liquidity_usd, deribit_prob, polymarket_price,
                    reasoning, scanned_at, refreshed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                i,
                r.get("polymarket_market_id"),
                r.get("polymarket_question"),
                r.get("instrument_t1"),
                r.get("instrument_t2"),
                r.get("option_type"),
                r.get("abs_edge_pct"),
                r.get("edge_pct"),
                r.get("direction"),
                r.get("payout_ratio"),
                r.get("liquidity_usd"),
                r.get("deribit_prob"),
                r.get("polymarket_price"),
                r.get("reasoning"),
                r.get("scanned_at"),
                now,
            ))
        await db.commit()


async def get_leaderboard(hours: int = 24, top_n: int = 5) -> list[dict]:
    """
    Default 24h / top 5: read materialized cache (updated every scan).
    Other windows: compute live from signals.
    """
    if hours == 24 and top_n == 5:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT COUNT(*) FROM alpha_leaderboard_cache"
            )
            n = (await cur.fetchone())[0]
            if n > 0:
                cur = await db.execute(
                    "SELECT * FROM alpha_leaderboard_cache ORDER BY rank ASC"
                )
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
    return await _compute_leaderboard_rows(hours, top_n)


async def get_recent_signals(hours: int = 1) -> list[dict]:
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM signals
            WHERE scanned_at >= ?
            ORDER BY scanned_at DESC
        """, (cutoff,))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
