"""Интерактивный генератор PDF-счетов и квитанций из CSV и HTML-шаблонов."""

import csv
import html
import os
import platform
import re
import subprocess
from pathlib import Path
from string import Template


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
DATA_DIR = BASE_DIR / "data"
HTML_OUTPUT_DIR = BASE_DIR / "output" / "html"
PDF_OUTPUT_DIR = BASE_DIR / "output" / "pdf"

PRINT_STYLES = """
<style>
    @page { size: A4; margin: 18mm; }
    body {
        color: #222;
        font-family: "Roboto", "Liberation Sans", Arial, sans-serif;
        font-size: 11pt;
        line-height: 1.45;
    }
    h1, h2, h3 { text-align: center; }
    table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
        margin: 1em 0;
    }
    th, td {
        border: 1px solid #888;
        padding: 8px;
        text-align: center;
        overflow-wrap: anywhere;
        word-wrap: break-word;
        hyphens: auto;
    }
    th { background: #f0f0f0; }
</style>
"""


def create_sample_files():
    """Создаёт примеры шаблонов и CSV, если соответствующие папки пусты."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not list(TEMPLATES_DIR.glob("*.html")):
        samples = {
            "invoice.html": """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Счёт $invoice_id</title></head>
<body>
  <h1>Счёт на оплату</h1>
  <p><strong>Номер:</strong> $invoice_id</p>
  <p><strong>Дата:</strong> $date</p>
  <p><strong>Плательщик:</strong> $customer</p>
  <table><thead><tr><th>Описание</th><th>Сумма</th></tr></thead>
  <tbody><tr><td>$description</td><td>$amount $currency</td></tr></tbody></table>
  <p><strong>Итого:</strong> $amount $currency</p>
</body></html>""",
            "receipt.html": """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Квитанция $receipt_id</title></head>
<body>
  <h1>Квитанция</h1>
  <p><strong>Номер:</strong> $receipt_id</p>
  <p><strong>Дата:</strong> $date</p>
  <p><strong>Получатель:</strong> $customer</p>
  <table><thead><tr><th>Назначение платежа</th><th>Сумма</th></tr></thead>
  <tbody><tr><td>$description</td><td>$amount $currency</td></tr></tbody></table>
  <p>Спасибо за оплату!</p>
