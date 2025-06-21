#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# A PyQt6 UI for audiblez

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import threading
import PyPDF2
import time
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QSettings
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QCheckBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
    QDialog,
)

import core

class CoreThread(QThread):
    core_started = pyqtSignal()
    progress = pyqtSignal(object)
    chapter_started = pyqtSignal(int)
    chapter_finished = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, **params):
        super().__init__()
        self.params = params

    def post_event(self, evt_name: str, **kwargs):
        if evt_name == "CORE_STARTED":
            self.core_started.emit()
        elif evt_name == "CORE_PROGRESS":
            self.progress.emit(kwargs["stats"])
        elif evt_name == "CORE_CHAPTER_STARTED":
            self.chapter_started.emit(kwargs.get("chapter_index", -1))
        elif evt_name == "CORE_CHAPTER_FINISHED":
            self.chapter_finished.emit(kwargs.get("chapter_index", -1))
        elif evt_name == "CORE_FINISHED":
            self.finished.emit()
        elif evt_name == "CORE_ERROR":
            self.error.emit(kwargs.get("message", "Unknown error"))

    def run(self):
        try:
            print("CoreThread started with params:", self.params)
            core.main(**self.params, post_event=self.post_event)
        except Exception as exc:
            print("CoreThread exception:", exc)
            self.error.emit(str(exc))



# Move open_file_dialog back to MainWindow
    # ----------------- Menu slots -----------------
