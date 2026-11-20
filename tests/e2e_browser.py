"""Kiểm thử bằng trình duyệt thật: iPhone giả lập, màn hình lớn 1920x1080, trang MC.

Chạy:  python tests/e2e_browser.py          (cần: pip install playwright && playwright install chromium)
Ảnh chụp lưu ở tests/shots/
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "tests" / "shots"
PORT = 5302
B = f"http://localhost:{PORT}"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"  {'ĐẠT ' if cond else 'LỖI '} {name}" + (f"  ({detail})" if detail and not cond else ""))


def main():
    SHOTS.mkdir(exist_ok=True)
    env = dict(os.environ, MC_KEY="k", STATE_FILE="/tmp/_pa_e2e.json")
    env.pop("DATABASE_URL", None)
    if os.path.exists("/tmp/_pa_e2e.json"):
        os.remove("/tmp/_pa_e2e.json")
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env)
    time.sleep(2)
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            iphone = p.devices["iPhone 13"]

            # màn hình lớn
            scr_ctx = br.new_context(viewport={"width": 1920, "height": 1080})
            scr = scr_ctx.new_page()
            scr.goto(B + "/screen")
            scr.wait_for_selector(".qrbox img")
            check("Màn hình lớn hiện mã QR", scr.locator(".qrbox img").evaluate("i => i.complete && i.naturalWidth > 0"))

            # MC
            mc_ctx = br.new_context(**iphone)
            mc = mc_ctx.new_page()
            mc.goto(B + "/mc?key=k")
            mc.wait_for_selector("#tabs button")
            check("MC vào được bằng link có mã", "Sảnh chờ" in mc.content())

            # người chơi
            phones = []
            for i, (name, group) in enumerate([("Duy Nguyễn", "IT + Legal"), ("Lan", "CN Hà Nội"), ("Minh", "Sales AW")]):
                ctx = br.new_context(**iphone)
                pg = ctx.new_page()
                pg.goto(B)
                if i == 0:
                    pg.click("button:has-text('Vào chơi')")
                    check("Thiếu tên thì báo lỗi rõ ràng", "nhập tên" in pg.locator(".notice.err").inner_text())
                    pg.fill("input[type=text]", name)
                    pg.click("button:has-text('Vào chơi')")
                    check("Thiếu nhóm thì báo lỗi rõ ràng", "chọn nhóm" in pg.locator(".notice.err").inner_text())
                    pg.screenshot(path=str(SHOTS / "01_phone_join.png"), full_page=True)
                else:
                    pg.fill("input[type=text]", name)
                pg.click(f".gbtn:has-text('{group}')")
                pg.click("button:has-text('Vào chơi')")
                pg.wait_for_selector("text=Bạn đã vào phòng")
                phones.append((ctx, pg))
            check("3 điện thoại vào phòng", len(phones) == 3)
            scr.wait_for_timeout(600)
            check("Màn hình lớn đếm đúng 3 người", scr.locator(".count").inner_text().startswith("3"))
            scr.screenshot(path=str(SHOTS / "02_screen_lobby.png"))
            phones[0][1].screenshot(path=str(SHOTS / "03_phone_lobby.png"))

            # vào lại sau khi tải lại trang: không phải nhập lại
            pg = phones[1][1]
            pg.reload()
            pg.wait_for_selector("text=Bạn đã vào phòng")
            check("Tải lại trang vẫn giữ người chơi", "Lan" in pg.locator("#me").inner_text())

            # thêm 25 người ở đủ 14 nhóm (không mở app) để bảng xếp hạng đầy như đêm thật
            import json, urllib.request
            codes = ["sales-aw", "sales-pgi", "admin", "ecom", "store", "mkt-aw", "mkt-pgi", "accounting",
                     "warehouse", "warranty", "car-audio", "it-legal", "hn", "dn"]
            for k in range(25):
                req = urllib.request.Request(B + "/api/join", data=json.dumps({"name": f"Nguyễn Văn Khách số {k:02d}", "group": codes[k % 14]}).encode(),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req).read()

            # đố vui
            mc.click("#tabs button:has-text('Đố vui')")
            mc.click("button:has-text('Bắt đầu Đố vui')")
            mc.wait_for_selector("button:has-text('Mở câu 1')")
            mc.click("button:has-text('Mở câu 1')")
            for _, pg in phones:
                pg.wait_for_selector(".ans")
            scr.wait_for_selector(".sq-q")
            scr.screenshot(path=str(SHOTS / "04_screen_question.png"))
            p0 = phones[0][1]
            p0.screenshot(path=str(SHOTS / "05_phone_question.png"))
            # chạm 2 lần thật nhanh vào hai đáp án khác nhau: chỉ tính lần đầu
            btns = p0.locator(".ans")
            box0, box1 = btns.nth(0).bounding_box(), btns.nth(1).bounding_box()
            p0.touchscreen.tap(box0["x"] + 20, box0["y"] + 20)
            p0.touchscreen.tap(box1["x"] + 20, box1["y"] + 20)
            p0.wait_for_timeout(700)
            check("Chạm dồn 2 đáp án: chỉ chốt đáp án đầu", p0.locator(".ans.pick").count() == 1 and "Tuần lộc" in p0.locator(".ans.pick").inner_text())
            check("Đã chốt thì khoá các nút", p0.locator(".ans:disabled").count() == 4)

            # mất mạng giữa câu rồi có lại
            ctx1, p1 = phones[1]
            ctx1.set_offline(True)
            p1.wait_for_timeout(1500)
            p1.evaluate("window.dispatchEvent(new Event('offline'))")
            ctx1.set_offline(False)
            p1.wait_for_timeout(3500)
            p1.wait_for_selector(".ans:not(:disabled)", timeout=8000)
            p1.locator(".ans").nth(0).click()
            p1.wait_for_timeout(600)
            check("Rớt mạng giữa câu, có mạng lại vẫn trả lời được", p1.locator(".ans.pick").count() == 1)

            phones[2][1].locator(".ans").nth(2).click()
            mc.wait_for_selector("button:has-text('Công bố đáp án')")
            mc.wait_for_timeout(500)
            mc.click("button:has-text('Công bố đáp án')")
            scr.wait_for_selector(".sopt.right")
            scr.wait_for_timeout(400)
            scr.screenshot(path=str(SHOTS / "06_screen_reveal.png"))
            p0.wait_for_selector(".result")
            check("Trả lời đúng thì báo Chính xác và cộng điểm", "Chính xác" in p0.inner_text("body") and "+" in p0.locator(".gain").inner_text())
            phones[2][1].wait_for_selector(".result.bad")
            check("Trả lời sai thì báo chưa đúng và hiện đáp án", "Đáp án đúng: A" in phones[2][1].inner_text("body"))
            p0.screenshot(path=str(SHOTS / "07_phone_reveal.png"))
            mc.click("button:has-text('Bảng xếp hạng')")
            scr.wait_for_selector(".board")
            scr.wait_for_timeout(400)
            check("Bảng xếp hạng có top 10 cá nhân và đủ 14 nhóm", scr.locator(".board .row").count() == 24, scr.locator(".board .row").count())
            low = scr.evaluate("Math.max(...[...document.querySelectorAll('.board .row')].map(r => r.getBoundingClientRect().bottom))")
            check("Bảng xếp hạng nằm gọn trong màn hình 1080px", low <= 1080, low)
            scr.screenshot(path=str(SHOTS / "08_screen_board.png"))
            mc.screenshot(path=str(SHOTS / "09_mc_quiz.png"), full_page=True)
            check("Trang MC hiện đủ 4 đáp án và đánh dấu đáp án đúng", mc.locator(".qo").count() == 4 and "✓" in mc.locator(".qo.r").inner_text())
            check("Trang MC không có chữ lỗi [object …]", "[object" not in mc.inner_text("body"))

            # rung chuông vàng
            mc.click("#tabs button:has-text('Rung chuông')")
            b = mc.locator("#panel .btn").first
            check("Nút bắt đầu Rung chuông vàng có mặt", "Bắt đầu Rung chuông vàng" in b.inner_text(), b.inner_text())
            b.click()
            mc.wait_for_timeout(400)
            check("Đổi trò giữa chừng phải chạm 2 lần", "Chạm lần nữa" in b.inner_text(), b.inner_text())
            phones[2][1].reload()               # có dữ liệu mới đổ về MC trong lúc chờ
            mc.wait_for_timeout(1200)
            check("Chờ chạm lần 2 không bị vẽ lại mất", "Chạm lần nữa" in b.inner_text(), b.inner_text())
            b.click()
            mc.wait_for_selector("button:has-text('Mở câu 1')")
            mc.click("button:has-text('Mở câu 1')")
            for _, pg in phones:
                pg.wait_for_selector(".ans")
            p0.locator(".ans").nth(0).click()
            phones[1][1].locator(".ans").nth(0).click()
            phones[2][1].locator(".ans").nth(1).click()
            mc.wait_for_selector("button:has-text('Công bố đáp án')")
            mc.click("button:has-text('Công bố đáp án')")
            mc.wait_for_selector("button:has-text('Xem số người còn lại')")
            mc.click("button:has-text('Xem số người còn lại')")
            scr.wait_for_selector(".bell-big .count")
            scr.wait_for_timeout(300)
            check("Rung chuông vàng: còn 2 người sau câu 1", scr.locator(".bell-big .count").inner_text().strip() == "2")
            scr.screenshot(path=str(SHOTS / "10_screen_bell.png"))
            phones[2][1].wait_for_selector("text=Bạn đã dừng cuộc chơi")
            check("Người bị loại thấy mình đã dừng", True)
            phones[2][1].screenshot(path=str(SHOTS / "11_phone_out.png"))

            # câu 2: cả 2 người còn lại đều sai → giữ lại cả 2, mọi màn hình phải nói rõ
            mc.click("button:has-text('Mở câu 2')")
            for pg in (p0, phones[1][1]):
                pg.wait_for_selector(".ans")
                pg.locator(".ans").nth(0).click()          # đáp án đúng là B (25/12)
            mc.wait_for_selector("button:has-text('Công bố đáp án')")
            mc.click("button:has-text('Công bố đáp án')")
            scr.wait_for_selector(".sopt.right")
            scr.wait_for_timeout(300)
            check("Cả lượt sai: màn hình lớn báo giữ lại cả 2 người", "Không ai trả lời đúng, nên cả 2 người được giữ lại" in scr.inner_text("body"))
            p0.wait_for_selector("text=Không ai trả lời đúng!")
            check("Cả lượt sai: điện thoại báo vẫn trong cuộc theo luật", "cả 2 người được giữ lại" in p0.inner_text("body") and "chưa đúng" not in p0.inner_text("body"))
            p0.screenshot(path=str(SHOTS / "11b_phone_saved_all.png"))
            check("Cả lượt sai: trang MC ghi rõ lý do", "giữ lại cả 2 người (theo luật)" in mc.inner_text("body"))
            mc.click("button:has-text('Xem số người còn lại')")
            scr.wait_for_selector("text=Câu vừa rồi không ai đúng")
            check("Cả lượt sai: bảng còn trong cuộc ghi rõ lý do", scr.locator(".bell-big .count").inner_text().strip() == "2")
            scr.screenshot(path=str(SHOTS / "11c_screen_saved_all.png"))

            # câu 3: chỉ 1 người đúng → còn 1 người, MC được gợi ý kết thúc
            mc.click("button:has-text('Mở câu 3')")
            p0.wait_for_selector(".ans")
            phones[1][1].wait_for_selector(".ans")
            p0.locator(".ans").nth(2).click()                # đúng: Phần Lan
            phones[1][1].locator(".ans").nth(0).click()
            mc.wait_for_selector("button:has-text('Công bố đáp án')")
            mc.click("button:has-text('Công bố đáp án')")
            mc.wait_for_selector("text=Chỉ còn 1 người")
            check("Còn 1 người: MC được gợi ý kết thúc để vinh danh", True)
            check("Rung chuông vàng: trang MC không hiện bảng điểm cá nhân", "Top cá nhân" not in mc.inner_text("body"))
            mc.screenshot(path=str(SHOTS / "11d_mc_champion.png"), full_page=True)

            # bình chọn
            mc.click("#tabs button:has-text('Bình chọn')")
            mc.click("button:has-text('Nhóm có trang phục Noel đẹp nhất')")
            mc.click("button:has-text('Mở bình chọn')")
            p0.wait_for_selector(".vopt")
            own = p0.locator(".vopt:has-text('IT + Legal')")
            check("Không bầu được cho nhóm mình", own.is_disabled())
            p0.locator(".vopt:has-text('CN Hà Nội')").click()
            p0.wait_for_selector(".vopt[aria-pressed=true]")
            p0.locator(".vopt:has-text('Sales AW')").click()
            p0.wait_for_timeout(500)
            check("Đổi ý bình chọn được", "Sales AW" in p0.locator(".vopt[aria-pressed=true]").inner_text())
            phones[1][1].locator(".vopt:has-text('Sales AW')").click()
            scr.wait_for_timeout(600)
            scr.screenshot(path=str(SHOTS / "12_screen_vote.png"))
            p0.screenshot(path=str(SHOTS / "13_phone_vote.png"), full_page=True)
            mc.click("button:has-text('Đóng bình chọn')")
            scr.wait_for_selector("text=Đã chốt kết quả")
            scr.wait_for_timeout(300)
            check("Đóng bình chọn: Sales AW dẫn đầu 2 phiếu", "🏆 Sales AW" in scr.inner_text("body"))

            # quay số
            mc.click("#tabs button:has-text('Quay số')")
            mc.fill("input[data-keep=prize]", "Giải Ba – Tai nghe")
            mc.click("button:has-text('Quay!')")
            scr.wait_for_selector(".reel.rolling")
            scr.wait_for_timeout(700)
            scr.screenshot(path=str(SHOTS / "14_screen_rolling.png"))
            scr.wait_for_selector(".reel.won", timeout=10000)
            scr.wait_for_timeout(700)
            scr.screenshot(path=str(SHOTS / "15_screen_winner.png"))
            winner_name = scr.locator(".reel.won div").first.inner_text().replace("🎉", "").strip()
            won = [pg for _, pg in phones if pg.locator("text=Bạn trúng thưởng").count()]
            check("Đúng 1 điện thoại báo trúng, khớp tên trên màn hình", len(won) == 1 and winner_name in won[0].locator("#me").inner_text(), winner_name)
            if won:
                won[0].screenshot(path=str(SHOTS / "16_phone_win.png"))
            mc.screenshot(path=str(SHOTS / "17_mc_draw.png"), full_page=True)

            for nm, pg in [("điện thoại", p0), ("MC", mc), ("màn hình lớn", scr)]:
                check(f"Trang {nm} không có chữ lỗi [object …] / undefined / NaN",
                      not re.search(r"\[object|undefined|NaN", pg.inner_text("body")))
            # không có thanh cuộn ngang ở mọi trang
            for nm, pg in [("điện thoại", p0), ("MC", mc)]:
                w = pg.evaluate("[innerWidth, document.documentElement.scrollWidth]")
                check(f"Trang {nm} không bị tràn ngang", w[1] <= w[0], w)
            w = scr.evaluate("[innerWidth, innerHeight, document.documentElement.scrollWidth, document.documentElement.scrollHeight]")
            check("Màn hình lớn vừa khung 1920x1080, không cuộn", w[2] <= w[0] and w[3] <= w[1], w)
            br.close()
    finally:
        srv.terminate()
    print(f"\n{sum(results)}/{len(results)} đạt")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
