"""
Плеер с кроссфейдом в реальном времени.

Как работает:
  ffmpeg декодирует каждый трек в сырой PCM (pipe) -> микшер в audio-callback
  sounddevice. За `crossfade` секунд до конца трека A второй декодер (B) уже
  готов, и микшер считает  out = A*cos(t*pi/2) + B*sin(t*pi/2)  (equal-power).
  Громкость треков не меняется, тишина по краям срезается (crossfade.py).

Зависимости:  pip install numpy sounddevice   +  ffmpeg в PATH
Файл crossfade.py должен лежать рядом.
"""
import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

import numpy as np

from crossfade import find_audio_end, find_audio_start, probe_duration

SR = 44100
CH = 2
CHUNK = 4096            # фреймов в одном куске из pipe
PREBUFFER = 8           # кусков, которые ждём перед стартом (~0.75 c)
QUEUE_MAX = 48          # ~4.5 c буфера на декодер


# ---------- Анализ трека ----------

class TrackInfo:
    def __init__(self, path: str):
        self.path = path
        self.dur = probe_duration(path)
        self.start = find_audio_start(path)
        self.end = find_audio_end(path, self.dur)
        if self.end < 1.0:
            self.end = self.dur
        if self.end - self.start < 1.0:
            self.start = 0.0
        self.length = self.end - self.start      # сколько секунд реально играем


# ---------- Декодер: ffmpeg -> PCM float32 ----------

