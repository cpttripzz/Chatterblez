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
    QSlider,
    QDoubleSpinBox,
    QGroupBox,
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
        self._should_stop = False

    def stop(self):
        self._should_stop = True

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
            core.main(**self.params, post_event=self.post_event, should_stop=lambda: self._should_stop)
        except Exception as exc:
            print("CoreThread exception:", exc)
            self.error.emit(str(exc))



# Move open_file_dialog back to MainWindow
    # ----------------- Menu slots -----------------
class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chatterblez – Audiobook Generator")
        self.resize(1200, 800)

        self.settings = QSettings("Chatterblez", "chatterblez-pyqt")
        self.document_chapters: list = []
        self.selected_file_path: str | None = None
        self.selected_wav_path: str | None = None
        self.core_thread: CoreThread | None = None

        self._build_ui()
        self.synth_running = False

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
        output_label = QLabel("Output Folder:")
        controls_layout.addWidget(output_label)
        self.output_dir_edit = QLineEdit(os.path.abspath("."))
        self.output_dir_edit.setReadOnly(True)
        controls_layout.addWidget(self.output_dir_edit)
        output_btn = QPushButton("Select Output Folder")
        output_btn.clicked.connect(self.select_output_folder)
        controls_layout.addWidget(output_btn)

        controls_layout.addStretch()

        # Start button
        self.start_btn = QPushButton("Start Synthesis")
        self.start_btn.clicked.connect(self.handle_start_stop_synthesis)
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
        # Task label (to the right of ETA)
        self.task_label = QLabel("")
        task_eta_layout = QHBoxLayout()
        task_eta_layout.addWidget(self.time_label)
        task_eta_layout.addWidget(self.task_label)
        task_eta_layout.addStretch()
        right_layout.addLayout(task_eta_layout)

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
        # Restore original panels if coming from batch mode
        if hasattr(self, "restore_original_panels"):
            self.restore_original_panels()
        self.selected_file_path = str(file_path)
        ext = file_path.suffix.lower()
        self.document_chapters.clear()
        self.chapter_list.clear()
        
        # Reset elapsed time when loading a new file
        self.time_label.setText("Elapsed: 00:00 | ETA: --:--")

        # Get ignore list from settings
        ignore_csv = self.settings.value("batch_ignore_chapter_names", "", type=str)
        ignore_list = [name.strip().lower() for name in ignore_csv.split(",") if name.strip()]

        if ext == ".epub":
            from ebooklib import epub
            book = epub.read_epub(str(file_path))
            self.document_chapters = core.find_document_chapters_and_extract_texts(book)
            good_chapters = core.find_good_chapters(self.document_chapters)
            for chap in self.document_chapters:
                chap_name_lower = chap.get_name().lower()
                is_ignored = any(ignore_name in chap_name_lower for ignore_name in ignore_list)
                chap.is_selected = chap in good_chapters and not is_ignored
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
        
        # Reset elapsed time when loading batch mode
        self.time_label.setText("Elapsed: 00:00 | ETA: --:--")
        
        # Show batch panel
        self.show_batch_panel(batch_files)

    def show_batch_panel(self, batch_files):
        # Store references to original panels before removing them
        self.original_panels = []
        for i in range(self.splitter.count()):
            widget = self.splitter.widget(i)
            self.original_panels.append(widget)
        
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


    def handle_start_stop_synthesis(self):
        if not self.synth_running:
            # Start synthesis
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
                    return
                # Get ignore list from settings
                ignore_csv = self.settings.value("batch_ignore_chapter_names", "", type=str)
                ignore_list = [name.strip() for name in ignore_csv.split(",") if name.strip()]

                # Write equivalent CLI command for batch mode
                self.write_cli_command(
                    batch_folder=os.path.dirname(selected_files[0]) if selected_files else "",
                    output_folder=self.output_dir_edit.text(),
                    filterlist=ignore_csv,
                    wav_path=self.selected_wav_path,
                    speed=1.0,
                    is_batch=True
                )

                # Batch progress bar and timer setup
                self.batch_progress_label.setText(f"Batch Progress: 0 / {len(selected_files)}")
                self.batch_progress_label.show()
                self.batch_progress_bar.setMaximum(len(selected_files))
                self.batch_progress_bar.setValue(0)
                self.batch_progress_bar.show()
                self.batch_start_time = time.time()

                # Start batch worker thread
                self.batch_worker = BatchWorker(
                    selected_files=selected_files,
                    output_dir=self.output_dir_edit.text(),
                    ignore_list=ignore_list,
                    wav_path=self.selected_wav_path,
                    repetition_penalty=self.settings.value('repetition_penalty', 1.2, type=float),
                    min_p=self.settings.value('min_p', 0.05, type=float),
                    top_p=self.settings.value('top_p', 1.0, type=float),
                    exaggeration=self.settings.value('exaggeration', 0.5, type=float),
                    cfg_weight=self.settings.value('cfg_weight', 0.5, type=float),
                    temperature=self.settings.value('temperature', 0.8, type=float)
                )
                self.batch_worker.progress_update.connect(self.on_batch_progress_update)
                self.batch_worker.chapter_progress.connect(self.on_core_progress)
                self.batch_worker.finished.connect(self.on_batch_finished)
                self.batch_worker.start()
                self.synth_running = True
                self.start_btn.setText("Stop Synthesizing")
                return
            else:
                if not selected_chapters:
                    print("No chapters selected after build. Aborting synthesis.")
                    QMessageBox.warning(self, "No chapters", "No chapters selected")
                    return
            if not selected_chapters:
                print("No chapters selected")
                QMessageBox.warning(self, "No chapters", "No chapters selected")
                return

            # Write equivalent CLI command for single file mode
            self.write_cli_command(
                file_path=self.selected_file_path,
                output_folder=self.output_dir_edit.text(),
                filterlist="",
                wav_path=self.selected_wav_path,
                speed=1.0,
                is_batch=False
            )

            print("About to create CoreThread with params:")
            params = dict(
                file_path=self.selected_file_path,
                pick_manually=False,
                speed=1.0,
                output_folder=self.output_dir_edit.text(),
                selected_chapters=selected_chapters,
                audio_prompt_wav=self.selected_wav_path,
                repetition_penalty=self.settings.value('repetition_penalty', 1.2, type=float),
                min_p=self.settings.value('min_p', 0.05, type=float),
                top_p=self.settings.value('top_p', 1.0, type=float),
                exaggeration=self.settings.value('exaggeration', 0.5, type=float),
                cfg_weight=self.settings.value('cfg_weight', 0.5, type=float),
                temperature=self.settings.value('temperature', 0.8, type=float),
            )
            print(params)
            try:
                self.core_thread = CoreThread(**params)
                self.core_thread.core_started.connect(self.on_core_started)
                self.core_thread.progress.connect(self.on_core_progress)
                self.core_thread.chapter_started.connect(self.on_core_chapter_started)
                self.core_thread.chapter_finished.connect(self.on_core_chapter_finished)
                self.core_thread.finished.connect(self.on_core_finished)
                self.core_thread.error.connect(self.on_core_error)
                self.core_thread.start()
                self.synth_running = True
                self.start_btn.setText("Stop Synthesizing")
            except Exception as e:
                print(f"Exception during CoreThread creation/start: {e}")
        else:
            # Stop synthesis
            print("Stop synthesis clicked")
            if self.core_thread is not None:
                self.core_thread.stop()
            # Stop batch worker if in batch mode
            if hasattr(self, "batch_worker") and self.batch_worker is not None:
                print("[DEBUG] MainWindow: calling batch_worker.stop()")
                self.batch_worker.stop()
            self.synth_running = False
            self.start_btn.setText("Start Synthesis")

