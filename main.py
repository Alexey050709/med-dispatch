import os
import re
import shutil
import zipfile
import json
from datetime import datetime
from pathlib import Path
from mailer import send_clinic_archive

def load_config():
    config_path = Path("config.json")
    if not config_path.exists():
        print("❌ Ошибка: Файл config.json не найден!")
        return None
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def detect_clinic(filename: str) -> str:
    name_clean = filename.lower().replace("_", " ").replace("-", " ")
    
    # Детские поликлиники (1 - 25, без 24)
    child_pattern = r"(?:дет|дгп|дп)\D*(\d+)"
    child_match = re.search(child_pattern, name_clean)
    if child_match:
        num = int(child_match.group(1))
        if 1 <= num <= 25 and num != 24:
            return f"Детская поликлиника №{num}"

    # Взрослые поликлиники (1 - 42)
    adult_pattern = r"(?:гп|п\s*ка|пол|поликлиника)\D*(\d+)"
    adult_match = re.search(adult_pattern, name_clean)
    if adult_match:
        num = int(adult_match.group(1))
        if 1 <= num <= 42:
            return f"Поликлиника №{num}"

    # Районные / ЦРБ
    if any(k in name_clean for k in ["црб", "район", "р н", "больница"]):
        return "ЦРБ и Районные больницы"

    return "Нераспознанные"

def process_epicrisis():
    config = load_config()
    if not config:
        return

    source_dir = Path(config.get("source_folder", "./Входящие_Эпикризы"))
    output_dir = Path(config.get("output_folder", "./Готовые_Архивы"))
    emails_map = config.get("clinics_emails", {})

    if not source_dir.exists():
        print(f"📁 Создана папка для входящих файлов: {source_dir.resolve()}")
        source_dir.mkdir(parents=True, exist_ok=True)
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    temp_dir = Path("./temp_sorting")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    allowed_exts = {".rtf", ".pdf", ".docx", ".doc", ".txt"}
    files = [f for f in source_dir.iterdir() if f.is_file() and f.suffix.lower() in allowed_exts]

    if not files:
        print("⚠️ В папке нет файлов для обработки.")
        return

    print(f"\n🔍 Всего найдено документов: {len(files)}")
    print("=" * 65)

    stats = {}
    for file_path in files:
        clinic_name = detect_clinic(file_path.name)
        target_folder = temp_dir / clinic_name
        target_folder.mkdir(exist_ok=True)
        shutil.copy2(file_path, target_folder / file_path.name)
        stats[clinic_name] = stats.get(clinic_name, 0) + 1
        
        route = "📧 EMAIL" if clinic_name in emails_map else ("📋 СМДО" if clinic_name != "Нераспознанные" else "❓ ПРОВЕРКА")
        print(f"[{route:<8}] {file_path.name[:35]:<35} ➡️ {clinic_name}")

    print("=" * 65)
    print("📦 Формирование архивов и распределение:\n")

    for clinic_folder in temp_dir.iterdir():
        if not clinic_folder.is_dir():
            continue

        clinic_name = clinic_folder.name
        clean_name = clinic_name.replace(" ", "_")
        
        # Определяем подпапку назначения
        if clinic_name in emails_map:
            subfolder = output_dir / "Для_Отправки_Email"
        elif clinic_name == "Нераспознанные":
            subfolder = output_dir / "Требуют_Проверки"
        else:
            subfolder = output_dir / "Для_СМДО"

        subfolder.mkdir(parents=True, exist_ok=True)
        archive_name = subfolder / f"{clean_name}_{today_str}.zip"

        with zipfile.ZipFile(archive_name, "w", zipfile.ZIP_DEFLATED) as zipf:
            for doc in clinic_folder.iterdir():
                zipf.write(doc, arcname=doc.name)

        print(f"✅ Создан архив: {archive_name.name} ({stats[clinic_name]} файлов)")

        # Отправляем только те, которые предназначены для Email
        if clinic_name in emails_map:
            recipient_email = emails_map[clinic_name]
            send_clinic_archive(clinic_name, recipient_email, archive_name, today_str, config)
        elif clinic_name == "Нераспознанные":
            print(f"  ⚠️ Внимание: файлы не распознаны, проверьте архив вручную.")
        else:
            print(f"  📋 Архив сохранен в папку СМДО (отправка по email не требуется).")
        print()

    shutil.rmtree(temp_dir)
    print("=" * 65)
    print(f"🎉 Обработка завершена! Архивы рассортированы в: {output_dir.resolve()}\n")

if __name__ == "__main__":
    process_epicrisis()