</body></html>""",
        }
        for name, content in samples.items():
            (TEMPLATES_DIR / name).write_text(content, encoding="utf-8")

    if not list(DATA_DIR.glob("*.csv")):
        samples = {
            "invoices.csv": [
                ["invoice_id", "date", "customer", "description", "amount", "currency"],
                ["INV-1001", "18.10.2026", "ООО «Ромашка»", "Разработка и сопровождение сайта", "25000", "руб."],
                ["INV-1002", "19.10.2026", "Иван Иванов", "Консультационные услуги и техническая поддержка", "8500", "руб."],
            ],
            "receipts.csv": [
                ["receipt_id", "date", "customer", "description", "amount", "currency"],
                ["REC-2001", "18.10.2026", "Мария Петрова", "Оплата заказа № 42", "3200", "руб."],
                ["REC-2002", "19.10.2026", "ООО «Север»", "Оплата услуг по договору № 15", "14700", "руб."],
            ],
        }
        for name, rows in samples.items():
            with (DATA_DIR / name).open("w", encoding="utf-8", newline="") as csv_file:
                csv.writer(csv_file).writerows(rows)


def scan_files(directory, extension):
    """Возвращает отсортированные файлы с заданным расширением."""
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == extension),
        key=lambda path: path.name.lower(),
    )


def display_menu(title, options):
    """Показывает нумерованное меню и вариант выхода."""
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option.name}")
    print("  0. Выход")


def get_user_choice(options, prompt="Ваш выбор: "):
    """Запрашивает и возвращает выбранный элемент списка либо None для выхода."""
    while True:
        try:
            choice = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            return None
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        print(f"✗ Введите число от 1 до {len(options)} или 0 для выхода.")


def read_csv_records(csv_path):
    """Загружает записи CSV с заголовками; поддерживает UTF-8 с BOM."""
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            if not reader.fieldnames:
                print(f"✗ В CSV нет строки заголовков: {csv_path.name}")
                return []
            records = []
            for row in reader:
                if row and any(value not in (None, "") for value in row.values()):
                    records.append({key: value or "" for key, value in row.items() if key is not None})
            return records
    except (OSError, csv.Error) as error:
        print(f"✗ Не удалось прочитать {csv_path.name}: {error}")
        return []


def select_record(records):
    """Даёт выбрать отдельную запись CSV или обработать все записи."""
    if not records:
        print("✗ В выбранном CSV нет записей.")
        return []

    print(f"\n{'=' * 60}\nВыберите запись\n{'=' * 60}")
    for index, record in enumerate(records, start=1):
        preview = " | ".join(
            f"{key}: {value}" for key, value in list(record.items())[:4]
        )
        print(f"  {index}. {preview}")
    print("  A. Все записи")
    print("  0. Выход")

    while True:
        try:
            choice = input("Ваш выбор: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            return []
        if choice == "0":
            return []
        if choice in ("a", "all"):
            return records
        if choice.isdigit() and 1 <= int(choice) <= len(records):
            return [records[int(choice) - 1]]
        print(f"✗ Введите номер от 1 до {len(records)}, A или 0.")


def safe_filename_part(value):
    """Оставляет в компоненте имени файла только безопасные символы."""
    cleaned = re.sub(r"[^\w-]+", "_", str(value), flags=re.UNICODE).strip("_-")
    return cleaned[:80] or "record"


def record_identifier(record, index):
    """Получает идентификатор записи из CSV или использует её номер."""
    for key in ("id", "record_id", "invoice_id", "receipt_id", "number"):
        if record.get(key, "").strip():
            return record[key]
    return str(index)


def render_html(template_path, record):
    """Подставляет безопасно экранированные значения CSV в HTML-шаблон."""
    try:
        template_text = template_path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"Не удалось прочитать шаблон {template_path.name}: {error}") from error

    escaped_record = {key: html.escape(value, quote=True) for key, value in record.items()}
    rendered = Template(template_text).safe_substitute(escaped_record)
    if re.search(r"</head\s*>", rendered, flags=re.IGNORECASE):
        return re.sub(
            r"</head\s*>",
            PRINT_STYLES + "\n</head>",
            rendered,
            count=1,
            flags=re.IGNORECASE,
        )
    return PRINT_STYLES + rendered


def report_weasyprint_import_error(error):
    """Объясняет, как исправить отсутствие WeasyPrint или его нативных библиотек."""
    print(f"✗ Не удалось загрузить WeasyPrint: {error}")
    if isinstance(error, ImportError):
        print("  Установите Python-пакет в активное окружение:")
        print("  python -m pip install weasyprint")
    elif platform.system() == "Windows":
        print("  Пакет WeasyPrint установлен, но не найдена нативная библиотека GTK.")
        print("  Установите MSYS2, затем в терминале MSYS2 UCRT64 выполните:")
        print("  pacman -S mingw-w64-ucrt-x86_64-gtk3")
        print("  Добавьте C:\\msys64\\ucrt64\\bin в PATH и перезапустите PowerShell.")
        print("  Для текущего окна PowerShell можно выполнить:")
        print('  $env:Path = "C:\\msys64\\ucrt64\\bin;$env:Path"')
    elif platform.system() == "Darwin":
        print("  Установите нативные зависимости WeasyPrint (например, через Homebrew):")
        print("  brew install pango")
    else:
        print("  Установите системные библиотеки WeasyPrint согласно документации:")
        print("  https://doc.courtbouillon.org/weasyprint/stable/first_steps.html")


def generate_pdf(template_path, record, index):
    """Сохраняет HTML и конвертирует его в PDF с помощью WeasyPrint."""
    try:
        HTML_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        PDF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"✗ Не удалось создать папки вывода: {error}")
        return None

    identifier = safe_filename_part(record_identifier(record, index))
    base_name = f"{safe_filename_part(template_path.stem)}_{identifier}"
    html_path = HTML_OUTPUT_DIR / f"{base_name}.html"
    pdf_path = PDF_OUTPUT_DIR / f"{base_name}.pdf"

    try:
        html_path.write_text(render_html(template_path, record), encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as error:
        print(f"✗ Не удалось создать HTML {html_path.name}: {error}")
        return None

    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        report_weasyprint_import_error(error)
        return None

    try:
        HTML(filename=str(html_path), encoding="utf-8").write_pdf(str(pdf_path))
    except Exception as error:
        print(f"✗ Ошибка конвертации {html_path.name} в PDF: {error}")
        return None

    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        print(f"✗ PDF не создан или пуст: {pdf_path}")
        return None
    print(f"✓ PDF создан: {pdf_path}")
    return pdf_path


def open_file(file_path):
    """Открывает файл программой просмотра в Windows, macOS или Linux."""
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(file_path))
        elif system == "Darwin":
            subprocess.run(["open", str(file_path)], check=True)
        elif system == "Linux":
            subprocess.run(["xdg-open", str(file_path)], check=True)
        else:
            print(f"✗ Автооткрытие не поддерживается на платформе {system}. Файл: {file_path}")
            return False
        print("✓ PDF открыт в системной программе.")
        return True
    except (OSError, subprocess.SubprocessError) as error:
        print(f"✗ Не удалось открыть PDF: {error}\nФайл сохранён: {file_path}")
        return False


def main():
    """Запускает меню выбора шаблона, CSV-файла и записей."""
    create_sample_files()
    templates = scan_files(TEMPLATES_DIR, ".html")
    data_files = scan_files(DATA_DIR, ".csv")

    if not templates:
        print(f"✗ HTML-шаблоны не найдены в папке: {TEMPLATES_DIR}")
        return
    if not data_files:
        print(f"✗ CSV-файлы не найдены в папке: {DATA_DIR}")
        return

    display_menu("Выберите HTML-шаблон", templates)
    template_path = get_user_choice(templates)
    if template_path is None:
        return

    display_menu("Выберите CSV-файл с данными", data_files)
    csv_path = get_user_choice(data_files)
    if csv_path is None:
        return

    records = read_csv_records(csv_path)
    selected_records = select_record(records)
    if not selected_records:
        return

    generated_pdfs = []
    for index, record in enumerate(selected_records, start=1):
        pdf_path = generate_pdf(template_path, record, index)
        if pdf_path is not None:
            generated_pdfs.append(pdf_path)

    if not generated_pdfs:
        print("\n✗ Не удалось создать ни одного PDF.")
        return

    print(f"\n✓ Готово. Создано PDF: {len(generated_pdfs)}")
    open_file(generated_pdfs[-1])


if __name__ == "__main__":
    main()
