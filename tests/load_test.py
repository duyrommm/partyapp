"""Thử tải: N điện thoại giả lập cùng vào, cùng trả lời, cùng bình chọn, rớt mạng rồi vào lại.

Chạy:   python tests/load_test.py            (tự bật máy chủ cục bộ, 200 máy)
        python tests/load_test.py --url https://ten-app.onrender.com --key MC_KEY --n 150
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ["sales-aw", "sales-pgi", "admin", "ecom", "store", "mkt-aw", "mkt-pgi", "accounting",
          "warehouse", "warranty", "car-audio", "it-legal", "hn", "dn"]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else float("nan")


class Phone:
    def __init__(self, i, base, ws_base):
        self.i, self.base, self.ws_base = i, base, ws_base
        self.name = f"Khách {i:03d}"
        self.group = GROUPS[i % len(GROUPS)]
        self.state = None
        self.seen = {}          # khoá sự kiện -> thời điểm thấy lần đầu
        self.acks = []
        self.ws = None
        self.task = None

    async def join(self, http):
        r = await http.post(self.base + "/api/join", json={"name": self.name, "group": self.group})
        r.raise_for_status()
        self.token = r.json()["token"]

    async def connect(self):
        self.ws = await websockets.connect(f"{self.ws_base}/ws?role=player&token={self.token}", max_size=2**20, open_timeout=20)
        self.task = asyncio.create_task(self.reader())

    async def reader(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                now = time.perf_counter()
                if m.get("t") == "state":
                    self.state = m
                    c = m["c"]
                    q = c.get("quiz")
                    if q:
                        self.seen.setdefault(f"q{q['qseq']}:{q['phase']}", now)
                    if c.get("vote"):
                        self.seen.setdefault(f"v{c['vote']['id']}:{c['vote']['open']}", now)
                    if c.get("draw"):
                        self.seen.setdefault(f"d{c['draw']['seq']}:{c['draw']['phase']}", now)
                elif m.get("t") == "ack":
                    self.acks.append((m["k"], m["r"], now))
        except Exception:
            pass

    async def drop(self):
        self.task.cancel()
        await self.ws.close()

    async def send(self, obj):
        t0 = time.perf_counter()
        n = len(self.acks)
        await self.ws.send(json.dumps(obj))
        for _ in range(400):
            if len(self.acks) > n:
                return self.acks[-1][1], self.acks[-1][2] - t0
            await asyncio.sleep(0.01)
        return "timeout", None


async def wait_all(phones, key, timeout=20):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if all(key in p.seen for p in phones):
            return True
        await asyncio.sleep(0.02)
    return False


async def run(base, key, n, report):
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    H = {"x-mc-key": key}
    limits = httpx.Limits(max_connections=60)
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        line = f"  {'ĐẠT ' if cond else 'LỖI '} {name}" + (f"  ({detail})" if detail else "")
        print(line)
        report.append(line)

    async with httpx.AsyncClient(timeout=30, limits=limits) as http:
        async def mc(**body):
            r = await http.post(base + "/api/mc", json=body, headers=H)
            return r.status_code, r.json()

        t0 = time.perf_counter()
        st, _ = await mc(a="reset")
        check("MC đăng nhập và xoá dữ liệu cũ", st == 200)
        phones = [Phone(i, base, ws_base) for i in range(n)]
        sem = asyncio.Semaphore(40)

        async def enter(p):
            async with sem:
                await p.join(http)
                await p.connect()
        await asyncio.gather(*[enter(p) for p in phones])
        t_join = time.perf_counter() - t0
        await asyncio.sleep(1.0)
        health = (await http.get(base + "/health")).json()
        check(f"{n} máy vào phòng và kết nối", health["players"] == n and health["conns"] >= n, f"{t_join:.1f}s, {health}")

        # ── đố vui ──
        await mc(a="questions", set="score", text="\n".join(f"Câu thử {k}\tA\tB\tC\tD\t{'ABCD'[k % 4]}\t20" for k in range(5)))
        await mc(a="quiz_start", kind="score", shuffle=False)
        lat_open, lat_ack, lat_reveal = [], [], []
        for k in range(3):
            q_idx = k - 1
            t_click = time.perf_counter()
            st, j = await mc(a="quiz_open", idx=q_idx)
            if st != 200:
                check(f"Mở câu {k + 1}", False, j)
                continue
            seq = None
            await asyncio.sleep(0.05)
            for p in phones:
                if p.state and p.state["c"].get("quiz", {}).get("phase") == "open":
                    seq = p.state["c"]["quiz"]["qseq"]
                    break
            got = await wait_all(phones, f"q{seq}:open")
            lat_open += [p.seen[f"q{seq}:open"] - t_click for p in phones if f"q{seq}:open" in p.seen]
            check(f"Câu {k + 1}: cả {n} máy thấy câu hỏi", got, f"chậm nhất {max(lat_open[-n:]):.2f}s")

            # câu 2: 30 máy rớt mạng rồi vào lại giữa lúc trả lời
            dropped = phones[:30] if k == 1 else []
            for p in dropped:
                await p.drop()

            correct = k % 4

            async def answer(p):
                await asyncio.sleep(random.uniform(0, 2.0))
                if p in dropped:
                    await p.connect()
                    await asyncio.sleep(0.3)
                o = correct if p.i % 3 else (correct + 1) % 4
                r, dt = await p.send({"t": "ans", "q": seq, "o": o})
                r2, _ = await p.send({"t": "ans", "q": seq, "o": o})          # bấm dồn
                return r, dt, r2
            res = await asyncio.gather(*[answer(p) for p in phones])
            lat_ack += [dt for _, dt, _ in res if dt is not None]
            oks = sum(1 for r, _, _ in res if r == "ok")
            # máy trả lời cuối có thể nhận "closed" khi câu đã tự đóng ngay sau đó; đếm phía máy chủ
            await asyncio.sleep(0.5)
            snap = None
            for p in phones:
                if p.state:
                    snap = p.state
            check(f"Câu {k + 1}: mọi câu trả lời được ghi nhận, bấm dồn không tính 2 lần",
                  oks == n and all(r2 in ("dup", "closed") for _, _, r2 in res),
                  f"ok={oks}, dồn={sorted(set(r2 for _, _, r2 in res))}")
            got = await wait_all(phones, f"q{seq}:closed", 10)
            check(f"Câu {k + 1}: ai cũng trả lời thì tự đóng câu", got)
            t_click = time.perf_counter()
            await mc(a="quiz_reveal", qseq=seq)
            got = await wait_all(phones, f"q{seq}:reveal")
            lat_reveal += [p.seen[f"q{seq}:reveal"] - t_click for p in phones if f"q{seq}:reveal" in p.seen]
            check(f"Câu {k + 1}: cả {n} máy thấy đáp án", got)
            await mc(a="quiz_board", qseq=seq)
        await asyncio.sleep(0.6)
        me_scores = [p.state["me"]["score"] for p in phones]
        expect_right = sum(1 for p in phones if p.i % 3)
        check("Điểm đúng: người trả lời đúng có 1500–3000 điểm sau 3 câu, người sai 0 điểm",
              all((1500 <= s <= 3000) if p.i % 3 else s == 0 for p, s in zip(phones, me_scores)),
              f"{expect_right} người đúng")
        check("Mỗi máy biết hạng của mình và hạng nhóm", all(p.state["me"].get("rank") and p.state["me"].get("grank") for p in phones))

        # ── bình chọn ──
        groups = [{"label": g, "group": g} for g in GROUPS]
        await mc(a="vote_start", title="Thử bình chọn", opts=groups, no_self=True)
        await asyncio.sleep(0.5)
        vid = phones[0].state["c"]["vote"]["id"]

        async def vote(p):
            await asyncio.sleep(random.uniform(0, 1.5))
            target = (GROUPS.index(p.group) + 1 + p.i % 5) % len(GROUPS)
            return await p.send({"t": "vote", "v": vid, "o": target})
        res = await asyncio.gather(*[vote(p) for p in phones])
        st, _ = await mc(a="vote_close", id=vid)
        got = await wait_all(phones, f"v{vid}:False")
        early = [p for p in phones if "counts" in p.state["c"]["vote"]]
        check("Bình chọn: đang công bố thì chưa máy nào thấy kết quả", got and not early, len(early))
        for _ in range(80):                                   # chờ màn hình lớn công bố xong (~20 giây với 14 nhóm)
            if all(p.state["c"]["vote"].get("revealed") for p in phones):
                break
            await asyncio.sleep(0.5)
        counts = phones[0].state["c"]["vote"].get("counts", [])
        same = all(p.state["c"]["vote"].get("counts") == counts for p in phones)
        check(f"Bình chọn: {n} phiếu được đếm đủ, mọi máy cùng kết quả sau khi công bố", sum(counts) == n and same and all(r == "ok" for r, _ in res), f"tổng {sum(counts)}")

        # ── quay số: zombie ăn bánh quy (tối đa 150 tên) ──
        gname = {"sales-aw": "Sales AW", "sales-pgi": "Sales PGI", "admin": "Admin PGI + AW", "ecom": "Ecom AW + PGI",
                 "store": "Store AW + PGI", "mkt-aw": "MKT AW", "mkt-pgi": "MKT PGI", "accounting": "Accounting AW + PGI",
                 "warehouse": "Warehouse", "warranty": "Warranty", "car-audio": "Car Audio + Construction",
                 "it-legal": "IT + Legal", "hn": "CN Hà Nội", "dn": "CN Đà Nẵng"}
        names = [f"{p.name} ({gname[p.group]})" for p in phones[:150]]
        st, j = await mc(a="draw_roll", prize="Thử", seq=0, names=names)
        check("Quay số: MC gửi danh sách 150 tên", st == 200, j)
        for _ in range(120):
            await asyncio.sleep(0.5)
            if all(p.state["c"].get("draw", {}).get("phase") == "done" for p in phones):
                break
        winners = [p for p in phones if p.state["me"].get("won_now")]
        last = phones[0].state["c"]["draw"].get("last", {}).get("name")
        check("Quay số: mọi máy thấy cùng người trúng, đúng 1 máy báo trúng",
              last in names and len(winners) == 1 and f"{winners[0].name} ({gname[winners[0].group]})" == last, last)

        for p in phones:
            await p.drop()
        await mc(a="reset")

    summary = (f"\nĐộ trễ (giây) với {n} máy:\n"
               f"  MC bấm mở câu → máy thấy câu hỏi: trung vị {statistics.median(lat_open):.2f}, 95% {pct(lat_open, .95):.2f}, chậm nhất {max(lat_open):.2f}\n"
               f"  Người chơi bấm → máy chủ xác nhận: trung vị {statistics.median(lat_ack):.3f}, 95% {pct(lat_ack, .95):.3f}, chậm nhất {max(lat_ack):.3f}\n"
               f"  MC công bố → máy thấy đáp án:      trung vị {statistics.median(lat_reveal):.2f}, 95% {pct(lat_reveal, .95):.2f}, chậm nhất {max(lat_reveal):.2f}")
    print(summary)
    report.append(summary)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--key", default="k")
    ap.add_argument("--n", type=int, default=200)
    a = ap.parse_args()
    srv = None
    if not a.url:
        port = 5301
        env = dict(os.environ, MC_KEY=a.key, STATE_FILE="/tmp/_pa_load.json")
        env.pop("DATABASE_URL", None)
        srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(port), "--log-level", "warning"],
                               cwd=ROOT, env=env)
        time.sleep(2)
        a.url = f"http://localhost:{port}"
    report = []
    t0 = time.time()
    try:
        ok = asyncio.run(run(a.url.rstrip("/"), a.key, a.n, report))
    finally:
        if srv:
            try:
                f = open(f"/proc/{srv.pid}/stat").read().split()
                cpu = (int(f[13]) + int(f[14])) / os.sysconf("SC_CLK_TCK")
                print(f"\nCPU máy chủ đã dùng: {cpu:.1f}s trong {time.time() - t0:.0f}s chạy "
                      f"(trung bình {cpu / (time.time() - t0) * 100:.0f}% của 1 nhân)")
            except OSError:
                pass
            srv.terminate()
    print("\nKẾT QUẢ:", "ĐẠT" if ok else "CÓ LỖI")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
