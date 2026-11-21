import os
import csv
import shutil
import pymupdf as fitz
import sys
import re
from contextlib import redirect_stdout
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPixmap, QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QMessageBox,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QLineEdit, QCheckBox, QProgressBar, QTextEdit, QListWidget,
    QListWidgetItem, QSplitter, QGroupBox, QStatusBar, QFrame,
    QDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView
)


def resource_path(relative_path):
    """Повертає шлях до ресурсу як у .py, так і в зібраному PyInstaller .exe."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


PDF_FILE = "3-Довідник-каталог-БПЛА.pdf"
OUTPUT_FOLDER = "2_all_pdf_images"

# True = перед запуском очистити OUTPUT_FOLDER,
# щоб там не залишались файли від попередніх запусків.
CLEAR_OUTPUT = True

# Масштаб для аварійного crop-render,
# якщо PyMuPDF бачить зображення, але не віддає його байти.
FALLBACK_ZOOM = 2.0


def normalize_ext(ext):
    if not ext:
        return "png"

    ext = ext.lower().lstrip(".")

    if ext == "jpeg":
        return "jpg"

    if ext == "tiff":
        return "tif"

    return ext


def same_bbox(a, b, tolerance=0.5):
    """Порівнює два bbox з невеликим допуском."""
    if not a or not b:
        return False

    return all(
        abs(float(x) - float(y)) <= tolerance
        for x, y in zip(a, b)
    )


def prepare_output(output_dir):
    if CLEAR_OUTPUT and os.path.isdir(output_dir):
        shutil.rmtree(output_dir)

    os.makedirs(output_dir, exist_ok=True)


def save_block_image(block, filepath_without_ext):
    """
    Зберігає байти image-block.
    Повертає (filename, method) або (None, None).
    """
    image_bytes = block.get("image")

    if not image_bytes:
        return None, None

    ext = normalize_ext(block.get("ext", "png"))
    filepath = f"{filepath_without_ext}.{ext}"

    with open(filepath, "wb") as f:
        f.write(image_bytes)

    return os.path.basename(filepath), "block-bytes"


def save_bbox_render(page, bbox, filepath_without_ext):
    """
    Резервний метод:
    рендерить саме область зображення зі сторінки в PNG.
    """
    rect = fitz.Rect(bbox)

    if rect.is_empty or rect.is_infinite:
        return None, None

    matrix = fitz.Matrix(FALLBACK_ZOOM, FALLBACK_ZOOM)

    pix = page.get_pixmap(
        matrix=matrix,
        clip=rect,
        alpha=False,
    )

    filepath = f"{filepath_without_ext}.png"
    pix.save(filepath)

    return os.path.basename(filepath), "bbox-render"


def extract_all_pdf_images(pdf_path, output_dir):
    """
    Проходить ВСІ сторінки PDF від 1 до останньої.

    Основний метод:
        page.get_text("dict") -> type == 1

    Він бачить як XREF-зображення, так і inline-зображення.

    Додаткова перевірка:
        page.get_image_info(xrefs=True)

    Якщо image_info бачить область, якої немає серед
    збережених image-blocks, ця область рендериться в PNG.

    ЖОДНИХ:
    - фільтрів за сторінками;
    - фільтрів за розміром;
    - фільтрів "дрон / не дрон";
    - дедуплікації.

    Тобто зберігається кожне знайдене входження зображення.
    """

    if not os.path.isfile(pdf_path):
        print(f"[ERROR] PDF не знайдено: {pdf_path}")
        return

    prepare_output(output_dir)

    report_path = os.path.join(
        output_dir,
        "extraction_report.csv",
    )

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"[ERROR] Не вдалося відкрити PDF: {e}")
        return

    total_saved = 0
    total_blocks = 0
    total_info = 0
    total_fallback = 0
    total_pages = len(doc)

    report_rows = []

    print("=" * 78)
    print(f"PDF: {pdf_path}")
    print(f"Сторінок: {total_pages}")
    print(f"Результат: {os.path.abspath(output_dir)}")
    print("=" * 78)

    for page_index in range(len(doc)):
        page = doc[page_index]
        page_num = page_index + 1

        # ----------------------------------------------------
        # 1. ОСНОВНИЙ СПОСІБ: усі image-blocks сторінки
        # ----------------------------------------------------

        try:
            page_dict = page.get_text("dict")
        except Exception as e:
            print(
                f"[PAGE {page_num:02d}] "
                f"[ERROR] get_text('dict'): {e}"
            )
            page_dict = {"blocks": []}

        image_blocks = [
            block
            for block in page_dict.get("blocks", [])
            if block.get("type") == 1
        ]

        # Сортуємо згори вниз / зліва направо,
        # щоб 06_01, 06_02... були стабільними.
        image_blocks.sort(
            key=lambda b: (
                float(b.get("bbox", (0, 0, 0, 0))[1]),
                float(b.get("bbox", (0, 0, 0, 0))[0]),
            )
        )

        total_blocks += len(image_blocks)

        saved_bboxes = []
        page_saved = 0

        for block_index, block in enumerate(
            image_blocks,
            start=1,
        ):
            page_saved += 1
            total_saved += 1

            base_name = (
                f"{page_num:02d}_"
                f"{page_saved:02d}"
            )

            base_path = os.path.join(
                output_dir,
                base_name,
            )

            filename = None
            method = None

            try:
                filename, method = save_block_image(
                    block,
                    base_path,
                )
            except Exception as e:
                print(
                    f"  [WARN] PAGE {page_num:02d}, "
                    f"block {block_index}: bytes save failed: {e}"
                )

            # Якщо байтів немає / не вдалося записати —
            # рендеримо bbox з самої сторінки.
            if filename is None:
                bbox = block.get("bbox")

                try:
                    filename, method = save_bbox_render(
                        page,
                        bbox,
                        base_path,
                    )
                    total_fallback += 1
                except Exception as e:
                    print(
                        f"  [ERROR] PAGE {page_num:02d}, "
                        f"block {block_index}: "
                        f"fallback render failed: {e}"
                    )
                    total_saved -= 1
                    page_saved -= 1
                    continue

            bbox = block.get("bbox")
            if bbox:
                saved_bboxes.append(tuple(bbox))

            report_rows.append({
                "page": page_num,
                "image_number": page_saved,
                "filename": filename,
                "method": method,
                "xref": block.get("xref", 0),
                "source_width": block.get("width", 0),
                "source_height": block.get("height", 0),
                "bbox": str(tuple(round(float(v), 3) for v in bbox))
                    if bbox else "",
            })

        # ----------------------------------------------------
        # 2. КОНТРОЛЬ: get_image_info
        #
        # Він показує кожне реально намальоване зображення.
        # Якщо знайде bbox, якого не було серед blocks,
        # рендеримо його окремо.
        # ----------------------------------------------------

        try:
            image_info = page.get_image_info(
                hashes=True,
                xrefs=True,
            )
        except Exception as e:
            print(
                f"[PAGE {page_num:02d}] "
                f"[WARN] get_image_info: {e}"
            )
            image_info = []

        total_info += len(image_info)

        extra_info = 0

        for info in image_info:
            bbox = info.get("bbox")

            if not bbox:
                continue

            already_saved = any(
                same_bbox(bbox, saved_bbox)
                for saved_bbox in saved_bboxes
            )

            if already_saved:
                continue

            # image_info бачить картинку,
            # а image-block її не віддав -> рятуємо crop.
            page_saved += 1
            total_saved += 1
            total_fallback += 1
            extra_info += 1

            base_name = (
                f"{page_num:02d}_"
                f"{page_saved:02d}"
                f"_fallback"
            )

            base_path = os.path.join(
                output_dir,
                base_name,
            )

            try:
                filename, method = save_bbox_render(
                    page,
                    bbox,
                    base_path,
                )
            except Exception as e:
                print(
                    f"  [ERROR] PAGE {page_num:02d}, "
                    f"image_info fallback: {e}"
                )
                total_saved -= 1
                page_saved -= 1
                total_fallback -= 1
                continue

            saved_bboxes.append(tuple(bbox))

            report_rows.append({
                "page": page_num,
                "image_number": page_saved,
                "filename": filename,
                "method": "image-info-fallback",
                "xref": info.get("xref", 0),
                "source_width": info.get("width", 0),
                "source_height": info.get("height", 0),
                "bbox": str(tuple(round(float(v), 3) for v in bbox)),
            })

        status = "OK"

        if page_saved < len(image_info):
            status = "CHECK"

        print(
            f"[PAGE {page_num:02d}/{total_pages:02d}] "
            f"blocks={len(image_blocks):2d} | "
            f"image_info={len(image_info):2d} | "
            f"saved={page_saved:2d} | "
            f"extra={extra_info:2d} | "
            f"{status}"
        )

    doc.close()

    # --------------------------------------------------------
    # CSV-звіт
    # --------------------------------------------------------

    with open(
        report_path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as csvfile:
        fieldnames = [
            "page",
            "image_number",
            "filename",
            "method",
            "xref",
            "source_width",
            "source_height",
            "bbox",
        ]

        writer = csv.DictWriter(
            csvfile,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(report_rows)

    print()
    print("=" * 78)
    print("ГОТОВО")
    print("=" * 78)
    print(f"Оброблено сторінок: {total_pages}/{total_pages}")
    print(f"Image-blocks знайдено: {total_blocks}")
    print(f"Image-info знайдено:   {total_info}")
    print(f"Файлів збережено:      {total_saved}")
    print(f"Fallback-render:        {total_fallback}")
    print(f"CSV-звіт: {os.path.abspath(report_path)}")
    print("=" * 78)


# ============================================================
# PyQt5 GUI — графічна оболонка. Алгоритм вилучення вище
# не змінюється: GUI викликає extract_all_pdf_images().
# ============================================================




class QtLogStream:
    """Перенаправляє print() з наявної функції у журнал GUI."""
    def __init__(self, signal):
        self.signal = signal
        self._buffer = ""

    def write(self, text):
        if not text:
            return
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.signal.emit(line)

    def flush(self):
        if self._buffer.strip():
            self.signal.emit(self._buffer)
        self._buffer = ""


class ExtractionWorker(QThread):
    log_message = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int)
    extraction_finished = pyqtSignal(str)
    extraction_failed = pyqtSignal(str)

    def __init__(self, pdf_path, output_dir):
        super().__init__()
        self.pdf_path = pdf_path
        self.output_dir = output_dir

    def run(self):
        stream = QtLogStream(self.log_message)
        try:
            with redirect_stdout(stream):
                extract_all_pdf_images(self.pdf_path, self.output_dir)
            stream.flush()
            self.extraction_finished.emit(self.output_dir)
        except Exception as exc:
            stream.flush()
            self.extraction_failed.emit(str(exc))



class CSVReportWindow(QDialog):
    """Внутрішнє вікно перегляду extraction_report.csv."""

    def __init__(self, report_path, parent=None):
        super().__init__(parent)
        self.report_path = report_path
        self.rows = []
        self.headers = []

        self.setWindowTitle("MIL-EXTRACT | Перегляд CSV-звіту")
        self.resize(1050, 650)
        self.setMinimumSize(760, 450)
        self.setStyleSheet(parent.stylesheet() if parent else "")

        layout = QVBoxLayout(self)
        heading = QLabel("ЗВІТ ВИЛУЧЕННЯ ЗОБРАЖЕНЬ")
        heading.setObjectName("section")
        layout.addWidget(heading)

        self.info_label = QLabel(os.path.basename(report_path))
        self.info_label.setObjectName("muted")
        layout.addWidget(self.info_label)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Пошук:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Фільтр за сторінкою, назвою файлу, методом, розмірами..."
        )
        self.search_input.textChanged.connect(self.filter_rows)
        search_row.addWidget(self.search_input, 1)
        layout.addLayout(search_row)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self.open_image_from_row)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.count_label = QLabel("Записів: 0")
        self.count_label.setObjectName("muted")
        footer.addWidget(self.count_label)
        footer.addStretch()

        open_image_button = QPushButton("ВІДКРИТИ ЗОБРАЖЕННЯ")
        open_image_button.clicked.connect(self.open_selected_image)
        footer.addWidget(open_image_button)

        open_csv_button = QPushButton("ВІДКРИТИ CSV ЗОВНІШНЬО")
        open_csv_button.clicked.connect(self.open_csv_externally)
        footer.addWidget(open_csv_button)

        close_button = QPushButton("ЗАКРИТИ")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self.load_csv()

    def load_csv(self):
        if not os.path.isfile(self.report_path):
            QMessageBox.warning(
                self, "Звіт не знайдено",
                "CSV-звіт не існує. Спочатку виконайте вилучення зображень."
            )
            self.close()
            return

        try:
            # Звіт створюється у UTF-8 з BOM (utf-8-sig).
            with open(self.report_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                self.headers = reader.fieldnames or []
                self.rows = list(reader)
        except UnicodeDecodeError:
            try:
                with open(self.report_path, "r", encoding="cp1251", newline="") as f:
                    reader = csv.DictReader(f)
                    self.headers = reader.fieldnames or []
                    self.rows = list(reader)
            except Exception as exc:
                QMessageBox.critical(
                    self, "Помилка читання CSV",
                    f"Не вдалося прочитати звіт:\n{exc}"
                )
                self.close()
                return
        except Exception as exc:
            QMessageBox.critical(
                self, "Помилка читання CSV",
                f"Не вдалося прочитати звіт:\n{exc}"
            )
            self.close()
            return

        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(self.headers))
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setRowCount(len(self.rows))

        filename_column = (
            self.headers.index("filename") if "filename" in self.headers else -1
        )

        for row_index, row_data in enumerate(self.rows):
            for column_index, header in enumerate(self.headers):
                value = row_data.get(header, "") or ""
                item = QTableWidgetItem(str(value))
                if column_index == filename_column and value:
                    image_path = os.path.join(
                        os.path.dirname(self.report_path), value
                    )
                    item.setData(Qt.UserRole, image_path)
                    item.setToolTip(
                        f"{image_path}\nПодвійне клацання — відкрити зображення"
                    )
                self.table.setItem(row_index, column_index, item)

        self.table.setSortingEnabled(True)
        self.filter_rows()
        self.info_label.setText(
            f"{os.path.abspath(self.report_path)} | "
            f"Колонок: {len(self.headers)}"
        )

    def filter_rows(self):
        query = self.search_input.text().strip().casefold()
        visible_count = 0

        for row in range(self.table.rowCount()):
            matches = not query
            if query:
                for column in range(self.table.columnCount()):
                    item = self.table.item(row, column)
                    if item and query in item.text().casefold():
                        matches = True
                        break

            self.table.setRowHidden(row, not matches)
            if matches:
                visible_count += 1

        self.count_label.setText(
            f"Показано: {visible_count} / {self.table.rowCount()} записів"
        )

    def selected_image_path(self):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None

        row = selected[0].row()
        if "filename" not in self.headers:
            return None

        column = self.headers.index("filename")
        item = self.table.item(row, column)
        if not item:
            return None

        path = item.data(Qt.UserRole)
        return path if path else None

    def open_selected_image(self):
        path = self.selected_image_path()
        if path and os.path.isfile(path):
            os.startfile(os.path.abspath(path))
        else:
            QMessageBox.information(
                self, "Зображення не знайдено",
                "Виберіть запис із файлом, який існує у папці результатів."
            )

    def open_image_from_row(self, row, column):
        if "filename" not in self.headers:
            return

        filename_column = self.headers.index("filename")
        item = self.table.item(row, filename_column)
        path = item.data(Qt.UserRole) if item else None

        if path and os.path.isfile(path):
            os.startfile(os.path.abspath(path))

    def open_csv_externally(self):
        if os.path.isfile(self.report_path):
            os.startfile(os.path.abspath(self.report_path))
        else:
            QMessageBox.information(
                self, "Звіт не знайдено", "CSV-файл більше не існує."
            )



class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.setWindowTitle("MIL-EXTRACT | Вилучення зображень із PDF")
        self.resize(1180, 780)
        self.setMinimumSize(900, 620)
        self.setStyleSheet(self.stylesheet())
        self.build_ui()

        # Початкові значення з оригінального скрипту.
        self.pdf_input.setText(PDF_FILE)
        self.output_input.setText(OUTPUT_FOLDER)

    def stylesheet(self):
        return """
        QWidget {
            background: #171d1b; color: #dce5dc;
            font-family: 'Segoe UI'; font-size: 10pt;
        }
        QMainWindow { background: #121815; }
        QGroupBox {
            border: 1px solid #3d5145; border-radius: 7px;
            margin-top: 12px; padding: 12px 9px 9px 9px;
            font-weight: bold; color: #a9c6ad;
        }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
        QLineEdit, QTextEdit, QListWidget {
            background: #0e1411; border: 1px solid #34483b;
            border-radius: 5px; padding: 7px; selection-background-color: #496b50;
        }
        QPushButton {
            background: #344d3b; border: 1px solid #55765d;
            border-radius: 5px; padding: 8px 14px; color: #f0f5ef;
            font-weight: bold;
        }
        QPushButton:hover { background: #45664d; }
        QPushButton:disabled { background: #252d28; color: #778078; }
        QPushButton#primary { background: #587b45; border: 1px solid #789b5e; }
        QPushButton#primary:hover { background: #6b914f; }
        QProgressBar {
            border: 1px solid #405747; border-radius: 5px;
            background: #0e1411; text-align: center; min-height: 18px;
        }
        QProgressBar::chunk { background: #789b55; border-radius: 4px; }
        QCheckBox { spacing: 8px; }
        QStatusBar { background: #0d120f; color: #9daf9e; }
        QSplitter::handle { background: #35453a; }
        QLabel#section { color: #a8c58c; font-size: 11pt; font-weight: bold; }
        QLabel#muted { color: #8e9d91; }
        """

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 15, 18, 12)
        root.setSpacing(11)

        # Header
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("MIL-EXTRACT")
        title.setStyleSheet("font-size: 23pt; font-weight: 800; letter-spacing: 2px; color: #b5d18e;")
        subtitle = QLabel("ІНСТРУМЕНТ ВИЛУЧЕННЯ ЗОБРАЖЕНЬ З PDF-ДОКУМЕНТІВ")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.state_label = QLabel("● ГОТОВИЙ ДО РОБОТИ")
        self.state_label.setStyleSheet("color: #9bc77c; font-weight: bold;")
        header.addWidget(self.state_label, alignment=Qt.AlignVCenter)
        root.addLayout(header)

        # Input/output controls
        paths_group = QGroupBox("ДЖЕРЕЛО ТА МІСЦЕ ЗБЕРЕЖЕННЯ")
        paths_layout = QGridLayout(paths_group)
        paths_layout.addWidget(QLabel("PDF-файл:"), 0, 0)
        self.pdf_input = QLineEdit()
        self.pdf_input.setPlaceholderText("Оберіть PDF-документ…")
        paths_layout.addWidget(self.pdf_input, 0, 1)
        self.pdf_button = QPushButton("ОБРАТИ PDF")
        self.pdf_button.clicked.connect(self.choose_pdf)
        paths_layout.addWidget(self.pdf_button, 0, 2)

        paths_layout.addWidget(QLabel("Папка результатів:"), 1, 0)
        self.output_input = QLineEdit()
        self.output_input.setPlaceholderText("Папка для вилучених зображень…")
        paths_layout.addWidget(self.output_input, 1, 1)
        self.output_button = QPushButton("ОБРАТИ ПАПКУ")
        self.output_button.clicked.connect(self.choose_output)
        paths_layout.addWidget(self.output_button, 1, 2)

        self.clear_checkbox = QCheckBox("Очищати папку результатів перед запуском")
        self.clear_checkbox.setChecked(CLEAR_OUTPUT)
        paths_layout.addWidget(self.clear_checkbox, 2, 1, 1, 2)
        root.addWidget(paths_group)

        # Action and progress
        action_row = QHBoxLayout()
        self.start_button = QPushButton("▶  ПОЧАТИ ВИЛУЧЕННЯ")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumHeight(42)
        self.start_button.clicked.connect(self.start_extraction)
        action_row.addWidget(self.start_button)
        self.open_folder_button = QPushButton("ВІДКРИТИ ПАПКУ РЕЗУЛЬТАТІВ")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        action_row.addWidget(self.open_folder_button)
        self.open_report_button = QPushButton("ВІДКРИТИ CSV-ЗВІТ")
        self.open_report_button.clicked.connect(self.open_report)
        action_row.addWidget(self.open_report_button)
        root.addLayout(action_row)

        progress_group = QGroupBox("СТАН ОПЕРАЦІЇ")
        progress_layout = QVBoxLayout(progress_group)
        self.progress_label = QLabel("Очікування запуску")
        self.progress_label.setObjectName("muted")
        progress_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        root.addWidget(progress_group)

        # Log + thumbnails
        splitter = QSplitter(Qt.Horizontal)

        log_group = QGroupBox("ЖУРНАЛ РОБОТИ")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Тут відображатимуться повідомлення процесу вилучення…")
        self.log_view.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_view)
        log_buttons = QHBoxLayout()
        clear_log_button = QPushButton("ОЧИСТИТИ ЖУРНАЛ")
        clear_log_button.clicked.connect(self.log_view.clear)
        log_buttons.addWidget(clear_log_button)
        log_buttons.addStretch()
        log_layout.addLayout(log_buttons)
        splitter.addWidget(log_group)

        preview_group = QGroupBox("МІНІАТЮРИ ВИЛУЧЕНИХ ЗОБРАЖЕНЬ")
        preview_layout = QVBoxLayout(preview_group)
        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setViewMode(QListWidget.IconMode)
        self.thumbnail_list.setIconSize(QPixmap(120, 90).size())
        self.thumbnail_list.setGridSize(self.thumbnail_list.gridSize())
        self.thumbnail_list.setResizeMode(QListWidget.Adjust)
        self.thumbnail_list.setMovement(QListWidget.Static)
        self.thumbnail_list.setSpacing(8)
        self.thumbnail_list.itemDoubleClicked.connect(self.open_thumbnail)
        preview_layout.addWidget(self.thumbnail_list)
        self.thumbnail_count = QLabel("0 зображень")
        self.thumbnail_count.setObjectName("muted")
        preview_layout.addWidget(self.thumbnail_count)
        splitter.addWidget(preview_group)
        splitter.setSizes([560, 500])
        root.addWidget(splitter, 1)

        self.statusBar().showMessage("Готово. Оберіть PDF-файл та папку результатів.")

    def choose_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Вибрати PDF-файл", "", "PDF-документи (*.pdf)"
        )
        if path:
            self.pdf_input.setText(path)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Вибрати папку результатів")
        if path:
            self.output_input.setText(path)

    def append_log(self, message):
        self.log_view.append(message)
        match = re.search(r"\[PAGE\s+(\d+)\s*/\s*(\d+)\]", message)
        if match:
            current, total = map(int, match.groups())
            if total > 0:
                self.progress_bar.setRange(0, total)
                self.progress_bar.setValue(current)
                self.progress_label.setText(
                    f"Обробка сторінки {current} із {total}"
                )
        self.statusBar().showMessage(message[:180])

    def start_extraction(self):
        pdf_path = self.pdf_input.text().strip().strip('"')
        output_dir = self.output_input.text().strip().strip('"')

        if not pdf_path or not os.path.isfile(pdf_path):
            QMessageBox.warning(self, "Не вибрано PDF", "Вкажіть наявний PDF-файл.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Не вибрано папку", "Вкажіть папку результатів.")
            return

        if self.clear_checkbox.isChecked() and os.path.isdir(output_dir):
            answer = QMessageBox.question(
                self, "Підтвердження очищення",
                "Увімкнено очищення папки перед запуском.\n"
                "Усі наявні файли в цій папці буде видалено.\n\nПродовжити?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                return

        global CLEAR_OUTPUT
        CLEAR_OUTPUT = self.clear_checkbox.isChecked()

        self.thumbnail_list.clear()
        self.thumbnail_count.setText("0 зображень")
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText("Запуск…")
        self.log_view.append("\n" + "=" * 58)
        self.log_view.append("ПОЧАТОК НОВОГО ЗАВДАННЯ")
        self.log_view.append(f"Джерело: {pdf_path}")
        self.log_view.append(f"Результат: {output_dir}")
        self.start_button.setEnabled(False)
        self.pdf_button.setEnabled(False)
        self.output_button.setEnabled(False)
        self.state_label.setText("● ВИКОНУЄТЬСЯ")
        self.state_label.setStyleSheet("color: #e0bd68; font-weight: bold;")

        self.worker = ExtractionWorker(pdf_path, output_dir)
        self.worker.log_message.connect(self.append_log)
        self.worker.extraction_finished.connect(self.on_finished)
        self.worker.extraction_failed.connect(self.on_failed)
        self.worker.start()

    def on_finished(self, output_dir):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_label.setText("Обробку завершено")
        self.state_label.setText("● ЗАВЕРШЕНО")
        self.state_label.setStyleSheet("color: #9bc77c; font-weight: bold;")
        self.log_view.append("\nЗАВДАННЯ ЗАВЕРШЕНО.")
        self.load_thumbnails(output_dir)
        self.restore_controls()
        self.statusBar().showMessage("Вилучення завершено.")

    def on_failed(self, error):
        self.state_label.setText("● ПОМИЛКА")
        self.state_label.setStyleSheet("color: #e07b70; font-weight: bold;")
        self.log_view.append(f"\n[КРИТИЧНА ПОМИЛКА] {error}")
        self.progress_label.setText("Виникла помилка")
        self.progress_bar.setRange(0, 100)
        self.restore_controls()
        QMessageBox.critical(self, "Помилка виконання", error)

    def restore_controls(self):
        self.start_button.setEnabled(True)
        self.pdf_button.setEnabled(True)
        self.output_button.setEnabled(True)

    def load_thumbnails(self, folder):
        self.thumbnail_list.clear()
        if not os.path.isdir(folder):
            return
        supported = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
        files = sorted(
            name for name in os.listdir(folder)
            if name.lower().endswith(supported)
        )
        for name in files:
            full_path = os.path.join(folder, name)
            pixmap = QPixmap(full_path)
            if pixmap.isNull():
                continue
            icon = QIcon(pixmap.scaled(
                120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation
            ))
            item = QListWidgetItem(icon, name)
            item.setToolTip(full_path)
            item.setData(Qt.UserRole, full_path)
            self.thumbnail_list.addItem(item)
        self.thumbnail_count.setText(f"{self.thumbnail_list.count()} зображень")

    def open_thumbnail(self, item):
        path = item.data(Qt.UserRole)
        if path and os.path.isfile(path):
            os.startfile(path)

    def open_output_folder(self):
        folder = self.output_input.text().strip().strip('"')
        if os.path.isdir(folder):
            os.startfile(os.path.abspath(folder))
        else:
            QMessageBox.information(self, "Папку не знайдено", "Папка результатів ще не створена.")

    def open_report(self):
        folder = self.output_input.text().strip().strip('"')
        report = os.path.join(folder, "extraction_report.csv")
        if os.path.isfile(report):
            viewer = CSVReportWindow(report, self)
            viewer.exec_()
        else:
            QMessageBox.information(
                self, "Звіт не знайдено",
                "CSV-звіт ще не створений. Спочатку виконайте вилучення зображень."
            )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MIL-EXTRACT")

    # Єдина іконка для панелі завдань, заголовка вікна та .exe.
    icon_path = resource_path(os.path.join("assets", "MIL-EXTRACT.ico"))
    if os.path.isfile(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)

    window = MainWindow()
    if os.path.isfile(icon_path):
        window.setWindowIcon(app_icon)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

