# -*- coding: utf-8 -*-
"""M1 API 端点冒烟验证（TestClient 走真实 ASGI 请求路径）。

用法：cd backend && uv run python scripts/smoke_api.py
"""
import sys

sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def run() -> int:
    c = TestClient(app)
    fail = 0

    def hit(name: str, method: str, url: str, **kw):
        nonlocal fail
        r = c.request(method, url, **kw)
        ok = r.status_code == 200
        body = r.json() if ok else r.text[:120]
        if isinstance(body, list):
            preview = f"{len(body)} 项"
            if body:
                preview += f"，首项: {str(body[0])[:80]}"
        else:
            preview = str(body)[:160]
        print(f"{'OK' if ok else 'FAIL'} {r.status_code} {name} -> {preview}")
        if not ok:
            fail += 1
        return r

    hit("健康检查", "GET", "/api/health")
    hit("市场列表", "GET", "/api/market/markets")
    r = hit("跨市场搜索", "GET", "/api/market/search", params={"q": "510300", "limit": 3})
    if r.status_code == 200 and r.json():
        item = r.json()[0]
        hit("实时报价", "GET", f"/api/market/quote/{item['market']}/{item['code']}")
        hit("日K线", "GET", f"/api/market/kline/{item['market']}/{item['code']}", params={"days": 30})
    else:
        print("搜索无结果，跳过 quote/kline")
        fail += 1
    r404 = c.get("/api/market/quote/a_stock/NOTEXIST")
    ok404 = r404.status_code == 404 and "未找到" in r404.text
    print(f"{'OK' if ok404 else 'FAIL'} {r404.status_code} 404 路径（预期 404+中文提示） -> {r404.text[:80]}")
    if not ok404:
        fail += 1
    print(f"\n=== 冒烟完成，失败 {fail} ===")
    return fail


if __name__ == "__main__":
    raise SystemExit(run())
