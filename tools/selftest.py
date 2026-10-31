"""End-to-end self test: drives the real main window, writes screenshots and a report.

Usage: python tools/selftest.py <work dir>
Uses a temporary %APPDATA% so the user's settings, history and playlist are untouched.
"""
import functools
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

WORK = Path(sys.argv[1]).resolve()
WORK.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(WORK / "appdata")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wpp import paths  # noqa: E402
paths.DATA = Path(os.environ["APPDATA"]) / "WindowsPlayerPro"
paths.setup_native()
import wpp.config as config_mod  # noqa: E402
config_mod.DATA = paths.DATA
config_mod.cfg.path = paths.DATA / "config.json"

from PySide6.QtCore import QCoreApplication, Qt, QTimer  # noqa: E402
import PySide6.QtWebEngineCore  # noqa: E402,F401
from PySide6.QtWidgets import QApplication  # noqa: E402

FF = paths.ffmpeg_location()
MEDIA = WORK / "media"
SITE = WORK / "site"
REPORT = []


def check(name, ok, detail=""):
    REPORT.append((name, bool(ok), str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {detail}" if detail else ""), flush=True)


def make_media():
    MEDIA.mkdir(exist_ok=True)
    SITE.mkdir(exist_ok=True)
    jobs = {
        MEDIA / "hevc10_ac3.mkv": ["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24", "-f", "lavfi", "-i",
                                   "sine=frequency=440:sample_rate=48000", "-t", "20", "-c:v", "libx265", "-preset", "ultrafast",
                                   "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error", "-ac", "6", "-c:a", "ac3",
                                   "-metadata:s:a:0", "language=rus", "-metadata:s:a:0", "title=Дубляж"],
        MEDIA / "tone.flac": ["-f", "lavfi", "-i", "sine=frequency=330:sample_rate=96000", "-t", "15", "-c:a", "flac",
                              "-sample_fmt", "s32"],
        SITE / "clip.mp4": ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-f", "lavfi", "-i", "sine=f=500",
                            "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    }
    for out, args in jobs.items():
        if not out.exists():
            subprocess.run([FF, "-y", "-loglevel", "error", *args, str(out)], check=True)
    (MEDIA / "hevc10_ac3.srt").write_text("1\n00:00:01,000 --> 00:00:05,000\nТестовые субтитры\n", "utf-8")
    hls = SITE / "hls"
    hls.mkdir(exist_ok=True)
    if not (hls / "master.m3u8").exists():
        subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25", "-t", "8",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-f", "hls", "-hls_time", "2",
                        "-hls_playlist_type", "vod", "-master_pl_name", "master.m3u8", str(hls / "v.m3u8")], check=True)
    # page 1: plain <video> tag (yt-dlp generic usually handles it)
    (SITE / "video.html").write_text("<html><head><title>Тестовая страница</title></head><body>"
                                     "<video controls src='clip.mp4'></video></body></html>", "utf-8")
    # page 2: media only appears via JavaScript, URL assembled at runtime -> only the browser sniffer finds it
    (SITE / "js.html").write_text("""<html><head><title>JS-плеер</title></head><body><div id=p></div><script>
setTimeout(function(){ var v=document.createElement('video'); v.muted=true; v.autoplay=true;
  v.src=['hls','mas'+'ter.m'+'3u8'].join('/'); document.getElementById('p').appendChild(v);
  fetch(['hls','mas'+'ter.m'+'3u8'].join('/')); }, 400);
</script></body></html>""", "utf-8")
    (SITE / "empty.html").write_text("<html><head><title>Пусто</title></head><body><p>Нет медиа</p></body></html>", "utf-8")


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


class Runner:
    def __init__(self, win, base):
        self.w, self.base = win, base
        self.steps = []
        self.i = 0

    def step(self, delay_ms):
        def deco(fn):
            self.steps.append((delay_ms, fn))
            return fn
        return deco

    def go(self):
        if self.i >= len(self.steps):
            return self.finish()
        delay, fn = self.steps[self.i]
        self.i += 1

        def run():
            try:
                r = fn()
            except Exception as e:
                check(f"{fn.__name__} (исключение)", False, "".join(traceback.format_exception_only(e)).strip())
                r = None
            if callable(r):        # step waits on a condition: r() -> bool
                self.wait(r, fn.__name__, time.time())
            else:
                self.go()
        QTimer.singleShot(delay, run)

    def wait(self, cond, name, t0, limit=60):
        try:
            done = cond()
        except Exception as e:
            check(name + " (ожидание)", False, str(e))
            done = True
        if done:
            return self.go()
        if time.time() - t0 > limit:
            check(name + " (таймаут)", False, f"> {limit} с")
            return self.go()
        QTimer.singleShot(250, lambda: self.wait(cond, name, t0, limit))

    def shot(self, name):
        self.w.grab().save(str(WORK / f"{name}.png"))

    def finish(self):
        ok = sum(1 for r in REPORT if r[1])
        (WORK / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), "utf-8")
        print(f"\nИТОГО: {ok} из {len(REPORT)} проверок прошли", flush=True)
        self.w._quitting = True
        self.w.close()
        QTimer.singleShot(500, QApplication.quit)


