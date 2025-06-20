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
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
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

    def _build_ui(self):
        open_action = QAction("&Open", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file_dialog)
        exit_action = QAction("&Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(QApplication.instance().quit)
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        settings_action = QAction("&Settings", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        settings_menu = menubar.addMenu("&Settings")
        settings_menu.addAction(settings_action)

        central = QWidget(self)
        self.setCentralWidget(central)
        central_layout = QVBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        central_layout.addWidget(splitter)

        chapter_panel = QWidget()
        chapter_layout = QVBoxLayout(chapter_panel)
        select_all_btn = QPushButton("Select All")
        unselect_all_btn = QPushButton("Unselect All")
        chapter_layout.addWidget(select_all_btn)
        chapter_layout.addWidget(unselect_all_btn)
        self.chapter_list = QListWidget()
        self.chapter_list.itemSelectionChanged.connect(self.on_chapter_selected)
        chapter_layout.addWidget(self.chapter_list)
        splitter.addWidget(chapter_panel)
        select_all_btn.clicked.connect(self.select_all_chapters)
        unselect_all_btn.clicked.connect(self.unselect_all_chapters)

        right_container = QWidget()
        splitter.addWidget(right_container)
        right_layout = QVBoxLayout(right_container)

        self.text_edit = QTextEdit()
        right_layout.addWidget(self.text_edit)

        controls = QWidget()
        right_layout.addWidget(controls)
        controls_layout = QHBoxLayout(controls)

        self.preview_btn = QPushButton("Preview")
        self.preview_btn.clicked.connect(self.handle_preview_button)
        controls_layout.addWidget(self.preview_btn)
        self.preview_thread = None
        self.preview_stop_flag = threading.Event()

        self.wav_button = QPushButton("Select Voice WAV")
        self.wav_button.clicked.connect(self.select_wav)
        controls_layout.addWidget(self.wav_button)

        self.output_dir_edit = QLineEdit(os.path.abspath("."))
        self.output_dir_edit.setReadOnly(True)
        controls_layout.addWidget(self.output_dir_edit)
        output_btn = QPushButton("Select Output Folder")
        output_btn.clicked.connect(self.select_output_folder)
        controls_layout.addWidget(output_btn)

        controls_layout.addStretch()

        self.start_btn = QPushButton("Start Synthesis")
        self.start_btn.clicked.connect(self.start_synthesis)
        controls_layout.addWidget(self.start_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        right_layout.addWidget(self.progress_bar)

        splitter.setSizes([300, 900])

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
            self.preview_stop_flag.set()
            self.preview_btn.setText("Preview")
        else:
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
            self.settings.setValue("selected_wav_path", wav_path)

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.output_dir_edit.setText(folder)
            self.settings.setValue("output_folder", folder)

    def start_synthesis(self):
        print("Start synthesis clicked")
        if not self.selected_file_path:
            print("No file selected")
            QMessageBox.warning(self, "No file", "Please open an e-book first")
            return

        for i, chap in enumerate(self.document_chapters):
            item = self.chapter_list.item(i)
            chap.is_selected = item.checkState() == Qt.CheckState.Checked

        selected_chapters = [c for c in self.document_chapters if c.is_selected]
        if not selected_chapters:
            print("No chapters selected")
            QMessageBox.warning(self, "No chapters", "No chapters selected")
            return

        self.start_btn.setEnabled(False)

        params = dict(
            file_path=self.selected_file_path,
            pick_manually=False,
            speed=1.0,
            book_year=self.book_year,
            output_folder=self.output_dir_edit.text(),
            selected_chapters=selected_chapters,
            audio_prompt_wav=self.selected_wav_path,
        )
        print("Starting CoreThread with params:", params)

        self.core_thread = CoreThread(**params)
        self.core_thread.core_started.connect(self.on_core_started)
        self.core_thread.progress.connect(self.on_core_progress)
        self.core_thread.chapter_started.connect(self.on_core_chapter_started)
        self.core_thread.chapter_finished.connect(self.on_core_chapter_finished)
        self.core_thread.finished.connect(self.on_core_finished)
        self.core_thread.error.connect(self.on_core_error)
        self.core_thread.start()

    def on_core_started(self):
        self.progress_bar.setValue(0)

    def on_core_progress(self, stats: SimpleNamespace):
        self.progress_bar.setValue(int(stats.progress))

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
        QMessageBox.information(self, "Done", "Audiobook synthesis completed")
        out_dir = self.output_dir_edit.text()
        if platform.system() == "Windows":
            os.startfile(out_dir)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", out_dir])
        else:
            subprocess.Popen(["xdg-open", out_dir])

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

def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
