"""Party App PGI 2026 — trò chơi tập thể trên điện thoại cho tiệc 100–200 người.

Ba màn hình:
  /        điện thoại người chơi (quét QR vào)
  /screen  màn hình lớn (máy chiếu / LED)
  /mc      bảng điều khiển của MC (cần MC_KEY)

Đồng bộ bằng WebSocket: máy chủ đẩy trạng thái xuống, gom thay đổi lại rồi gửi
tối đa vài lần mỗi giây, nên 150–300 máy cùng bấm vẫn nhẹ.

Chạy 1 tiến trình duy nhất (trạng thái nằm trong bộ nhớ, lưu xuống Postgres hoặc file):
  uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1
"""
import asyncio
import csv
import io
import json
import logging
import os
import re
import secrets
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("party")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE = Path(__file__).parent
EVENT = os.environ.get("EVENT_NAME", "Party App PGI 2026")
MC_KEY = os.environ.get("MC_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
STATE_FILE = os.environ.get("STATE_FILE", str(BASE / "state.json"))
MAX_PLAYERS = int(os.environ.get("MAX_PLAYERS", "400"))
BROADCAST_EVERY = 0.15      # giây: gom thay đổi rồi mới gửi
KEEPALIVE_EVERY = 15        # giây
GRACE = 0.8                 # giây: bù độ trễ mạng khi hết giờ
ROLL_SECS = 4.5             # giây quay số trên màn hình

GROUPS = [
    ("sales-aw", "Sales AW", "#E4572E"),
    ("sales-pgi", "Sales PGI", "#F3A712"),
    ("admin", "Admin PGI + AW", "#A8C256"),
    ("ecom", "Ecom AW + PGI", "#29B6A8"),
    ("store", "Store AW + PGI", "#4C9BE8"),
    ("mkt-aw", "MKT AW", "#B46EE8"),
    ("mkt-pgi", "MKT PGI", "#E86EB4"),
    ("accounting", "Accounting AW + PGI", "#E8D36E"),
    ("warehouse", "Warehouse", "#8FD6E8"),
    ("warranty", "Warranty", "#F08A6E"),
    ("car-audio", "Car Audio + Construction", "#7FE0A0"),
    ("it-legal", "IT + Legal", "#9AA7F5"),
    ("hn", "CN Hà Nội", "#F5B5C8"),
    ("dn", "CN Đà Nẵng", "#C9E86E"),
]
GROUP_NAME = {c: n for c, n, _ in GROUPS}

SAMPLE_QUESTIONS = [
    {"q": "Xe kéo của ông già Noel do con vật nào kéo?", "opts": ["Tuần lộc", "Ngựa", "Chó kéo xe", "Lạc đà"], "ans": 0, "time": 20},
    {"q": "Lễ Giáng sinh là ngày nào?", "opts": ["24/11", "25/12", "31/12", "1/1"], "ans": 1, "time": 15},
    {"q": "Làng ông già Noel Rovaniemi nằm ở nước nào?", "opts": ["Na Uy", "Thụy Điển", "Phần Lan", "Đan Mạch"], "ans": 2, "time": 20},
    {"q": "Tết Nguyên đán 2027 là năm con gì?", "opts": ["Ngựa", "Rồng", "Khỉ", "Dê"], "ans": 3, "time": 20},
    {"q": "Phím tắt nào để hoàn tác (undo) trên Windows?", "opts": ["Ctrl + Z", "Ctrl + Y", "Ctrl + U", "Ctrl + X"], "ans": 0, "time": 15},
    {"q": "Trong Excel, hàm nào tra cứu theo cột dọc?", "opts": ["SUMIF", "VLOOKUP", "HLOOKUP", "CONCAT"], "ans": 1, "time": 20},
    {"q": "Bài hát “Last Christmas” do nhóm nào trình bày?", "opts": ["ABBA", "Westlife", "Wham!", "Backstreet Boys"], "ans": 2, "time": 20},
    {"q": "Bánh Noel truyền thống của Pháp có hình khúc gỗ tên là gì?", "opts": ["Macaron", "Croissant", "Tiramisu", "Bûche de Noël"], "ans": 3, "time": 20},
    {"q": "Một năm có khoảng bao nhiêu tuần?", "opts": ["52", "48", "54", "50"], "ans": 0, "time": 15},
    {"q": "Ngôi sao trên cây thông Noel thường được đặt ở đâu?", "opts": ["Dưới gốc", "Trên ngọn", "Giữa thân", "Treo ở cửa"], "ans": 1, "time": 15},
]

VOTE_PRESETS = {
    "costume": "Nhóm có trang phục Noel đẹp nhất",
    "show": "Tiết mục văn nghệ được yêu thích nhất",
}


# ───────────────────────────── trạng thái ─────────────────────────────

def new_state():
    return {
        "players": {},          # pid -> {name, group, token, joined, key}
        "tokens": {},           # token -> pid
        "mode": "lobby",        # lobby | quiz | vote | draw
        "questions": [dict(q) for q in SAMPLE_QUESTIONS],
        "quiz": None,
        "vote": None,
        "draw": {"phase": "idle", "prize": "", "winners": [], "seq": 0, "only_online": True},
        "seq": 0,               # bộ đếm chung cho các phiên quiz/vote
    }


S = new_state()
CONNS = {}          # ws -> {"role": ..., "pid": ...}
ONLINE = {}         # pid -> số socket đang mở
RT = {"dirty": True, "last_send": 0.0, "last_ka": 0.0, "save_at": None, "saving": False}


def mono():
    return time.monotonic()


def touch(save=True):
    RT["dirty"] = True
    if save and RT["save_at"] is None:
        RT["save_at"] = mono() + 1.5


def norm_name(s):
    s = unicodedata.normalize("NFC", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:24]


def name_key(name, group):
    return unicodedata.normalize("NFC", name).casefold() + "|" + group


def is_online(pid):
    return ONLINE.get(pid, 0) > 0


def label(pid):
    p = S["players"].get(pid)
    return f'{p["name"]} ({GROUP_NAME.get(p["group"], "")})' if p else "?"


# ───────────────────────────── lưu / nạp ─────────────────────────────

def dump_state():
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if not str(k).startswith("_")}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return o
    return json.dumps(clean(S), ensure_ascii=False)


