import json
import re
from pathlib import Path

import pytest

from conftest import A, MC, join, mc, until, state_of

ROOT = Path(__file__).resolve().parents[1]


def players(c, *names, group="it-legal"):
    return [join(c, n, group).json()["token"] for n in names]


def pws(c, token):
    return c.websocket_connect("/ws?role=player&token=" + token)


# ── vào phòng ──

def test_join_validation_and_reentry(c):
    assert join(c, "", "it-legal").status_code == 409
    assert join(c, "An", "khong-co").status_code == 409
    t = join(c, "  An   Nguyễn ", "it-legal").json()["token"]
    assert A.S["players"][A.S["tokens"][t]]["name"] == "An Nguyễn"
    # cùng token: cùng người
    assert join(c, "x", "hn", token=t).json()["token"] == t
    # cùng tên + nhóm, máy cũ offline: vào lại đúng người, không tạo trùng
    assert join(c, "an nguyễn", "it-legal").json()["token"] == t
    assert len(A.S["players"]) == 1
    # cùng tên khác nhóm: người khác
    assert join(c, "An Nguyễn", "hn").json()["token"] != t
    assert len(A.S["players"]) == 2


def test_same_name_blocked_while_other_phone_online(c):
    t = join(c, "Bình").json()["token"]
    with pws(c, t) as ws:
        state_of(ws)
        r = join(c, "Bình")
        assert r.status_code == 409 and "máy khác" in r.json()["error"]
    # máy cũ thoát: vào lại được
    assert join(c, "Bình").json()["token"] == t


def test_bad_token_gets_nojoin(c):
    with pws(c, "sai") as ws:
        assert ws.receive_json()["t"] == "nojoin"


def test_mc_requires_key(c):
    with pytest.raises(Exception):
        with c.websocket_connect("/ws?role=mc") as ws:
            ws.receive_json()
    assert c.post("/api/mc", json={"a": "quiz_start", "kind": "score"}).status_code == 403
    assert c.get("/mc", follow_redirects=False).status_code == 403
    assert c.get("/mc?key=sai", follow_redirects=False).status_code == 403
    r = c.get("/mc?key=k", follow_redirects=False)
    assert r.status_code == 303 and "mc=k" in r.headers["set-cookie"]
    with c.websocket_connect("/ws?role=mc") as ws:          # có cookie thì vào được
        assert ws.receive_json()["role"] == "mc"


# ── đố vui tính điểm ──

def test_quiz_scoring_speed_and_groups(c, clock):
    t1 = join(c, "Nhanh", "it-legal").json()["token"]
    t2 = join(c, "Chậm", "hn").json()["token"]
    t3 = join(c, "Sai", "hn").json()["token"]
    assert mc(c, a="quiz_start", kind="score").status_code == 200
    with pws(c, t1) as w1, pws(c, t2) as w2, pws(c, t3) as w3:
        assert mc(c, a="quiz_open", idx=-1).status_code == 200
        assert mc(c, a="quiz_open", idx=-1).status_code == 400          # bấm trùng không nhảy 2 câu
        m = state_of(w1, lambda m: m["c"]["quiz"]["phase"] == "open")
        q = m["c"]["quiz"]
        assert "ans" not in q, "không được lộ đáp án xuống điện thoại khi đang mở"
        ans = A.S["quiz"]["qs"][0]["ans"]
        w1.send_json({"t": "ans", "q": q["qseq"], "o": ans})
        assert until(w1, lambda m: m.get("t") == "ack")["r"] == "ok"
        w1.send_json({"t": "ans", "q": q["qseq"], "o": (ans + 1) % 4})      # đổi ý: không được
        assert until(w1, lambda m: m.get("t") == "ack")["r"] == "dup"
        clock[0] += 10
        w2.send_json({"t": "ans", "q": q["qseq"], "o": ans})
        until(w2, lambda m: m.get("t") == "ack")
        w3.send_json({"t": "ans", "q": q["qseq"] + 5, "o": ans})           # câu cũ/khác
        assert until(w3, lambda m: m.get("t") == "ack")["r"] == "closed"
        w3.send_json({"t": "ans", "q": q["qseq"], "o": (ans + 1) % 4})
        until(w3, lambda m: m.get("t") == "ack")
        qz = A.S["quiz"]
        assert qz["phase"] == "closed", "ai online cũng trả lời rồi thì tự đóng"
        assert mc(c, a="quiz_reveal", qseq=qz["qseq"]).status_code == 200
        assert mc(c, a="quiz_reveal", qseq=qz["qseq"]).status_code == 400
    s = qz["scores"]
    pid = lambda t: A.S["tokens"][t]
    assert 500 <= s[pid(t2)] < s[pid(t1)] <= 1000
    assert pid(t3) not in s
    assert qz["result"]["counts"][ans] == 2 and qz["result"]["right"] == 2
    assert [f["name"] for f in qz["result"]["fastest"]] == ["Nhanh", "Chậm"], "xếp người đúng theo tốc độ, không có người sai"
    gb = A.group_board(qz)
    hn = next(g for g in gb if g["code"] == "hn")
    assert hn["avg"] == round(s[pid(t2)] / 2), "điểm nhóm = trung bình mỗi người"
    assert A.ranking(qz)[0][0] == pid(t1)


