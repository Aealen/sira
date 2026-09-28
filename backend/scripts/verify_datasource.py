# -*- coding: utf-8 -*-
"""M1 数据源真实数据验证：search / quote / kline（M1 归档"恢复开发第一件事"）。

用法：cd backend && uv run python scripts/verify_datasource.py
覆盖市场与关键词见 CASES；退出码 = 失败数。
"""
import sys
import time

sys.path.insert(0, ".")

from app.services.datasource import get_source  # noqa: E402

CASES = [
    ("a_stock", "茅台"),
    ("etf", "510300"),
    ("fund", "华夏成长"),
]


def run() -> int:
    fail = 0
    for market, kw in CASES:
        src = get_source(market)
        t0 = time.time()
        try:
            results = src.search(kw, 3)
            assert results, "搜索无结果"
            top = results[0]
            print(f"[{market}] search({kw!r}) {time.time()-t0:.1f}s -> {top.code} {top.name}")
        except Exception as e:
            print(f"[{market}] search FAIL {type(e).__name__}: {e}")
            fail += 1
            continue

        t0 = time.time()
        try:
            q = src.get_quote(top.code)
            print(f"[{market}] quote {time.time()-t0:.1f}s -> {q.name} price={q.price} pct={q.change_pct}% stale={q.stale}")
        except Exception as e:
            print(f"[{market}] quote FAIL {type(e).__name__}: {e}")
            fail += 1

        t0 = time.time()
        try:
            bars, stale = src.get_kline(top.code, 30)
            assert bars, "K线无数据"
            last = bars[-1]
            print(f"[{market}] kline {time.time()-t0:.1f}s -> {len(bars)} bars, last={last.date} close={last.close} stale={stale}")
        except Exception as e:
            print(f"[{market}] kline FAIL {type(e).__name__}: {e}")
            fail += 1
    print(f"\n=== search 3 市场 / quote+kline 共 6 项，失败 {fail} ===")
    return fail


if __name__ == "__main__":
    raise SystemExit(run())
