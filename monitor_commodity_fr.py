#!/usr/bin/env python3
"""
Hyperliquid × Extended Exchange — Commodity Funding Rate Arbitrage Monitor

両建てFRアービトラージをモニターします:
  - Hyperliquid Trade.xyz DEX (HIP-3, dex="xyz")
  - Extended Exchange  https://app.extended.exchange/perp

Usage:
  python3 monitor_commodity_fr.py
  DISCORD_WEBHOOK_URL=https://... python3 monitor_commodity_fr.py
"""

import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── .env 読み込み ──────────────────────────────────────────────────────────────

def _load_env(path: Path = Path(__file__).parent / ".env") -> None:
    """シンプルな .env パーサー。既存の環境変数は上書きしない。"""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

_load_env()

# ── API endpoints ──────────────────────────────────────────────────────────────

HL_API_URL       = "https://api.hyperliquid.xyz/info"
EXT_API_BASE_URL = "https://api.starknet.extended.exchange/api/v1"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# ── Commodity pair mapping ─────────────────────────────────────────────────────
# (hl_xyz_symbol, ext_symbol, display_name, std_ticker)
#
# Hyperliquid xyz DEX  ←→  Extended Exchange
#   xyz:GOLD           ←→  XAU-USD   (Gold)
#   xyz:SILVER         ←→  XAG-USD   (Silver)
#   xyz:PLATINUM       ←→  XPT-USD   (Platinum)
#   xyz:CL             ←→  WTI-USD   (WTI Crude Oil)  ※名称異なる
#   xyz:BRENTOIL       ←→  XBR-USD   (Brent Crude)    ※名称異なる
#   xyz:NATGAS         ←→  XNG-USD   (Natural Gas)    ※名称異なる
#   xyz:COPPER         ←→  XCU-USD   (Copper)

COMMODITY_PAIRS: list[tuple[str, str, str, str]] = [
    ("xyz:GOLD",     "XAU-USD", "Gold",          "XAU"),
    ("xyz:SILVER",   "XAG-USD", "Silver",        "XAG"),
    ("xyz:PLATINUM", "XPT-USD", "Platinum",      "XPT"),
    ("xyz:CL",       "WTI-USD", "WTI Crude Oil", "WTI"),
    ("xyz:BRENTOIL", "XBR-USD", "Brent Crude",   "XBR"),
    ("xyz:NATGAS",   "XNG-USD", "Natural Gas",   "XNG"),
    ("xyz:COPPER",   "XCU-USD", "Copper",        "XCU"),
]

# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class AssetInfo:
    symbol: str
    exchange: str        # "Hyperliquid" or "Extended"
    funding_1h: float    # 現在の1h funding rate (decimal)
    mark_price: float
    avg_7h:  Optional[float] = None   # 過去7h平均 (decimal)
    avg_24h: Optional[float] = None   # 過去24h平均 (decimal)
    avg_1w:  Optional[float] = None   # 過去1W平均 (decimal)

    @property
    def funding_apy(self) -> float:
        return self.funding_1h * 24 * 365 * 100

    def avg_apy(self, avg: Optional[float]) -> Optional[float]:
        return avg * 24 * 365 * 100 if avg is not None else None


@dataclass
class ArbOpportunity:
    std_ticker: str
    display_name: str
    long_asset: AssetInfo
    short_asset: AssetInfo
    spread_1h: float      # 現在スプレッド 1h (%)
    spread_daily: float   # 現在スプレッド 日次 (%)
    spread_apy: float     # 現在スプレッド 年利 (%)
    spread_apy_7h: Optional[float] = None   # 7h平均スプレッド 年利 (%)
    spread_apy_24h: Optional[float] = None  # 24h平均スプレッド 年利 (%)
    spread_apy_1w: Optional[float] = None   # 1W平均スプレッド 年利 (%)

    @property
    def price_basis_pct(self) -> float:
        return abs(self.long_asset.mark_price - self.short_asset.mark_price) \
               / self.long_asset.mark_price * 100

# ── API: Hyperliquid ───────────────────────────────────────────────────────────