# ----------------- Slots connected to CoreThread signals -----------------
    def on_core_started(self):
        self.progress_bar.setValue(0)
        self.start_time = time.time()
        self.time_label.setText("Elapsed: 00:00 | ETA: --:--")
        self.time_label.show()
        self.set_task_label("Synthesizing")

    def on_core_progress(self, stats: SimpleNamespace):
        self.progress_bar.setValue(int(stats.progress))
        # Update elapsed time and ETA
        if hasattr(self, "start_time"):
            elapsed = int(time.time() - self.start_time)
            days, remainder = divmod(elapsed, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)

            if days > 0:
                elapsed_str = f"{int(days)}d {int(hours):02d}h"
            elif hours > 0:
                elapsed_str = f"{int(hours):02d}h {int(minutes):02d}m"
            else:
                elapsed_str = f"{int(minutes):02d}:{int(seconds):02d}"
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
        self.synth_running = False
        self.start_btn.setText("Start Synthesis")
        self.set_task_label("")
        # To set "Transcoding" or "Multiplexing", call  or self.set_task_label("Multiplexing") at the appropriate place in your workflow.
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
        # open output folder
        if os.path.isdir(out_dir):
            if platform.system() == "Windows":
                os.startfile(out_dir)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", out_dir])
            else:
                subprocess.Popen(["xdg-open", out_dir])
        # Always show "All files completed" at the end
        elapsed_time = self.time_label.text().split(" | ")[0]
        QMessageBox.information(self, "All files completed", f"All files completed in {elapsed_time}")

    def on_core_error(self, message: str):
        self.synth_running = False
        self.start_btn.setText("Start Synthesis")
        print(f"Error: {message}")
        QMessageBox.critical(self, "Error", message)

    def set_task_label(self, task: str):
        """Set the current task label (e.g., Synthesizing, Transcoding, Multiplexing)."""
        self.task_label.setText(task)

    def write_cli_command(self, file_path=None, batch_folder=None, output_folder=".", filterlist="", wav_path=None, speed=1.0, is_batch=False):
        """
        Write the equivalent CLI command to last_cli_command.txt in the working directory.
        Returns the CLI command string.
        """
        def to_posix(path):
            return path.replace("\\", "/") if isinstance(path, str) else path

        cmd = ["python", "cli.py"]
        if is_batch:
            if batch_folder:
                cmd += ["--batch", f'"{to_posix(batch_folder)}"']
        else:
            if file_path:
                cmd += ["--file", f'"{to_posix(file_path)}"']
        if output_folder:
            cmd += ["--output", f'"{to_posix(output_folder)}"']
        if filterlist:
            cmd += ["--filterlist", f'"{filterlist}"']
        if wav_path:
            cmd += ["--wav", f'"{to_posix(wav_path)}"']
        if speed and speed != 1.0:
            cmd += ["--speed", str(speed)]
        cli_command = " ".join(cmd)
        print(f"cli_command: {cli_command}")
        try:
            with open("last_cli_command.txt", "w", encoding="utf-8") as f:
                f.write(cli_command + "\n")
        except Exception as e:
            print(f"Failed to write CLI command: {e}")
        return cli_command