def test_answer_after_deadline_rejected_and_autoclose(c, clock):
    t = join(c, "A").json()["token"]
    join(c, "B")                       # B không online: không chặn tự đóng
    mc(c, a="quiz_start", kind="score")
    mc(c, a="quiz_open", idx=-1)
    q = A.S["quiz"]
    with pws(c, t) as ws:
        state_of(ws)
        clock[0] += A.S["quiz"]["qs"][0]["time"] + A.GRACE + 0.5
        ws.send_json({"t": "ans", "q": q["qseq"], "o": 0})
        assert until(ws, lambda m: m.get("t") == "ack")["r"] == "closed"
        m = state_of(ws, lambda m: m["c"]["quiz"]["phase"] == "closed")
        assert m["me"]["answer"] is None


def test_quiz_flow_guards(c):
    join(c, "A")
    assert mc(c, a="quiz_open", idx=-1).status_code == 400          # chưa bắt đầu
    mc(c, a="questions", set="score", text="Q1\tx\ty\tA\nQ2\tx\ty\tB")
    mc(c, a="quiz_start", kind="score", shuffle=False)
    assert mc(c, a="quiz_board", qseq=0).status_code == 400
    mc(c, a="quiz_open", idx=-1)
    assert mc(c, a="quiz_end").status_code == 400                    # đang mở
    assert mc(c, a="questions", set="score", text="Q\ta\tb\tA").status_code == 200  # sửa bộ câu hỏi giữa chừng được
    assert len(A.S["quiz"]["qs"]) == 2, "trò đang chơi giữ bản sao riêng, không bị ảnh hưởng"
    qs = A.S["quiz"]["qseq"]
    mc(c, a="quiz_reveal", qseq=qs)
    mc(c, a="quiz_board", qseq=qs)
    mc(c, a="quiz_open", idx=0)
    mc(c, a="quiz_reveal", qseq=A.S["quiz"]["qseq"])
    r = mc(c, a="quiz_open", idx=1)
    assert r.status_code == 400 and "Hết câu" in r.json()["error"]
    assert mc(c, a="quiz_end").status_code == 200


# ── rung chuông vàng ──

