import os
import re
import sys
import shutil
import zipfile
import json
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from mailer import send_clinic_archive


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def load_config() -> dict:
    config_path = get_base_dir() / "config.json"
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict):
    config_path = get_base_dir() / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def detect_clinic(filename: str) -> str:
    clean_name = filename.strip().lower()
    first_token = clean_name.split()[0] if clean_name.split() else ""
    first_token = re.sub(r"[^\w]", "", first_token)

    # 1. По стандарту: 17дгп, 10гп, црб
    child_prefix = re.match(r"^(\d+)(?:дгп|дп)$", first_token)
    if child_prefix:
        num = int(child_prefix.group(1))
        if 1 <= num <= 25 and num != 24:
            return f"Детская поликлиника №{num}"

    adult_prefix = re.match(r"^(\d+)(?:гп|п)$", first_token)
    if adult_prefix:
        num = int(adult_prefix.group(1))
        if 1 <= num <= 42:
            return f"Поликлиника №{num}"

    if "црб" in first_token or first_token.startswith("црб"):
        return "ЦРБ и Районные больницы"

    # 2. Резервный поиск при опечатках врачей
    child_fallback = re.search(r"(?:^|\D)(\d+)\s*(?:дгп|дп)|(?:дгп|дп)\s*(\d+)", clean_name)
    if child_fallback:
        num = int(child_fallback.group(1) or child_fallback.group(2))
        if 1 <= num <= 25 and num != 24:
            return f"Детская поликлиника №{num}"

    adult_fallback = re.search(r"(?:^|\D)(\d+)\s*(?:гп|п)|(?:гп|п-ка|пол)\s*(\d+)", clean_name)
    if adult_fallback:
        num = int(adult_fallback.group(1) or adult_fallback.group(2))
        if 1 <= num <= 42:
            return f"Поликлиника №{num}"

    if "црб" in clean_name or "район" in clean_name:
        return "ЦРБ и Районные больницы"

    return "Нераспознанные"


class MedDispatchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MedDispatch — Маршрутизация эпикризов (УЗ «ГКБСМП»)")
        self.geometry("820x640")
        self.minsize(700, 500)

        self.config_data = load_config()
        self.setup_ui()

    def setup_ui(self):
        # Стиль
        style = ttk.Style(self)
        style.theme_use("clam")

        # Заголовок
        header_frame = ttk.Frame(self, padding="10 10 10 5")
        header_frame.pack(fill=tk.X)
        title_lbl = ttk.Label(header_frame, text="🏥 MedDispatch: Сортировка, Архивация и Отправка", font=("Segoe UI", 12, "bold"))
        title_lbl.pack(side=tk.LEFT)

        # Панель путей
        path_frame = ttk.LabelFrame(self, text="Папки", padding="10")
        path_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(path_frame, text="Входящие эпикризы:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.src_entry = ttk.Entry(path_frame, width=60)
        self.src_entry.grid(row=0, column=1, padx=5, pady=2, sticky=tk.EW)
        self.src_entry.insert(0, self.config_data.get("source_folder", "./Входящие_Эпикризы"))
        ttk.Button(path_frame, text="Обзор...", command=self.browse_src).grid(row=0, column=2, pady=2)

        ttk.Label(path_frame, text="Папка с архивами:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.out_entry = ttk.Entry(path_frame, width=60)
        self.out_entry.grid(row=1, column=1, padx=5, pady=2, sticky=tk.EW)
        self.out_entry.insert(0, self.config_data.get("output_folder", "./Готовые_Архивы"))
        ttk.Button(path_frame, text="Обзор...", command=self.browse_out).grid(row=1, column=2, pady=2)

        path_frame.columnconfigure(1, weight=1)

        # Опции
        opts_frame = ttk.Frame(self, padding="10 5")
        opts_frame.pack(fill=tk.X, padx=10)

        self.dry_run_var = tk.BooleanVar(value=self.config_data.get("dry_run", False))
        dry_chk = ttk.Checkbutton(opts_frame, text="Тестовый режим (Dry Run) — без реальной отправки писем", variable=self.dry_run_var)
        dry_chk.pack(side=tk.LEFT)

        # Кнопки управления
        btn_frame = ttk.Frame(self, padding="5 10")
        btn_frame.pack(fill=tk.X, padx=10)

        self.run_btn = tk.Button(btn_frame, text="▶ Запустить обработку", bg="#007acc", fg="white", font=("Segoe UI", 10, "bold"), padx=15, pady=5, command=self.start_processing_thread)
        self.run_btn.pack(side=tk.LEFT, padx=5)

        self.open_out_btn = ttk.Button(btn_frame, text="📂 Открыть папку с архивами", command=self.open_output_folder)
        self.open_out_btn.pack(side=tk.LEFT, padx=5)

        # Окно лога
        log_frame = ttk.LabelFrame(self, text="Журнал операций", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_config("info", foreground="#569cd6")
        self.log_text.tag_config("success", foreground="#4ec9b0")
        self.log_text.tag_config("warn", foreground="#ce9178")
        self.log_text.tag_config("error", foreground="#f44747")
        self.log_text.tag_config("header", foreground="#dcdcaa", font=("Consolas", 9, "bold"))

        # Статус-бар внизу
        self.status_lbl = ttk.Label(self, text="Готов к работе", relief=tk.SUNKEN, padding="3 5")
        self.status_lbl.pack(side=tk.BOTTOM, fill=tk.X)

    def log(self, text: str, tag: str = "info"):
        self.log_text.insert(tk.END, text + "\n", tag)
        self.log_text.see(tk.END)

    def browse_src(self):
        folder = filedialog.askdirectory(title="Выберите папку с входящими эпикризами")
        if folder:
            self.src_entry.delete(0, tk.END)
            self.src_entry.insert(0, folder)

    def browse_out(self):
        folder = filedialog.askdirectory(title="Выберите папку для сохранения архивов")
        if folder:
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, folder)

    def open_output_folder(self):
        base_dir = get_base_dir()
        out_path = base_dir / self.out_entry.get().strip()
        out_path.mkdir(parents=True, exist_ok=True)
        os.startfile(out_path.resolve())

    def start_processing_thread(self):
        self.run_btn.config(state=tk.DISABLED)
        self.status_lbl.config(text="Идет обработка файлов...")
        threading.Thread(target=self.run_pipeline, daemon=True).start()

    def run_pipeline(self):
        try:
            base_dir = get_base_dir()
            cfg = self.config_data.copy()
            cfg["source_folder"] = self.src_entry.get().strip()
            cfg["output_folder"] = self.out_entry.get().strip()
            cfg["dry_run"] = self.dry_run_var.get()
            save_config(cfg)

            source_dir = (base_dir / cfg["source_folder"]).resolve()
            output_dir = (base_dir / cfg["output_folder"]).resolve()
            emails_map = cfg.get("clinics_emails", {})
            today_str = datetime.now().strftime("%Y-%m-%d")

            self.log_text.delete(1.0, tk.END)
            self.log(f"[{datetime.now().strftime('%H:%M:%S')}] Начало процесса обработки...", "header")

            if not source_dir.exists():
                self.log(f"❌ Исходная папка не найдена: {source_dir}", "error")
                source_dir.mkdir(parents=True, exist_ok=True)
                self.log(f"📁 Папка создана. Поместите туда файлы и нажмите запуск.", "info")
                return

            output_dir.mkdir(parents=True, exist_ok=True)
            unmatched_dir = output_dir / "Требуют_Проверки"
            unmatched_dir.mkdir(parents=True, exist_ok=True)

            allowed_exts = {".rtf", ".pdf", ".docx", ".doc", ".txt"}
            files = [f for f in source_dir.iterdir() if f.is_file() and f.suffix.lower() in allowed_exts]

            if not files:
                self.log("⚠️ В папке нет подходящих файлов для обработки.", "warn")
                return

            self.log(f"🔍 Найдено файлов: {len(files)}", "info")
            self.log("-" * 60, "info")

            temp_dir = base_dir / "temp_sorting"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)

            unmatched_count = 0
            stats = {}

            # Сортировка
            for file_path in files:
                clinic_name = detect_clinic(file_path.name)
                
                # Если файл не распознан — копируем прямо в Требуют_Проверки (без архивации)
                if clinic_name == "Нераспознанные":
                    shutil.copy2(file_path, unmatched_dir / file_path.name)
                    unmatched_count += 1
                    self.log(f"[❓ ПРОВЕРКА] {file_path.name} ➡️ скопирован в 'Требуют_Проверки'", "warn")
                    continue

                target_folder = temp_dir / clinic_name
                target_folder.mkdir(exist_ok=True)
                shutil.copy2(file_path, target_folder / file_path.name)
                stats[clinic_name] = stats.get(clinic_name, 0) + 1

                route = "📧 EMAIL" if clinic_name in emails_map else "📋 СМДО"
                self.log(f"[{route:<8}] {file_path.name} ➡️ {clinic_name}", "info")

            self.log("-" * 60, "info")
            self.log("📦 Формирование ZIP-архивов и рассылка...", "header")

            # Архивирование распознанных поликлиник
            for clinic_folder in temp_dir.iterdir():
                if not clinic_folder.is_dir():
                    continue

                clinic_name = clinic_folder.name
                clean_name = clinic_name.replace(" ", "_")

                if clinic_name in emails_map:
                    subfolder = output_dir / "Для_Отправки_Email"
                else:
                    subfolder = output_dir / "Для_СМДО"

                subfolder.mkdir(parents=True, exist_ok=True)
                archive_name = subfolder / f"{clean_name}_{today_str}.zip"

                with zipfile.ZipFile(archive_name, "w", zipfile.ZIP_DEFLATED) as zipf:
                    for doc in clinic_folder.iterdir():
                        zipf.write(doc, arcname=doc.name)

                self.log(f"✅ Создан архив: {archive_name.name} ({stats[clinic_name]} файлов)", "success")

                # Отправка email
                if clinic_name in emails_map:
                    recipient_email = emails_map[clinic_name]
                    ok = send_clinic_archive(clinic_name, recipient_email, archive_name, today_str, cfg)
                    if ok:
                        self.log(f"  ✉️ Успешно: [{clinic_name}] ➡️ {recipient_email}", "success")
                    else:
                        self.log(f"  ❌ Ошибка отправки: [{clinic_name}] ➡️ {recipient_email}", "error")
                else:
                    self.log(f"  📋 Архив сохранен в 'Для_СМДО'", "info")

            shutil.rmtree(temp_dir)
            self.log("=" * 60, "header")
            self.log(f"🎉 Обработка завершена! Нераспознанных файлов: {unmatched_count}", "success")

        except Exception as e:
            self.log(f"❌ Критическая ошибка: {e}", "error")
        finally:
            self.run_btn.config(state=tk.NORMAL)
            self.status_lbl.config(text="Готов к работе")


if __name__ == "__main__":
    app = MedDispatchApp()
    app.mainloop()