from PyQt6.QtCore import pyqtSignal

class BatchWorker(QThread):
    progress_update = pyqtSignal(int, int, str, str)  # completed, total, elapsed_str, eta_str
    chapter_progress = pyqtSignal(object)  # stats object from core
    finished = pyqtSignal()

    def __init__(self, selected_files, output_dir, ignore_list, wav_path, repetition_penalty, min_p, top_p, exaggeration, cfg_weight, temperature):
        super().__init__()
        self.selected_files = selected_files
        self.output_dir = output_dir
        self.ignore_list = ignore_list
        self.wav_path = wav_path
        self.repetition_penalty = repetition_penalty
        self.min_p = min_p
        self.top_p = top_p
        self.exaggeration = exaggeration
        self.cfg_weight = cfg_weight
        self.temperature = temperature
        self._should_stop = False

    def stop(self):
        print("[DEBUG] BatchWorker.stop() called")
        self._should_stop = True

    def run(self):
        import core
        import time
        completed = 0
        total = len(self.selected_files)
        batch_start_time = time.time()

        def post_event(evt_name, **kwargs):
            if evt_name == "CORE_PROGRESS":
                stats = kwargs.get("stats")
                self.chapter_progress.emit(stats)

        for file_path in self.selected_files:
            if self._should_stop:
                print("[DEBUG] BatchWorker.run() detected stop, breaking batch loop")
                break
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
                if not any(ignore.lower() in c.get_name().lower() for ignore in self.ignore_list)
            ]
            # Run core.main for this file
            core.main(
                file_path=file_path,
                pick_manually=False,
                speed=1.0,
                output_folder=self.output_dir,
                selected_chapters=filtered_chapters,
                audio_prompt_wav=self.wav_path if self.wav_path else None,
                post_event=post_event,
                should_stop=lambda: self._should_stop,
                repetition_penalty=self.repetition_penalty,
                min_p=self.min_p,
                top_p=self.top_p,
                exaggeration=self.exaggeration,
                cfg_weight=self.cfg_weight,
                temperature=self.temperature
            )
            completed += 1
            now = time.time()
            elapsed = int(now - batch_start_time)
            days, remainder = divmod(elapsed, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)

            if days > 0:
                elapsed_str = f"{int(days)}d {int(hours):02d}h"
            elif hours > 0:
                elapsed_str = f"{int(hours):02d}h {int(minutes):02d}m"
            else:
                elapsed_str = f"{int(minutes):02d}:{int(seconds):02d}"
            if completed > 0:
                total_est = elapsed / completed
                eta = int(total_est * total - elapsed)
                eta_min = eta // 60
                eta_sec = eta % 60
                eta_str = f"{eta_min:02d}:{eta_sec:02d}"
            else:
                eta_str = "--:--"
            self.progress_update.emit(completed, total, elapsed_str, eta_str)
        self.finished.emit()