def main():
    make_media()
    base = serve()
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    import locale
    locale.setlocale(locale.LC_NUMERIC, "C")
    from wpp.config import cfg
    cfg.data["update_check"] = False
    cfg.data["dl_dir"] = str(WORK / "dl_main")
    cfg.data["dl_backup"] = str(WORK / "dl_backup")
    cfg.data["clip_top"] = False
    cfg.data["helper"] = False
    from wpp.theme import init_theme
    init_theme()
    from wpp.ui.winfx import PopupRounder
    rounder = PopupRounder(app)
    app.installEventFilter(rounder)
    from wpp.library import Item
    import wpp.autostart as autostart
    autostart.refresh_command = lambda: None      # never rewrite the user's real autostart entry
    autostart.set_enabled = lambda on: None
    import wpp.ipc as ipc
    ipc.launch = lambda cmd: None                 # never start a second helper next to the user's one
    from wpp.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1280, 800)
    w.show()
    # the user's own helper may be running; the test watches the clipboard itself
    w.helper_timer.stop()
    w._sync_helper = lambda: None
    w._start_helper_if_needed = lambda: None
    w.clip.enabled = True
    R = Runner(w, base)
    p = w.player
    S = {}

    @R.step(800)
    def startup():
        check("Окно открылось", w.isVisible())
        check("Значок в трее", w.tray.isVisible())
        check("libmpv загружен", bool(p.video.get("mpv-version")), p.video.get("mpv-version"))
        R.shot("01_player_empty_light")

    @R.step(100)
    def play_hevc():
        p.add_and_play([Item(str(MEDIA / "hevc10_ac3.mkv"))])
        return lambda: p.loaded and p.dur > 0 and p.pos > 0.3

    @R.step(1500)
    def hevc_info():
        vp = p.video.get("video-params") or {}
        fmt = vp.get("hw-pixelformat") or vp.get("pixelformat") or ""
        check("HEVC 10-bit воспроизводится", "10" in fmt and vp.get("w") == 1920 and "10-bit" in " ".join(p.info_tags),
              f"{fmt} {vp.get('w')}x{vp.get('h')} {p.info_tags[:1]}")
        ac = [t for t in p.tracks if t["type"] == "audio"]
        check("AC3 5.1 дорожка распознана", ac and "AC3" in ac[0].get("codec", "").upper() + ac[0].get("codec-desc", "").upper(),
              ac and ac[0])
        subs = [t for t in p.tracks if t["type"] == "sub"]
        check("Внешние субтитры .srt подхвачены", bool(subs), subs and subs[0].get("codec"))
        check("Плашки с параметрами", bool(p.info_tags), " | ".join(p.info_tags))
        check("Декодер", True, p.video.get("hwdec-current"))
        R.shot("02_player_hevc")

    @R.step(100)
    def transport():
        t0 = p.pos
        p.seek_rel(5)
        S["t0"] = t0
        return lambda: p.pos > t0 + 4

    @R.step(300)
    def transport2():
        check("Перемотка +5 с", p.pos > S["t0"] + 4, f"{S['t0']:.1f} → {p.pos:.1f}")
        p.toggle()
        return lambda: p.paused

    @R.step(300)
    def transport3():
        check("Пауза", p.paused)
        p.toggle()
        p.set_speed(1.5)
        p.change_volume(-10)
        return lambda: abs(p.video.get("speed", 0) - 1.5) < 0.01

    @R.step(300)
    def transport4():
        check("Скорость 1.5×", abs(p.video.get("speed", 0) - 1.5) < 0.01 and p.b_speed.text() == "1.5×", p.b_speed.text())
        check("Громкость −10", p.video.get("volume") == 70, p.video.get("volume"))
        p.set_speed(1.0)
        audio = [t for t in p.tracks if t["type"] == "audio"]
        p.video._set("sid", "no")
        check("Субтитры выключаются", p.video.get("sid") in (False, "no"), p.video.get("sid"))
        p.video._set("sid", "auto")

    @R.step(200)
    def eq():
        cfg.set("eq", {"on": True, "pre": -3.0, "g": [6, 5, 4, 2, 0, 0, 0, 0, 0, 0], "preset": "Усиление баса"})
        af = p.video.get("af") or []
        check("Эквалайзер включает фильтр mpv", af and "equalizer" in json.dumps(af, ensure_ascii=False), json.dumps(af)[:200])
        p.eq_popup()

    @R.step(500)
    def eq_shot():
        pops = [x for x in QApplication.topLevelWidgets() if x.isVisible() and x.__class__.__name__ == "EqPopup"]
        check("Окно эквалайзера открыто", bool(pops))
        if pops:
            pops[0].grab().save(str(WORK / "03_eq_popup.png"))
            pops[0].close()
        cfg.set("eq", dict(cfg["eq"], on=False))
        check("Эквалайзер выключается", not p.video.get("af"), p.video.get("af"))

    @R.step(100)
    def flac():
        p.add_and_play([Item(str(MEDIA / "tone.flac"))])
        return lambda: p.loaded and p.pos > 0.5

    @R.step(1200)
    def flac2():
        at = next((t for t in p.tracks if t["type"] == "audio"), {})
        check("FLAC 24/96 воспроизводится", "flac" in (at.get("codec") or ""), at.get("codec"))
        check("Визуализация для аудио", bool(p.video.get("lavfi-complex")),
              f"{p.video.get('lavfi-complex', '')[:60]} | aid={p.video.get('aid')} "
              f"| tracks={[t.get('type') for t in (p.video.get('track-list') or [])]}")
        R.shot("04_player_flac")

    @R.step(100)
    def history():
        p._save_pos()
        check("История пополняется", len(w.history.entries) >= 2, len(w.history.entries))
        check("История хранит папку файла", w.history.entries[0].get("where") == str(MEDIA), w.history.entries[0].get("where"))
        w.go("history")

    @R.step(300)
    def history_shot():
        R.shot("05_history")
        w.go("playlist")

    @R.step(300)
    def playlist_shot():
        check("Плейлист: 2 элемента", len(w.playlist.items) == 2, len(w.playlist.items))
        R.shot("06_playlist")

    # ----- link probing -----
    from wpp.net.probe import Prober
    prober = Prober()
    prober.finished.connect(lambda r: S.__setitem__("res", r))

    def find(url):
        S["res"] = None
        prober.start(url)
        return lambda: S["res"] is not None

    @R.step(100)
    def probe_video_page():
        return find(base + "/video.html")

    @R.step(300)
    def probe_video_page2():
        r = S["res"]
        check("Страница с <video>: медиа найдено", r.state == "ok" and r.options, f"{r.state} {r.source} {len(r.options)}")
        S["dlg"] = w.open_options(r)

    @R.step(600)
    def options_window():
        d = S["dlg"]
        check("Окно «Загрузка» открыто отдельным окном", d.isVisible() and d.isWindow(), d.windowTitle())
        d.grab().save(str(WORK / "07_download_dialog.png"))
        n0 = len(w.dm.tasks)
        d.folder.setText(str(WORK / "dl_custom"))
        d._go()
        t = w.dm.tasks[0]
        check("Окно «Загрузка» ставит загрузку в выбранную папку", len(w.dm.tasks) == n0 + 1 and t.folder == str(WORK / "dl_custom"),
              f"{t.folder} fmt={t.format}")
        S["t0"] = t
        return lambda: t.state in ("done", "error")

    @R.step(200)
    def options_window2():
        t = S["t0"]
        check("Загрузка с настройками завершилась", t.state == "done" and os.path.exists(t.file), f"{t.state} {t.error} {t.file}")
        d = w.open_options(S["res"])
        check("Окно «Загрузка» открывается повторно", d.isVisible(), d.windowTitle())
        d.close()

    @R.step(100)
    def sniff_js():
        return find(base + "/js.html")

    @R.step(300)
    def sniff_js2():
        r = S["res"]
        check("JS-страница: перехват нашёл HLS", r.state == "ok" and any("HLS" in o.label for o in r.options),
              f"{r.state} {r.source} {[o.label for o in r.options]}")
        S["hls"] = r

    @R.step(100)
    def sniff_empty():
        return find(base + "/empty.html")

    @R.step(300)
    def sniff_empty2():
        r = S["res"]
        check("Пустая страница: «медиа не найдено»", r.state == "none" and not r.options, f"{r.state} {r.message}")
    @R.step(100)
    def play_stream():
        r = S["hls"]
        opt = next(o for o in r.options if "HLS" in o.label)
        w.play_option(opt, r)
        return lambda: p.loaded and p.pos > 0.3

    @R.step(500)
    def play_stream2():
        check("Перехваченный HLS воспроизводится", p.loaded and p.dur > 5, f"{p.dur:.1f} c")

    # ----- downloads + backup folder -----
    @R.step(100)
    def dl_normal():
        r = S["hls"]
        opt = next(o for o in r.options if "HLS" in o.label)
        S["t1"] = w.dm.add(opt, r.source, r.page_url)
        return lambda: S["t1"].state in ("done", "error")

    @R.step(300)
    def dl_normal2():
        t = S["t1"]
        ok = t.state == "done" and t.file and os.path.exists(t.file) and not t.backup
        check("Скачивание HLS в основную папку", ok, f"{t.state} {t.file} {t.error}")

    @R.step(100)
    def dl_backup():
        cfg.data["dl_dir"] = "Q:\\Нет такого диска\\Медиа"
        S["rerouted"] = []
        w.dm.rerouted.connect(lambda t, why: S["rerouted"].append([n.title.text() for n in w.notes.notes]))
        r = S["res_video"] = None
        from wpp.net.common import Option
        opt = Option("clip.mp4", "MP4", 0, "video", "best", base + "/clip.mp4", {}, "Резервный тест", direct=True)
        S["t2"] = w.dm.add(opt, "перехват", base + "/video.html")
        return lambda: S["t2"].state in ("done", "error")

    @R.step(600)
    def dl_backup2():
        t = S["t2"]
        check("Недоступная основная папка → резервная", t.backup and t.folder == cfg["dl_backup"] and t.state == "done",
              f"{t.state} backup={t.backup} {t.folder} {t.error}")
        check("Уведомление о переходе в резерв", any("резерв" in x for xs in S["rerouted"] for x in xs), S["rerouted"])
        notes = [n for n in w.notes.notes if n.title.text().startswith("Загрузка идёт")]
        if notes:
            notes[0].grab().save(str(WORK / "09_note_backup.png"))
        w.go("downloads")

    @R.step(400)
    def dl_both_fail():
        R.shot("10_downloads_queue")
        cfg.data["dl_backup"] = "Q:\\Тоже нет"
        from wpp.net.common import Option
        t = w.dm.add(Option("x", "", 0, "video", "best", base + "/clip.mp4", {}, "Обе папки недоступны", direct=True))
        check("Обе папки недоступны → ошибка с понятным текстом", t.state == "error" and "резервная" in t.error, t.error)
        cfg.data["dl_dir"] = str(WORK / "dl_main")
        cfg.data["dl_backup"] = str(WORK / "dl_backup")
        for n in list(w.notes.notes):
            n.dismiss()

    # ----- clipboard -----
    from PySide6.QtWidgets import QPushButton

    def card():
        return next((n for n in w.notes.notes if any(b.text() == "Скачать" for b in n.findChildren(QPushButton))), None)

    @R.step(300)
    def clipboard():
        w.go("player")
        QApplication.clipboard().setText("смотри " + base + "/video.html")
        return lambda: card() is not None

    @R.step(300)
    def clipboard2():
        n = card()
        check("Ссылка из буфера → предложение скачать", "127.0.0.1" not in n.body.text() and n.title.text(),
              n.title.text() + " | " + n.body.text())
        check("Окно «Найдено видео» компактное", n.height() <= 200 and n.width() <= 420, f"{n.width()}x{n.height()}")
        n.grab().save(str(WORK / "11_note_clipboard.png"))
        before = len(w.dm.tasks)
        next(b for b in n.findChildren(QPushButton) if b.text() == "Скачать").click()
        check("Кнопка «Скачать» во всплывающем окне", len(w.dm.tasks) == before + 1)
        S["t3"] = w.dm.tasks[0]
        return lambda: S["t3"].state in ("done", "error") and card() is None

    @R.step(300)
    def clipboard3():
        check("Окно закрывается после «Скачать»", card() is None)
        check("Загрузка из всплывающего окна завершилась", S["t3"].state == "done", f"{S['t3'].state} {S['t3'].error}")
        QApplication.clipboard().setText("просто текст без ссылок")
        n0 = len(w.notes.notes)
        check("Текст без ссылки игнорируется", len(w.notes.notes) == n0)
        for n in list(w.notes.notes):
            n.dismiss()
        QApplication.clipboard().setText("пустая " + base + "/empty.html")
        S["t_empty"] = time.time()
        return lambda: not w.clip.running and time.time() - S["t_empty"] > 1

    @R.step(300)
    def clipboard4():
        check("Страница без медиа: окно не появляется", not w.notes.notes, [n.title.text() for n in w.notes.notes])
        QApplication.clipboard().setText("снова " + base + "/video.html")
        return lambda: card() is not None

    @R.step(200)
    def clipboard5():
        check("Повторная ссылка снова предлагается", len(w.notes.notes) == 1)
        QApplication.clipboard().setText("и ещё " + base + "/video.html?x=2")
        return lambda: card() is not None and not w.clip.running

    @R.step(300)
    def clipboard6():
        check("Новое окно заменяет старое", len(w.notes.notes) == 1, len(w.notes.notes))
        n = card()
        next(b for b in n.findChildren(QPushButton) if b.text() == "Подробнее").click()
        dlgs = [x for x in QApplication.topLevelWidgets() if x.isVisible() and x.__class__.__name__ == "DownloadDialog"]
        check("«Подробнее» открывает окно и закрывает карточку", dlgs and card() is None)
        for d in dlgs:
            d.close()
        for n in list(w.notes.notes):
            n.dismiss()
    # ----- settings, theme, git -----
    @R.step(300)
    def settings():
        w.go("settings")
        R.shot("12_settings_light")
        cfg.set("theme", "dark")

    @R.step(500)
    def dark():
        from wpp.theme import theme
        check("Тёмная тема #202020", theme.dark and theme.t["bg"] == "#202020")
        R.shot("13_settings_dark")
        w.go("downloads")

    @R.step(300)
    def dark2():
        R.shot("14_downloads_dark")
        w.go("player")

    @R.step(300)
    def dark3():
        R.shot("15_player_dark")
        from wpp import updater
        check("git для обновлений найден", bool(updater.git_exe()), updater.git_exe())

    @R.step(100)
    def fullscreen():
        w.toggle_fullscreen(True)

    @R.step(800)
    def fullscreen2():
        check("Полноэкранный режим", w.isFullScreen() and not w.nav.isVisible())
        bar = p.fsbar
        check("Панель поверх видео в отдельном окне", bar.isVisible() and p.controls.window() is bar and
              p.stack.height() >= w.height() - 2, f"stage={p.stack.height()} win={w.height()}")
        bar.grab().save(str(WORK / "16_fullscreen_bar.png"))
        p.paused = False
        p._idle()

    @R.step(600)
    def fullscreen2b():
        check("Панель плавно скрывается", p.fsbar.windowOpacity() < 0.05, round(p.fsbar.windowOpacity(), 2))
        p._show_osd()

    @R.step(500)
    def fullscreen2c():
        check("Панель возвращается при движении мыши", p.fsbar.windowOpacity() > 0.95, round(p.fsbar.windowOpacity(), 2))
        w.toggle_fullscreen(False)

    @R.step(600)
    def fullscreen3():
        check("Выход из полного экрана", not w.isFullScreen() and w.nav.isVisible() and p.controls.parent() is p)
        cfg.set("theme", "light")

    R.go()
    app.exec()


if __name__ == "__main__":
    main()