def _db_conn():
    import psycopg2
    return psycopg2.connect(DATABASE_URL, connect_timeout=5)


def save_sync(text):
    if DATABASE_URL:
        with _db_conn() as c, c.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS party_state (id int primary key, data text, at timestamptz default now())")
            cur.execute("INSERT INTO party_state (id, data) VALUES (1, %s) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, at = now()", (text,))
    else:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, STATE_FILE)


def load_sync():
    text = None
    try:
        if DATABASE_URL:
            with _db_conn() as c, c.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS party_state (id int primary key, data text, at timestamptz default now())")
                cur.execute("SELECT data FROM party_state WHERE id = 1")
                row = cur.fetchone()
                text = row[0] if row else None
        elif os.path.exists(STATE_FILE):
            text = open(STATE_FILE, encoding="utf-8").read()
    except Exception:
        log.exception("Không nạp được trạng thái, bắt đầu mới")
        return
    if not text:
        log.info("Chưa có trạng thái cũ, bắt đầu mới")
        return
    data = json.loads(text)
    S.clear()
    S.update(new_state())
    S.update(data)
    q = S.get("quiz")
    if q and q.get("phase") == "open":       # máy chủ khởi động lại giữa câu hỏi: đóng câu đó
        q["phase"] = "closed"
    d = S["draw"]
    if d.get("phase") == "rolling":            # đang quay dở: chốt luôn người đã chọn
        finish_roll()
    log.info("Đã nạp trạng thái: %d người chơi, chế độ %s", len(S["players"]), S["mode"])


async def save_now():
    RT["save_at"] = None
    RT["saving"] = True
    try:
        await asyncio.to_thread(save_sync, dump_state())
    except Exception:
        log.exception("Lưu trạng thái lỗi, sẽ thử lại")
        RT["save_at"] = mono() + 5
    finally:
        RT["saving"] = False


# ───────────────────────────── người chơi ─────────────────────────────

class GameError(Exception):
    pass


def join(name, group, token=None):
    if token and token in S["tokens"] and S["tokens"][token] in S["players"]:
        pid = S["tokens"][token]
        return pid, token
    name = norm_name(name)
    if not name:
        raise GameError("Bạn nhập tên giúp mình nhé.")
    if group not in GROUP_NAME:
        raise GameError("Bạn chọn nhóm của mình nhé.")
    key = name_key(name, group)
    for pid, p in S["players"].items():
        if p["key"] == key:
            if is_online(pid):
                raise GameError(f"Tên “{name}” ở nhóm {GROUP_NAME[group]} đang được dùng trên một máy khác. "
                                "Nếu đó là bạn, hãy đóng trình duyệt cũ rồi thử lại; nếu không, thêm họ hoặc biệt danh.")
            return pid, p["token"]          # vào lại trên máy mới
    if len(S["players"]) >= MAX_PLAYERS:
        raise GameError("Phòng đã đầy.")
    pid = secrets.token_hex(4)
    while pid in S["players"]:
        pid = secrets.token_hex(4)
    token = secrets.token_urlsafe(18)
    S["players"][pid] = {"name": name, "group": group, "token": token, "joined": time.time(), "key": key}
    S["tokens"][token] = pid
    touch()
    return pid, token


def remove_player(pid):
    p = S["players"].pop(pid, None)
    if not p:
        raise GameError("Không tìm thấy người chơi.")
    S["tokens"].pop(p["token"], None)
    q = S["quiz"]
    if q:
        q["answers"].pop(pid, None)
        q["scores"].pop(pid, None)
        q["ms"].pop(pid, None)
        if pid in q.get("alive", []):
            q["alive"].remove(pid)
    v = S["vote"]
    if v:
        v["votes"].pop(pid, None)
    touch()


