import os
import re
import sys
import shutil
import zipfile
import json
import threading
from datetime import datetime
from pathlib import Path
import customtkinter as ctk
from tkinter import filedialog, messagebox

from mailer import send_clinic_archive

# Оформление интерфейса
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def load_config() -> dict:
    config_path = get_base_dir() / "config.json"
    if not config_path.exists():
        return {
            "source_folder": "./Входящие_Эпикризы",
            "output_folder": "./Готовые_Архивы",
            "dry_run": False,
            "smtp_settings": {
                "server": "ms7.g-cloud.by",
                "port": 465,
                "use_ssl": True,
                "use_tls": False,
                "sender_email": "info@bsmp.by",
                "sender_password": "",
                "subject_template": "Выписные эпикризы за {date} — УЗ «ГКБСМП»",
                "body_template": "Здравствуйте!\n\nУЗ «Городская клиническая больница скорой медицинской помощи» направляет архив с выписными эпикризами за {date}.\n\nПолучатель: {clinic_name}\n\nС уважением,\nАдминистрация УЗ «ГКБСМП»"
            },
            "clinics_emails": {
                "Поликлиника №12": "12gp@example.by",
                "Поликлиника №25": "25gp@example.by",
                "Поликлиника №29": "29gp@example.by",
                "Поликлиника №31": "31gp@example.by",
                "Поликлиника №42": "42gp@example.by",
                "Детская поликлиника №17": "17dgp@example.by"
            }
        }
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


class MedDispatchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MedDispatch — УЗ «ГКБСМП»")
        self.geometry("920x740")
        self.minsize(840, 640)

        self.config_data = load_config()
        self.clinic_rows = []  # Список для хранения виджетов настроек поликлиник

        self.setup_ui()

    def setup_ui(self):
        # 1. Шапка приложения
        header_frame = ctk.CTkFrame(self, corner_radius=10)
        header_frame.pack(fill="x", padx=15, pady=(15, 5))

        title_lbl = ctk.CTkLabel(header_frame, text="🏥 MedDispatch", font=ctk.CTkFont(size=22, weight="bold"))
        title_lbl.pack(side="left", padx=(15, 10), pady=10)

        sub_lbl = ctk.CTkLabel(
            header_frame,
            text="Маршрутизация эпикризов • УЗ «ГКБСМП»",
            text_color=("#0284c7", "#38bdf8"),
            font=ctk.CTkFont(size=14, weight="bold")
        )
        sub_lbl.pack(side="left", padx=5, pady=10)

        # 2. Вкладки (Tabs)
        self.tabview = ctk.CTkTabview(self, corner_radius=10)
        self.tabview.pack(fill="both", expand=True, padx=15, pady=10)

        self.tab_main = self.tabview.add("🚀 Маршрутизация")
        self.tab_settings = self.tabview.add("⚙️ Настройки и Поликлиники")

        self.setup_main_tab()
        self.setup_settings_tab()

    # ==========================================
    # ВКЛАДКА 1: ОСНОВНОЙ РАБОЧИЙ ЭКРАН
    # ==========================================
    def setup_main_tab(self):
        # Карточка путей
        path_card = ctk.CTkFrame(self.tab_main, corner_radius=10)
        path_card.pack(fill="x", padx=10, pady=(5, 10))

        ctk.CTkLabel(path_card, text="📁 Рабочие директории", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 5))

        ctk.CTkLabel(path_card, text="Входящие файлы:").grid(row=1, column=0, sticky="w", padx=12, pady=5)
        self.src_entry = ctk.CTkEntry(path_card, width=480)
        self.src_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        self.src_entry.insert(0, self.config_data.get("source_folder", "./Входящие_Эпикризы"))
        ctk.CTkButton(path_card, text="Обзор...", width=80, command=self.browse_src).grid(row=1, column=2, padx=12, pady=5)

        ctk.CTkLabel(path_card, text="Куда сохранять:").grid(row=2, column=0, sticky="w", padx=12, pady=(5, 10))
        self.out_entry = ctk.CTkEntry(path_card, width=480)
        self.out_entry.grid(row=2, column=1, padx=5, pady=(5, 10), sticky="ew")
        self.out_entry.insert(0, self.config_data.get("output_folder", "./Готовые_Архивы"))
        ctk.CTkButton(path_card, text="Обзор...", width=80, command=self.browse_out).grid(row=2, column=2, padx=12, pady=(5, 10))

        path_card.columnconfigure(1, weight=1)

        # Панель управления
        action_frame = ctk.CTkFrame(self.tab_main, fg_color="transparent")
        action_frame.pack(fill="x", padx=10, pady=5)

        self.dry_run_var = ctk.BooleanVar(value=self.config_data.get("dry_run", False))
        self.dry_chk = ctk.CTkSwitch(action_frame, text="Тестовый режим (Dry Run)", font=ctk.CTkFont(size=13), variable=self.dry_run_var)
        self.dry_chk.pack(side="left", padx=5)

        self.run_btn = ctk.CTkButton(
            action_frame,
            text="▶  Запустить обработку",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=("#0284c7", "#0284c7"),
            hover_color=("#0369a1", "#0369a1"),
            height=38,
            corner_radius=8,
            command=self.start_processing_thread
        )
        self.run_btn.pack(side="right", padx=5)

        self.open_out_btn = ctk.CTkButton(
            action_frame,
            text="📂 Открыть архивы",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=("#059669", "#10b981"),
            hover_color=("#047857", "#059669"),
            text_color="white",
            height=38,
            corner_radius=8,
            command=self.open_output_folder
        )
        self.open_out_btn.pack(side="right", padx=5)

        # Журнал логов
        log_frame = ctk.CTkFrame(self.tab_main, corner_radius=10)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.log_text = ctk.CTkTextbox(log_frame, font=("Consolas", 12), activate_scrollbars=True, fg_color=("#f8fafc", "#0f172a"))
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)

        self.log_text.tag_config("info", foreground="#38bdf8")
        self.log_text.tag_config("success", foreground="#34d399")
        self.log_text.tag_config("warn", foreground="#fbbf24")
        self.log_text.tag_config("error", foreground="#f87171")
        self.log_text.tag_config("header", foreground="#facc15")

        self.log("Готов к работе. Нажмите «Запустить обработку».", "info")

    # ==========================================
    # ВКЛАДКА 2: РЕДАКТИРОВАНИЕ ПОЛИКЛИНИК И SMTP
    # ==========================================
    def setup_settings_tab(self):
        # 1. Секция поликлиник
        clinic_section = ctk.CTkFrame(self.tab_settings, corner_radius=10)
        clinic_section.pack(fill="both", expand=True, padx=10, pady=5)

        head_bar = ctk.CTkFrame(clinic_section, fg_color="transparent")
        head_bar.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(head_bar, text="📧 Поликлиники для отправки по Email", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(head_bar, text="➕ Добавить поликлинику", width=160, fg_color="#0284c7", command=lambda: self.add_clinic_row_ui("", "")).pack(side="right")

        # Прокручиваемый список поликлиник
        self.clinics_scroll = ctk.CTkScrollableFrame(clinic_section, height=220, corner_radius=8)
        self.clinics_scroll.pack(fill="both", expand=True, padx=10, pady=5)

        # Заполняем текущими поликлиниками из config.json
        emails_map = self.config_data.get("clinics_emails", {})
        for clinic, email in emails_map.items():
            self.add_clinic_row_ui(clinic, email)

        # 2. Секция настроек SMTP
        smtp_section = ctk.CTkFrame(self.tab_settings, corner_radius=10)
        smtp_section.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(smtp_section, text="⚙️ Настройки почтового сервера (G-Cloud / SMTP)", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 6))

        smtp_cfg = self.config_data.get("smtp_settings", {})

        ctk.CTkLabel(smtp_section, text="Сервер:").grid(row=1, column=0, sticky="w", padx=12, pady=3)
        self.smtp_server_entry = ctk.CTkEntry(smtp_section, width=200)
        self.smtp_server_entry.grid(row=1, column=1, padx=5, pady=3, sticky="w")
        self.smtp_server_entry.insert(0, smtp_cfg.get("server", "ms7.g-cloud.by"))

        ctk.CTkLabel(smtp_section, text="Порт:").grid(row=1, column=2, sticky="w", padx=12, pady=3)
        self.smtp_port_entry = ctk.CTkEntry(smtp_section, width=80)
        self.smtp_port_entry.grid(row=1, column=3, padx=5, pady=3, sticky="w")
        self.smtp_port_entry.insert(0, str(smtp_cfg.get("port", 465)))

        ctk.CTkLabel(smtp_section, text="Email больницы:").grid(row=2, column=0, sticky="w", padx=12, pady=3)
        self.smtp_sender_entry = ctk.CTkEntry(smtp_section, width=200)
        self.smtp_sender_entry.grid(row=2, column=1, padx=5, pady=3, sticky="w")
        self.smtp_sender_entry.insert(0, smtp_cfg.get("sender_email", "info@bsmp.by"))

        ctk.CTkLabel(smtp_section, text="Пароль почты:").grid(row=2, column=2, sticky="w", padx=12, pady=3)
        self.smtp_pass_entry = ctk.CTkEntry(smtp_section, width=160, show="*")
        self.smtp_pass_entry.grid(row=2, column=3, padx=5, pady=3, sticky="w")
        self.smtp_pass_entry.insert(0, smtp_cfg.get("sender_password", ""))

        # 3. Кнопка сохранения всех настроек
        save_bar = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        save_bar.pack(fill="x", padx=10, pady=(5, 10))

        self.save_btn = ctk.CTkButton(
            save_bar,
            text="💾  Сохранить все настройки в config.json",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=("#059669", "#10b981"),
            hover_color=("#047857", "#059669"),
            height=40,
            command=self.save_all_settings_from_ui
        )
        self.save_btn.pack(fill="x")

    def add_clinic_row_ui(self, clinic_name: str, email: str):
        row_frame = ctk.CTkFrame(self.clinics_scroll, fg_color="transparent")
        row_frame.pack(fill="x", pady=3)

        name_entry = ctk.CTkEntry(row_frame, width=220, placeholder_text="Название (напр. Поликлиника №10)")
        name_entry.pack(side="left", padx=(0, 5))
        name_entry.insert(0, clinic_name)

        email_entry = ctk.CTkEntry(row_frame, width=320, placeholder_text="Email (напр. 10gp@example.by)")
        email_entry.pack(side="left", fill="x", expand=True, padx=5)
        email_entry.insert(0, email)

        del_btn = ctk.CTkButton(
            row_frame, text="✕", width=32, height=28, fg_color="#ef4444", hover_color="#dc2626",
            command=lambda: self.remove_clinic_row_ui(row_frame, (name_entry, email_entry, row_frame))
        )
        del_btn.pack(side="right", padx=(5, 0))

        self.clinic_rows.append((name_entry, email_entry, row_frame))

    def remove_clinic_row_ui(self, row_frame, item_tuple):
        if item_tuple in self.clinic_rows:
            self.clinic_rows.remove(item_tuple)
        row_frame.destroy()

    def save_all_settings_from_ui(self):
        try:
            # 1. Собираем список поликлиник
            new_emails_map = {}
            for name_entry, email_entry, _ in self.clinic_rows:
                c_name = name_entry.get().strip()
                c_email = email_entry.get().strip()
                if c_name and c_email:
                    new_emails_map[c_name] = c_email

            # 2. Собираем SMTP параметры
            smtp_cfg = self.config_data.get("smtp_settings", {})
            smtp_cfg["server"] = self.smtp_server_entry.get().strip()
            smtp_cfg["port"] = int(self.smtp_port_entry.get().strip())
            smtp_cfg["sender_email"] = self.smtp_sender_entry.get().strip()
            smtp_cfg["sender_password"] = self.smtp_pass_entry.get().strip()

            # 3. Обновляем config_data
            self.config_data["clinics_emails"] = new_emails_map
            self.config_data["smtp_settings"] = smtp_cfg
            self.config_data["source_folder"] = self.src_entry.get().strip()
            self.config_data["output_folder"] = self.out_entry.get().strip()
            self.config_data["dry_run"] = self.dry_run_var.get()

            save_config(self.config_data)
            messagebox.showinfo("Успех", "✅ Все настройки успешно сохранены в config.json!")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить настройки: {e}")

    # ==========================================
    # ОБЩИЕ МЕТОДЫ И ОБРАБОТКА
    # ==========================================
    def log(self, text: str, tag: str = "info"):
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")

    def browse_src(self):
        folder = filedialog.askdirectory(title="Выберите папку с входящими эпикризами")
        if folder:
            self.src_entry.delete(0, "end")
            self.src_entry.insert(0, folder)

    def browse_out(self):
        folder = filedialog.askdirectory(title="Выберите папку для сохранения архивов")
        if folder:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, folder)

    def open_output_folder(self):
        base_dir = get_base_dir()
        out_path = base_dir / self.out_entry.get().strip()
        out_path.mkdir(parents=True, exist_ok=True)
        os.startfile(out_path.resolve())

    def start_processing_thread(self):
        self.run_btn.configure(state="disabled", text="⏳ Обработка...")
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

            self.log_text.delete("1.0", "end")
            self.log(f"[{datetime.now().strftime('%H:%M:%S')}] Начало процесса обработки...", "header")
            self.log("=" * 65, "header")

            if not source_dir.exists():
                self.log(f"❌ Исходная папка не найдена: {source_dir}", "error")
                source_dir.mkdir(parents=True, exist_ok=True)
                self.log(f"📁 Создана пустая папка. Поместите туда файлы и повторите запуск.", "info")
                return

            output_dir.mkdir(parents=True, exist_ok=True)
            unmatched_dir = output_dir / "Требуют_Проверки"
            unmatched_dir.mkdir(parents=True, exist_ok=True)

            allowed_exts = {".rtf", ".pdf", ".docx", ".doc", ".txt"}
            files = [f for f in source_dir.iterdir() if f.is_file() and f.suffix.lower() in allowed_exts]

            if not files:
                self.log("⚠️ В папке нет подходящих файлов (.rtf, .pdf, .docx).", "warn")
                return

            self.log(f"🔍 Найдено документов: {len(files)}", "info")
            self.log("-" * 65, "info")

            temp_dir = base_dir / "temp_sorting"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)

            unmatched_count = 0
            stats = {}

            # 1. Сортировка файлов
            for file_path in files:
                clinic_name = detect_clinic(file_path.name)

                if clinic_name == "Нераспознанные":
                    shutil.copy2(file_path, unmatched_dir / file_path.name)
                    unmatched_count += 1
                    self.log(f"[❓ ПРОВЕРКА] {file_path.name} ➡️ в папку 'Требуют_Проверки'", "warn")
                    continue

                target_folder = temp_dir / clinic_name
                target_folder.mkdir(exist_ok=True)
                shutil.copy2(file_path, target_folder / file_path.name)
                stats[clinic_name] = stats.get(clinic_name, 0) + 1

                route = "📧 EMAIL" if clinic_name in emails_map else "📋 СМДО"
                self.log(f"[{route:<8}] {file_path.name} ➡️ {clinic_name}", "info")

            self.log("-" * 65, "info")
            self.log("📦 Формирование ZIP-архивов и отправка:\n", "header")

            # 2. Архивация и отправка
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

                # Отправка почты
                if clinic_name in emails_map:
                    recipient_email = emails_map[clinic_name]
                    ok = send_clinic_archive(clinic_name, recipient_email, archive_name, today_str, cfg)
                    if ok:
                        self.log(f"  ✉️ Успешно отправлено ➡️ {recipient_email}", "success")
                    else:
                        self.log(f"  ❌ Ошибка отправки ➡️ {recipient_email}", "error")
                else:
                    self.log(f"  📋 Архив сохранен в папку 'Для_СМДО'", "info")

            shutil.rmtree(temp_dir)
            self.log("=" * 65, "header")
            self.log(f"🎉 Обработка завершена! Нераспознанных файлов: {unmatched_count}", "success")

        except Exception as e:
            self.log(f"❌ Ошибка: {e}", "error")
        finally:
            self.run_btn.configure(state="normal", text="▶  Запустить обработку")


if __name__ == "__main__":
    app = MedDispatchApp()
    app.mainloop()