#!/usr/bin/env python3
import json
import os
import queue
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, DISABLED, LEFT, NORMAL, RIGHT, StringVar, Tk, ttk


APP_NAME = "raspi-card-ingest"
CONFIG_PATH = Path.home() / ".config" / APP_NAME / "config.json"
DATA_DIR = Path.home() / ".local" / "share" / APP_NAME
HISTORY_PATH = DATA_DIR / "history.jsonl"
WINDOW_WIDTH = 480
WINDOW_HEIGHT = 320


PROGRESS_RE = re.compile(
    r"(?P<bytes>[\d,.]+[kKmMgGtTpP]?)\s+"
    r"(?P<pct>\d+)%\s+"
    r"(?P<speed>[\d,.]+[kKmMgGtTpP]?B/s)\s+"
    r"(?P<eta>\d+:\d+:\d+)"
)

STAT_PATTERNS = {
    "files": re.compile(r"Number of regular files transferred:\s+([\d,]+)"),
    "bytes": re.compile(r"Total transferred file size:\s+([\d,]+) bytes"),
    "sent": re.compile(r"Total bytes sent:\s+([\d,]+)"),
    "received": re.compile(r"Total bytes received:\s+([\d,]+)"),
}


@dataclass
class CopyStats:
    started: datetime
    current_file: str = "-"
    files: str = "-"
    bytes: str = "-"
    sent: str = "-"
    received: str = "-"
    output_lines: list[str] = field(default_factory=list)


def compact_path(path, max_chars=34):
    if len(path) <= max_chars:
        return path
    tail = path[-(max_chars - 1):]
    return "..." + tail


def compact_file(path, max_chars=34):
    name = Path(path).name or path
    if len(name) <= max_chars:
        return name
    return "..." + name[-(max_chars - 1):]


def human_bytes(value):
    try:
        size = int(str(value).replace(",", ""))
    except ValueError:
        return str(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024


def normalize_speed(value):
    text = str(value).strip().replace(",", ".")
    match = re.fullmatch(r"([\d.]+)([kKmMgGtTpP]?B/s)", text)
    if not match:
        return text
    amount = float(match.group(1))
    unit = match.group(2).upper()
    scale = {
        "B/S": 1,
        "KB/S": 1024,
        "MB/S": 1024**2,
        "GB/S": 1024**3,
        "TB/S": 1024**4,
        "PB/S": 1024**5,
    }.get(unit, 1)
    bytes_per_second = amount * scale
    if bytes_per_second >= 1024**3:
        return f"{bytes_per_second / 1024**3:.1f} GB/s"
    if bytes_per_second >= 1024**2:
        return f"{bytes_per_second / 1024**2:.1f} MB/s"
    if bytes_per_second >= 1024:
        return f"{bytes_per_second / 1024:.1f} KB/s"
    return f"{bytes_per_second:.0f} B/s"


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def load_config():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config nao encontrada: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)
    config.setdefault("scan_interval_seconds", 2)
    config.setdefault("auto_start_copy", True)
    config.setdefault("folder_template", "{date}/{card}")
    return config


def run_json(command):
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def flatten_devices(devices):
    for device in devices:
        yield device
        for child in device.get("children") or []:
            yield from flatten_devices([child])


def list_partitions():
    data = run_json(["lsblk", "-J", "-o", "NAME,PATH,TYPE,FSTYPE,UUID,LABEL,MOUNTPOINTS,SIZE"])
    volumes = []
    for device in flatten_devices(data.get("blockdevices", [])):
        if device.get("type") == "part":
            volumes.append(device)
        elif device.get("type") == "disk" and device.get("fstype") and (device.get("uuid") or device.get("label")):
            volumes.append(device)
    return volumes


def first_mountpoint(device):
    mountpoints = device.get("mountpoints") or []
    for point in mountpoints:
        if point:
            return point
    return None


def mount_name(device):
    return safe_folder_name(device.get("label") or device.get("uuid") or Path(device["path"]).name)


def mount_partition(device, card):
    path = device["path"]
    target = Path("/media") / os.environ.get("USER", "mobiker") / safe_folder_name(card["name"])
    result = subprocess.run(["mount", str(target)], capture_output=True, text=True)
    combined_output = (result.stderr + result.stdout).lower()
    if result.returncode != 0 and "already mounted" not in combined_output:
        raise RuntimeError("Falha ao montar cartao")
    time.sleep(0.5)
    for part in list_partitions():
        if part.get("path") == path:
            mountpoint = first_mountpoint(part)
            if mountpoint:
                return mountpoint
    return str(target)