# ───────────────────────────── quiz / rung chuông vàng ─────────────────────────────

def quiz_start(kind):
    if kind not in ("score", "bell"):
        raise GameError("Sai loại trò chơi.")
    if not S["questions"]:
        raise GameError("Chưa có câu hỏi nào.")
    S["seq"] += 1
    S["quiz"] = {"kind": kind, "id": S["seq"], "idx": -1, "phase": "ready", "qseq": 0,
                 "answers": {}, "scores": {}, "ms": {}, "gain": {}, "result": None,
                 "alive": list(S["players"]) if kind == "bell" else [], "started": False}
    S["mode"] = "quiz"
    touch()


def _quiz():
    q = S["quiz"]
    if not q or S["mode"] != "quiz":
        raise GameError("Chưa bắt đầu trò hỏi đáp.")
    return q


def eligible(q):
    if q["kind"] == "bell":
        return [p for p in q["alive"] if p in S["players"]]
    return list(S["players"])


def quiz_open(expect_idx):
    q = _quiz()
    if q["phase"] not in ("ready", "reveal", "board"):
        raise GameError("Câu hiện tại chưa xong.")
    if expect_idx != q["idx"]:
        raise GameError("Đã chuyển câu rồi (bấm trùng).")
    if q["idx"] + 1 >= len(S["questions"]):
        raise GameError("Hết câu hỏi rồi. Bấm “Kết thúc” để xem kết quả chung cuộc.")
    if q["kind"] == "bell" and not eligible(q):
        raise GameError("Không còn ai trong cuộc. Bấm “Hồi sinh tất cả” hoặc “Kết thúc”.")
    qq = S["questions"][q["idx"] + 1]
    q["idx"] += 1
    q["qseq"] += 1
    q["phase"] = "open"
    q["answers"] = {}
    q["gain"] = {}
    q["result"] = None
    q["started"] = True
    q["_open"] = mono()
    q["_deadline"] = mono() + int(qq.get("time") or 20)
    q["_expect"] = {p for p in eligible(q) if is_online(p)}   # ai online lúc mở câu thì được chờ
    touch()


def quiz_close(qseq):
    q = _quiz()
    if q["qseq"] != qseq or q["phase"] != "open":
        raise GameError("Câu này đã đóng.")
    q["phase"] = "closed"
    touch()


def quiz_reveal(qseq):
    q = _quiz()
    if q["qseq"] != qseq or q["phase"] not in ("open", "closed"):
        raise GameError("Đã công bố đáp án rồi.")
    qq = S["questions"][q["idx"]]
    limit = int(qq.get("time") or 20) * 1000
    counts = [0] * len(qq["opts"])
    right = []
    for pid, a in q["answers"].items():
        counts[a["o"]] += 1
        if a["o"] == qq["ans"]:
            right.append(pid)
            gain = 500 + round(500 * max(0.0, 1 - a["ms"] / limit))
            q["gain"][pid] = gain
            q["scores"][pid] = q["scores"].get(pid, 0) + gain
            q["ms"][pid] = q["ms"].get(pid, 0) + a["ms"]
    res = {"counts": counts, "ans": qq["ans"], "right": len(right), "total": len(q["answers"])}
    if q["kind"] == "bell":
        before = eligible(q)
        if right:
            q["alive"] = [p for p in before if p in set(right)]
            res["out"] = len(before) - len(q["alive"])
            res["saved_all"] = False
        else:
            res["out"] = 0
            res["saved_all"] = True      # không ai đúng thì cứu tất cả
    q["result"] = res
    q["phase"] = "reveal"
    touch()


def quiz_board(qseq):
    q = _quiz()
    if q["qseq"] != qseq or q["phase"] != "reveal":
        raise GameError("Chưa công bố đáp án.")
    q["phase"] = "board"
    touch()


def quiz_end():
    q = _quiz()
    if q["phase"] == "open":
        raise GameError("Đang mở câu hỏi, đóng câu trước đã.")
    q["phase"] = "end"
    touch()


def bell_revive():
    q = _quiz()
    if q["kind"] != "bell":
        raise GameError("Chỉ dùng cho Rung chuông vàng.")
    if q["phase"] == "open":
        raise GameError("Đang mở câu hỏi, chờ đóng câu đã.")
    q["alive"] = list(S["players"])
    touch()