def on_batch_progress_update(self, completed, total, elapsed_str, eta_str):
    self.batch_progress_label.setText(f"Batch Progress: {completed} / {total}")
    self.batch_progress_bar.setValue(completed)
    self.time_label.setText(f"Batch Elapsed: {elapsed_str} | Batch ETA: {eta_str}")
    QApplication.processEvents()

def restore_original_panels(self):
    """Restore the original left and right panels after batch mode"""
    if hasattr(self, 'original_panels') and self.original_panels:
        # Remove batch panel
        if hasattr(self, 'batch_panel') and self.batch_panel:
            self.batch_panel.setParent(None)
        
        # Restore original panels
        for panel in self.original_panels:
            self.splitter.addWidget(panel)
        
        # Restore original sizes
        self.splitter.setSizes([300, 900])
        
        # Reassign right_panel to the correct widget in the splitter
        if self.splitter.count() > 1:
            self.right_panel = self.splitter.widget(1)
            for child in self.right_panel.findChildren(QWidget):
                child.show()
        
        # Clear the stored panels
        self.original_panels = []
        
        # Rebuild the UI to ensure all controls are present and visible
        self._build_ui()
        # Restore output folder path to the new output_dir_edit
        output_folder = self.settings.value("output_folder", "", type=str)
        if output_folder and hasattr(self, "output_dir_edit"):
            self.output_dir_edit.setText(output_folder)

def on_batch_finished(self):
    self.batch_progress_label.hide()
    self.batch_progress_bar.hide()
    
    # Restore original panels after batch mode
    self.restore_original_panels()
    self.set_task_label("")
    self.on_core_finished()