def _hl_post(payload: dict) -> list:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        HL_API_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def fetch_hl_xyz_assets() -> dict[str, AssetInfo]:
    """Trade.xyz DEX (dex='xyz') の全資産を {symbol: AssetInfo} で返す。"""
    result = _hl_post({"type": "metaAndAssetCtxs", "dex": "xyz"})
    universe, ctxs = result[0]["universe"], result[1]
    assets: dict[str, AssetInfo] = {}
    for i, asset in enumerate(universe):
        ctx = ctxs[i]
        assets[asset["name"]] = AssetInfo(
            symbol=asset["name"],
            exchange="Hyperliquid",
            funding_1h=float(ctx["funding"]),
            mark_price=float(ctx["markPx"]),
        )
    return assets


def fetch_hl_fr_history(symbol: str, start_ms: int, end_ms: int) -> list[float]:
    """Hyperliquid の資産の FR 履歴を [float, ...] で返す (新しい順)。"""
    records = _hl_post({
        "type": "fundingHistory",
        "coin": symbol,
        "startTime": start_ms,
        "endTime": end_ms,
    })
    return [float(r["fundingRate"]) for r in records]

# ── API: Extended Exchange ─────────────────────────────────────────────────────

def _ext_get(path: str) -> dict:
    url = f"{EXT_API_BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def fetch_ext_assets() -> dict[str, AssetInfo]:
    """Extended Exchange の全市場を {symbol: AssetInfo} で返す。"""
    data = _ext_get("/info/markets")
    markets = data if isinstance(data, list) else data.get("data", data)
    assets: dict[str, AssetInfo] = {}
    for mkt in markets:
        name = mkt.get("name", "")
        stats = mkt.get("marketStats", {})
        fr_raw = stats.get("fundingRate")
        px_raw = stats.get("markPrice")
        if fr_raw is None or px_raw is None:
            continue
        assets[name] = AssetInfo(
            symbol=name,
            exchange="Extended",
            funding_1h=float(fr_raw),
            mark_price=float(px_raw),
        )
    return assets


def fetch_ext_fr_history(symbol: str, start_ms: int, end_ms: int) -> list[float]:
    """Extended Exchange の資産の FR 履歴を [float, ...] で返す (新しい順)。"""
    path = f"/info/{symbol}/funding?startTime={start_ms}&endTime={end_ms}&limit=10000"
    data = _ext_get(path)
    records = data.get("data", [])
    return [float(r["f"]) for r in records]

# ── FR 履歴の平均計算 ──────────────────────────────────────────────────────────

def _avg(rates: list[float], n: int) -> Optional[float]:
    """最新 n 件の平均を返す。件数不足の場合は None。"""
    if len(rates) < n:
        return None
    return sum(rates[:n]) / n


def enrich_with_history(
    assets: dict[str, AssetInfo],
    symbols: list[str],
    fetch_history_fn,
    now_ms: int,
) -> None:
    """指定シンボルの FR 履歴を取得し、AssetInfo に 7h/24h/1W 平均を追加する。"""
    start_ms = now_ms - 7 * 24 * 3600 * 1000  # 1W前から取得
    for symbol in symbols:
        if symbol not in assets:
            continue
        try:
            rates = fetch_history_fn(symbol, start_ms, now_ms)
            asset = assets[symbol]
            asset.avg_7h  = _avg(rates,   7)
            asset.avg_24h = _avg(rates,  24)
            asset.avg_1w  = _avg(rates, 168)
        except Exception as e:
            print(f"  [WARN] FR履歴取得失敗 {symbol}: {e}", file=sys.stderr)

# ── Arbitrage calculation ──────────────────────────────────────────────────────

def _spread_apy(hl_avg: Optional[float], ext_avg: Optional[float]) -> Optional[float]:
    """2資産の平均FRからスプレッドAPY(%)を計算する。"""
    if hl_avg is None or ext_avg is None:
        return None
    return abs(hl_avg - ext_avg) * 24 * 365 * 100


def calculate_opportunity(
    hl_asset: AssetInfo,
    ext_asset: AssetInfo,
    display_name: str,
    std_ticker: str,
) -> ArbOpportunity:
    """
    どちらをロング/ショートするか決定し、純スプレッドを計算する。
    戦略: FRが高い方をショート、低い方をロング。
    """
    if hl_asset.funding_1h >= ext_asset.funding_1h:
        short_asset, long_asset = hl_asset, ext_asset
    else:
        short_asset, long_asset = ext_asset, hl_asset

    net_1h = abs(hl_asset.funding_1h - ext_asset.funding_1h)

    return ArbOpportunity(
        std_ticker=std_ticker,
        display_name=display_name,
        long_asset=long_asset,
        short_asset=short_asset,
        spread_1h=net_1h * 100,
        spread_daily=net_1h * 24 * 100,
        spread_apy=net_1h * 24 * 365 * 100,
        spread_apy_7h=_spread_apy(hl_asset.avg_7h,  ext_asset.avg_7h),
        spread_apy_24h=_spread_apy(hl_asset.avg_24h, ext_asset.avg_24h),
        spread_apy_1w=_spread_apy(hl_asset.avg_1w,  ext_asset.avg_1w),
    )