def answer(pid, qseq, o):
    q = S["quiz"]
    if S["mode"] != "quiz" or not q or q["phase"] != "open" or q["qseq"] != qseq:
        return "closed"
    if mono() > q["_deadline"] + GRACE:
        return "closed"
    if q["kind"] == "bell" and pid not in q["alive"]:
        return "out"
    if pid in q["answers"]:
        return "dup"
    qq = S["questions"][q["idx"]]
    if not isinstance(o, int) or not 0 <= o < len(qq["opts"]):
        return "bad"
    q["answers"][pid] = {"o": o, "ms": int((mono() - q["_open"]) * 1000)}
    touch(save=False)
    expect = (q.get("_expect", set()) | {p for p in eligible(q) if is_online(p)}) & set(eligible(q))
    if expect and all(p in q["answers"] for p in expect):
        q["phase"] = "closed"           # ai cũng trả lời rồi thì đóng sớm
        touch()
    return "ok"


def quiz_tick():
    q = S["quiz"]
    if S["mode"] == "quiz" and q and q["phase"] == "open" and mono() > q["_deadline"] + GRACE:
        q["phase"] = "closed"
        touch()


def ranking(q):
    rows = [(pid, q["scores"].get(pid, 0), q["ms"].get(pid, 0)) for pid in S["players"]]
    rows.sort(key=lambda r: (-r[1], r[2], S["players"][r[0]]["joined"]))
    return rows


def group_board(q):
    tot, cnt = {}, {}
    for pid, p in S["players"].items():
        cnt[p["group"]] = cnt.get(p["group"], 0) + 1
        tot[p["group"]] = tot.get(p["group"], 0) + (q["scores"].get(pid, 0) if q else 0)
    rows = [{"code": c, "name": n, "color": col, "members": cnt[c], "avg": round(tot[c] / cnt[c])}
            for c, n, col in GROUPS if cnt.get(c)]
    rows.sort(key=lambda r: (-r["avg"], -r["members"]))
    return rows


# ───────────────────────────── bình chọn ─────────────────────────────

def vote_start(title, opts, no_self):
    title = (title or "").strip()[:80] or "Bình chọn"
    clean = []
    for o in opts:
        lab = (o.get("label") or "").strip()[:40]
        if lab:
            clean.append({"label": lab, "group": o.get("group") if o.get("group") in GROUP_NAME else None})
    if len(clean) < 2:
        raise GameError("Cần ít nhất 2 lựa chọn.")
    if len(clean) > 20:
        raise GameError("Tối đa 20 lựa chọn.")
    S["seq"] += 1
    S["vote"] = {"id": S["seq"], "title": title, "opts": clean, "open": True, "votes": {},
                 "no_self": bool(no_self) and any(o["group"] for o in clean)}
    S["mode"] = "vote"
    touch()


def vote_cast(pid, vid, o):
    v = S["vote"]
    if S["mode"] != "vote" or not v or not v["open"] or v["id"] != vid:
        return "closed"
    if not isinstance(o, int) or not 0 <= o < len(v["opts"]):
        return "bad"
    if v["no_self"] and v["opts"][o]["group"] == S["players"][pid]["group"]:
        return "self"
    if v["votes"].get(pid) == o:
        return "dup"
    v["votes"][pid] = o
    touch()
    return "ok"


def vote_close(vid):
    v = S["vote"]
    if not v or v["id"] != vid or not v["open"]:
        raise GameError("Bình chọn đã đóng.")
    v["open"] = False
    touch()


def vote_counts(v):
    c = [0] * len(v["opts"])
    for o in v["votes"].values():
        if 0 <= o < len(c):
            c[o] += 1
    return c


# ───────────────────────────── quay số ─────────────────────────────

def draw_pool():
    d = S["draw"]
    taken = {w["pid"] for w in d["winners"]}
    return [pid for pid in S["players"] if pid not in taken and (is_online(pid) or not d["only_online"])]


def draw_roll(prize, expect_seq, only_online=True):
    d = S["draw"]
    if d["phase"] == "rolling":
        raise GameError("Đang quay.")
    if expect_seq != d["seq"]:
        raise GameError("Vừa quay xong rồi (bấm trùng).")
    d["only_online"] = bool(only_online)
    pool = draw_pool()
    if not pool:
        raise GameError("Không còn ai để quay (mọi người đã trúng hoặc không ai online).")
    d["seq"] += 1
    d["prize"] = (prize or "").strip()[:60]
    d["phase"] = "rolling"
    d["pick"] = secrets.choice(pool)     # lưu cả khi máy chủ khởi động lại; không gửi xuống máy nào
    d["_until"] = mono() + ROLL_SECS
    sample = list(pool)
    secrets.SystemRandom().shuffle(sample)
    d["_names"] = [label(p) for p in sample[:80]]
    S["mode"] = "draw"
    touch()


def finish_roll():
    d = S["draw"]
    pid = d.pop("pick", None)
    d.pop("_until", None)
    d.pop("_names", None)
    d["phase"] = "done"
    if pid and pid in S["players"]:
        p = S["players"][pid]
        d["winners"].append({"pid": pid, "name": p["name"], "group": p["group"], "prize": d["prize"], "absent": False})
    touch()


def draw_tick():
    d = S["draw"]
    if d["phase"] == "rolling" and mono() >= d.get("_until", 0):
        finish_roll()


