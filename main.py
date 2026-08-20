import os
import re
import shutil
import zipfile
import json
import sys
from datetime import datetime
from pathlib import Path
from mailer import send_clinic_archive
from pathlib import Path

def get_base_dir() -> Path:
    """Определяет реальную папку, где лежит .exe или .py файл"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent

def load_config():
    # Ищем config.json строго рядом с .exe файлом
    config_path = get_base_dir() / "config.json"
    if not config_path.exists():
        print(f"❌ Ошибка: Файл config.json не найден рядом с программой ({config_path})!")
        return None
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def detect_clinic(filename: str) -> str:
    """
    Распознавание поликлиники по стандарту начальницы:
    Префикс до первого пробела: 10ГП, 17ДГП, ЦРБ и т.д.
    """
    clean_name = filename.strip().lower()
    
    # 1. Основной режим: проверка первого токена до пробела
    first_token = clean_name.split()[0] if clean_name.split() else ""
    
    # Очищаем токен от лишних знаков препинания на случай опечаток (например "10гп," или "10гп_")
    first_token = re.sub(r"[^\w]", "", first_token)

    # Проверка детских поликлиник по префиксу (например: "17дгп", "3дгп", "17дп")
    child_prefix = re.match(r"^(\d+)(?:дгп|дп)$", first_token)
    if child_prefix:
        num = int(child_prefix.group(1))
        if 1 <= num <= 25 and num != 24:
            return f"Детская поликлиника №{num}"

    # Проверка взрослых поликлиник по префиксу (например: "10гп", "25гп", "12гп")
    adult_prefix = re.match(r"^(\d+)(?:гп|п)$", first_token)
    if adult_prefix:
        num = int(adult_prefix.group(1))
        if 1 <= num <= 42:
            return f"Поликлиника №{num}"

    # Проверка ЦРБ в первом токене
    if "црб" in first_token or first_token.startswith("црб"):
        return "ЦРБ и Районные больницы"

    # =========================================================================
    # 2. РЕЗЕРВНЫЙ РЕЖИМ (страховка от опечаток врачей, если забыли стандарт)
    # =========================================================================
    # Если врач случайно поставил пробел: "17 дгп Иванов" или написал старым стилем: "гп10"
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


def process_epicrisis():
    config = load_config()
    if not config:
        return

    base_dir = get_base_dir()
    source_dir = base_dir / config.get("source_folder", "Входящие_Эпикризы")
    output_dir = base_dir / config.get("output_folder", "Готовые_Архивы")
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
    input("\nНажмите Enter для выхода...")