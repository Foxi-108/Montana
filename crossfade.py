"""
Плавный, незаметный кроссфейд двух треков через FFmpeg.

Громкость треков НЕ изменяется (никаких volume/dB-компенсаций).
Единственное "умное" действие — срезаются тишина в конце трека A и в начале
трека B, чтобы переход происходил по реальному звуку, а не по пустоте.
Само затухание/нарастание делает acrossfade с кривой equal-power (qsin).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SAMPLE_RATE = 44100
SILENCE_DB = -50          # всё тише этого порога считаем тишиной
SILENCE_MIN_LEN = 0.3     # минимальная длина тишины, сек
ANALYZE_WINDOW = 20.0     # сколько секунд с краёв трека анализируем


# ---------- FFmpeg / FFprobe ----------

def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise RuntimeError(f"'{tool}' не найден в PATH. Установите FFmpeg.")
    return path


def probe_duration(path: str) -> float:
    out = subprocess.check_output(
        [_require("ffprobe"), "-v", "error", "-show_entries",
         "format=duration", "-of", "json", path],
        text=True,
    )
    return float(json.loads(out)["format"]["duration"])


# ---------- Поиск тишины по краям ----------

def _silence_events(path: str, start: float, length: float):
    """Список ('start'|'end', время_в_файле) от silencedetect на окне файла."""
    cmd = [
        _require("ffmpeg"), "-hide_banner", "-nostats",
        "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
        "-i", path, "-vn",
        "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_LEN}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace")
    found = re.findall(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)",
                       proc.stderr)
    return [(kind, float(val) + start) for kind, val in found]


def find_audio_start(path: str) -> float:
    """Где в файле заканчивается вступительная тишина (0, если её нет)."""
    ev = _silence_events(path, 0.0, ANALYZE_WINDOW)
    if len(ev) >= 2 and ev[0][0] == "start" and ev[0][1] <= 0.05 \
            and ev[1][0] == "end":
        return max(0.0, ev[1][1])
    return 0.0


def find_audio_end(path: str, duration: float) -> float:
    """Где в файле начинается хвостовая тишина (duration, если её нет)."""
    win_start = max(0.0, duration - ANALYZE_WINDOW)
    ev = _silence_events(path, win_start, duration - win_start)
    starts = [i for i, (kind, _) in enumerate(ev) if kind == "start"]
    if not starts:
        return duration
    i = starts[-1]
    # если после последнего start тишина закончилась до конца файла —
    # это тишина посреди трека, а не хвост
    if any(kind == "end" and t < duration - 0.15 for kind, t in ev[i + 1:]):
        return duration
    return min(duration, max(ev[i][1], win_start))


# ---------- Кроссфейд ----------

def make_crossfade(a_path: str,
                   b_path: str,
                   out_path: str,
                   crossfade: float = 8.0,
                   trim_silence: bool = True,
                   sample_rate: int = SAMPLE_RATE,
                   bitrate: str = "320k") -> dict:
    """
    A плавно затухает, B одновременно плавно нарастает.
    Итоговая длина = len(A) + len(B) - crossfade.
    Громкость исходных треков не меняется.
    """
    ffmpeg = _require("ffmpeg")

    dur_a = probe_duration(a_path)
    dur_b = probe_duration(b_path)

    end_a, start_b = dur_a, 0.0
    if trim_silence:
        end_a = find_audio_end(a_path, dur_a)
        start_b = find_audio_start(b_path)
        # защита от «трек целиком тишина»
        if end_a < 1.0:
            end_a = dur_a
        if dur_b - start_b < 1.0:
            start_b = 0.0

    len_a = end_a
    len_b = dur_b - start_b

    # кроссфейд не может быть длиннее половины любого из треков
    crossfade = min(crossfade, len_a / 2, len_b / 2)
    if crossfade < 0.1:
        raise ValueError("Треки слишком короткие для кроссфейда.")

    # Приводим оба входа к одному формату — acrossfade этого требует.
    norm = (f"aresample={sample_rate},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo")
    filter_complex = ";".join([
        f"[0:a]atrim=end={end_a:.3f},asetpts=PTS-STARTPTS,{norm}[a]",
        f"[1:a]atrim=start={start_b:.3f},asetpts=PTS-STARTPTS,{norm}[b]",
        # qsin + qsin = equal-power: суммарная энергия в переходе ровная
        f"[a][b]acrossfade=d={crossfade:.3f}:c1=qsin:c2=qsin[out]",
    ])

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", a_path, "-i", b_path,
           "-filter_complex", filter_complex,
           "-map", "[out]", "-vn"]
    ext = os.path.splitext(out_path)[1].lower()
    if ext in (".mp3", ".m4a", ".aac", ".ogg"):
        cmd += ["-b:a", bitrate]
    cmd.append(out_path)

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg завершился с ошибкой:\n{proc.stderr}")

    return {
        "dur_a": round(dur_a, 2),
        "dur_b": round(dur_b, 2),
        "a_ends_at": round(end_a, 2),
        "b_starts_at": round(start_b, 2),
        "crossfade": round(crossfade, 2),
        "out": out_path,
    }


# ---------- Консольный UI ----------

def console_ui():
    print("=== Crossfade (консоль) ===")
    a = input("Трек A (затухающий), путь: ").strip().strip('"')
    b = input("Трек B (нарастающий), путь: ").strip().strip('"')
    try:
        cf = float(input("Длина кроссфейда, сек [8]: ").strip() or 8)
    except ValueError:
        cf = 8.0
    out = input("Выходной файл [crossfade_out.mp3]: ").strip() \
        or "crossfade_out.mp3"

    for p in (a, b):
        if not os.path.isfile(p):
            print(f"Файл не найден: {p}")
            return

    print("Анализ и рендер...")
    meta = make_crossfade(a, b, out, crossfade=cf)
    print("\nГотово:")
    for k, v in meta.items():
        print(f"  {k}: {v}")


# ---------- Tkinter UI ----------

def tk_ui():
    root = tk.Tk()
    root.title("Crossfade")
    root.geometry("620x340")

    files = {"a": "", "b": ""}
    names = {"a": tk.StringVar(value="(не выбран)"),
             "b": tk.StringVar(value="(не выбран)")}

    def pick(key, label):
        p = filedialog.askopenfilename(
            title=f"Выберите трек {label}",
            filetypes=[("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac"),
                       ("All", "*.*")],
        )
        if p:
            files[key] = p
            names[key].set(os.path.basename(p))

    for key, label, row in (("a", "A — затухает", 0),
                            ("b", "B — нарастает", 1)):
        tk.Label(root, text=f"Трек {label}:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=8, pady=4)
        tk.Entry(root, textvariable=names[key], width=50,
                 state="readonly").grid(row=row, column=1, padx=4)
        tk.Button(root, text="Обзор…",
                  command=lambda k=key, l=label: pick(k, l)).grid(
            row=row, column=2, padx=4)

    tk.Label(root, text="Кроссфейд (сек):").grid(
        row=2, column=0, sticky="w", padx=8)
    cf_var = tk.DoubleVar(value=8.0)
    cf_text = tk.StringVar(value="8.0")
    ttk.Scale(root, from_=1, to=30, variable=cf_var, orient="horizontal",
              command=lambda v: cf_text.set(f"{float(v):.1f}")).grid(
        row=2, column=1, sticky="ew", padx=4)
    tk.Label(root, textvariable=cf_text, width=5).grid(row=2, column=2)

    trim_var = tk.BooleanVar(value=True)
    tk.Checkbutton(root, text="Срезать тишину по краям треков",
                   variable=trim_var).grid(row=3, column=0, columnspan=3,
                                           sticky="w", padx=8, pady=4)

    log = tk.Text(root, height=8, width=78)
    log.grid(row=4, column=0, columnspan=3, padx=8, pady=4, sticky="ew")

    btn = tk.Button(root, text="Сделать кроссфейд", height=2)
    btn.grid(row=5, column=0, columnspan=3, padx=8, pady=8, sticky="ew")

    def finish(text, error=False):
        log.insert("end", text)
        btn.config(state="normal")
        if error:
            messagebox.showerror("Ошибка", text)

    def run():
        if not files["a"] or not files["b"]:
            messagebox.showwarning("Нет файлов", "Выберите оба трека.")
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".mp3",
            filetypes=[("MP3", "*.mp3"), ("WAV", "*.wav")],
            initialfile="crossfade_out.mp3",
        )
        if not out:
            return
        log.delete("1.0", "end")
        log.insert("end", "Анализ и рендер...\n")
        btn.config(state="disabled")
        cf, trim = cf_var.get(), trim_var.get()

        def worker():
            try:
                meta = make_crossfade(files["a"], files["b"], out,
                                      crossfade=cf, trim_silence=trim)
                lines = "".join(f"  {k}: {v}\n" for k, v in meta.items())
                root.after(0, finish, lines + "\nГотово.\n")
            except Exception as e:
                root.after(0, finish, f"Ошибка: {e}\n", True)

        threading.Thread(target=worker, daemon=True).start()

    btn.config(command=run)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        console_ui()
    else:
        tk_ui()