def draw_absent(seq):
    d = S["draw"]
    if d["seq"] != seq or d["phase"] != "done" or not d["winners"] or d["winners"][-1]["absent"]:
        raise GameError("Không có kết quả để huỷ.")
    d["winners"][-1]["absent"] = True     # người vắng không quay lại vòng sau
    touch()


# ───────────────────────────── câu hỏi ─────────────────────────────

def parse_questions(text):
    """Dán từ Google Sheet: Câu hỏi | A | B | C | D | Đáp án (A/B/C/D) | Giây (tuỳ chọn)."""
    out, errors = [], []
    lines = [ln for ln in (text or "").replace("\r\n", "\n").split("\n") if ln.strip()]
    for n, ln in enumerate(lines, 1):
        row = ln.split("\t") if "\t" in ln else next(csv.reader([ln]))
        row = [c.strip() for c in row]
        if len(row) < 4:
            errors.append(f"Dòng {n}: thiếu cột")
            continue
        q = row[0]
        secs = 20
        if len(row) >= 6:               # đúng mẫu cột: Câu | A | B | C | D | Đáp án | Giây
            opts = [c for c in row[1:5] if c]
            ans_raw = row[5]
            if len(row) >= 7 and re.fullmatch(r"\d{1,3}", row[6]):
                secs = max(5, min(120, int(row[6])))
        else:                           # dòng ngắn: Câu | lựa chọn… | Đáp án
            opts = [c for c in row[1:-1] if c]
            ans_raw = row[-1]
        ans = None
        a = ans_raw.strip().upper()
        if a in ("A", "B", "C", "D"):
            ans = "ABCD".index(a)
        elif a in ("1", "2", "3", "4"):
            ans = int(a) - 1
        else:
            for i, o in enumerate(opts):
                if o.casefold() == ans_raw.casefold():
                    ans = i
        if ans is None:
            if n == 1:
                continue        # dòng tiêu đề
            errors.append(f"Dòng {n}: không hiểu đáp án “{ans_raw}”")
            continue
        if not q or len(opts) < 2:
            errors.append(f"Dòng {n}: cần câu hỏi và ít nhất 2 lựa chọn")
            continue
        if ans >= len(opts):
            errors.append(f"Dòng {n}: đáp án {a} không có trong các lựa chọn")
            continue
        out.append({"q": q[:200], "opts": [o[:80] for o in opts], "ans": ans, "time": secs})
    return out, errors


# ───────────────────────────── góc nhìn gửi xuống ─────────────────────────────

def groups_summary():
    cnt = {}
    for p in S["players"].values():
        cnt[p["group"]] = cnt.get(p["group"], 0) + 1
    return [{"code": c, "name": n, "color": col, "count": cnt.get(c, 0)} for c, n, col in GROUPS]


def quiz_public(q, for_mc=False):
    if not q:
        return None
    qq = S["questions"][q["idx"]] if 0 <= q["idx"] < len(S["questions"]) else None
    out = {"kind": q["kind"], "id": q["id"], "idx": q["idx"], "total": len(S["questions"]), "phase": q["phase"],
           "qseq": q["qseq"], "answered": len(q["answers"]), "eligible": len(eligible(q)),
           "eligible_online": len([p for p in eligible(q) if is_online(p)])}
    if qq:
        out.update(q=qq["q"], opts=qq["opts"], time=int(qq.get("time") or 20))
        if q["phase"] == "open":
            out["left_ms"] = max(0, int((q["_deadline"] - mono()) * 1000))
        if q["phase"] in ("reveal", "board", "end") or for_mc:
            out["ans"] = qq["ans"] if (q["phase"] in ("reveal", "board", "end") or for_mc) else None
    if q["phase"] in ("closed",) or for_mc:
        c = [0] * len(qq["opts"]) if qq else []
        for a in q["answers"].values():
            if a["o"] < len(c):
                c[a["o"]] += 1
        out["live_counts"] = c
    if q["result"]:
        out["result"] = q["result"]
    if q["kind"] == "bell":
        alive = eligible(q)
        out["alive"] = len(alive)
        if len(alive) <= 12:
            out["alive_names"] = [label(p) for p in alive]
    if q["phase"] in ("board", "end") or for_mc:
        rk = ranking(q)
        out["top"] = [{"name": S["players"][p]["name"], "group": GROUP_NAME[S["players"][p]["group"]], "score": s}
                      for p, s, _ in rk[:10]]
        out["groups"] = group_board(q)
    return out


def vote_public(v, reveal=True):
    if not v:
        return None
    out = {"id": v["id"], "title": v["title"], "opts": v["opts"], "open": v["open"], "no_self": v["no_self"],
           "total": len(v["votes"])}
    if reveal:
        out["counts"] = vote_counts(v)
    return out