class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audiblez – Audiobook Generator (PyQt6)")
        self.resize(1200, 800)

        self.settings = QSettings("audiblez", "audiblez-pyqt")
        self.document_chapters: list = []
        self.selected_file_path: str | None = None
        self.selected_wav_path: str | None = None
        self.book_year: str = ""
        self.core_thread: CoreThread | None = None

        self._build_ui()

        wav_path = self.settings.value("selected_wav_path", "", type=str)
        if wav_path:
            self.selected_wav_path = wav_path
            self.wav_button.setText(Path(wav_path).name)
        output_folder = self.settings.value("output_folder", "", type=str)
        if output_folder:
            self.output_dir_edit.setText(output_folder)

        # ----------------- UI BUILD -----------------

    def _build_ui(self):
        # Menu
        open_action = QAction("&Open", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file_dialog)
        exit_action = QAction("&Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(QApplication.instance().quit)
        batch_action = QAction("&Batch Mode", self)
        batch_action.setShortcut("Ctrl+B")
        batch_action.triggered.connect(self.open_batch_mode)
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        file_menu.addAction(batch_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        # Settings menu
        settings_action = QAction("&Settings", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        settings_menu = menubar.addMenu("&Settings")
        settings_menu.addAction(settings_action)

        # Central widget
        central = QWidget(self)
        self.setCentralWidget(central)
        central_layout = QVBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        central_layout.addWidget(splitter)
        self.splitter = splitter  # For batch mode panel replacement

        # Left pane – chapters list with select/unselect all buttons
        chapter_panel = QWidget()
        chapter_layout = QVBoxLayout(chapter_panel)
        # Buttons
        select_all_btn = QPushButton("Select All")
        unselect_all_btn = QPushButton("Unselect All")
        chapter_layout.addWidget(select_all_btn)
        chapter_layout.addWidget(unselect_all_btn)
        # Chapter list
        self.chapter_list = QListWidget()
        self.chapter_list.itemSelectionChanged.connect(self.on_chapter_selected)
        chapter_layout.addWidget(self.chapter_list)
        splitter.addWidget(chapter_panel)
        self.left_panel = chapter_panel  # Store reference to left panel
        # Connect buttons
        select_all_btn.clicked.connect(self.select_all_chapters)
        unselect_all_btn.clicked.connect(self.unselect_all_chapters)

        # Right pane
        right_container = QWidget()
        splitter.addWidget(right_container)
        self.right_panel = right_container  # Store reference to right panel
        right_layout = QVBoxLayout(right_container)

        # Text edit
        self.text_edit = QTextEdit()
        right_layout.addWidget(self.text_edit)

        # Controls pane
        controls = QWidget()
        right_layout.addWidget(controls)
        controls_layout = QHBoxLayout(controls)

        # Preview button (replaces Speed)
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.clicked.connect(self.handle_preview_button)
        controls_layout.addWidget(self.preview_btn)
        self.preview_thread = None
        self.preview_stop_flag = threading.Event()

        # WAV button
        self.wav_button = QPushButton("Select Voice WAV")
        self.wav_button.clicked.connect(self.select_wav)
        controls_layout.addWidget(self.wav_button)

        # Output dir
        self.output_dir_edit = QLineEdit(os.path.abspath("."))
        self.output_dir_edit.setReadOnly(True)
        controls_layout.addWidget(self.output_dir_edit)
        output_btn = QPushButton("Select Output Folder")
        output_btn.clicked.connect(self.select_output_folder)
        controls_layout.addWidget(output_btn)

        controls_layout.addStretch()

        # Start button
        self.start_btn = QPushButton("Start Synthesis")
        self.start_btn.clicked.connect(self.start_synthesis)
        controls_layout.addWidget(self.start_btn)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        right_layout.addWidget(self.progress_bar)

        # Batch progress bar and label (hidden by default)
        self.batch_progress_label = QLabel("Batch Progress:")
        self.batch_progress_label.hide()
        right_layout.addWidget(self.batch_progress_label)
        self.batch_progress_bar = QProgressBar()
        self.batch_progress_bar.setMaximum(100)
        self.batch_progress_bar.hide()
        right_layout.addWidget(self.batch_progress_bar)

        # Time/ETA label
        self.time_label = QLabel("Elapsed: 00:00 | ETA: --:--")
        right_layout.addWidget(self.time_label)

        splitter.setSizes([300, 900])

        # ----------------- Settings Dialog -----------------

    def open_settings_dialog(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open e-book",
            "",
            "E-books (*.epub *.pdf);;All files (*)",
        )
        if file_path:
            self.load_ebook(Path(file_path))

    # ----------------- Load e-book -----------------
    def load_ebook(self, file_path: Path):
        self.selected_file_path = str(file_path)
        ext = file_path.suffix.lower()
        self.document_chapters.clear()
        self.chapter_list.clear()

        if ext == ".epub":
            from ebooklib import epub
            book = epub.read_epub(str(file_path))
            self.document_chapters = core.find_document_chapters_and_extract_texts(book)
            good_chapters = core.find_good_chapters(self.document_chapters)
            for chap in self.document_chapters:
                chap.is_selected = chap in good_chapters
                item = QListWidgetItem(chap.get_name())
                item.setCheckState(Qt.CheckState.Checked if chap.is_selected else Qt.CheckState.Unchecked)
                self.chapter_list.addItem(item)
        elif ext == ".pdf":
            self.load_pdf(file_path)
        else:
            QMessageBox.warning(self, "Unsupported", "File type not supported")
            return

        if self.document_chapters:
            self.chapter_list.setCurrentRow(0)

    def load_pdf(self, file_path: Path):
        import PyPDF2
        pdf_reader = PyPDF2.PdfReader(str(file_path))
        chapters = []
        class PDFChapter:
            def __init__(self, name, text, idx):
                self._name = name
                self.extracted_text = text
                self.chapter_index = idx
                self.is_selected = True
            def get_name(self):
                return self._name
        buffer = ""
        idx = 0
        for i, page in enumerate(pdf_reader.pages):
            buffer += (page.extract_text() or "") + "\n"
            if len(buffer) >= 5000 or i == len(pdf_reader.pages) - 1:
                chapters.append(PDFChapter(f"Pages {idx + 1}-{i + 1}", buffer.strip(), idx))
                buffer = ""
                idx += 1
        self.document_chapters = chapters
        for chap in chapters:
            item = QListWidgetItem(chap.get_name())
            item.setCheckState(Qt.CheckState.Checked)
            self.chapter_list.addItem(item)

    # ----------------- Batch Mode -----------------
    def open_batch_mode(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder with e-books")
        if not folder:
            return
        supported_exts = [".epub", ".pdf"]
        files = [
            str(Path(folder) / f)
            for f in os.listdir(folder)
            if os.path.isfile(str(Path(folder) / f)) and os.path.splitext(f)[1].lower() in supported_exts
        ]
        if not files:
            QMessageBox.information(self, "No Files", "No supported files (.epub, .pdf) found in the selected folder.")
            return
        batch_files = [{"path": f, "selected": True, "year": ""} for f in files]
        # Try to load batch state from disk and merge
        import json
        try:
            with open("batch_state.json", "r", encoding="utf-8") as f:
                saved_batch = json.load(f)
            saved_map = {item["path"]: item for item in saved_batch}
            for fileinfo in batch_files:
                if fileinfo["path"] in saved_map:
                    fileinfo.update({k: v for k, v in saved_map[fileinfo["path"]].items() if k in ("title", "year")})
        except Exception:
            pass
        # Set self.batch_files so it is available in start_synthesis
        self.batch_files = batch_files
        # Show batch panel
        self.show_batch_panel(batch_files)

    def show_batch_panel(self, batch_files):
        # Remove all widgets from splitter
        for i in reversed(range(self.splitter.count())):
            widget = self.splitter.widget(i)
            self.splitter.widget(i).setParent(None)
        # Create a vertical panel with batch table and controls
        batch_panel = QWidget()
        layout = QVBoxLayout(batch_panel)
        batch_files_panel = BatchFilesPanel(batch_files, parent=self)
        layout.addWidget(batch_files_panel)
        # Controls panel (copied from right panel)
        controls_panel = QWidget()
        controls_layout = QVBoxLayout(controls_panel)
        # No text edit in batch mode
        # Controls row
        controls_row = QWidget()
        controls_row_layout = QHBoxLayout(controls_row)
        controls_row_layout.addWidget(self.preview_btn)
        controls_row_layout.addWidget(self.wav_button)
        controls_row_layout.addWidget(self.output_dir_edit)
        controls_row_layout.addWidget(self.start_btn)
        controls_row_layout.addStretch()
        controls_row_layout.addWidget(self.progress_bar)
        controls_row_layout.addWidget(self.batch_progress_label)
        controls_row_layout.addWidget(self.batch_progress_bar)
        controls_row_layout.addWidget(self.time_label)
        controls_panel.setLayout(controls_layout)
        controls_layout.addWidget(controls_row)
        layout.addWidget(controls_panel)
        self.splitter.addWidget(batch_panel)
        self.splitter.setSizes([400, 800])
        self.batch_panel = batch_panel

# ----------------- UI callbacks -----------------
    def select_all_chapters(self):
        for i in range(self.chapter_list.count()):
            item = self.chapter_list.item(i)
            item.setCheckState(Qt.CheckState.Checked)
            if 0 <= i < len(self.document_chapters):
                self.document_chapters[i].is_selected = True

    def unselect_all_chapters(self):
        for i in range(self.chapter_list.count()):
            item = self.chapter_list.item(i)
            item.setCheckState(Qt.CheckState.Unchecked)
            if 0 <= i < len(self.document_chapters):
                self.document_chapters[i].is_selected = False

    def on_chapter_selected(self):
        row = self.chapter_list.currentRow()
        if 0 <= row < len(self.document_chapters):
            self.text_edit.setPlainText(self.document_chapters[row].extracted_text)

    def handle_preview_button(self):
        if self.preview_thread and self.preview_thread.is_alive():
            # Stop preview
            self.preview_stop_flag.set()
            self.preview_btn.setText("Preview")
        else:
            # Start preview
            self.preview_stop_flag.clear()
            self.preview_btn.setText("Stop Preview")
            self.preview_thread = threading.Thread(target=self.preview_chapter_thread)
            self.preview_thread.start()

    def preview_chapter_thread(self):
        try:
            from tempfile import NamedTemporaryFile
            import torch
            from chatterbox.tts import ChatterboxTTS
            import core

            row = self.chapter_list.currentRow()
            if not (0 <= row < len(self.document_chapters)):
                print("Preview Unavailable: No chapter selected.")
                QMessageBox.information(self, "Preview Unavailable", "No chapter selected.")
                self.preview_btn.setText("Preview")
                return
            chapter = self.document_chapters[row]
            text = chapter.extracted_text[:1000]
            # Clean text: remove disallowed chars, keep only lines with words
            cleaned_lines = []
            for line in text.splitlines():
                cleaned_line = core.allowed_chars_re.sub('', line)
                if cleaned_line.strip() and re.search(r'\w', cleaned_line):
                    cleaned_lines.append(cleaned_line)
            text = "\n".join(cleaned_lines)
            if not text.strip():
                print("Preview Unavailable: No text to preview.")
                QMessageBox.information(self, "Preview Unavailable", "No text to preview.")
                self.preview_btn.setText("Preview")
                return

            device = "cuda" if torch.cuda.is_available() else "cpu"
            cb_model = ChatterboxTTS.from_pretrained(device=device)
            if self.selected_wav_path:
                cb_model.prepare_conditionals(wav_fpath=self.selected_wav_path)
            torch.manual_seed(12345)
            sentences = re.split(r'(?<=[.!?])\s+', text)
            chunks = [sent.strip() for sent in sentences if sent.strip()]
            if not chunks:
                chunks = [text[i:i+50] for i in range(0, len(text), 50)]
            for chunk in chunks:
                if self.preview_stop_flag.is_set():
                    break
                wav = cb_model.generate(chunk)
                with NamedTemporaryFile(suffix=".wav", delete=False) as tmpf:
                    import torchaudio as ta
                    ta.save(tmpf.name, wav, cb_model.sr)
                    tmpf.flush()
                    # Play using OS default player
                    if self.preview_stop_flag.is_set():
                        break
                    if platform.system() == "Windows":
                        os.startfile(tmpf.name)
                    elif platform.system() == "Darwin":
                        subprocess.Popen(["afplay", tmpf.name])
                    else:
                        subprocess.Popen(["aplay", tmpf.name])
        except Exception as e:
            print(f"Preview Error: {e}")
            QMessageBox.critical(self, "Preview Error", f"Preview failed: {e}")
        finally:
            self.preview_btn.setText("Preview")

    def select_wav(self):
        wav_path, _ = QFileDialog.getOpenFileName(
            self, "Select WAV file", "", "Wave files (*.wav)"
        )
        if wav_path:
            self.selected_wav_path = wav_path
            self.wav_button.setText(Path(wav_path).name)
            # Save to persistent settings
            self.settings.setValue("selected_wav_path", wav_path)

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.output_dir_edit.setText(folder)
            # Save to persistent settings
            self.settings.setValue("output_folder", folder)


    def start_synthesis(self):
        print("Start synthesis clicked")
        if not self.selected_file_path and not (hasattr(self, "batch_files") and self.batch_files):
            print("No file selected")
            QMessageBox.warning(self, "No file", "Please open an e-book first")
            return

        # update chapter selection flags
        for i, chap in enumerate(self.document_chapters):
            item = self.chapter_list.item(i)
            chap.is_selected = item.checkState() == Qt.CheckState.Checked

        selected_chapters = [c for c in self.document_chapters if c.is_selected]
        if hasattr(self, "batch_files") and self.batch_files:
            selected_files = [f["path"] for f in self.batch_files if f["selected"]]
            if not selected_files:
                QMessageBox.information(self, "No Files", "No files selected for batch synthesis.")
                self.start_btn.setEnabled(True)
                return
            # Get ignore list from settings
            ignore_csv = self.settings.value("batch_ignore_chapter_names", "", type=str)
            ignore_list = [name.strip() for name in ignore_csv.split(",") if name.strip()]

            # Batch progress bar and timer setup
            self.batch_progress_label.setText(f"Batch Progress: 0 / {len(selected_files)}")
            self.batch_progress_label.show()
            self.batch_progress_bar.setMaximum(len(selected_files))
            self.batch_progress_bar.setValue(0)
            self.batch_progress_bar.show()
            self.batch_start_time = time.time()
            completed = 0

            for file_path in selected_files:
                file_start_time = time.time()
                ext = os.path.splitext(file_path)[1].lower()
                chapters = []
                if ext == ".epub":
                    from ebooklib import epub
                    book = epub.read_epub(file_path)
                    chapters = core.find_document_chapters_and_extract_texts(book)
                elif ext == ".pdf":
                    import PyPDF2
                    pdf_reader = PyPDF2.PdfReader(file_path)
                    class PDFChapter:
                        def __init__(self, name, text, idx):
                            self._name = name
                            self.extracted_text = text
                            self.chapter_index = idx
                            self.is_selected = True
                        def get_name(self):
                            return self._name
                    buffer = ""
                    idx = 0
                    for i, page in enumerate(pdf_reader.pages):
                        buffer += (page.extract_text() or "") + "\n"
                        if len(buffer) >= 5000 or i == len(pdf_reader.pages) - 1:
                            chapters.append(PDFChapter(f"Pages {idx + 1}-{i + 1}", buffer.strip(), idx))
                            buffer = ""
                            idx += 1
                # Filter chapters
                filtered_chapters = [
                    c for c in chapters
                    if not any(ignore.lower() in c.get_name().lower() for ignore in ignore_list)
                ]

                print(f"Starting Audiobook Synthesis (batch) for {file_path} with {len(filtered_chapters)} chapters (ignored: {ignore_list})")

                # Custom progress handler for per-file progress, updating batch elapsed/eta
                def make_progress_handler(batch_completed, batch_total, batch_start_time):
                    def handler(stats):
                        now = time.time()
                        elapsed = int(now - batch_start_time)
                        elapsed_min = elapsed // 60
                        elapsed_sec = elapsed % 60
                        elapsed_str = f"{elapsed_min:02d}:{elapsed_sec:02d}"
                        # Estimate ETA for batch
                        done = batch_completed + stats.progress / 100.0
                        if done > 0:
                            total_est = elapsed / done
                            eta = int(total_est * batch_total - elapsed)
                            eta_min = eta // 60
                            eta_sec = eta % 60
                            eta_str = f"{eta_min:02d}:{eta_sec:02d}"
                        else:
                            eta_str = "--:--"
                        self.time_label.setText(f"Batch Elapsed: {elapsed_str} | Batch ETA: {eta_str}")
                        QApplication.processEvents()
                    return handler

                core_thread = CoreThread(
                    file_path=file_path,
                    pick_manually=False,
                    speed=1.0,
                    book_year="",
                    output_folder=self.output_dir_edit.text(),
                    selected_chapters=filtered_chapters,
                    audio_prompt_wav=self.selected_wav_path if self.selected_wav_path else None
                )
                # Connect progress to batch-aware handler
                core_thread.progress.connect(
                    make_progress_handler(completed, len(selected_files), self.batch_start_time)
                )
                core_thread.start()
                core_thread.wait()
                completed += 1
                # Update batch progress and time after each file
                now = time.time()
                elapsed = int(now - self.batch_start_time)
                elapsed_min = elapsed // 60
                elapsed_sec = elapsed % 60
                elapsed_str = f"{elapsed_min:02d}:{elapsed_sec:02d}"
                if completed > 0:
                    total_est = elapsed / completed
                    eta = int(total_est * len(selected_files) - elapsed)
                    eta_min = eta // 60
                    eta_sec = eta % 60
                    eta_str = f"{eta_min:02d}:{eta_sec:02d}"
                else:
                    eta_str = "--:--"
                self.batch_progress_label.setText(f"Batch Progress: {completed} / {len(selected_files)}")
                self.batch_progress_bar.setValue(completed)
                self.time_label.setText(f"Batch Elapsed: {elapsed_str} | Batch ETA: {eta_str}")
                QApplication.processEvents()
            self.batch_progress_label.hide()
            self.batch_progress_bar.hide()
            self.time_label.setText("Elapsed: 00:00 | ETA: --:--")
            self.on_core_finished()
            return

        if not selected_chapters:
            print("No chapters selected")
            QMessageBox.warning(self, "No chapters", "No chapters selected")
            return

        self.start_btn.setEnabled(False)

        print("Starting CoreThread with params:", dict(
            file_path=self.selected_file_path,
            pick_manually=False,
            speed=1.0,
            book_year=self.book_year,
            output_folder=self.output_dir_edit.text(),
            selected_chapters=selected_chapters,
            audio_prompt_wav=self.selected_wav_path,
        ))

        self.core_thread = CoreThread(
            file_path=self.selected_file_path,
            pick_manually=False,
            speed=1.0,
            book_year=self.book_year,
            output_folder=self.output_dir_edit.text(),
            selected_chapters=selected_chapters,
            audio_prompt_wav=self.selected_wav_path,
        )
        self.core_thread.core_started.connect(self.on_core_started)
        self.core_thread.progress.connect(self.on_core_progress)
        self.core_thread.chapter_started.connect(self.on_core_chapter_started)
        self.core_thread.chapter_finished.connect(self.on_core_chapter_finished)
        self.core_thread.finished.connect(self.on_core_finished)
        self.core_thread.error.connect(self.on_core_error)
        self.core_thread.start()

# ----------------- Slots connected to CoreThread signals -----------------
    def on_core_started(self):
        self.progress_bar.setValue(0)
        self.start_time = time.time()
        self.time_label.setText("Elapsed: 00:00 | ETA: --:--")
        self.time_label.show()

    def on_core_progress(self, stats: SimpleNamespace):
        self.progress_bar.setValue(int(stats.progress))
        # Update elapsed time and ETA
        if hasattr(self, "start_time"):
            elapsed = int(time.time() - self.start_time)
            elapsed_min = elapsed // 60
            elapsed_sec = elapsed % 60
            elapsed_str = f"{elapsed_min:02d}:{elapsed_sec:02d}"
        else:
            elapsed_str = "00:00"
        eta_str = getattr(stats, "eta", "--:--")
        self.time_label.setText(f"Elapsed: {elapsed_str} | ETA: {eta_str}")

    def on_core_chapter_started(self, idx: int):
        if 0 <= idx < self.chapter_list.count():
            item = self.chapter_list.item(idx)
            item.setText(f"{item.text()} (working)")

    def on_core_chapter_finished(self, idx: int):
        if 0 <= idx < self.chapter_list.count():
            item = self.chapter_list.item(idx)
            txt = item.text().split("(working)")[0].strip()
            item.setText(f"{txt} ✔")

    def on_core_finished(self):
        self.progress_bar.setValue(100)
        self.start_btn.setEnabled(True)
        # Delete all .wav files in the output folder with extra debug output
        import glob
        out_dir = os.path.abspath(self.output_dir_edit.text())
        print(f"[DEBUG] Output directory: {out_dir}")
        if not os.path.isdir(out_dir):
            print(f"[DEBUG] Output directory does not exist: {out_dir}")
        else:
            all_files = os.listdir(out_dir)
            print(f"[DEBUG] Files in output directory before deletion: {all_files}")
            wav_files = [os.path.join(out_dir, f) for f in all_files if f.lower().endswith('.wav')]
            print(f"[DEBUG] .wav files to delete: {wav_files}")
            for wav_file in wav_files:
                try:
                    os.remove(wav_file)
                    print(f"[DEBUG] Deleted: {wav_file}")
                except Exception as e:
                    print(f"[DEBUG] Failed to delete {wav_file}: {e}")
            all_files_after = os.listdir(out_dir)
            print(f"[DEBUG] Files in output directory after deletion: {all_files_after}")
        self.time_label.setText("Elapsed: 00:00 | ETA: --:--")
        # open output folder
        if os.path.isdir(out_dir):
            if platform.system() == "Windows":
                os.startfile(out_dir)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", out_dir])
            else:
                subprocess.Popen(["xdg-open", out_dir])
        # Always show "All files completed" at the end
        QMessageBox.information(self, "All files completed", "All files completed")

    def on_core_error(self, message: str):
        self.start_btn.setEnabled(True)
        print(f"Error: {message}")
        QMessageBox.critical(self, "Error", message)

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        batch_label = QLabel("<b>Batch Settings</b>")
        layout.addWidget(batch_label)
        chapter_names_label = QLabel("Comma separated values of chapter names to ignore:")
        layout.addWidget(chapter_names_label)
        self.chapter_names_edit = QLineEdit()
        layout.addWidget(self.chapter_names_edit)
        settings = QSettings("audiblez", "audiblez-pyqt")
        value = settings.value("batch_ignore_chapter_names", "", type=str)
        self.chapter_names_edit.setText(value)
        self.chapter_names_edit.textChanged.connect(self.save_chapter_names)
        btn_box = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        btn_box.addStretch()
        btn_box.addWidget(ok_btn)
        layout.addLayout(btn_box)
    def save_chapter_names(self, text):
        settings = QSettings("audiblez", "audiblez-pyqt")
        settings.setValue("batch_ignore_chapter_names", text)

class BatchFilesPanel(QWidget):
    def __init__(self, batch_files, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.batch_files = batch_files
        self.selected_row = 0
        layout = QVBoxLayout(self)

        title = QLabel("Select files to include in batch synthesis:")
        layout.addWidget(title)

        # Table
        self.table = QTableWidget(len(batch_files), 3)
        self.table.setHorizontalHeaderLabels(["Included", "File Name", "File Path"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i, fileinfo in enumerate(batch_files):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(fileinfo.get("selected", True))
            cb.stateChanged.connect(lambda state, row=i: self.set_selected(row, state))
            self.table.setCellWidget(i, 0, cb)
            # File name
            fname = os.path.basename(fileinfo["path"])
            self.table.setItem(i, 1, QTableWidgetItem(fname))

            # File path
            self.table.setItem(i, 2, QTableWidgetItem(fileinfo["path"]))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.selectRow(0)

        layout.addWidget(self.table)

        # Select All / Unselect All
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        unselect_all_btn = QPushButton("Unselect All")
        select_all_btn.clicked.connect(self.select_all)
        unselect_all_btn.clicked.connect(self.unselect_all)
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(unselect_all_btn)
        layout.addLayout(btn_layout)

    def set_selected(self, row, state):
        self.batch_files[row]["selected"] = bool(state)


    def on_selection_changed(self):
        selected = self.table.currentRow()
        self.selected_row = selected

    def select_all(self):
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            cb.setChecked(True)
            self.batch_files[i]["selected"] = True

    def unselect_all(self):
        for i in range(self.table.rowCount()):
            cb = self.table.cellWidget(i, 0)
            cb.setChecked(False)
            self.batch_files[i]["selected"] = False


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
