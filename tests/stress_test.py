#!/usr/bin/env python3
"""
Stress test script: load tests an HTTP/HTTPS proxy server

Uses aiohttp, supports steady and ramp modes.
Outputs throughput, success rate, response time percentiles, etc.
"""

import asyncio
import base64
import gc
import math
import os
import sys
import time
import argparse
from typing import Optional
from urllib.parse import urlparse

import aiohttp


# Default test target
DEFAULT_TEST_URL = "https://www.163.com"

# Percentile configuration
PERCENTILES = [50, 75, 90, 95, 99]


class StressTester:
    """Stress tester"""

    def __init__(self, host: str, port: int, username: str, password: str,
                 concurrency: int, duration: int, test_url: str):
        self.proxy_url = f"http://{host}:{port}"
        self.auth_header = self._build_auth(username, password) if username else None
        self.concurrency = concurrency
        self.duration = duration
        self.test_url = test_url

        self.results = []
        self.errors = {"timeout": 0, "refused": 0, "status": 0, "other": 0}
        self.start_time = 0
        self._done_count = 0
        self._progress_task = None

    def _build_auth(self, username: str, password: str) -> str:
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {encoded}"

    def _make_proxy_auth(self) -> aiohttp.BasicAuth:
        if not self.auth_header:
            return None
        decoded = base64.b64decode(self.auth_header.split(" ", 1)[1]).decode()
        user, pwd = decoded.split(":", 1)
        return aiohttp.BasicAuth(user, pwd)

    async def _do_request(self, session: aiohttp.ClientSession, url: str) -> dict:
        """Execute one proxy request, return metrics"""
        start = time.perf_counter()
        connect_start = 0
        connect_time = 0
        status = 0
        try:
            connect_start = time.perf_counter()
            async with session.get(url, proxy=self.proxy_url,
                                   proxy_auth=self._make_proxy_auth(),
                                   timeout=aiohttp.ClientTimeout(connect=10, total=30)) as resp:
                connect_time = time.perf_counter() - connect_start
                await resp.read()
                elapsed = time.perf_counter() - start
                status = resp.status
                return {"ok": True, "elapsed": elapsed, "connect": connect_time, "status": status}
        except asyncio.TimeoutError:
            self.errors["timeout"] += 1
        except aiohttp.ClientConnectorError:
            self.errors["refused"] += 1
        except aiohttp.ClientResponseError as e:
            self.errors["status"] += 1
            status = e.status
        except Exception:
            self.errors["other"] += 1
        elapsed = time.perf_counter() - start
        return {"ok": False, "elapsed": elapsed, "connect": 0, "status": status}

    async def _worker(self, worker_id: int, queue: asyncio.Queue, session: aiohttp.ClientSession):
        """Worker coroutine: fetch URLs from queue concurrently"""
        try:
            while True:
                url = await queue.get()
                result = await self._do_request(session, url)
                self.results.append(result)
                self._done_count += 1
                queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _progress_reporter(self, deadline: float):
        """Print real-time progress periodically"""
        last_total = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(5)
            if time.monotonic() >= deadline:
                break
            total = self._done_count
            elapsed = time.monotonic() - self.start_time
            rps = total / elapsed if elapsed > 0 else 0
            interval_count = total - last_total
            interval_rps = interval_count / 5
            active = self.concurrency - (interval_count if interval_count < self.concurrency else 0)
            print(f"  [{elapsed:4.0f}s]  Done {total}, "
                  f"RPS={rps:.1f},  Window RPS={interval_rps:.1f},  Active={self.concurrency}")
            last_total = total

    async def run_steady(self) -> dict:
        """Steady mode: fixed concurrency for specified duration"""
        queue = asyncio.Queue(maxsize=self.concurrency * 2)
        connector = aiohttp.TCPConnector(limit=self.concurrency, limit_per_host=self.concurrency)
        async with aiohttp.ClientSession(connector=connector) as session:
            workers = [asyncio.create_task(self._worker(i, queue, session))
                       for i in range(self.concurrency)]
            deadline = time.monotonic() + self.duration
            reporter = asyncio.create_task(self._progress_reporter(deadline))
            while time.monotonic() < deadline:
                try:
                    queue.put_nowait(self.test_url)
                except asyncio.QueueFull:
                    await asyncio.sleep(0.001)
            reporter.cancel()
            for w in workers:
                w.cancel()
            await asyncio.gather(reporter, *workers, return_exceptions=True)
            await asyncio.sleep(0.25)
        return self._compute_stats()

    async def run_ramp(self) -> dict:
        """Ramp mode: gradually increase concurrency"""
        max_conc = self.concurrency
        step = max(1, max_conc // 10)
        all_stats = []
        for conc in range(step, max_conc + 1, step):
            self.concurrency = conc
            self.results = []
            self._done_count = 0
            self.errors = {"timeout": 0, "refused": 0, "status": 0, "other": 0}
            print(f"\n  ▶ Concurrency={conc}:")
            stats = await self.run_steady()
            stats["concurrency"] = conc
            all_stats.append(stats)
            if stats["success_rate"] < 0.95:
                print(f"  Concurrency {conc}: success rate {stats['success_rate']:.1%} < 95%, stopping ramp")
                break
        return all_stats

    def _compute_stats(self) -> dict:
        """Compute statistics"""
        total = len(self.results)
        if total == 0:
            return {"total": 0, "success": 0, "success_rate": 0, "rps": 0,
                    "elapsed": {}, "connect": {}, "errors": self.errors, "duration": 0}

        elapsed_times = sorted([r["elapsed"] for r in self.results if r["ok"]])
        connect_times = sorted([r["connect"] for r in self.results if r["ok"]])
        success_count = len(elapsed_times)
        success_rate = success_count / total if total else 0

        duration = time.monotonic() - self.start_time if self.start_time else 1
        rps = success_count / duration if duration else 0

        def percentile(data, p):
            if not data:
                return 0
            k = (len(data) - 1) * p / 100
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return data[int(k)] * 1000
            return (data[f] * (c - k) + data[c] * (k - f)) * 1000

        elapsed_p = {p: percentile(elapsed_times, p) for p in PERCENTILES}
        connect_p = {p: percentile(connect_times, p) for p in PERCENTILES}

        return {
            "total": total,
            "success": success_count,
            "success_rate": success_rate,
            "rps": rps,
            "elapsed": elapsed_p,
            "connect": connect_p,
            "errors": self.errors,
            "duration": duration,
        }

    def print_report(self, stats):
        """Print report"""
        if isinstance(stats, list):
            self._print_ramp_report(stats)
            return
        s = stats
        print()
        print(f"{'─' * 68}")
        print(f"  Stress Test Report")
        print(f"{'─' * 68}")
        print(f"  {'Metric':<20} {'Total':>10} {'p50':>8} {'p75':>8} {'p90':>8} {'p95':>8} {'p99':>8}")
        print(f"{' ' * 4}{'─' * 64}")
        print(f"  {'Requests':<20} {s['total']:>10}")
        print(f"  {'Successes':<20} {s['success']:>10}")
        print(f"  {'Success Rate':<20} {s['success_rate']:>9.1%}")
        print(f"  {'Throughput (RPS)':<20} {s['rps']:>9.1f}")
        ep = s["elapsed"]
        print(f"  {'Response (ms)':<20} {'':>10} {ep.get(50, 0):>7.1f} {ep.get(75, 0):>7.1f} {ep.get(90, 0):>7.1f} {ep.get(95, 0):>7.1f} {ep.get(99, 0):>7.1f}")
        cp = s["connect"]
        print(f"  {'Connect (ms)':<20} {'':>10} {cp.get(50, 0):>7.1f} {cp.get(75, 0):>7.1f} {cp.get(90, 0):>7.1f} {cp.get(95, 0):>7.1f} {cp.get(99, 0):>7.1f}")
        print(f"{'─' * 68}")
        print(f"  Error Breakdown:")
        print(f"    Timeout:      {s['errors']['timeout']}")
        print(f"    Refused:      {s['errors']['refused']}")
        print(f"    Status:       {s['errors']['status']}")
        print(f"    Other:        {s['errors']['other']}")
        print(f"{'─' * 68}")
        print()

    def _print_ramp_report(self, all_stats):
        """Print ramp mode report"""
        print()
        print(f"{'─' * 68}")
        print(f"  Ramp Stress Test Report")
        print(f"{'─' * 68}")
        header = f"  {'Concur':>6} {'Total':>8} {'Success':>8} {'RPS':>8} {'p50(ms)':>8} {'p95(ms)':>8} {'p99(ms)':>8}"
        print(header)
        print(f"{' ' * 4}{'─' * 60}")
        for s in all_stats:
            ep = s["elapsed"]
            print(f"  {s.get('concurrency', self.concurrency):>6} {s['total']:>8} {s['success_rate']:>7.1%} {s['rps']:>7.1f} {ep.get(50, 0):>7.1f} {ep.get(95, 0):>7.1f} {ep.get(99, 0):>7.1f}")
        print(f"{'─' * 68}")


def load_config() -> dict:
    """Read proxy settings from config file (script-location relative, not cwd)"""
    import yaml
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(script_dir, "..", "config.yaml")
    config = {}
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception:
            pass
    return config


def main():
    parser = argparse.ArgumentParser(description="Proxy server stress test tool")
    parser.add_argument("--host", help="Proxy address (default from config.yaml)")
    parser.add_argument("--port", type=int, help="Proxy port")
    parser.add_argument("--user", help="Username")
    parser.add_argument("--passwd", help="Password")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent connections (default 10)")
    parser.add_argument("--duration", type=int, default=10, help="Test duration in seconds (default 10)")
    parser.add_argument("--url", default=DEFAULT_TEST_URL, help=f"Test target URL (default {DEFAULT_TEST_URL})")
    parser.add_argument("--ramp", action="store_true", help="Enable ramp mode")
    args = parser.parse_args()

    cfg = load_config()
    host = args.host or cfg.get("host", "127.0.0.1").replace("0.0.0.0", "127.0.0.1")
    port = args.port or cfg.get("port", 8080)
    user = args.user or cfg.get("username", "")
    passwd = args.passwd or cfg.get("password", "")

    print(f"{'=' * 60}")
    print(f"  Proxy Stress Test")
    print(f"{'=' * 60}")
    print(f"  Target:     {host}:{port}")
    print(f"  Auth:       {'Enabled' if user else 'Disabled'}")
    print(f"  Test URL:   {args.url}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Duration:   {args.duration}s")
    print(f"  Mode:       {'Ramp' if args.ramp else 'Steady'}")
    print(f"{'=' * 60}")

    tester = StressTester(
        host=host, port=port, username=user, password=passwd,
        concurrency=args.concurrency, duration=args.duration,
        test_url=args.url,
    )
    tester.start_time = time.monotonic()

    try:
        if args.ramp:
            stats = asyncio.run(tester.run_ramp())
        else:
            stats = asyncio.run(tester.run_steady())
        tester.print_report(stats)
    except KeyboardInterrupt:
        print("\nTest interrupted")
        sys.exit(1)
    finally:
        gc.collect()


if __name__ == "__main__":
    main()