def draw_public(full_pool=False):
    d = S["draw"]
    out = {"phase": d["phase"], "prize": d["prize"], "seq": d["seq"], "only_online": d["only_online"],
           "winners": [{"name": w["name"], "group": GROUP_NAME.get(w["group"], ""), "prize": w["prize"], "absent": w["absent"]}
                       for w in d["winners"]],
           "pool": len(draw_pool())}
    if d["phase"] == "rolling":
        out["left_ms"] = max(0, int((d["_until"] - mono()) * 1000))
        if full_pool:
            out["names"] = d.get("_names", [])
    if d["phase"] == "done" and d["winners"]:
        out["last"] = out["winners"][-1]
    return out


def screen_view():
    recent = sorted(S["players"].values(), key=lambda p: -p["joined"])[:14]
    return {"t": "state", "event": EVENT, "mode": S["mode"], "players": len(S["players"]),
            "online": len([p for p in S["players"] if is_online(p)]),
            "groups": groups_summary(), "recent": [{"name": p["name"], "group": p["group"]} for p in recent],
            "quiz": quiz_public(S["quiz"]) if S["mode"] == "quiz" else None,
            "vote": vote_public(S["vote"]) if S["mode"] == "vote" else None,
            "draw": draw_public(full_pool=True) if S["mode"] == "draw" else None}


def mc_view():
    v = screen_view()
    v["role"] = "mc"
    v["quiz"] = quiz_public(S["quiz"], for_mc=True) if S["mode"] == "quiz" else None
    v["vote"] = vote_public(S["vote"]) if S["mode"] == "vote" else None
    v["draw"] = draw_public()
    v["questions"] = [{"q": q["q"], "opts": q["opts"], "ans": q["ans"], "time": q.get("time", 20)} for q in S["questions"]]
    v["roster"] = sorted(({"pid": pid, "name": p["name"], "group": p["group"], "online": is_online(pid)}
                          for pid, p in S["players"].items()), key=lambda r: (r["group"], r["name"].casefold()))
    v["presets"] = VOTE_PRESETS
    return v


def player_common():
    q = S["quiz"] if S["mode"] == "quiz" else None
    common = {"t": "state", "event": EVENT, "mode": S["mode"], "groups": [{"code": c, "name": n, "color": col} for c, n, col in GROUPS]}
    if q:
        qq = S["questions"][q["idx"]] if 0 <= q["idx"] < len(S["questions"]) else None
        cq = {"kind": q["kind"], "idx": q["idx"], "total": len(S["questions"]), "phase": q["phase"], "qseq": q["qseq"]}
        if qq and q["phase"] != "ready":
            cq.update(q=qq["q"], opts=qq["opts"])
            if q["phase"] == "open":
                cq["left_ms"] = max(0, int((q["_deadline"] - mono()) * 1000))
            if q["phase"] in ("reveal", "board", "end"):
                cq["ans"] = qq["ans"]
        if q["kind"] == "bell":
            cq["alive"] = len(eligible(q))
        common["quiz"] = cq
    if S["mode"] == "vote" and S["vote"]:
        common["vote"] = vote_public(S["vote"], reveal=not S["vote"]["open"])
    if S["mode"] == "draw":
        common["draw"] = draw_public()
    return common


def player_me(pid, rank_of, grank_of):
    p = S["players"][pid]
    me = {"pid": pid, "name": p["name"], "group": p["group"], "group_name": GROUP_NAME[p["group"]]}
    q = S["quiz"] if S["mode"] == "quiz" else None
    if q:
        a = q["answers"].get(pid)
        me["answer"] = a["o"] if a else None
        me["score"] = q["scores"].get(pid, 0)
        me["gain"] = q["gain"].get(pid, 0)
        me["rank"] = rank_of.get(pid)
        me["grank"] = grank_of.get(p["group"])
        if q["kind"] == "bell":
            me["alive"] = pid in q["alive"]
            me["late"] = pid not in q["alive"] and not q["started"]
    if S["mode"] == "vote" and S["vote"]:
        me["vote"] = S["vote"]["votes"].get(pid)
    d = S["draw"]
    won = [w for w in d["winners"] if w["pid"] == pid and not w["absent"]]
    if won:
        me["won"] = won[-1]["prize"] or "giải thưởng"
        me["won_now"] = d["phase"] == "done" and d["winners"][-1]["pid"] == pid
    return me


# ───────────────────────────── phát sóng ─────────────────────────────

async def _send(ws, text):
    try:
        await asyncio.wait_for(ws.send_text(text), timeout=3)
        return None
    except Exception:
        return ws


