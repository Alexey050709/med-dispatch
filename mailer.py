import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.header import Header
from email.utils import formatdate, make_msgid
from pathlib import Path

def send_clinic_archive(clinic_name: str, recipient_email: str, archive_path: Path, date_str: str, config: dict) -> bool:
    smtp_cfg = config.get("smtp_settings", {})
    is_dry_run = config.get("dry_run", True)

    raw_subject = smtp_cfg.get("subject_template", "Эпикризы").format(date=date_str, clinic_name=clinic_name)
    body = smtp_cfg.get("body_template", "").format(date=date_str, clinic_name=clinic_name)
    sender_email = smtp_cfg.get("sender_email", "")

    if is_dry_run:
        print(f"  [ТЕСТОВЫЙ РЕЖИМ] Письмо для [{clinic_name}] готово к отправке:")
        print(f"    - Кому: {recipient_email}")
        print(f"    - Тема: {raw_subject}")
        print(f"    - Вложение: {archive_path.name} ({archive_path.stat().st_size // 1024} КБ)")
        return True

    # Формирование строгого MIME-сообщения по RFC стандартам
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg["Subject"] = Header(raw_subject, "utf-8").encode()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="bsmp.by")
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Прикрепление ZIP-архива с корректной кодировкой имени файла
    try:
        with open(archive_path, "rb") as f:
            part = MIMEBase("application", "zip")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{archive_path.name}"'
        )
        msg.attach(part)
    except Exception as e:
        print(f"❌ Ошибка при чтении архива {archive_path.name}: {e}")
        return False

    server_addr = smtp_cfg.get("server")
    port = smtp_cfg.get("port", 465)
    password = smtp_cfg.get("sender_password")

    try:
        if smtp_cfg.get("use_ssl", True):
            with smtplib.SMTP_SSL(server_addr, port) as server:
                server.login(sender_email, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(server_addr, port) as server:
                if smtp_cfg.get("use_tls", False):
                    server.starttls()
                if password:
                    server.login(sender_email, password)
                server.send_message(msg)

        print(f"  ✉️ Письмо успешно отправлено в [{clinic_name}] ({recipient_email})")
        return True

    except Exception as e:
        print(f"  ❌ Сбой отправки для [{clinic_name}] ({recipient_email}): {e}")
        return False
