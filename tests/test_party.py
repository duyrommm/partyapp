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
        ans = A.S["questions"][0]["ans"]
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
        clock[0] += A.S["questions"][0]["time"] + A.GRACE + 0.5
        ws.send_json({"t": "ans", "q": q["qseq"], "o": 0})
        assert until(ws, lambda m: m.get("t") == "ack")["r"] == "closed"
        m = state_of(ws, lambda m: m["c"]["quiz"]["phase"] == "closed")
        assert m["me"]["answer"] is None


def test_quiz_flow_guards(c):
    join(c, "A")
    assert mc(c, a="quiz_open", idx=-1).status_code == 400          # chưa bắt đầu
    mc(c, a="questions", text="Q1\tx\ty\tA\nQ2\tx\ty\tB")
    mc(c, a="quiz_start", kind="score")
    assert mc(c, a="quiz_board", qseq=0).status_code == 400
    mc(c, a="quiz_open", idx=-1)
    assert mc(c, a="quiz_end").status_code == 400                    # đang mở
    assert mc(c, a="questions", text="Q\ta\tb\tA").status_code == 400  # không đổi câu hỏi giữa chừng
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
    mc(c, a="questions", text="Q1\tx\ty\tA\nQ2\tx\ty\tA\nQ3\tx\ty\tA")
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
            assert set(A.S["quiz"]["alive"]) == {pid(ta), pid(tc)}
            assert mc(c, a="bell_revive").status_code == 200
            assert len(A.S["quiz"]["alive"]) == 4


# ── bình chọn ──

def test_vote_no_self_change_and_close(c):
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
        wb.send_json({"t": "vote", "v": v["id"], "o": 1})
        assert until(wb, lambda m: m.get("t") == "ack")["r"] == "closed"
        m = state_of(wa, lambda m: m["c"]["vote"]["open"] is False)
        assert m["c"]["vote"]["counts"][0] == 2


# ── quay số ──

def test_draw_only_online_no_repeat_absent(c, clock):
    ta, tb, tc = players(c, "A", "B", "C")
    with pws(c, ta) as wa, pws(c, tb) as wb:
        state_of(wa); state_of(wb)
        won = set()
        for i in range(2):
            seq = A.S["draw"]["seq"]
            assert mc(c, a="draw_roll", prize="Giải", seq=seq, only_online=True).status_code == 200
            assert mc(c, a="draw_roll", prize="Giải", seq=seq, only_online=True).status_code == 400   # bấm trùng
            assert A.S["draw"]["pick"] != A.S["tokens"][tc], "người không mở app không được quay"
            clock[0] += A.ROLL_SECS + 0.2
            m = state_of(wa, lambda m: m["c"].get("draw", {}).get("phase") == "done" and len(m["c"]["draw"]["winners"]) == i + 1)
            won.add(A.S["draw"]["winners"][-1]["pid"])
        assert len(won) == 2, "không trúng 2 lần"
        r = mc(c, a="draw_roll", prize="", seq=A.S["draw"]["seq"], only_online=True)
        assert r.status_code == 400 and "Không còn ai" in r.json()["error"]
        seq = A.S["draw"]["seq"]
        assert mc(c, a="draw_absent", seq=seq).status_code == 200
        assert mc(c, a="draw_absent", seq=seq).status_code == 400
        assert A.S["draw"]["winners"][-1]["absent"] is True
        # quay cả người offline
        assert mc(c, a="draw_roll", prize="", seq=seq, only_online=False).status_code == 200
        assert A.S["draw"]["pick"] == A.S["tokens"][tc]


def test_winner_not_leaked_before_reveal(c, clock):
    ta, = players(c, "A")
    with pws(c, ta) as wa:
        state_of(wa)
        mc(c, a="draw_roll", prize="Xe", seq=0)
        m = state_of(wa, lambda m: m["c"].get("draw", {}).get("phase") == "rolling")
        assert "last" not in m["c"]["draw"] and not m["me"].get("won")
        clock[0] += A.ROLL_SECS + 0.2
        m = state_of(wa, lambda m: m["c"].get("draw", {}).get("phase") == "done")
        assert m["me"]["won_now"] is True and m["me"]["won"] == "Xe"


# ── câu hỏi ──

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
    r = mc(c, a="questions", text="Q1\tx\ty\tA\nQ2\tx\ty\tZ")
    assert r.status_code == 400
    assert len(A.S["questions"]) == len(A.SAMPLE_QUESTIONS), "lỗi thì không lưu nửa vời"


# ── lưu / nạp ──

def test_state_survives_restart(c, tmp_path):
    t = join(c, "A").json()["token"]
    mc(c, a="quiz_start", kind="score")
    mc(c, a="quiz_open", idx=-1)
    mc(c, a="draw_roll", prize="Quà", seq=0, only_online=False)
    A.save_sync(A.dump_state())
    A.S.clear(); A.S.update(A.new_state())
    A.load_sync()
    assert A.S["tokens"][t] in A.S["players"]
    assert A.S["quiz"]["phase"] == "closed", "đang mở câu mà máy chủ khởi động lại thì đóng câu đó"
    assert A.S["draw"]["phase"] == "done" and len(A.S["draw"]["winners"]) == 1


def test_reset_keeps_questions_and_kicks_players(c):
    t = join(c, "A").json()["token"]
    mc(c, a="questions", text="Q\tx\ty\tA")
    with pws(c, t) as ws:
        state_of(ws)
        assert mc(c, a="reset").status_code == 200
        assert until(ws, lambda m: m.get("t") == "nojoin")
    assert A.S["players"] == {} and len(A.S["questions"]) == 1


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


def test_draw_pick_never_sent_to_screen_or_mc(c):
    players(c, "A", "B", "C")
    with c.websocket_connect("/ws?role=screen") as ws, c.websocket_connect("/ws?role=mc", headers=MC) as wm:
        mc(c, a="draw_roll", prize="", seq=0, only_online=False)
        pick = A.S["draw"]["pick"]
        m = state_of(ws, lambda m: m["draw"] and m["draw"]["phase"] == "rolling")
        assert pick not in json.dumps(m) and "pick" not in m["draw"]
        m = state_of(wm, lambda m: m["draw"]["phase"] == "rolling")
        assert "pick" not in m["draw"]


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
    assert mc(c, a="draw_roll", prize="", seq=0, only_online=False).status_code == 200
    clock[0] += A.ROLL_SECS + 0.2
    A.draw_tick()
    assert mc(c, a="draw_roll", prize="", seq=0, only_online=False).status_code == 400   # lần bấm "Quay" đến trễ
    assert A.S["draw"]["phase"] == "done" and len(A.S["draw"]["winners"]) == 1


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