async def broadcast():
    RT["dirty"] = False
    RT["last_send"] = mono()
    if not CONNS:
        return
    sv = mv = None
    common_json = None
    rank_of, grank_of = {}, {}
    q = S["quiz"] if S["mode"] == "quiz" else None
    if q:
        rank_of = {pid: i + 1 for i, (pid, _, _) in enumerate(ranking(q))}
        grank_of = {g["code"]: i + 1 for i, g in enumerate(group_board(q))}
    jobs = []
    for ws, info in list(CONNS.items()):
        role = info["role"]
        if role == "screen":
            sv = sv or json.dumps(screen_view(), ensure_ascii=False)
            jobs.append(_send(ws, sv))
        elif role == "mc":
            mv = mv or json.dumps(mc_view(), ensure_ascii=False)
            jobs.append(_send(ws, mv))
        else:
            pid = info["pid"]
            if pid not in S["players"]:
                jobs.append(_send(ws, '{"t":"nojoin"}'))
                continue
            common_json = common_json or json.dumps(player_common(), ensure_ascii=False)
            me = json.dumps(player_me(pid, rank_of, grank_of), ensure_ascii=False)
            jobs.append(_send(ws, '{"t":"state","c":' + common_json + ',"me":' + me + "}"))
    dead = [w for w in await asyncio.gather(*jobs) if w is not None]
    for ws in dead:
        await drop(ws)


async def send_one(ws):
    info = CONNS.get(ws)
    if not info:
        return
    if info["role"] == "screen":
        text = json.dumps(screen_view(), ensure_ascii=False)
    elif info["role"] == "mc":
        text = json.dumps(mc_view(), ensure_ascii=False)
    else:
        pid = info["pid"]
        if pid not in S["players"]:
            text = '{"t":"nojoin"}'
        else:
            q = S["quiz"] if S["mode"] == "quiz" else None
            rank_of = {p: i + 1 for i, (p, _, _) in enumerate(ranking(q))} if q else {}
            grank_of = {g["code"]: i + 1 for i, g in enumerate(group_board(q))} if q else {}
            text = json.dumps({"t": "state", "c": player_common(), "me": player_me(pid, rank_of, grank_of)}, ensure_ascii=False)
    await _send(ws, text)


async def drop(ws):
    info = CONNS.pop(ws, None)
    if info and info.get("pid"):
        pid = info["pid"]
        ONLINE[pid] = max(0, ONLINE.get(pid, 1) - 1)
        if not ONLINE[pid]:
            ONLINE.pop(pid, None)
        touch(save=False)
    try:
        await ws.close()
    except Exception:
        pass


async def ticker():
    while True:
        try:
            quiz_tick()
            draw_tick()
            now = mono()
            if RT["dirty"] and now - RT["last_send"] >= BROADCAST_EVERY:
                await broadcast()
            elif now - RT["last_ka"] >= KEEPALIVE_EVERY:
                RT["last_ka"] = now
                await asyncio.gather(*[_send(ws, '{"t":"ka"}') for ws in list(CONNS)])
            if RT["save_at"] is not None and now >= RT["save_at"] and not RT["saving"]:
                asyncio.create_task(save_now())
        except Exception:
            log.exception("ticker")
        await asyncio.sleep(0.05)


# ───────────────────────────── app ─────────────────────────────

@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(load_sync)
    task = asyncio.create_task(ticker())
    if not MC_KEY:
        log.warning("Chưa đặt MC_KEY: trang /mc sẽ bị khoá.")
    yield
    task.cancel()
    try:
        await asyncio.to_thread(save_sync, dump_state())
    except Exception:
        log.exception("Lưu khi tắt lỗi")


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


def page(name):
    html = (BASE / "static" / name).read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{EVENT}}", EVENT), headers={"Cache-Control": "no-store"})


@app.get("/")
def player_page():
    return page("player.html")


@app.get("/screen")
def screen_page():
    return page("screen.html")


def mc_ok(request_or_ws):
    if not MC_KEY:
        return False
    got = request_or_ws.cookies.get("mc") or request_or_ws.headers.get("x-mc-key") or ""
    return secrets.compare_digest(got.encode(), MC_KEY.encode())


@app.get("/mc")
def mc_page(request: Request, key: str = ""):
    if not MC_KEY:
        return HTMLResponse("<p style='font:18px sans-serif;padding:24px'>Chưa đặt biến môi trường MC_KEY trên máy chủ.</p>", status_code=503)
    if key:
        if not secrets.compare_digest(key.encode(), MC_KEY.encode()):
            return HTMLResponse("<p style='font:18px sans-serif;padding:24px'>Sai mã MC.</p>", status_code=403)
        r = RedirectResponse("/mc", status_code=303)
        r.set_cookie("mc", key, max_age=7 * 86400, httponly=True, samesite="lax")
        return r
    if not mc_ok(request):
        return HTMLResponse("<p style='font:18px sans-serif;padding:24px'>Mở bằng đường link có mã MC: /mc?key=…</p>", status_code=403)
    return page("mc.html")


@app.get("/health")
def health():
    return {"ok": True, "players": len(S["players"]), "conns": len(CONNS)}


