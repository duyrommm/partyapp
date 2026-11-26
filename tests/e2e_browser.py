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
            scr_ctx.add_init_script("window.__PA_KEEPALIVE_MS = 1000")
            scr = scr_ctx.new_page()
            js_errors = []
            scr.on("pageerror", lambda e: js_errors.append(("màn hình lớn", str(e))))
            health_calls = []
            scr.on("request", lambda r: health_calls.append(r.url) if r.url.endswith("/health") else None)
            scr.goto(B + "/screen")
            scr.wait_for_selector(".qrbox img")
            check("Màn hình lớn hiện mã QR", scr.locator(".qrbox img").evaluate("i => i.complete && i.naturalWidth > 0"))
            check("Có nút toàn màn hình cho máy chiếu", scr.locator("#fs").is_visible())
            check("Màn hình lớn nhắc bấm để bật âm thanh", scr.locator("#snd").is_visible())
            scr.click("#snd")
            scr.wait_for_timeout(500)
            check("Bấm một lần là bật được âm thanh, nút nhắc tự ẩn", scr.evaluate("Sound.unlocked()") and not scr.locator("#snd").is_visible())
            scr.wait_for_timeout(2500)
            check("Màn hình lớn tự gọi máy chủ định kỳ để giữ app thức", len(health_calls) >= 2, len(health_calls))

            # MC
            mc_ctx = br.new_context(**iphone)
            mc = mc_ctx.new_page()
            mc.on("pageerror", lambda e: js_errors.append(("MC", str(e))))
            mc.goto(B + "/mc?key=k")
            mc.wait_for_selector("#tabs button")
            check("MC vào được bằng link có mã", "Sảnh chờ" in mc.content())

            # người chơi
            phones = []
            for i, (name, group) in enumerate([("Duy Nguyễn", "IT + Legal"), ("Lan", "CN Hà Nội"), ("Minh", "Sales AW")]):
                ctx = br.new_context(**iphone)
                ctx.add_init_script("""Object.defineProperty(navigator, 'wakeLock', { value: { request: () => {
                    window.__wl = (window.__wl || 0) + 1; return Promise.resolve({ released: false, release(){}, addEventListener(){} }); } } });""")
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: js_errors.append(("điện thoại", str(e))))
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
            check("Điện thoại xin giữ màn hình sáng khi chơi", all((pg.evaluate("window.__wl || 0") >= 1) for _, pg in phones))
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
            check("MC chọn được số câu và lấy ngẫu nhiên", mc.locator("input[data-keep=qcount_score]").is_visible()
                  and mc.locator("input[data-keep=qshuf_score]").is_checked())
            mc.uncheck("input[data-keep=qshuf_score]")          # giữ thứ tự để kịch bản test biết đáp án
            mc.click("button:has-text('Bắt đầu Đố vui')")
            mc.wait_for_selector("button:has-text('Mở câu 1')")
            mc.click("button:has-text('Mở câu 1')")
            for _, pg in phones:
                pg.wait_for_selector(".ans")
            scr.wait_for_selector(".sq-q")
            scr.wait_for_timeout(400)
            check("Đang trả lời: màn hình lớn phát nhạc nền đố vui", scr.evaluate("Sound.current()") == "quiz")
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
            scr.wait_for_timeout(300)
            log = scr.evaluate("window.__soundLog.slice()")
            check("Công bố đáp án: tắt nhạc nền, phát chuông", "sfx:ding" in log and scr.evaluate("Sound.current()") is None, log[-6:])
            check("Màn hình lớn vinh danh người đúng nhanh nhất", "Nhanh nhất" in scr.inner_text("body") and "Duy Nguyễn" in scr.locator(".fastest").inner_text())
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
            mc.uncheck("input[data-keep=qshuf_bell]")
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

            # bình chọn: số ảo chạy + đếm ngược, hết giờ tự đóng rồi công bố lần lượt
            mc.click("#tabs button:has-text('Bình chọn')")
            mc.click("button:has-text('Nhóm có trang phục Noel đẹp nhất')")
            mc.fill("input[data-keep=vsecs]", "15")
            mc.click("button:has-text('Mở bình chọn')")
            p0.wait_for_selector(".vopt")
            check("Điện thoại thấy đồng hồ đếm ngược bình chọn", p0.locator(".timer").count() == 1)
            own = p0.locator(".vopt:has-text('IT + Legal')")
            check("Không bầu được cho nhóm mình", own.is_disabled())
            p0.locator(".vopt:has-text('CN Hà Nội')").click()
            p0.wait_for_selector(".vopt[aria-pressed=true]")
            p0.locator(".vopt:has-text('Sales AW')").click()
            p0.wait_for_timeout(500)
            check("Đổi ý bình chọn được", "Sales AW" in p0.locator(".vopt[aria-pressed=true]").inner_text())
            phones[1][1].locator(".vopt:has-text('Sales AW')").click()
            scr.wait_for_selector(".vbox")
            n1 = scr.evaluate("[...document.querySelectorAll('.vrow .n')].map(e => e.textContent).join(',')")
            scr.wait_for_timeout(400)
            n2 = scr.evaluate("[...document.querySelectorAll('.vrow .n')].map(e => e.textContent).join(',')")
            check("Đang mở: màn hình lớn chạy số ảo liên tục", n1 != n2 and scr.locator(".vrow.fake").count() == 14)
            check("Đang mở: có đồng hồ đếm ngược và nhạc nền bình chọn", scr.locator(".tbig").count() == 1 and scr.evaluate("Sound.current()") == "vote")
            lv = max(scr.evaluate("Sound.level()") for _ in range(10) if not scr.wait_for_timeout(80))
            check("Nhạc nền thật sự phát ra loa (đo được âm lượng)", lv > 0.005, lv)
            check("Đang mở: màn hình lớn không lộ kết quả thật", "🏆" not in scr.inner_text("body"))
            low = scr.evaluate("Math.max(...[...document.querySelectorAll('.vrow')].map(r => r.getBoundingClientRect().bottom))")
            check("Đang mở: 14 nhóm kèm đồng hồ vẫn nằm gọn trong màn hình", low <= 1080, low)
            scr.screenshot(path=str(SHOTS / "12_screen_vote.png"))
            p0.screenshot(path=str(SHOTS / "13_phone_vote.png"), full_page=True)
            scr.wait_for_selector("text=Đang công bố kết quả", timeout=20000)
            scr.wait_for_timeout(300)                      # bộ điều phối âm thanh cập nhật 10 lần/giây
            check("Hết giờ: tự đóng và chuyển sang trống công bố", scr.evaluate("Sound.current()") == "drum")
            p0.wait_for_selector("text=Đang công bố kết quả trên màn hình lớn")
            check("Đang công bố: điện thoại chưa thấy kết quả", "Dẫn đầu" not in p0.inner_text("body"))
            scr.wait_for_timeout(4500)
            mid_real = scr.locator(".vrow:not(.fake)").count()
            check("Công bố lần lượt: một phần đã hiện, phần còn lại vẫn chạy số ảo", 0 < mid_real < 14, mid_real)
            scr.screenshot(path=str(SHOTS / "12b_screen_vote_revealing.png"))
            scr.wait_for_selector("text=Kết quả bình chọn", timeout=30000)
            scr.wait_for_timeout(500)
            check("Công bố xong: Sales AW thắng với 2 phiếu, hiện sau cùng", "🏆 Sales AW" in scr.inner_text("body")
                  and scr.locator(".vrow.vwin .n").inner_text() == "2")
            log = scr.evaluate("window.__soundLog.slice()")
            check("Công bố có tiếng từng lựa chọn và kèn chiến thắng", log.count("sfx:pop") >= 10 and "sfx:fanfare" in log, (log.count("sfx:pop"), log[-4:]))
            low = scr.evaluate("Math.max(...[...document.querySelectorAll('.vrow')].map(r => r.getBoundingClientRect().bottom))")
            check("Kết quả bình chọn nằm gọn trong màn hình", low <= 1080, low)
            scr.screenshot(path=str(SHOTS / "12c_screen_vote_result.png"))
            p0.wait_for_selector("text=Dẫn đầu: Sales AW", timeout=8000)
            check("Công bố xong: điện thoại mới thấy kết quả", True)

            # quay số: zombie ăn bánh quy
            mc.click("#tabs button:has-text('Quay số')")
            mc.fill("input[data-keep=prize]", "Giải Ba – Tai nghe")
            mc.click("button:has-text('Lấy tất cả đã check-in')")
            n_names = len([x for x in mc.locator("textarea[data-keep=dnames]").input_value().split("\n") if x.strip()])
            check("Lấy danh sách người đã check-in vào ô tên", n_names == 28, n_names)
            mc.click("button:has-text('Cho zombie ăn!')")
            scr.wait_for_selector(".zstage .zc")
            check("Màn hình lớn bày đủ bánh quy theo danh sách", scr.locator(".zc").count() == 28, scr.locator(".zc").count())
            scr.wait_for_timeout(300)
            cut = scr.evaluate("[...document.querySelectorAll('.zn')].filter(e => e.scrollHeight > e.clientHeight + 1).length")
            check("Với 28 người, tên trên bánh hiện đủ, không bị cắt", cut == 0, cut)
            scr.wait_for_timeout(2500)
            t1 = scr.locator(".zombie").evaluate("e => e.style.transform")
            scr.wait_for_timeout(700)
            t2 = scr.locator(".zombie").evaluate("e => e.style.transform")
            check("Zombie di chuyển trên màn hình", t1 != t2 and "translate" in t1, (t1, t2))
            tops = []
            for _ in range(12):
                tops.append(scr.evaluate("document.querySelector('.zombie').getBoundingClientRect().top - document.querySelector('.zstage').getBoundingClientRect().top"))
                scr.wait_for_timeout(250)
            check("Zombie không bị cắt ở mép trên khung", min(tops) >= -1, min(tops))
            scr.wait_for_timeout(2500)
            scr.screenshot(path=str(SHOTS / "14_screen_zombie.png"))
            eaten_before = scr.locator(".zc.eaten").count()
            check("Zombie ăn dần các bánh", eaten_before >= 2, eaten_before)
            sync = scr.evaluate("""() => new Promise(done => {
                const log = window.__soundLog, res = { chomps: 0, offBubble: 0, bubbles: 0, silentBubbles: 0 };
                let n = log.length, shown = false, heard = false;
                const iv = setInterval(() => {
                    const vis = !document.querySelector('.zbubble').classList.contains('hidden');
                    const newC = log.slice(n).filter(x => x === 'sfx:chomp').length; n = log.length;
                    if (newC) { res.chomps += newC; heard = true; if (!vis) res.offBubble += newC; }
                    if (vis && !shown) { res.bubbles++; heard = newC > 0; }
                    if (!vis && shown && !heard) res.silentBubbles++;
                    shown = vis;
                }, 20);
                setTimeout(() => { clearInterval(iv); done(res); }, 6000);
            })""")
            check("Tiếng cạp phát đúng lúc chữ Măm! hiện, không có lần Măm nào im lặng",
                  sync["chomps"] >= 3 and sync["offBubble"] == 0 and sync["silentBubbles"] == 0, sync)
            check("Quay số có nhạc nền và tiếng nhai bánh", scr.evaluate("Sound.current()") == "draw"
                  and scr.evaluate("window.__soundLog.filter(x => x === 'sfx:chomp').length") >= 2)
            p0.wait_for_selector("text=Zombie đang ăn bánh quy")
            check("Điện thoại báo zombie đang ăn, chưa lộ kết quả", "Chúc mừng" not in p0.inner_text("body"))
            # mở lại màn hình lớn giữa chừng: hoạt cảnh chạy tiếp đúng chỗ, không bắt đầu lại
            scr.reload()
            scr.wait_for_selector(".zstage .zc")
            scr.wait_for_timeout(600)
            eaten_after = scr.locator(".zc.eaten").count()
            check("Tải lại màn hình lớn giữa chừng vẫn đúng tiến độ", eaten_after >= eaten_before, (eaten_before, eaten_after))
            scr.wait_for_selector(".zwin", timeout=60000)
            scr.wait_for_timeout(900)
            scr.screenshot(path=str(SHOTS / "15_screen_winner.png"))
            check("Chỉ còn đúng 1 chiếc bánh chưa bị ăn", scr.locator(".zc:not(.eaten)").count() == 1)
            win_cookie = scr.locator(".zc.win .zn").inner_text()
            win_banner = scr.locator(".zwin-name").inner_text().replace("🎉", "").strip()
            check("Tên trên bảng công bố khớp chiếc bánh còn lại", win_cookie == win_banner, (win_cookie, win_banner))
            check("Bảng công bố ghi tên giải", "Giải Ba – Tai nghe" in scr.locator(".zwin").inner_text())
            check("Công bố người trúng bằng tiếng cười hô hô hô", "sfx:hohoho" in scr.evaluate("window.__soundLog.slice(-4)"))
            phones[0][1].wait_for_timeout(800)
            won = [pg for _, pg in phones if pg.locator("text=Bánh của bạn sống sót").count()]
            others_ok = all(("Chúc mừng " + win_banner) in pg.inner_text("body") for _, pg in phones if pg not in won)
            mine = [pg for _, pg in phones if win_banner.startswith(pg.locator("#me").inner_text().strip() + " (")]
            check("Điện thoại người trúng báo trúng, máy khác báo tên người trúng", len(won) == len(mine) and others_ok, (len(won), len(mine), win_banner))
            if won:
                won[0].screenshot(path=str(SHOTS / "16_phone_win.png"))
            mc.screenshot(path=str(SHOTS / "17_mc_draw.png"), full_page=True)
            check("Trang MC ghi người vừa trúng", win_banner in mc.locator(".card").nth(1).inner_text())

            # danh sách tự nhập, 150 tên: vừa màn hình, chữ không quá nhỏ
            big = "\n".join(f"Nguyễn Văn Khách mời số {i:03d}" for i in range(150))
            mc.fill("textarea[data-keep=dnames]", big)
            mc.click("button:has-text('Cho zombie ăn!')")
            scr.wait_for_function("document.querySelectorAll('.zc').length === 150")
            scr.wait_for_timeout(2500)
            box = scr.evaluate("""() => { const g = document.querySelector('.zstage').getBoundingClientRect();
                const cs = [...document.querySelectorAll('.zc')].map(c => c.getBoundingClientRect());
                const f = parseFloat(getComputedStyle(document.querySelector('.zn')).fontSize);
                return { inside: cs.every(r => r.bottom <= g.bottom + 1 && r.right <= g.right + 1), font: f, bottom: g.bottom }; }""")
            check("150 bánh vẫn nằm gọn trong màn hình, chữ đọc được", box["inside"] and box["font"] >= 10 and box["bottom"] <= 1080, box)
            scr.screenshot(path=str(SHOTS / "14b_screen_zombie_150.png"))

            # câu hỏi dài không tràn màn hình lớn; kết thúc có vinh danh nhóm vô địch
            long_q = ("Trong năm 2026, phòng ban nào của công ty đã tổ chức nhiều buổi đào tạo nội bộ nhất "
                      "cho nhân viên mới, kéo dài từ tháng Một cho đến tận tháng Mười Hai và có đông người tham dự nhất?")
            opts = ["Phòng Kinh doanh khu vực miền Nam mở rộng", "Phòng Kế toán và Kiểm soát nội bộ tập đoàn",
                    "Phòng Công nghệ thông tin và Pháp chế", "Chi nhánh Hà Nội cùng Chi nhánh Đà Nẵng"]
            r = mc.evaluate("""async (t) => (await fetch('/api/mc', {method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({a:'questions', set:'score', text:t})})).status""", "\t".join([long_q, *opts, "C", "20"]) + "\nCâu ngắn\tA\tB\tA")
            check("Lưu bộ câu hỏi mới khi không có trò đang chạy", r == 200, r)
            mc.click("#tabs button:has-text('Đố vui')")
            mc.uncheck("input[data-keep=qshuf_score]")
            mc.click("button:has-text('Bắt đầu Đố vui')")
            if mc.locator("button[data-armed]").count():
                mc.locator("button[data-armed]").click()
            mc.wait_for_selector("button:has-text('Mở câu 1')")
            mc.click("button:has-text('Mở câu 1')")
            scr.wait_for_selector(".sq-q.xlong, .sq-q.long")
            scr.wait_for_timeout(300)
            low = scr.evaluate("Math.max(...[...document.querySelectorAll('.sopt, .progress')].map(e => e.getBoundingClientRect().bottom))")
            check("Câu hỏi dài vẫn nằm gọn trong màn hình lớn", low <= 1080, low)
            scr.screenshot(path=str(SHOTS / "18_screen_long_question.png"))
            p0.wait_for_selector(".ans")
            p0.locator(".ans").nth(2).click()
            mc.wait_for_selector("button:has-text('Công bố đáp án')")
            mc.click("button:has-text('Công bố đáp án')")
            mc.wait_for_selector("button:has-text('Kết thúc, xem chung cuộc')")
            mc.click("button:has-text('Kết thúc, xem chung cuộc')")
            if mc.locator("button[data-armed]").count():
                mc.locator("button[data-armed]").click()
            scr.wait_for_selector(".champ")
            scr.wait_for_timeout(300)
            check("Chung cuộc vinh danh nhóm vô địch", "Nhóm vô địch: IT + Legal" in scr.locator(".champ").inner_text())
            low = scr.evaluate("Math.max(...[...document.querySelectorAll('.board .row')].map(r => r.getBoundingClientRect().bottom))")
            check("Bảng chung cuộc (14 nhóm + top 10) nằm gọn trong màn hình", low <= 1080, low)
            scr.screenshot(path=str(SHOTS / "19_screen_final.png"))

            mc.click("#tabs button:has-text('Câu hỏi')")
            check("Tab Câu hỏi có 2 bộ riêng cho Đố vui và Rung chuông vàng",
                  mc.locator("textarea[data-keep=qtext_score]").count() == 1 and mc.locator("textarea[data-keep=qtext_bell]").count() == 1)
            mc.click("#tabs button:has-text('Sảnh chờ')")
            mc.click("button:has-text('Đang bật · bấm để tắt')")
            scr.wait_for_timeout(600)
            check("MC tắt âm thanh từ điện thoại, màn hình lớn nhận ngay", scr.evaluate("S.sound.on") is False)
            mc.click("button:has-text('Đang tắt · bấm để bật')")

            check("Không có lỗi JavaScript nào trên cả 3 trang trong suốt lượt test", not js_errors, js_errors[:3])
            # không có thanh cuộn ngang ở mọi trang
            for nm, pg in [("điện thoại", p0), ("MC", mc)]:
                w = pg.evaluate("[innerWidth, document.documentElement.scrollWidth]")
                check(f"Trang {nm} không bị tràn ngang", w[1] <= w[0], w)
            w = scr.evaluate("[innerWidth, innerHeight, document.documentElement.scrollWidth, document.documentElement.scrollHeight]")
            check("Màn hình lớn vừa khung 1920x1080, không cuộn", w[2] <= w[0] and w[3] <= w[1], w)
            # file âm thanh thật (static/sfx): có thì phát file thay tiếng tổng hợp
            sfx_dir = ROOT / "static" / "sfx"
            made = []
            for name, freq in (("bite", 2500), ("santa", 180)):
                f = sfx_dir / f"{name}.mp3"
                if not f.exists():
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=0.6",
                                    "-b:a", "96k", str(f)], check=True)
                    made.append(f)
            try:
                pg2 = scr_ctx.new_page()
                pg2.goto(B + "/screen")
                pg2.click("#snd")
                pg2.wait_for_function("Sound.clips().length === 2", timeout=8000)
                pg2.evaluate("Sound.sfx('chomp')")
                lv = max(pg2.evaluate("Sound.level()") for _ in range(8) if not pg2.wait_for_timeout(50))
                check("Có file tiếng cạp / Santa thì app nạp và phát file đó", lv > 0.005, lv)
                pg2.close()
            finally:
                for f in made:
                    f.unlink()
            br.close()
    finally:
        srv.terminate()
    print(f"\n{sum(results)}/{len(results)} đạt")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