class Decoder:
    def __init__(self, path: str, start: float, length: float):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg не найден в PATH")
        self.proc = subprocess.Popen(
            [ffmpeg, "-v", "error", "-ss", f"{start:.3f}",
             "-t", f"{length:.3f}", "-i", path, "-vn",
             "-f", "f32le", "-ac", str(CH), "-ar", str(SR), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self.buf = np.zeros((0, CH), np.float32)
        self.eof = False
        self.stopped = False
        self.done = threading.Event()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            while not self.stopped:
                data = self.proc.stdout.read(CHUNK * CH * 4)
                if not data:
                    break
                data = data[: len(data) // (CH * 4) * (CH * 4)]
                if not data:
                    continue
                arr = np.frombuffer(data, np.float32).reshape(-1, CH)
                while not self.stopped:
                    try:
                        self.q.put(arr, timeout=0.2)
                        break
                    except queue.Full:
                        pass
        except Exception:
            pass
        finally:
            self.done.set()

    def wait_ready(self, timeout: float = 8.0):
        t0 = time.time()
        while (self.q.qsize() < PREBUFFER and not self.done.is_set()
               and time.time() - t0 < timeout and not self.stopped):
            time.sleep(0.01)

    def read(self, n: int) -> np.ndarray:
        """Ровно n фреймов; нехватку (underrun / конец файла) добивает тишиной."""
        while len(self.buf) < n and not self.eof:
            finished = self.done.is_set()
            try:
                self.buf = np.concatenate((self.buf, self.q.get_nowait()))
            except queue.Empty:
                if finished:
                    self.eof = True
                break
        out = np.zeros((n, CH), np.float32)
        take = min(n, len(self.buf))
        out[:take] = self.buf[:take]
        self.buf = self.buf[take:]
        return out

    def stop(self):
        self.stopped = True
        try:
            self.proc.kill()
            self.proc.wait(timeout=2)
        except Exception:
            pass


# ---------- Плеер ----------

class Player:
    def __init__(self, playlist=None, crossfade: float = 8.0):
        self.playlist: list[str] = list(playlist or [])
        self.crossfade = crossfade
        self.lock = threading.RLock()
        self.cache: dict[str, TrackInfo] = {}

        self.idx = -1
        self.cur: Decoder | None = None
        self.cur_info: TrackInfo | None = None
        self.cur_len = 0                # фреймов
        self.cur_pos = 0                # фреймов
        self.nxt: Decoder | None = None
        self.nxt_info: TrackInfo | None = None
        self.nxt_len = 0
        self.fade: tuple[int, int] | None = None   # (старт_фрейм, длина_фреймов)
        self.paused = False
        self.finished = False

        self._gen = 0                   # поколение: сбрасывается при play_index
        self._seek_id = 0
        self._preparing = None
        self._stream = None
        self._alive = True

    # ----- запуск / остановка -----

    def start(self):
        import sounddevice as sd
        self._stream = sd.OutputStream(
            samplerate=SR, channels=CH, dtype="float32", blocksize=1024,
            callback=self._callback)
        self._stream.start()
        threading.Thread(target=self._manager, daemon=True).start()

    def close(self):
        self._alive = False
        with self.lock:
            for d in (self.cur, self.nxt):
                if d:
                    d.stop()
            self.cur = self.nxt = None
        if self._stream:
            self._stream.stop()
            self._stream.close()

    # ----- публичное управление -----

    def play_index(self, i: int, seek_s: float = 0.0):
        if not (0 <= i < len(self.playlist)):
            return
        with self.lock:
            self._gen += 1
            g = self._gen
            for d in (self.cur, self.nxt):
                if d:
                    d.stop()
            self.cur = self.nxt = None
            self.fade = None
            self.idx = i
            self.finished = False
            self.paused = False
        threading.Thread(target=self._load, args=(i, g, seek_s),
                         daemon=True).start()

    def toggle_pause(self):
        self.paused = not self.paused

    def next(self):
        with self.lock:
            if self.idx + 1 >= len(self.playlist) or self.fade:
                return
            if self.cur and self.nxt:
                # быстрый кроссфейд (до 2 с) прямо сейчас
                length = int(min(2 * SR, self.cur_len - self.cur_pos,
                                 self.nxt_len // 2))
                if length > 0:
                    self.fade = (self.cur_pos, length)
                    return
            nxt = self.idx + 1
        self.play_index(nxt)

    def prev(self):
        if self.position() > 3 or self.idx == 0:
            self.play_index(max(self.idx, 0))
        else:
            self.play_index(self.idx - 1)

    def seek(self, seconds: float):
        with self.lock:
            if not self.cur_info:
                return
            info, i, g = self.cur_info, self.idx, self._gen
            self._seek_id += 1
            sid = self._seek_id
        seconds = max(0.0, min(seconds, info.length - 1.0))

        def work():
            dec = Decoder(info.path, info.start + seconds,
                          info.length - seconds)
            dec.wait_ready()
            with self.lock:
                if g != self._gen or i != self.idx or sid != self._seek_id:
                    dec.stop()
                    return
                old, self.cur = self.cur, dec
                self.cur_pos = int(seconds * SR)
                if self.fade and self.nxt:      # B уже частично проигран
                    self.nxt.stop()
                    self.nxt = None
                self.fade = None
            if old:
                old.stop()
        threading.Thread(target=work, daemon=True).start()

    def position(self) -> float:
        return self.cur_pos / SR

    def length(self) -> float:
        return self.cur_len / SR

    # ----- внутреннее -----

    def _info(self, path: str) -> TrackInfo:
        if path not in self.cache:
            self.cache[path] = TrackInfo(path)
        return self.cache[path]

    def _load(self, i: int, g: int, seek_s: float):
        try:
            info = self._info(self.playlist[i])
            seek_s = min(seek_s, max(0.0, info.length - 1.0))
            dec = Decoder(info.path, info.start + seek_s,
                          info.length - seek_s)
            dec.wait_ready()
        except Exception as e:
            print("Ошибка загрузки:", e)
            return
        with self.lock:
            if g != self._gen:
                dec.stop()
                return
            self.cur, self.cur_info = dec, info
            self.cur_len = int(info.length * SR)
            self.cur_pos = int(seek_s * SR)

    def _manager(self):
        """Заранее анализирует и буферизует следующий трек."""
        while self._alive:
            time.sleep(0.1)
            with self.lock:
                i = self.idx + 1
                key = (self._gen, i)
                need = (self.cur is not None and self.nxt is None
                        and i < len(self.playlist) and self._preparing != key)
                if need:
                    self._preparing = key
                    g = self._gen
            if not need:
                continue
            try:
                info = self._info(self.playlist[i])
                dec = Decoder(info.path, info.start, info.length)
                dec.wait_ready()
            except Exception as e:
                print("Ошибка подготовки:", e)
                continue
            with self.lock:
                if g != self._gen or self.idx + 1 != i or self.nxt:
                    dec.stop()
                    continue
                self.nxt, self.nxt_info = dec, info
                self.nxt_len = int(info.length * SR)

    def _switch(self, consumed: int):
        old = self.cur
        self.cur, self.cur_info = self.nxt, self.nxt_info
        self.cur_len, self.cur_pos = self.nxt_len, consumed
        self.nxt = self.nxt_info = None
        self.fade = None
        self.idx += 1
        if old:
            threading.Thread(target=old.stop, daemon=True).start()

    def _finish(self):
        old, self.cur = self.cur, None
        self.finished = True
        if old:
            threading.Thread(target=old.stop, daemon=True).start()

    def _callback(self, outdata, frames, time_info, status):
        with self.lock:
            if self.cur is None or self.paused:
                outdata.fill(0)
                return

            a = self.cur.read(frames)
            pos = self.cur_pos

            # пора начинать кроссфейд?
            if self.fade is None and self.nxt is not None:
                cfe = int(min(self.crossfade * SR, self.cur_len / 2,
                              self.nxt_len / 2))
                if cfe > SR // 10 and pos + frames > self.cur_len - cfe:
                    start = max(self.cur_len - cfe, pos)
                    if self.cur_len - start > 0:
                        self.fade = (start, self.cur_len - start)

            if self.fade:
                start, length = self.fade
                b = self.nxt.read(frames)
                t = (pos - start + np.arange(frames)) / length
                t = np.clip(t, 0.0, 1.0).astype(np.float32)[:, None]
                out = a * np.cos(t * np.pi / 2) + b * np.sin(t * np.pi / 2)
                if pos + frames >= start + length:
                    self._switch(consumed=pos + frames - start)
                else:
                    self.cur_pos = pos + frames
            else:
                out = a
                self.cur_pos = pos + frames
                if self.cur_pos >= self.cur_len:
                    if self.nxt is not None:
                        self._switch(consumed=0)
                    elif self.idx + 1 >= len(self.playlist):
                        self._finish()
                    # иначе ждём, пока _manager подготовит следующий

            # защита от выхода за ±1.0 (не регулировка громкости)
            outdata[:] = np.clip(out, -1.0, 1.0)


# ---------- Tkinter UI ----------

def fmt(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}"


def tk_ui():
    player = Player()
    player.start()

    root = tk.Tk()
    root.title("Crossfade Player")
    root.geometry("560x420")

    lb = tk.Listbox(root, height=10, activestyle="none")
    lb.pack(fill="both", expand=True, padx=8, pady=8)

    def add_files():
        paths = filedialog.askopenfilenames(
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac"),
                       ("All", "*.*")])
        for p in paths:
            player.playlist.append(p)
            lb.insert("end", os.path.basename(p))

    def play_selected(_=None):
        sel = lb.curselection()
        if sel:
            player.play_index(sel[0])
        elif player.playlist and player.idx < 0:
            player.play_index(0)

    lb.bind("<Double-Button-1>", play_selected)

    seek_var = tk.DoubleVar(value=0)
    dragging = {"on": False}
    seek = ttk.Scale(root, from_=0, to=1000, variable=seek_var)
    seek.pack(fill="x", padx=8)
    seek.bind("<ButtonPress-1>", lambda e: dragging.update(on=True))

    def seek_release(_):
        dragging["on"] = False
        if player.cur_len:
            player.seek(seek_var.get() / 1000 * player.length())
    seek.bind("<ButtonRelease-1>", seek_release)

    time_lbl = tk.Label(root, text="0:00 / 0:00")
    time_lbl.pack()

    bar = tk.Frame(root)
    bar.pack(pady=4)
    tk.Button(bar, text="+ Файлы", command=add_files).pack(side="left", padx=3)
    tk.Button(bar, text="⏮", width=3, command=player.prev).pack(side="left", padx=3)
    tk.Button(bar, text="▶ / ⏸", width=7,
              command=lambda: player.toggle_pause() if player.cur
              else play_selected()).pack(side="left", padx=3)
    tk.Button(bar, text="⏭", width=3, command=player.next).pack(side="left", padx=3)

    cf = tk.Frame(root)
    cf.pack(fill="x", padx=8, pady=6)
    tk.Label(cf, text="Кроссфейд:").pack(side="left")
    cf_text = tk.StringVar(value="8.0 с")

    def on_cf(v):
        player.crossfade = float(v)
        cf_text.set(f"{float(v):.1f} с")
    cf_scale = ttk.Scale(cf, from_=1, to=30, command=on_cf)
    cf_scale.set(8.0)
    cf_scale.pack(side="left", fill="x", expand=True, padx=6)
    tk.Label(cf, textvariable=cf_text, width=7).pack(side="left")

    status = tk.Label(root, text="", fg="#666")
    status.pack()

    def tick():
        length = player.length()
        pos = player.position()
        if length and not dragging["on"]:
            seek_var.set(min(1000, pos / length * 1000))
        time_lbl.config(text=f"{fmt(pos)} / {fmt(length)}")
        if 0 <= player.idx < len(player.playlist):
            lb.selection_clear(0, "end")
            lb.selection_set(player.idx)
        if player.fade:
            status.config(text="кроссфейд…")
        elif player.finished:
            status.config(text="конец плейлиста")
        elif player.paused:
            status.config(text="пауза")
        else:
            status.config(text="")
        root.after(200, tick)

    def on_close():
        player.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    tick()
    root.mainloop()


if __name__ == "__main__":
    tk_ui()