@app.get("/qr.svg")
def qr(u: str):
    import qrcode
    import qrcode.image.svg
    if not re.match(r"^https?://", u) or len(u) > 200:
        return Response(status_code=400)
    code = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(u)
    code.make(fit=True)
    img = code.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    return Response(img.to_string(encoding="unicode"), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.post("/api/join")
async def api_join(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        pid, token = join(body.get("name", ""), body.get("group", ""), body.get("token"))
    except GameError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    return {"token": token, "pid": pid}


MC_ACTIONS = {
    "quiz_start": lambda b: quiz_start(b.get("kind")),
    "quiz_open": lambda b: quiz_open(int(b.get("idx", -99))),
    "quiz_close": lambda b: quiz_close(int(b.get("qseq", -1))),
    "quiz_reveal": lambda b: quiz_reveal(int(b.get("qseq", -1))),
    "quiz_board": lambda b: quiz_board(int(b.get("qseq", -1))),
    "quiz_end": lambda b: quiz_end(),
    "bell_revive": lambda b: bell_revive(),
    "vote_start": lambda b: vote_start(b.get("title"), b.get("opts") or [], b.get("no_self")),
    "vote_close": lambda b: vote_close(int(b.get("id", -1))),
    "draw_roll": lambda b: draw_roll(b.get("prize"), int(b.get("seq", -1)), b.get("only_online", True)),
    "draw_absent": lambda b: draw_absent(int(b.get("seq", -1))),
    "remove_player": lambda b: remove_player(b.get("pid")),
}


@app.post("/api/mc")
async def api_mc(request: Request):
    if not mc_ok(request):
        return JSONResponse({"error": "Chưa đăng nhập MC."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Dữ liệu không hợp lệ."}, status_code=400)
    a = body.get("a")
    try:
        if a == "mode":
            m = body.get("mode")
            if m not in ("lobby", "quiz", "vote", "draw"):
                raise GameError("Sai chế độ.")
            if m == "quiz" and not S["quiz"]:
                raise GameError("Chưa có trò hỏi đáp.")
            if m == "vote" and not S["vote"]:
                raise GameError("Chưa có bình chọn.")
            if S["mode"] == "quiz" and S["quiz"] and S["quiz"]["phase"] == "open":
                S["quiz"]["phase"] = "closed"
            S["mode"] = m
            touch()
        elif a == "questions":
            qs, errors = parse_questions(body.get("text", ""))
            if errors:
                return JSONResponse({"error": "Chưa lưu. " + "; ".join(errors[:6])}, status_code=400)
            if not qs:
                raise GameError("Không đọc được câu hỏi nào.")
            if S["mode"] == "quiz" and S["quiz"] and S["quiz"]["phase"] not in ("end",):
                raise GameError("Đang chơi hỏi đáp, kết thúc trò rồi mới đổi câu hỏi.")
            S["questions"] = qs
            touch()
            return {"ok": True, "count": len(qs)}
        elif a == "reset":
            keep_q = S["questions"]
            S.clear()
            S.update(new_state())
            S["questions"] = keep_q
            for ws, info in list(CONNS.items()):
                if info["role"] == "player":
                    info["pid"] = None
            ONLINE.clear()
            touch()
        elif a in MC_ACTIONS:
            MC_ACTIONS[a](body)
        else:
            raise GameError("Lệnh không rõ.")
    except GameError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except (TypeError, ValueError):
        return JSONResponse({"error": "Dữ liệu không hợp lệ."}, status_code=400)
    await broadcast()          # MC bấm thì gửi ngay, không chờ nhịp
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    role = ws.query_params.get("role", "player")
    pid = None
    if role == "mc":
        if not mc_ok(ws):
            await ws.close(code=4403)
            return
    elif role == "player":
        token = ws.query_params.get("token", "")
        pid = S["tokens"].get(token)
    elif role != "screen":
        await ws.close(code=4400)
        return
    await ws.accept()
    if role == "player" and (not pid or pid not in S["players"]):
        await ws.send_text('{"t":"nojoin"}')
        await ws.close()
        return
    CONNS[ws] = {"role": role, "pid": pid}
    if pid:
        ONLINE[pid] = ONLINE.get(pid, 0) + 1
        touch(save=False)
    await send_one(ws)
    try:
        while True:
            text = await ws.receive_text()
            if len(text) > 500:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            t = msg.get("t")
            info = CONNS.get(ws)
            if not info:
                break
            if t == "ping":
                await _send(ws, '{"t":"pong"}')
            elif info["role"] == "player" and info["pid"] in S["players"]:
                if t == "ans":
                    r = answer(info["pid"], msg.get("q"), msg.get("o"))
                    await _send(ws, json.dumps({"t": "ack", "k": "ans", "r": r}))
                    await send_one(ws)            # cho người bấm thấy ngay đã chốt
                elif t == "vote":
                    r = vote_cast(info["pid"], msg.get("v"), msg.get("o"))
                    await _send(ws, json.dumps({"t": "ack", "k": "vote", "r": r}))
                    await send_one(ws)
            elif info["role"] == "player":
                await _send(ws, '{"t":"nojoin"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws")
    finally:
        await drop(ws)