def test_bell_elimination_saved_all_late_and_revive(c):
    ta, tb, tc = players(c, "A", "B", "C")
    mc(c, a="questions", set="bell", text="Q1\tx\ty\tA\nQ2\tx\ty\tA\nQ3\tx\ty\tA")
    mc(c, a="quiz_start", kind="bell")
    with pws(c, ta) as wa, pws(c, tb) as wb, pws(c, tc) as wc:
        mc(c, a="quiz_open", idx=-1)
        q = A.S["quiz"]["qseq"]
        for w, o in ((wa, 0), (wb, 1), (wc, 0)):
            w.send_json({"t": "ans", "q": q, "o": o}); until(w, lambda m: m.get("t") == "ack")
        mc(c, a="quiz_reveal", qseq=q)
        alive = set(A.S["quiz"]["alive"])
        pid = lambda t: A.S["tokens"][t]
        assert alive == {pid(ta), pid(tc)}
        # người vào muộn sau khi đã mở câu: chỉ xem
        td = join(c, "D").json()["token"]
        with pws(c, td) as wd:
            me = state_of(wd)["me"]
            assert me["alive"] is False and me["late"] is False
            mc(c, a="quiz_board", qseq=q)
            mc(c, a="quiz_open", idx=0)
            q2 = A.S["quiz"]["qseq"]
            wb.send_json({"t": "ans", "q": q2, "o": 0})
            assert until(wb, lambda m: m.get("t") == "ack")["r"] == "out"
            wd.send_json({"t": "ans", "q": q2, "o": 0})
            assert until(wd, lambda m: m.get("t") == "ack")["r"] == "out"
            for w in (wa, wc):                      # cả hai còn lại đều sai
                w.send_json({"t": "ans", "q": q2, "o": 1}); until(w, lambda m: m.get("t") == "ack")
            mc(c, a="quiz_reveal", qseq=q2)
            assert A.S["quiz"]["result"]["saved_all"] is True
            m = state_of(wa, lambda m: m["c"]["quiz"]["phase"] == "reveal")
            assert m["c"]["quiz"]["saved_all"] is True and m["me"]["alive"] is True, "điện thoại phải biết là được giữ lại vì không ai đúng"
            assert set(A.S["quiz"]["alive"]) == {pid(ta), pid(tc)}
            assert mc(c, a="bell_revive").status_code == 200
            assert len(A.S["quiz"]["alive"]) == 4


# ── bình chọn ──

def test_vote_no_self_change_and_close(c, clock):
    ta = join(c, "A", "hn").json()["token"]
    tb = join(c, "B", "dn").json()["token"]
    opts = [{"label": n, "group": g} for g, n, _ in A.GROUPS]
    assert mc(c, a="vote_start", title="Trang phục", opts=[{"label": "x"}]).status_code == 400
    assert mc(c, a="vote_start", title="Trang phục", opts=opts, no_self=True).status_code == 200
    v = A.S["vote"]
    hn = next(i for i, o in enumerate(v["opts"]) if o["group"] == "hn")
    dn = next(i for i, o in enumerate(v["opts"]) if o["group"] == "dn")
    with pws(c, ta) as wa, pws(c, tb) as wb:
        m = state_of(wa)
        assert "counts" not in m["c"]["vote"], "điện thoại không thấy kết quả khi đang mở"
        wa.send_json({"t": "vote", "v": v["id"], "o": hn})
        assert until(wa, lambda m: m.get("t") == "ack")["r"] == "self"
        wa.send_json({"t": "vote", "v": v["id"], "o": dn}); until(wa, lambda m: m.get("t") == "ack")
        wa.send_json({"t": "vote", "v": v["id"], "o": 0}); until(wa, lambda m: m.get("t") == "ack")   # đổi ý
        wb.send_json({"t": "vote", "v": v["id"], "o": 0}); until(wb, lambda m: m.get("t") == "ack")
        assert A.vote_counts(v)[0] == 2 and A.vote_counts(v)[dn] == 0
        assert mc(c, a="vote_close", id=v["id"]).status_code == 200
        assert mc(c, a="vote_close", id=v["id"]).status_code == 400
        m = state_of(wa, lambda m: m["c"]["vote"]["open"] is False)
        assert "counts" not in m["c"]["vote"], "đang công bố trên màn hình lớn thì điện thoại chưa thấy kết quả"
        clock[0] += v["reveal_ms"] / 1000 + 0.5
        wb.send_json({"t": "vote", "v": v["id"], "o": 1})
        assert until(wb, lambda m: m.get("t") == "ack")["r"] == "closed"
        m = state_of(wa, lambda m: m["c"]["vote"].get("revealed") is True)
        assert m["c"]["vote"]["counts"][0] == 2


