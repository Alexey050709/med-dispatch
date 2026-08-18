import os
import re
import shutil
import zipfile
import json
from datetime import datetime
from pathlib import Path

# Загрузка конфигурации
def load_config():
    config_path = Path("config.json")
    if not config_path.exists():
        print("❌ Ошибка: Файл config.json не найден!")
        return None
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def detect_clinic(filename: str) -> str:
    """
    Интеллектуальное определение поликлиники по названию файла.
    """
    name_clean = filename.lower().replace("_", " ").replace("-", " ")
    
    # 1. Проверка на детские поликлиники (1 - 25, без 24)
    child_pattern = r"(?:дет|дгп|дп)\D*(\d+)"
    child_match = re.search(child_pattern, name_clean)
    if child_match:
        num = int(child_match.group(1))
        if 1 <= num <= 25 and num != 24:
            return f"Детская поликлиника №{num}"

    # 2. Проверка на взрослые поликлиники (1 - 42)
    # Ищет паттерны: "гп 10", "пол 18", "п ка 40", "п ка №26", "поликлиника 2"
    adult_pattern = r"(?:гп|п\s*ка|пол|поликлиника)\D*(\d+)"
    adult_match = re.search(adult_pattern, name_clean)
    if adult_match:
        num = int(adult_match.group(1))
        if 1 <= num <= 42:
            return f"Поликлиника №{num}"

    # 3. Проверка на ЦРБ / Районные больницы
    if "црб" in name_clean or "район" in name_clean or "р н" in name_clean or "больница" in name_clean:
        return "ЦРБ и Районные больницы"

    # Если не удалось однозначно распознать
    return "Нераспознанные"

def process_epicrisis():
    config = load_config()
    if not config:
        return

    source_dir = Path(config.get("source_folder", "./Входящие_Эпикризы"))
    output_dir = Path(config.get("output_folder", "./Готовые_Архивы"))

    if not source_dir.exists():
        print(f"📁 Папка с исходными файлами не найдена: {source_dir.resolve()}")
        print("Создаю тестовую папку. Поместите туда файлы и перезапустите программу.")
        source_dir.mkdir(parents=True, exist_ok=True)
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Временная папка для группировки
    temp_dir = Path("./temp_sorting")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Список поддерживаемых расширений
    allowed_exts = {".rtf", ".pdf", ".docx", ".doc", ".txt"}
    files = [f for f in source_dir.iterdir() if f.is_file() and f.suffix.lower() in allowed_exts]

    if not files:
        print("⚠️ В папке нет подходящих файлов для обработки.")
        return

    print(f"🔍 Найдено файлов для обработки: {len(files)}")
    print("-" * 50)

    stats = {}

    # Сортировка по временным папкам
    for file_path in files:
        clinic_name = detect_clinic(file_path.name)
        target_folder = temp_dir / clinic_name
        target_folder.mkdir(exist_ok=True)

        # Копируем файл во временную директорию
        shutil.copy2(file_path, target_folder / file_path.name)
        stats[clinic_name] = stats.get(clinic_name, 0) + 1
        print(f"📄 {file_path.name}  ➡️  [{clinic_name}]")

    print("-" * 50)
    print("📦 Формирование ZIP-архивов...")

    # Упаковка каждой папки в отдельный ZIP-архив
    for clinic_folder in temp_dir.iterdir():
        if clinic_folder.is_dir():
            clinic_name = clinic_folder.name
            
            # Имя архива, например: Поликлиника_№10_2026-08-18.zip
            clean_name = clinic_name.replace(" ", "_")
            archive_name = output_dir / f"{clean_name}_{today_str}.zip"

            with zipfile.ZipFile(archive_name, "w", zipfile.ZIP_DEFLATED) as zipf:
                for doc in clinic_folder.iterdir():
                    zipf.write(doc, arcname=doc.name)

            print(f"✅ Создан архив: {archive_name.name} ({stats[clinic_name]} файлов)")

    # Очищаем временную папку
    shutil.rmtree(temp_dir)

    print("=" * 50)
    print(f"🎉 Готово! Все архивы сохранены в: {output_dir.resolve()}")

if __name__ == "__main__":
    process_epicrisis()