# ── Output: stdout ─────────────────────────────────────────────────────────────

_R      = "\033[0m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_ORANGE = "\033[38;5;208m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"


def _apy_str(apy: Optional[float], width: int = 8) -> str:
    if apy is None:
        return f"{'N/A':>{width}}"
    color = _GREEN if apy >= 20 else (_YELLOW if apy >= 10 else _ORANGE)
    s = f"{apy:+.1f}%"
    return f"{color}{s:>{width}}{_R}"


def _fr_str(asset: AssetInfo, role: str) -> str:
    fr = asset.funding_1h
    income_1h = (-fr if role == "LONG" else fr) * 100
    arrow = "↑受取" if income_1h >= 0 else "↓支払"
    return (
        f"{asset.exchange:>10} {asset.symbol:<15}"
        f"FR={fr*100:+.6f}%  APY={asset.funding_apy:+.2f}%  "
        f"{arrow}{abs(income_1h):.6f}%/h"
    )


def print_opportunities(opps: list[ArbOpportunity]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    W = 92
    print(f"\n{_BOLD}{'═' * W}{_R}")
    print(f"{_BOLD}  Hyperliquid × Extended — Commodity FR Arbitrage Monitor{_R}  {_DIM}{now}{_R}")
    print(f"{_BOLD}{'═' * W}{_R}\n")

    if not opps:
        print(f"  {_YELLOW}スプレッドがプラスの機会は現在ありません。{_R}\n")
        return

    # ヘッダー
    print(f"  {'Ticker':<6} {'Commodity':<14}  "
          f"{'現在APY':>9}  {'7h平均':>9}  {'24h平均':>9}  {'1W平均':>9}  "
          f"{'basis':>6}")
    print(f"  {'─'*6} {'─'*14}  {'─'*9}  {'─'*9}  {'─'*9}  {'─'*9}  {'─'*6}")

    for op in opps:
        basis_color = _YELLOW if op.price_basis_pct > 0.5 else _DIM
        print(
            f"  {op.std_ticker:<6} {op.display_name:<14}  "
            f"{_apy_str(op.spread_apy):>9}  "
            f"{_apy_str(op.spread_apy_7h):>9}  "
            f"{_apy_str(op.spread_apy_24h):>9}  "
            f"{_apy_str(op.spread_apy_1w):>9}  "
            f"{basis_color}{op.price_basis_pct:>5.3f}%{_R}"
        )

    print()

    # 詳細
    for op in opps:
        basis_warn = (f"  {_YELLOW}⚠ basis {op.price_basis_pct:.3f}%{_R}"
                      if op.price_basis_pct > 0.5
                      else f"  basis {op.price_basis_pct:.3f}%")
        print(f"  {_BOLD}{op.std_ticker}  {op.display_name}{_R}")
        print(f"    スプレッド  現在: {_apy_str(op.spread_apy)}  "
              f"7h平均: {_apy_str(op.spread_apy_7h)}  "
              f"24h平均: {_apy_str(op.spread_apy_24h)}  "
              f"1W平均: {_apy_str(op.spread_apy_1w)}")
        print(f"    LONG   {_fr_str(op.long_asset,  'LONG')}")
        print(f"    SHORT  {_fr_str(op.short_asset, 'SHORT')}")
        print(f"    価格  LONG側: ${op.long_asset.mark_price:,.3f}"
              f"  SHORT側: ${op.short_asset.mark_price:,.3f}{basis_warn}")
        print()

    print(f"  {_DIM}スプレッドAPY降順（現在値基準）。プラスのみ表示。FR(1h) × 24 × 365 = 年利{_R}\n")

# ── Output: Discord ────────────────────────────────────────────────────────────

def _discord_color(apy: float) -> int:
    return 0x00FF00 if apy >= 20 else (0xFFFF00 if apy >= 10 else 0xFF6600)


def _fmt_apy(apy: Optional[float]) -> str:
    return f"{apy:+.2f}%" if apy is not None else "N/A"


def build_discord_message(opps: list[ArbOpportunity]) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    if not opps:
        return {"embeds": [{
            "title": "📊 Commodity FR Arb Monitor",
            "description": "現在プラスのスプレッド機会はありません。",
            "color": 0x808080,
            "timestamp": now,
            "footer": {"text": "Hyperliquid × Extended FR Monitor"},
        }]}

    embeds = []
    for op in opps:
        la, sa = op.long_asset, op.short_asset
        embeds.append({
            "title": f"📊 {op.display_name} ({op.std_ticker})  現在{op.spread_apy:+.2f}% APY",
            "color": _discord_color(op.spread_apy),
            "fields": [
                {
                    "name": f"🟢 LONG — {la.exchange} `{la.symbol}`",
                    "value": (
                        f"FR(1h): `{la.funding_1h*100:+.6f}%`\n"
                        f"APY: **{la.funding_apy:+.2f}%**\n"
                        f"Price: ${la.mark_price:,.3f}"
                    ),
                    "inline": True,
                },
                {
                    "name": f"🔴 SHORT — {sa.exchange} `{sa.symbol}`",
                    "value": (
                        f"FR(1h): `{sa.funding_1h*100:+.6f}%`\n"
                        f"APY: **{sa.funding_apy:+.2f}%**\n"
                        f"Price: ${sa.mark_price:,.3f}"
                    ),
                    "inline": True,
                },
                {
                    "name": "💰 スプレッド推移",
                    "value": (
                        f"現在:   **{_fmt_apy(op.spread_apy)}**\n"
                        f"7h平均: **{_fmt_apy(op.spread_apy_7h)}**\n"
                        f"24h平均: **{_fmt_apy(op.spread_apy_24h)}**\n"
                        f"1W平均: **{_fmt_apy(op.spread_apy_1w)}**"
                    ),
                    "inline": True,
                },
                {
                    "name": "⚖️ ベーシスリスク",
                    "value": f"価格差: **{op.price_basis_pct:.3f}%**",
                    "inline": False,
                },
            ],
            "timestamp": now,
            "footer": {"text": "Hyperliquid × Extended FR Monitor"},
        })
    return {"embeds": embeds}


def post_to_discord(message: dict) -> int:
    if not DISCORD_WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL not set")
    body = json.dumps(message).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "CommodityFRMonitor/2.0"},
    )
    with urllib.request.urlopen(req) as r:
        return r.status

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    now_ms = int(time.time() * 1000)

    print("現在のFR取得中...", file=sys.stderr)
    hl_assets  = fetch_hl_xyz_assets()
    ext_assets = fetch_ext_assets()

    hl_symbols  = [p[0] for p in COMMODITY_PAIRS]
    ext_symbols = [p[1] for p in COMMODITY_PAIRS]

    print("FR履歴取得中（7h/24h/1W平均）...", file=sys.stderr)
    enrich_with_history(hl_assets,  hl_symbols,  fetch_hl_fr_history,  now_ms)
    enrich_with_history(ext_assets, ext_symbols, fetch_ext_fr_history, now_ms)

    opps: list[ArbOpportunity] = []
    for hl_sym, ext_sym, display_name, std_ticker in COMMODITY_PAIRS:
        hl  = hl_assets.get(hl_sym)
        ext = ext_assets.get(ext_sym)
        if hl is None:
            print(f"  [WARN] {hl_sym} が Hyperliquid xyz DEX に見つかりません", file=sys.stderr)
            continue
        if ext is None:
            print(f"  [WARN] {ext_sym} が Extended Exchange に見つかりません", file=sys.stderr)
            continue
        op = calculate_opportunity(hl, ext, display_name, std_ticker)
        if op.spread_apy > 0:
            opps.append(op)

    opps.sort(key=lambda o: o.spread_apy, reverse=True)
    print_opportunities(opps)

    if DISCORD_WEBHOOK_URL:
        status = post_to_discord(build_discord_message(opps))
        print(f"Discord 送信完了 (HTTP {status})", file=sys.stderr)
    else:
        print("ヒント: DISCORD_WEBHOOK_URL を設定するとDiscordにも通知されます。", file=sys.stderr)


if __name__ == "__main__":
    main()