# ── quay số ──



def test_parse_questions():
    text = ("Câu hỏi\tA\tB\tC\tD\tĐáp án\tGiây\n"
            "Hai cộng hai?\t3\t4\t5\t6\tB\t15\n"
            "Ngày Noel?\t24/12\t25/12\t\t\t2\n"
            'Thủ đô?,"Hà Nội, VN",Huế,a\n')
    qs, err = A.parse_questions(text)
    assert err == []
    assert qs[0] == {"q": "Hai cộng hai?", "opts": ["3", "4", "5", "6"], "ans": 1, "time": 15}
    assert qs[1]["opts"] == ["24/12", "25/12"] and qs[1]["ans"] == 1 and qs[1]["time"] == 20
    assert qs[2]["opts"] == ["Hà Nội, VN", "Huế"] and qs[2]["ans"] == 0
    _, err = A.parse_questions("Q\tx\ty\tE\nQ\tx\ty\tD")
    assert len(err) == 1 and "Dòng 2" in err[0]


def test_mc_questions_endpoint_rejects_errors(c):
    r = mc(c, a="questions", set="score", text="Q1\tx\ty\tA\nQ2\tx\ty\tZ")
    assert r.status_code == 400
    assert len(A.S["qsets"]["score"]) == len(A.SAMPLE_QUESTIONS), "lỗi thì không lưu nửa vời"
    assert mc(c, a="questions", text="Q1\tx\ty\tA").status_code == 400, "phải nói rõ lưu vào bộ nào"


# ── lưu / nạp ──

def test_state_survives_restart(c, tmp_path):
    t = join(c, "A").json()["token"]
    mc(c, a="quiz_start", kind="score")
    mc(c, a="quiz_open", idx=-1)
    mc(c, a="draw_roll", prize="Quà", seq=0, names=["An", "Bình", "Chi"])
    A.save_sync(A.dump_state())
    A.S.clear(); A.S.update(A.new_state())
    A.load_sync()
    assert A.S["tokens"][t] in A.S["players"]
    assert A.S["quiz"]["phase"] == "closed", "đang mở câu mà máy chủ khởi động lại thì đóng câu đó"
    assert A.S["draw"]["phase"] == "done" and len(A.S["draw"]["winners"]) == 1
    assert A.S["draw"]["winners"][0]["name"] in ("An", "Bình", "Chi")


def test_reset_keeps_questions_and_kicks_players(c):
    t = join(c, "A").json()["token"]
    mc(c, a="questions", set="bell", text="Q\tx\ty\tA")
    mc(c, a="sound", on=False, vol=30)
    with pws(c, t) as ws:
        state_of(ws)
        assert mc(c, a="reset").status_code == 200
        assert until(ws, lambda m: m.get("t") == "nojoin")
    assert A.S["players"] == {} and len(A.S["qsets"]["bell"]) == 1
    assert A.S["sound"] == {"on": False, "vol": 30}, "xoá người chơi nhưng giữ câu hỏi và âm thanh"


def test_remove_player(c):
    t = join(c, "A").json()["token"]
    with pws(c, t) as ws:
        state_of(ws)
        mc(c, a="remove_player", pid=A.S["tokens"][t])
        assert until(ws, lambda m: m.get("t") == "nojoin")


def test_screen_never_shows_answer_while_open(c):
    join(c, "A")
    mc(c, a="quiz_start", kind="score")
    with c.websocket_connect("/ws?role=screen") as ws:
        mc(c, a="quiz_open", idx=-1)
        m = state_of(ws, lambda m: m["quiz"] and m["quiz"]["phase"] == "open")
        assert "ans" not in m["quiz"] and "live_counts" not in m["quiz"]


def test_frontend_never_uses_innerhtml():
    for f in (ROOT / "static").glob("*.*"):
        assert not re.search(r"\.innerHTML|insertAdjacentHTML|document\.write", f.read_text(encoding="utf-8")), f.name