# Patch MainWindow to add batch progress handlers and restore method
MainWindow.on_batch_progress_update = on_batch_progress_update
MainWindow.restore_original_panels = restore_original_panels
MainWindow.on_batch_finished = on_batch_finished

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)
        self.settings = QSettings("chatterblez", "chatterblez-pyqt")

        layout = QVBoxLayout(self)

        # Batch Settings
        batch_group = QGroupBox("Batch Settings")
        batch_layout = QVBoxLayout(batch_group)
        chapter_names_label = QLabel("Comma separated values of chapter names to ignore:")
        batch_layout.addWidget(chapter_names_label)
        self.chapter_names_edit = QLineEdit()
        batch_layout.addWidget(self.chapter_names_edit)
        value = self.settings.value("batch_ignore_chapter_names", "", type=str)
        self.chapter_names_edit.setText(value)
        self.chapter_names_edit.textChanged.connect(self.save_chapter_names)
        batch_group.setLayout(batch_layout)
        layout.addWidget(batch_group)

        # Model Settings
        model_group = QGroupBox("Model Settings")
        model_layout = QVBoxLayout(model_group)

        # Repetition Penalty
        self.repetition_penalty_label = QLabel(f"Repetition Penalty: {self.settings.value('repetition_penalty', 1.2, type=float)}")
        model_layout.addWidget(self.repetition_penalty_label)
        self.repetition_penalty_slider = QSlider(Qt.Orientation.Horizontal)
        self.repetition_penalty_slider.setRange(10, 20)
        self.repetition_penalty_slider.setValue(int(self.settings.value('repetition_penalty', 1.2, type=float) * 10))
        self.repetition_penalty_slider.valueChanged.connect(self.update_repetition_penalty)
        model_layout.addWidget(self.repetition_penalty_slider)

        # Min P
        self.min_p_label = QLabel(f"Min P: {self.settings.value('min_p', 0.05, type=float)}")
        model_layout.addWidget(self.min_p_label)
        self.min_p_slider = QSlider(Qt.Orientation.Horizontal)
        self.min_p_slider.setRange(0, 100)
        self.min_p_slider.setValue(int(self.settings.value('min_p', 0.05, type=float) * 100))
        self.min_p_slider.valueChanged.connect(self.update_min_p)
        model_layout.addWidget(self.min_p_slider)

        # Top P
        self.top_p_label = QLabel(f"Top P: {self.settings.value('top_p', 1.0, type=float)}")
        model_layout.addWidget(self.top_p_label)
        self.top_p_slider = QSlider(Qt.Orientation.Horizontal)
        self.top_p_slider.setRange(0, 100)
        self.top_p_slider.setValue(int(self.settings.value('top_p', 1.0, type=float) * 100))
        self.top_p_slider.valueChanged.connect(self.update_top_p)
        model_layout.addWidget(self.top_p_slider)

        # Exaggeration
        self.exaggeration_label = QLabel(f"Exaggeration: {self.settings.value('exaggeration', 0.5, type=float)}")
        model_layout.addWidget(self.exaggeration_label)
        self.exaggeration_slider = QSlider(Qt.Orientation.Horizontal)
        self.exaggeration_slider.setRange(0, 100)
        self.exaggeration_slider.setValue(int(self.settings.value('exaggeration', 0.5, type=float) * 100))
        self.exaggeration_slider.valueChanged.connect(self.update_exaggeration)
        model_layout.addWidget(self.exaggeration_slider)

        # CFG Weight
        self.cfg_weight_label = QLabel(f"CFG Weight: {self.settings.value('cfg_weight', 0.5, type=float)}")
        model_layout.addWidget(self.cfg_weight_label)
        self.cfg_weight_slider = QSlider(Qt.Orientation.Horizontal)
        self.cfg_weight_slider.setRange(0, 100)
        self.cfg_weight_slider.setValue(int(self.settings.value('cfg_weight', 0.5, type=float) * 100))
        self.cfg_weight_slider.valueChanged.connect(self.update_cfg_weight)
        model_layout.addWidget(self.cfg_weight_slider)

        # Temperature
        self.temperature_label = QLabel(f"Temperature: {self.settings.value('temperature', 0.8, type=float)}")
        model_layout.addWidget(self.temperature_label)
        self.temperature_slider = QSlider(Qt.Orientation.Horizontal)
        self.temperature_slider.setRange(0, 100)
        self.temperature_slider.setValue(int(self.settings.value('temperature', 0.8, type=float) * 100))
        self.temperature_slider.valueChanged.connect(self.update_temperature)
        model_layout.addWidget(self.temperature_slider)

        model_group.setLayout(model_layout)
        layout.addWidget(model_group)

        btn_box = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        btn_box.addStretch()
        btn_box.addWidget(ok_btn)
        layout.addLayout(btn_box)

    def save_chapter_names(self, text):
        self.settings.setValue("batch_ignore_chapter_names", text)

    def update_repetition_penalty(self, value):
        val = value / 10.0
        self.repetition_penalty_label.setText(f"Repetition Penalty: {val:.2f}")
        self.settings.setValue("repetition_penalty", val)

    def update_min_p(self, value):
        val = value / 100.0
        self.min_p_label.setText(f"Min P: {val:.2f}")
        self.settings.setValue("min_p", val)

    def update_top_p(self, value):
        val = value / 100.0
        self.top_p_label.setText(f"Top P: {val:.2f}")
        self.settings.setValue("top_p", val)

    def update_exaggeration(self, value):
        val = value / 100.0
        self.exaggeration_label.setText(f"Exaggeration: {val:.2f}")
        self.settings.setValue("exaggeration", val)

    def update_cfg_weight(self, value):
        val = value / 100.0
        self.cfg_weight_label.setText(f"CFG Weight: {val:.2f}")
        self.settings.setValue("cfg_weight", val)

    def update_temperature(self, value):
        val = value / 100.0
        self.temperature_label.setText(f"Temperature: {val:.2f}")
        self.settings.setValue("temperature", val)

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