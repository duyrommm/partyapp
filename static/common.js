// Phần dùng chung: dựng phần tử an toàn (không dùng innerHTML với dữ liệu người dùng),
// kết nối WebSocket tự nối lại, đếm ngược, xác nhận hai lần chạm.
(function () {
  "use strict";

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
        else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
        else if (k === "text") el.textContent = v;
        else el.setAttribute(k, v === true ? "" : v);
      }
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  // Kết nối bền: tự nối lại khi rớt mạng, khi mở lại tab, khi im lặng quá lâu.
  function live(url, handlers) {
    let ws = null, timer = null, backoff = 500, lastMsg = 0, closedByUs = false;
    const status = handlers.status || (() => {});

    function open() {
      clearTimeout(timer);
      if (ws && (ws.readyState === 0 || ws.readyState === 1)) return;
      status("connecting");
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(proto + "//" + location.host + (typeof url === "function" ? url() : url));
      ws.onopen = () => { backoff = 500; lastMsg = Date.now(); status("online"); };
      ws.onmessage = (e) => {
        lastMsg = Date.now();
        let m;
        try { m = JSON.parse(e.data); } catch (_) { return; }
        if (m.t === "ka" || m.t === "pong") return;
        m._at = performance.now();
        handlers.message(m);
      };
      ws.onclose = () => {
        if (closedByUs) return;
        status("offline");
        timer = setTimeout(open, backoff);
        backoff = Math.min(backoff * 1.7, 5000);
      };
      ws.onerror = () => { try { ws.close(); } catch (_) {} };
    }
    function send(obj) {
      if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; }
      return false;
    }
    function stop() { closedByUs = true; clearTimeout(timer); if (ws) ws.close(); }
    function restart() { closedByUs = false; if (ws) { try { ws.close(); } catch (_) {} } ws = null; open(); }
    setInterval(() => {
      if (!ws || ws.readyState !== 1) return;
      send({ t: "ping" });
      if (Date.now() - lastMsg > 40000) { try { ws.close(); } catch (_) {} }
    }, 20000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !closedByUs && (!ws || ws.readyState > 1)) { backoff = 300; open(); }
    });
    window.addEventListener("online", () => { if (!closedByUs) { backoff = 300; open(); } });
    window.addEventListener("pageshow", (e) => { if (e.persisted && !closedByUs) { backoff = 300; open(); } });
    if (handlers.keepAwake) {
      // Render miễn phí cho app ngủ khi lâu không có lượt truy cập HTTP mới; WebSocket chưa chắc được tính.
      const every = window.__PA_KEEPALIVE_MS || 240000;
      setInterval(() => { fetch("/health", { cache: "no-store" }).catch(() => {}); }, every);
    }
    open();
    return { send, stop, restart };
  }

  // Đếm ngược theo đồng hồ của máy chủ: left_ms tính tại lúc máy chủ gửi.
  function secondsLeft(leftMs, receivedAt) {
    return Math.max(0, Math.ceil((leftMs - (performance.now() - receivedAt)) / 1000));
  }

  // Nút cần chạm hai lần mới chạy (Chrome iOS chặn confirm()).
  function armed(btn, label, fn) {
    let t = null;
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      if (btn.dataset.armed) { clearTimeout(t); delete btn.dataset.armed; btn.textContent = label; fn(); return; }
      btn.dataset.armed = "1";
      btn.textContent = "Chạm lần nữa để chắc chắn";
      t = setTimeout(() => { delete btn.dataset.armed; btn.textContent = label; if (PA.onDisarm) PA.onDisarm(); }, 3500);
    });
  }

  const SHAPES = ["▲", "◆", "●", "■"];
  const LETTERS = ["A", "B", "C", "D"];

  window.PA = { h, live, secondsLeft, armed, SHAPES, LETTERS };
})();