@pytest.mark.skipif(not __import__("os").environ.get("TEST_DATABASE_URL"), reason="cần TEST_DATABASE_URL")
def test_postgres_roundtrip(c, monkeypatch):
    import os
    monkeypatch.setattr(A, "DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    t = join(c, "Lưu DB", "dn").json()["token"]
    mc(c, a="quiz_start", kind="bell")
    A.save_sync(A.dump_state())
    A.S.clear(); A.S.update(A.new_state())
    A.load_sync()
    assert A.S["players"][A.S["tokens"][t]]["name"] == "Lưu DB"
    assert A.S["quiz"]["kind"] == "bell"


def test_delayed_duplicate_clicks_after_step_finished(c, clock):
    """Mạng chậm: lần bấm thứ hai đến máy chủ sau khi bước trước đã xong, không được nhảy thêm bước."""
    players(c, "A", "B", "C")
    mc(c, a="quiz_start", kind="score")
    assert mc(c, a="quiz_open", idx=-1).status_code == 200
    mc(c, a="quiz_reveal", qseq=A.S["quiz"]["qseq"])
    assert mc(c, a="quiz_open", idx=-1).status_code == 400       # lần bấm "Mở câu 1" đến trễ
    assert A.S["quiz"]["idx"] == 0
    assert mc(c, a="draw_roll", prize="", seq=0, names=["An", "Bình", "Chi"]).status_code == 200
    clock[0] += 60
    A.draw_tick()
    assert mc(c, a="draw_roll", prize="", seq=0, names=["An", "Bình", "Chi"]).status_code == 400   # lần bấm đến trễ
    assert A.S["draw"]["phase"] == "done" and len(A.S["draw"]["winners"]) == 1
    assert A.S["draw"]["winners"][0]["name"] in ("An", "Bình", "Chi")


def test_autoclose_waits_for_phone_that_dropped_mid_question(c):
    ta, tb = players(c, "A", "B")
    mc(c, a="quiz_start", kind="score")
    with pws(c, ta) as wa:
        wb = pws(c, tb).__enter__()
        state_of(wa); state_of(wb)
        mc(c, a="quiz_open", idx=-1)
        q = A.S["quiz"]["qseq"]
        wb.__exit__(None, None, None)             # B rớt mạng giữa câu
        import time; time.sleep(0.2)
        wa.send_json({"t": "ans", "q": q, "o": 0}); until(wa, lambda m: m.get("t") == "ack")
        assert A.S["quiz"]["phase"] == "open", "chưa đóng: B có thể đang nối lại"
        with pws(c, tb) as wb2:
            state_of(wb2)
            wb2.send_json({"t": "ans", "q": q, "o": 1})
            assert until(wb2, lambda m: m.get("t") == "ack")["r"] == "ok"
        assert A.S["quiz"]["phase"] == "closed"


# ── quay số: zombie ăn bánh quy ──

def roll(c, names, seq=None, **kw):
    return mc(c, a="draw_roll", prize=kw.pop("prize", "Quà"), seq=A.S["draw"]["seq"] if seq is None else seq, names=names, **kw)


def finish(clock):
    clock[0] += 60
    A.draw_tick()


def test_draw_name_list_validation(c):
    assert roll(c, ["Một mình"]).status_code == 400
    assert roll(c, ["", "  "]).status_code == 400
    r = roll(c, ["An", "an ", "Bình"])
    assert r.status_code == 400 and "trùng" in r.json()["error"].lower()
    assert roll(c, [f"Người {i}" for i in range(151)]).status_code == 400
    assert roll(c, "An\nBình").status_code == 400                    # phải là danh sách
    assert roll(c, ["  An   Nguyễn ", "Bình", "x" * 80]).status_code == 200
    d = A.S["draw"]
    assert d["names"] == ["An Nguyễn", "Bình", "x" * 40]
    assert A.S["mode"] == "draw" and d["phase"] == "rolling"


def test_draw_schedule_and_winner_is_the_uneaten_one(c, clock):
    names = [f"Người {i}" for i in range(12)]
    assert roll(c, names).status_code == 200
    d = A.S["draw"]
    pick = d["pick"]
    assert sorted(d["order"] + [pick]) == list(range(12)), "mỗi bánh bị ăn đúng 1 lần, trừ bánh trúng"
    t = d["times"]
    assert len(t) == 11 and all(a < b for a, b in zip(t, t[1:]))
    gaps = [b - a for a, b in zip(t, t[1:])]
    assert gaps[-1] > gaps[0] * 1.8, "2 miếng cuối chậm hơn cho hồi hộp"
    assert 8000 <= t[-1] <= 45000
    assert roll(c, names).status_code == 400                            # đang ăn thì không quay chồng
    finish(clock)
    assert d["phase"] == "done" and d["winners"][-1]["name"] == names[pick]


def test_draw_timing_bounds():
    for n in (2, 3, 10, 50, 150):
        times, total = A.draw_schedule(n)
        assert len(times) == n - 1
        assert 9000 <= total <= 50000, (n, total)
    times, _ = A.draw_schedule(150)
    assert min(b - a for a, b in zip(times, times[1:])) >= 200, "150 tên vẫn đủ chậm để nhìn kịp"


def test_draw_excludes_previous_winners_and_absent(c, clock):
    names = ["An", "Bình", "Chi"]
    roll(c, names); finish(clock)
    w1 = A.S["draw"]["winners"][-1]["name"]
    roll(c, names); finish(clock)
    w2 = A.S["draw"]["winners"][-1]["name"]
    assert w1 != w2
    assert mc(c, a="draw_absent", seq=A.S["draw"]["seq"]).status_code == 200
    r = roll(c, names)
    assert r.status_code == 400 and "đã trúng" in r.json()["error"], "chỉ còn 1 tên chưa trúng"
    assert roll(c, names, exclude_winners=False).status_code == 200


def test_draw_winner_phone_matched_by_checkin_label(c, clock):
    ta = join(c, "An", "hn").json()["token"]
    tb = join(c, "Bình", "dn").json()["token"]
    labels = [A.label(A.S["tokens"][ta]), A.label(A.S["tokens"][tb])]
    assert labels == ["An (CN Hà Nội)", "Bình (CN Đà Nẵng)"]
    with pws(c, ta) as wa, pws(c, tb) as wb:
        state_of(wa); state_of(wb)
        roll(c, labels + ["Khách mời không dùng app"])
        m = state_of(wa, lambda m: m["c"].get("draw", {}).get("phase") == "rolling")
        raw = json.dumps(m)
        assert "names" not in m["c"]["draw"] and "order" not in m["c"]["draw"] and "pick" not in raw, "điện thoại không biết trước kết quả"
        winner = A.S["draw"]["names"][A.S["draw"]["pick"]]
        finish(clock)
        ma = state_of(wa, lambda m: m["c"]["draw"]["phase"] == "done")
        mb = state_of(wb, lambda m: m["c"]["draw"]["phase"] == "done")
        assert ma["c"]["draw"]["last"]["name"] == winner
        won = [m for m, l in ((ma, labels[0]), (mb, labels[1])) if m["me"].get("won_now")]
        assert len(won) == (1 if winner in labels else 0), "chỉ máy của người trúng báo trúng"


def test_draw_views_by_role(c):
    with c.websocket_connect("/ws?role=screen") as ws, c.websocket_connect("/ws?role=mc", headers=MC) as wm:
        roll(c, ["An", "Bình", "Chi"])
        m = state_of(ws, lambda m: m["draw"] and m["draw"]["phase"] == "rolling")
        dr = m["draw"]
        assert dr["names"] == ["An", "Bình", "Chi"] and len(dr["order"]) == 2 and len(dr["times"]) == 2
        assert "pick" not in dr and 0 <= dr["elapsed_ms"] < 5000
        m = state_of(wm, lambda m: m["draw"]["phase"] == "rolling")
        assert "order" not in m["draw"] and "pick" not in m["draw"]


def test_old_saved_state_still_loads(c):
    """Trạng thái lưu từ bản trước (quay số kiểu cũ) không làm máy chủ lỗi khi nạp."""
    old = json.loads(A.dump_state())
    old["draw"] = {"phase": "rolling", "prize": "", "winners": [{"pid": "x", "name": "A", "group": "hn", "prize": "", "absent": False}],
                   "seq": 3, "only_online": True, "pick": "abcd1234"}
    A.save_sync(json.dumps(old))
    A.load_sync()
    assert A.S["draw"]["phase"] == "done" and len(A.S["draw"]["winners"]) == 1
    assert roll(c, ["An", "Bình"]).status_code == 200



# ── bộ câu hỏi riêng, lấy ngẫu nhiên ──

def test_separate_question_sets_random_and_count(c):
    mc(c, a="questions", set="score", text="\n".join(f"Đố {i}\tx\ty\tA" for i in range(8)))
    mc(c, a="questions", set="bell", text="\n".join(f"Chuông {i}\tx\ty\tB" for i in range(5)))
    assert mc(c, a="questions", set="khac", text="Q\tx\ty\tA").status_code == 400
    mc(c, a="quiz_start", kind="bell")
    assert all(q["q"].startswith("Chuông") for q in A.S["quiz"]["qs"]) and len(A.S["quiz"]["qs"]) == 5
    orders = set()
    for _ in range(15):
        assert mc(c, a="quiz_start", kind="score", count=3).status_code == 200
        qs = A.S["quiz"]["qs"]
        assert len(qs) == 3 and all(q["q"].startswith("Đố") for q in qs) and len({q["q"] for q in qs}) == 3
        orders.add(tuple(q["q"] for q in qs))
    assert len(orders) > 3, "câu hỏi được lấy ngẫu nhiên"
    mc(c, a="quiz_start", kind="score", shuffle=False)
    assert [q["q"] for q in A.S["quiz"]["qs"]] == [f"Đố {i}" for i in range(8)]
    mc(c, a="quiz_start", kind="score", count=99)
    assert len(A.S["quiz"]["qs"]) == 8, "xin nhiều hơn số câu đang có thì chơi tất cả"
    assert mc(c, a="quiz_start", kind="score", count="abc").status_code == 400
    assert mc(c, a="quiz_start", kind="score", count=-1).status_code == 400
    A.S["qsets"]["bell"] = []
    r = mc(c, a="quiz_start", kind="bell")
    assert r.status_code == 400 and "Chưa có câu hỏi" in r.json()["error"]


def test_old_saved_questions_migrate(c):
    old = json.loads(A.dump_state())
    old.pop("qsets"); old.pop("sound")
    old["questions"] = [{"q": "Cũ", "opts": ["a", "b"], "ans": 0, "time": 20}]
    old["quiz"] = {"kind": "score", "id": 1, "idx": 0, "phase": "reveal", "qseq": 1, "answers": {}, "scores": {}, "ms": {},
                   "gain": {}, "result": None, "alive": [], "started": True}
    old["mode"] = "quiz"
    A.save_sync(json.dumps(old))
    A.load_sync()
    assert A.S["qsets"]["score"][0]["q"] == "Cũ" and A.S["qsets"]["bell"][0]["q"] == "Cũ"
    assert A.S["quiz"]["qs"][0]["q"] == "Cũ" and A.S["sound"]["on"] is True
    assert mc(c, a="quiz_board", qseq=1).status_code == 200


# ── bình chọn hẹn giờ, công bố lần lượt ──

def test_vote_timer_autoclose_and_reveal_order(c, clock):
    ts = [join(c, f"P{i}", g).json()["token"] for i, g in enumerate(["hn", "hn", "dn", "dn", "it-legal", "store"])]
    opts = [{"label": "A"}, {"label": "B"}, {"label": "C"}]
    assert mc(c, a="vote_start", title="T", opts=opts, secs=5).status_code == 400          # dưới 10 giây
    assert mc(c, a="vote_start", title="T", opts=opts, secs=1000).status_code == 400
    assert mc(c, a="vote_start", title="T", opts=opts, secs="x").status_code == 400
    assert mc(c, a="vote_start", title="T", opts=opts, secs=20).status_code == 200
    v = A.S["vote"]
    with c.websocket_connect("/ws?role=screen") as scr, c.websocket_connect("/ws?role=mc", headers=MC) as wm:
        socks = [pws(c, t).__enter__() for t in ts]
        for w in socks:
            state_of(w)
        for w, o in zip(socks, [1, 1, 1, 0, 0, 2]):          # B 3 phiếu, A 2, C 1
            w.send_json({"t": "vote", "v": v["id"], "o": o}); until(w, lambda m: m.get("t") == "ack")
        m = state_of(scr, lambda m: m["vote"] and m["vote"]["total"] == 6)
        assert "counts" not in m["vote"] and 0 < m["vote"]["left_ms"] <= 20000, "màn hình lớn không lộ số thật khi đang mở"
        m = state_of(wm, lambda m: m["vote"] and m["vote"]["total"] == 6)
        assert m["vote"]["counts"] == [2, 3, 1], "MC thấy số thật"
        clock[0] += 21
        m = state_of(scr, lambda m: m["vote"] and m["vote"]["open"] is False)
        vv = m["vote"]
        assert vv["reveal_order"] == [2, 0, 1], "công bố từ thấp đến cao, người thắng sau cùng"
        assert vv["counts"] == [2, 3, 1] and vv["step_ms"] >= 900 and 0 <= vv["since_close_ms"] < 3000
        w0 = socks[0]
        m = state_of(w0, lambda m: m["c"]["vote"]["open"] is False)
        assert "counts" not in m["c"]["vote"] and m["c"]["vote"]["revealed"] is False
        late = socks[1]
        late.send_json({"t": "vote", "v": v["id"], "o": 2})
        assert until(late, lambda m: m.get("t") == "ack")["r"] == "closed", "hết giờ thì không bầu được nữa"
        clock[0] += v["reveal_ms"] / 1000 + 0.5
        m = state_of(w0, lambda m: m["c"]["vote"]["revealed"] is True)
        assert m["c"]["vote"]["counts"] == [2, 3, 1]
        for w in socks:
            w.__exit__(None, None, None)


def test_vote_tie_winners_revealed_last():
    v = {"votes": {"a": 0, "b": 2, "c": 0, "d": 2}, "opts": [{"label": x} for x in "ABCD"]}
    A._vote_finish_open(v)
    assert v["reveal_order"][-2:] in ([0, 2], [2, 0]) and set(v["reveal_order"][:2]) == {1, 3}


def test_vote_close_early_and_restart_keeps_timer(c, clock):
    mc(c, a="vote_start", title="T", opts=[{"label": "A"}, {"label": "B"}], secs=60)
    A.save_sync(A.dump_state())
    A.load_sync()
    v = A.S["vote"]
    assert v["open"] and 55 < v["ends_at"] - A.wall() <= 60, "khởi động lại vẫn giữ giờ kết thúc"
    assert mc(c, a="vote_close", id=v["id"]).status_code == 200
    assert A.S["vote"]["open"] is False and A.S["vote"]["final"] == [0, 0]


# ── âm thanh ──

def test_sound_settings(c):
    assert mc(c, a="sound", on=False).status_code == 200 and A.S["sound"]["on"] is False
    assert mc(c, a="sound", vol=150).status_code == 200 and A.S["sound"]["vol"] == 100
    assert mc(c, a="sound", vol=-5).status_code == 200 and A.S["sound"]["vol"] == 0
    assert mc(c, a="sound", vol="to").status_code == 400
    with c.websocket_connect("/ws?role=screen") as ws:
        assert state_of(ws)["sound"] == {"on": False, "vol": 0}