def unmount_partition(device, card):
    mountpoint = first_mountpoint(device)
    target = mountpoint or str(Path("/media") / os.environ.get("USER", "mobiker") / safe_folder_name(card["name"]))
    result = subprocess.run(["umount", target], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("Falha ao ejetar")
    return "Cartao ejetado"


def safe_folder_name(value):
    value = value.strip().lower().replace(" ", "-")
    return re.sub(r"[^a-z0-9._-]+", "-", value).strip("-") or "card"


class IngestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Raspi Card Ingest")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+0+0")
        self.root.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.root.resizable(False, False)

        self.config = load_config()
        self.cards = self.config.get("cards", [])
        self.events = queue.Queue()
        self.active_uuids = set()
        self.copying = False
        self.ejecting = False
        self.selected_device = None

        self.status = StringVar(value="Aguardando cartao conhecido...")
        self.card_name = StringVar(value="-")
        self.eta_text = StringVar(value="-")
        self.destination = StringVar(value=self.config.get("destination_root", "-"))
        self.progress_text = StringVar(value="0%")
        self.speed_text = StringVar(value="-")
        self.current_file = StringVar(value="-")
        self.summary = StringVar(value="Aguardando cartao conhecido.")

        self.build_ui()
        self.root.after(200, self.process_events)
        threading.Thread(target=self.scan_loop, daemon=True).start()

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("DejaVu Sans", 7))
        style.configure("Title.TLabel", font=("DejaVu Sans", 14, "bold"))
        style.configure("Big.TLabel", font=("DejaVu Sans", 9))
        style.configure("Status.TLabel", font=("DejaVu Sans", 8))
        style.configure("Metric.TLabel", font=("DejaVu Sans", 11, "bold"))
        style.configure("Accent.TButton", font=("DejaVu Sans", 8, "bold"))

        main = ttk.Frame(self.root, padding=4)
        main.pack(fill=BOTH, expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x")
        ttk.Label(top, text="Card Ingest", style="Title.TLabel").pack(side=LEFT)
        ttk.Label(top, textvariable=self.status, style="Status.TLabel").pack(side=RIGHT)

        info = ttk.Frame(main, padding=(0, 4, 0, 2))
        info.pack(fill="x")
        self.add_info(info, "Origem", self.card_name, 0, 0)
        self.add_info(info, "Restante", self.eta_text, 0, 1)
        self.add_info(info, "Destino", self.destination, 1, 0, colspan=2)
        self.add_info(info, "Arquivo", self.current_file, 2, 0, colspan=2)

        progress_area = ttk.Frame(main, padding=(0, 3, 0, 3))
        progress_area.pack(fill="x")
        self.progress = ttk.Progressbar(progress_area, orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x", ipady=5)

        metrics = ttk.Frame(main)
        metrics.pack(fill="x")
        self.add_metric(metrics, "Progresso", self.progress_text, 0)
        self.add_metric(metrics, "Velocidade", self.speed_text, 1)

        buttons = ttk.Frame(main, padding=(0, 3, 0, 2))
        buttons.pack(fill="x")
        self.copy_button = ttk.Button(buttons, text="Copiar agora", style="Accent.TButton", command=self.start_selected_copy)
        self.copy_button.pack(side=LEFT, ipadx=5, ipady=2)
        self.eject_button = ttk.Button(buttons, text="Ejetar", command=self.eject_selected_card)
        self.eject_button.pack(side=LEFT, padx=6, ipadx=5, ipady=2)
        self.eject_button.configure(state=DISABLED)
        ttk.Button(buttons, text="Recarregar", command=self.reload_config).pack(side=LEFT, padx=6, ipadx=5, ipady=2)

        ttk.Label(main, textvariable=self.summary, style="Status.TLabel", wraplength=WINDOW_WIDTH - 18).pack(fill="x", pady=(1, 0))

    def add_info(self, parent, label, variable, row, col, colspan=1):
        frame = ttk.Frame(parent, padding=2)
        frame.grid(row=row, column=col, sticky="ew", columnspan=colspan)
        parent.columnconfigure(col, weight=1)
        ttk.Label(frame, text=label).pack(anchor="w")
        ttk.Label(frame, textvariable=variable, style="Big.TLabel", wraplength=WINDOW_WIDTH - 18).pack(anchor="w")

    def add_metric(self, parent, label, variable, col):
        frame = ttk.Frame(parent, padding=2)
        frame.grid(row=0, column=col, sticky="ew")
        parent.columnconfigure(col, weight=1)
        ttk.Label(frame, text=label).pack(anchor="w")
        ttk.Label(frame, textvariable=variable, style="Metric.TLabel").pack(anchor="w")

    def add_log(self, message):
        self.summary.set(message)

    def reload_config(self):
        self.config = load_config()
        self.cards = self.config.get("cards", [])
        self.destination.set(compact_path(self.config.get("destination_root", "-")))
        self.add_log("Configuracao recarregada")

    def scan_loop(self):
        while True:
            try:
                known = []
                for part in list_partitions():
                    match = self.match_card(part)
                    if match:
                        known.append(match)
                self.events.put(("devices", known))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            time.sleep(float(self.config.get("scan_interval_seconds", 2)))

    def match_card(self, part):
        part_uuid = (part.get("uuid") or "").upper()
        part_label = (part.get("label") or "").upper()
        for card in self.cards:
            card_uuid = (card.get("uuid") or "").upper()
            card_label = (card.get("label") or "").upper()
            if card_uuid and part_uuid == card_uuid:
                return (card_uuid, card, part)
            if card_label and part_label == card_label:
                return (f"LABEL:{card_label}", card, part)
        return None

    def process_events(self):
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "devices":
                    self.handle_devices(payload)
                elif event == "progress":
                    self.handle_progress(payload)
                elif event == "done":
                    self.handle_done(payload)
                elif event == "ejected":
                    self.handle_ejected(payload)
                elif event == "error":
                    self.status.set("Erro")
                    self.add_log(payload)
        except queue.Empty:
            pass
        self.root.after(200, self.process_events)

    def handle_devices(self, devices):
        if self.copying:
            return
        present = {key for key, _, _ in devices}
        self.active_uuids &= present
        if not devices:
            self.selected_device = None
            self.status.set("Aguardando cartao conhecido...")
            self.card_name.set("-")
            self.eta_text.set("-")
            self.speed_text.set("-")
            self.current_file.set("-")
            self.destination.set(compact_path(self.config.get("destination_root", "-")))
            self.summary.set("Aguardando cartao conhecido.")
            self.progress["value"] = 0
            self.progress_text.set("0%")
            self.eject_button.configure(state=DISABLED)
            return

        key, card, device = devices[0]
        self.selected_device = (key, card, device)
        self.card_name.set(card["name"])
        self.eta_text.set("-")
        self.status.set("Cartao detectado")
        self.eject_button.configure(state=NORMAL)

        if self.config.get("auto_start_copy", True) and key not in self.active_uuids:
            self.active_uuids.add(key)
            self.start_copy(key, card, device)

    def start_selected_copy(self):
        if self.selected_device and not self.copying:
            key, card, device = self.selected_device
            self.start_copy(key, card, device)

    def eject_selected_card(self):
        if self.selected_device and not self.copying and not self.ejecting:
            _, card, device = self.selected_device
            self.ejecting = True
            self.eject_button.configure(state=DISABLED)
            self.copy_button.configure(state=DISABLED)
            self.status.set("Ejetando cartao...")
            self.summary.set(f"Ejetando {card['name']}...")
            threading.Thread(target=self.eject_worker, args=(card, device), daemon=True).start()

    def eject_worker(self, card, device):
        try:
            message = unmount_partition(device, card)
            self.events.put(("ejected", {"ok": True, "message": f"{card['name']} ejetado", "detail": message}))
        except Exception as exc:
            self.events.put(("ejected", {"ok": False, "message": str(exc)}))

    def start_copy(self, key, card, device):
        self.copying = True
        self.copy_button.configure(state=DISABLED)
        self.eject_button.configure(state=DISABLED)
        threading.Thread(target=self.copy_worker, args=(key, card, device), daemon=True).start()

    def build_destination(self, card_name):
        root = Path(self.config["destination_root"])
        now = datetime.now()
        relative = self.config.get("folder_template", "{date}/{card}").format(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H%M%S"),
            card=safe_folder_name(card_name),
        )
        return root / relative

    def copy_worker(self, key, card, device):
        source = first_mountpoint(device)
        try:
            if not source:
                self.events.put(("progress", {"status": "Montando cartao..."}))
                source = mount_partition(device, card)

            destination_root = Path(self.config["destination_root"])
            if not destination_root.exists():
                raise RuntimeError(f"Destino nao montado: {destination_root}")

            destination = self.build_destination(card["name"])
            destination.mkdir(parents=True, exist_ok=True)
            stats = CopyStats(started=datetime.now())
            self.events.put(("progress", {
                "status": f"Copiando {card['name']}...",
                "destination": compact_path(str(destination)),
                "summary": "Preparando lista de arquivos...",
            }))

            command = [
                "rsync",
                "-a",
                "--ignore-existing",
                "--info=progress2",
                "--out-format=%n",
                "--stats",
                f"{source.rstrip('/')}/",
                f"{str(destination).rstrip('/')}/",
            ]
            started = datetime.now()
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            last_line = ""
            buffer = ""
            assert proc.stdout is not None
            while True:
                char = proc.stdout.read(1)
                if char == "" and proc.poll() is not None:
                    break
                if not char:
                    continue
                if char in ("\r", "\n"):
                    last_line = buffer.strip()
                    buffer = ""
                    self.consume_rsync_line(last_line, stats)
                else:
                    buffer += char
            if buffer.strip():
                self.consume_rsync_line(buffer.strip(), stats)
            return_code = proc.wait()
            if return_code != 0:
                raise RuntimeError(last_line or f"rsync retornou {return_code}")

            finished = datetime.now()
            self.write_history({
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "key": key,
                "uuid": card.get("uuid", ""),
                "label": card.get("label", ""),
                "card": card["name"],
                "source": source,
                "destination": str(destination),
                "command": " ".join(shlex.quote(x) for x in command),
            })
            elapsed = format_duration((finished - started).total_seconds())
            transferred = human_bytes(stats.bytes) if stats.bytes != "-" else "-"
            self.events.put(("done", {
                "ok": True,
                "message": f"Copia concluida: {card['name']}",
                "summary": f"Concluido em {elapsed}. Arquivos: {stats.files}. Transferido: {transferred}.",
            }))
        except Exception as exc:
            self.events.put(("done", {"ok": False, "message": str(exc)}))

    def consume_rsync_line(self, line, stats):
        if not line:
            return
        stats.output_lines.append(line)
        match = PROGRESS_RE.search(line)
        if match:
            payload = match.groupdict()
            payload["file"] = stats.current_file
            self.events.put(("progress", payload))
            return
        for key, pattern in STAT_PATTERNS.items():
            stat_match = pattern.search(line)
            if stat_match:
                setattr(stats, key, stat_match.group(1))
                return
        if not line.startswith(("Number of", "Total ", "Literal ", "Matched ", "File list", "sent ")):
            stats.current_file = line
            self.events.put(("progress", {"file": compact_file(line), "summary": f"Copiando {compact_file(line)}"}))

    def handle_progress(self, payload):
        if "status" in payload:
            self.status.set(payload["status"])
        if "destination" in payload:
            self.destination.set(payload["destination"])
        if "summary" in payload:
            self.summary.set(payload["summary"])
        if "file" in payload:
            self.current_file.set(compact_file(payload["file"]))
        if "pct" in payload:
            pct = int(payload["pct"])
            self.progress["value"] = pct
            self.progress_text.set(f"{pct}%")
            self.speed_text.set(normalize_speed(payload.get("speed", "-")))
            self.eta_text.set(payload.get("eta", "-"))

    def handle_done(self, payload):
        self.copying = False
        self.copy_button.configure(state=NORMAL)
        self.status.set("Concluido" if payload["ok"] else "Erro")
        if payload["ok"]:
            self.progress["value"] = 100
            self.progress_text.set("100%")
        if payload.get("summary"):
            self.summary.set(f"{payload['message']}. {payload['summary']}")
        else:
            self.add_log(payload["message"])
        if self.selected_device:
            self.eject_button.configure(state=NORMAL)

    def handle_ejected(self, payload):
        self.ejecting = False
        self.copy_button.configure(state=NORMAL)
        if payload["ok"]:
            self.selected_device = None
            self.status.set("Cartao ejetado")
            self.card_name.set("-")
            self.current_file.set("-")
            self.eta_text.set("-")
            self.summary.set(payload.get("detail") or payload["message"])
        else:
            self.status.set("Erro ao ejetar")
            self.summary.set(payload["message"])
            if self.selected_device:
                self.eject_button.configure(state=NORMAL)

    def write_history(self, entry):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")


def main():
    if not CONFIG_PATH.exists():
        print(f"Config nao encontrada: {CONFIG_PATH}")
        print("Copie config.example.json para esse caminho e edite os UUIDs.")
        raise SystemExit(1)
    root = Tk()
    IngestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
