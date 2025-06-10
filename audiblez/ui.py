#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# A simple wxWidgets UI for audiblez

import re
import torch.cuda
import numpy as np
import soundfile
import threading
import platform
import subprocess
import io
import os
import wx
from wx.lib.newevent import NewEvent
from wx.lib.scrolledpanel import ScrolledPanel
from PIL import Image
from tempfile import NamedTemporaryFile
from pathlib import Path
import wikipedia
import json

from audiblez.voices import voices, flags
import audiblez.core as core # Import core to access probe_duration etc.

EVENTS = {
    'CORE_STARTED': NewEvent(),
    'CORE_PROGRESS': NewEvent(),
    'CORE_CHAPTER_STARTED': NewEvent(),
    'CORE_CHAPTER_FINISHED': NewEvent(),
    'CORE_FINISHED': NewEvent()
}

border = 5


class MainWindow(wx.Frame):
    def __init__(self, parent, title):
        screen_width, screen_h = wx.GetDisplaySize()
        self.window_width = int(screen_width * 0.6)
        super().__init__(parent, title=title, size=(self.window_width, self.window_width * 3 // 4))
        self.chapters_panel = None
        self.preview_threads = []
        self.table = None
        self.selected_chapter = None
        self.selected_book = None
        self.synthesis_in_progress = False

        self.Bind(EVENTS['CORE_STARTED'][1], self.on_core_started)
        self.Bind(EVENTS['CORE_CHAPTER_STARTED'][1], self.on_core_chapter_started)
        self.Bind(EVENTS['CORE_CHAPTER_FINISHED'][1], self.on_core_chapter_finished)
        self.Bind(EVENTS['CORE_PROGRESS'][1], self.on_core_progress)
        self.Bind(EVENTS['CORE_FINISHED'][1], self.on_core_finished)

        self.config = wx.Config("Audiblez") # Create a config object

        self.create_menu()
        self.create_layout()
        self.Centre()
        self.Show(True)
        if Path('../epub/lewis.epub').exists(): self.open_epub('../epub/lewis.epub')

    def create_menu(self):
        menubar = wx.MenuBar()
        file_menu = wx.Menu()
        open_item = wx.MenuItem(file_menu, wx.ID_OPEN, "&Open\tCtrl+O")
        file_menu.Append(open_item)
        self.Bind(wx.EVT_MENU, self.on_open, open_item)  # Bind the event

        exit_item = wx.MenuItem(file_menu, wx.ID_EXIT, "&Exit\tCtrl+Q")
        file_menu.Append(exit_item)
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)

        menubar.Append(file_menu, "&File")
        self.SetMenuBar(menubar)

    def on_core_started(self, event):
        print('CORE_STARTED')
        self.progress_bar_label.Show()
        self.progress_bar.Show()
        self.progress_bar.SetValue(0)
        self.progress_bar.Layout()
        self.eta_label.Show()
        self.params_panel.Layout()
        self.synth_panel.Layout()

    def on_core_chapter_started(self, event):
        # print('CORE_CHAPTER_STARTED', event.chapter_index)
        self.set_table_chapter_status(event.chapter_index, "⏳ In Progress")

    def on_core_chapter_finished(self, event):
        # print('CORE_CHAPTER_FINISHED', event.chapter_index)
        self.set_table_chapter_status(event.chapter_index, "✅ Done")
        self.start_button.Show()

    def on_core_progress(self, event):
        # print('CORE_PROGRESS', event.progress)
        self.progress_bar.SetValue(event.stats.progress)
        if hasattr(event.stats, "stage") and event.stats.stage == "ffmpeg":
            self.progress_bar_label.SetLabel(f"Multiplexing Progress: {event.stats.progress}%")
        else:
            self.progress_bar_label.SetLabel(f"Synthesis Progress: {event.stats.progress}%")
        if hasattr(event.stats, "eta") and event.stats.eta is not None:
            self.eta_label.SetLabel(f"Estimated Time Remaining: {event.stats.eta}")
        else:
            self.eta_label.SetLabel("Estimated Time Remaining: --")
        self.synth_panel.Layout()

    def on_core_finished(self, event):
        self.synthesis_in_progress = False
        self.open_folder_with_explorer(self.output_folder_text_ctrl.GetValue())
        # Delete all .wav files in the output folder on success
        output_folder = self.output_folder_text_ctrl.GetValue()
        import glob
        wav_files = glob.glob(os.path.join(output_folder, "*.wav"))
        for wav_file in wav_files:
            try:
                os.remove(wav_file)
            except Exception as e:
                print(f"Failed to delete {wav_file}: {e}")
        elapsed = None
        if hasattr(self, "synthesis_start_time"):
            elapsed = time.time() - self.synthesis_start_time
            mins, secs = divmod(int(elapsed), 60)
            hours, mins = divmod(mins, 60)
            elapsed_str = f"{hours}h {mins}m {secs}s" if hours else (f"{mins}m {secs}s" if mins else f"{secs}s")
            msg = f"Audiobook synthesis completed successfully!\nTotal time elapsed: {elapsed_str}"
        else:
            msg = "Audiobook synthesis completed successfully!"
        wx.MessageBox(msg, "Success", style=wx.OK | wx.ICON_INFORMATION)
        # Clear progress bar, label, and ETA
        self.progress_bar.SetValue(0)
        self.progress_bar_label.SetLabel("Synthesis Progress:")
        self.progress_bar_label.Hide()
        self.progress_bar.Hide()
        self.eta_label.SetLabel("Estimated Time Remaining: ")
        self.eta_label.Hide()
        self.synth_panel.Layout()
        # Re-enable controls
        if hasattr(self, "start_button"):
            self.start_button.Enable()
        if hasattr(self, "params_panel"):
            self.params_panel.Enable()
        if hasattr(self, "table"):
            self.table.EnableCheckBoxes(True)

    def create_layout(self):
        # Panels layout looks like this:
        # splitter
        #    splitter_left
        #        chapters_panel
        #    splitter_right
        #        center_panel
        #            text_area
        #        right_panel
        #            book_info_panel_box
        #                book_info_panel
        #                    cover_bitmap
        #                    book_details_panel
        #            param_panel_box
        #                    param_panel
        #                        ...
        #            synth_panel_box
        #                    synth_panel
        #                        start_button
        #                        ...

        top_panel = wx.Panel(self)
        top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        top_panel.SetSizer(top_sizer)

        # Open Epub button
        open_epub_button = wx.Button(top_panel, label="📁 Open File")
        open_epub_button.Bind(wx.EVT_BUTTON, self.on_open)
        top_sizer.Add(open_epub_button, 0, wx.ALL, 5)

        # Open Folder button for batch mode
        open_folder_button = wx.Button(top_panel, label="📂 Open Folder")
        open_folder_button.Bind(wx.EVT_BUTTON, self.on_open_folder)
        top_sizer.Add(open_folder_button, 0, wx.ALL, 5)

        # Open Markdown .md
        # open_md_button = wx.Button(top_panel, label="📁 Open Markdown (.md)")
        # open_md_button.Bind(wx.EVT_BUTTON, self.on_open)
        # top_sizer.Add(open_md_button, 0, wx.ALL, 5)

        # Open .txt
        # open_txt_button = wx.Button(top_panel, label="📁 Open .txt")
        # open_txt_button.Bind(wx.EVT_BUTTON, self.on_open)
        # top_sizer.Add(open_txt_button, 0, wx.ALL, 5)

        # Open PDF
        # open_pdf_button = wx.Button(top_panel, label="📁 Open PDF")
        # open_pdf_button.Bind(wx.EVT_BUTTON, self.on_open)
        # top_sizer.Add(open_pdf_button, 0, wx.ALL, 5)

        # About button
        help_button = wx.Button(top_panel, label="ℹ️ About")
        help_button.Bind(wx.EVT_BUTTON, lambda event: self.about_dialog())
        top_sizer.Add(help_button, 0, wx.ALL, 5)

        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.main_sizer)

        # self.splitter = wx.SplitterWindow(self, -1)
        # self.splitter.SetSashGravity(0.9)
        self.splitter = wx.Panel(self)
        self.splitter_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.splitter.SetSizer(self.splitter_sizer)

        self.main_sizer.Add(top_panel, 0, wx.ALL | wx.EXPAND, 5)
        self.main_sizer.Add(self.splitter, 1, wx.EXPAND)

    def create_layout_for_ebook(self, splitter):
        splitter_left = wx.Panel(splitter, -1)
        splitter_right = wx.Panel(self.splitter)
        self.splitter_left, self.splitter_right = splitter_left, splitter_right
        self.splitter_sizer.Add(splitter_left, 1, wx.ALL | wx.EXPAND, 5)
        self.splitter_sizer.Add(splitter_right, 2, wx.ALL | wx.EXPAND, 5)

        self.left_sizer = wx.BoxSizer(wx.VERTICAL)
        splitter_left.SetSizer(self.left_sizer)

        # add center panel with large text area
        self.center_panel = wx.Panel(splitter_right)
        self.center_sizer = wx.BoxSizer(wx.VERTICAL)
        self.center_panel.SetSizer(self.center_sizer)
        self.text_area = wx.TextCtrl(self.center_panel, style=wx.TE_MULTILINE, size=(int(self.window_width * 0.4), -1))
        font = wx.Font(14, wx.MODERN, wx.NORMAL, wx.NORMAL)
        self.text_area.SetFont(font)
        # On text change, update the extracted_text attribute of the selected_chapter:
        self.text_area.Bind(wx.EVT_TEXT, lambda event: setattr(self.selected_chapter, 'extracted_text', self.text_area.GetValue()))

        # Save and Undo buttons
        save_button = wx.Button(self.center_panel, label="💾 Save")
        undo_button = wx.Button(self.center_panel, label="↩️ Undo")
        save_button.Bind(wx.EVT_BUTTON, self.on_save_text)
        undo_button.Bind(wx.EVT_BUTTON, self.on_undo_text)


        self.chapter_label = wx.StaticText(
            self.center_panel, label=f'Edit / Preview content for section \"{self.selected_chapter.short_name}\":')
        preview_button = wx.Button(self.center_panel, label="🔊 Preview")
        preview_button.Bind(wx.EVT_BUTTON, self.on_preview_chapter)

        # Add checkbox to show/hide regex controls, next to preview button
        preview_and_regex_sizer = wx.BoxSizer(wx.HORIZONTAL)
        preview_and_regex_sizer.Add(preview_button, 0, wx.ALL, 0)
        self.show_regex_checkbox = wx.CheckBox(self.center_panel, label="Show Regex Tools")
        self.show_regex_checkbox.SetValue(False)
        preview_and_regex_sizer.Add(self.show_regex_checkbox, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 10)
        self.show_regex_checkbox.Bind(wx.EVT_CHECKBOX, self.on_toggle_regex_panel)

        self.center_sizer.Add(self.chapter_label, 0, wx.ALL, 5)
        self.center_sizer.Add(preview_and_regex_sizer, 0, wx.ALL, 5)

        # Regex controls panel (hidden by default)
        self.regex_panel = wx.Panel(self.center_panel)
        regex_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self.regex_panel.SetSizer(regex_panel_sizer)

        regex_label = wx.StaticText(self.regex_panel, label="Regex:")
        self.regex_text_ctrl = wx.TextCtrl(self.regex_panel, value="", style=wx.TE_PROCESS_ENTER)
        regex_panel_sizer.Add(regex_label, 0, wx.ALL, 5)
        regex_panel_sizer.Add(self.regex_text_ctrl, 0, wx.ALL | wx.EXPAND, 5)

        replace_label = wx.StaticText(self.regex_panel, label="Replace With:")
        self.replacement_text_ctrl = wx.TextCtrl(self.regex_panel, value="", style=wx.TE_PROCESS_ENTER)
        regex_panel_sizer.Add(replace_label, 0, wx.ALL, 5)
        regex_panel_sizer.Add(self.replacement_text_ctrl, 0, wx.ALL | wx.EXPAND, 5)

        flags_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.multiline_checkbox = wx.CheckBox(self.regex_panel, label="Multiline (^ $)")
        self.multiline_checkbox.SetValue(True)
        flags_sizer.Add(self.multiline_checkbox, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.dotall_checkbox = wx.CheckBox(self.regex_panel, label="Dotall (.)")
        flags_sizer.Add(self.dotall_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)
        regex_panel_sizer.Add(flags_sizer, 0, wx.ALL | wx.EXPAND, 5)

        apply_regex_button = wx.Button(self.regex_panel, label="Apply Regex")
        apply_regex_button.Bind(wx.EVT_BUTTON, self.on_apply_regex)
        regex_panel_sizer.Add(apply_regex_button, 0, wx.ALL, 5)

        self.center_sizer.Add(self.regex_panel, 0, wx.ALL | wx.EXPAND, 0)
        self.regex_panel.Hide()

        self.center_sizer.Add(self.text_area, 1, wx.ALL | wx.EXPAND, 5)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(save_button, 0, wx.ALL, 5)
        btn_sizer.Add(undo_button, 0, wx.ALL, 5)
        self.center_sizer.Add(btn_sizer, 0, wx.ALL, 0)

        splitter_right_sizer = wx.BoxSizer(wx.HORIZONTAL)
        splitter_right.SetSizer(splitter_right_sizer)

        self.create_right_panel(splitter_right)
        splitter_right_sizer.Add(self.center_panel, 1, wx.ALL | wx.EXPAND, 5)
        splitter_right_sizer.Add(self.right_panel, 1, wx.ALL | wx.EXPAND, 5)

    def on_toggle_regex_panel(self, event):
        if self.show_regex_checkbox.GetValue():
            self.regex_panel.Show()
        else:
            self.regex_panel.Hide()
        self.center_panel.Layout()

    def about_dialog(self):
        msg = ("A simple tool to generate audiobooks from EPUB files using Kokoro-82M models\n" +
               "Distributed under the MIT License.\n\n" +
               "by Claudio Santini 2025\nand many contributors.\n\n" +
               "https://claudio.uk\n\n")
        wx.MessageBox(msg, "Audiblez")

    def create_right_panel(self, splitter_right):
        self.right_panel = wx.Panel(splitter_right)
        self.right_sizer = wx.BoxSizer(wx.VERTICAL)
        self.right_panel.SetSizer(self.right_sizer)

        self.book_info_panel_box = wx.Panel(self.right_panel, style=wx.SUNKEN_BORDER)
        book_info_panel_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.book_info_panel_box, "Book Details")
        self.book_info_panel_box.SetSizer(book_info_panel_box_sizer)
        self.right_sizer.Add(self.book_info_panel_box, 1, wx.ALL | wx.EXPAND, 5)

        self.book_info_panel = wx.Panel(self.book_info_panel_box, style=wx.BORDER_NONE)
        self.book_info_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.book_info_panel.SetSizer(self.book_info_sizer)
        book_info_panel_box_sizer.Add(self.book_info_panel, 1, wx.ALL | wx.EXPAND, 5)

        # Add cover image
        self.cover_bitmap = wx.StaticBitmap(self.book_info_panel, -1)
        self.book_info_sizer.Add(self.cover_bitmap, 0, wx.ALL, 5)

        self.cover_bitmap.Refresh()
        self.book_info_panel.Refresh()
        self.book_info_panel.Layout()
        self.cover_bitmap.Layout()

        self.create_book_details_panel()
        self.create_params_panel()
        self.create_synthesis_panel()

    def create_book_details_panel(self):
        book_details_panel = wx.Panel(self.book_info_panel)
        book_details_sizer = wx.GridBagSizer(10, 10)
        book_details_panel.SetSizer(book_details_sizer)
        self.book_info_sizer.Add(book_details_panel, 1, wx.ALL | wx.EXPAND, 5)

        # Batch mode: show editable title/year for selected file
        if hasattr(self, 'batch_files') and getattr(self, 'batch_files', None):
            # Track selected file index, default to 0
            if not hasattr(self, 'selected_batch_file_idx'):
                self.selected_batch_file_idx = 0
            selected_idx = getattr(self, 'selected_batch_file_idx', 0)
            if selected_idx >= len(self.batch_files):
                selected_idx = 0
            fileinfo = self.batch_files[selected_idx]
            # Editable Title
            title_label = wx.StaticText(book_details_panel, label="Title:")
            self.batch_title_text = wx.TextCtrl(book_details_panel, value=fileinfo.get("title") or os.path.splitext(os.path.basename(fileinfo["path"]))[0])
            book_details_sizer.Add(title_label, pos=(0, 0), flag=wx.ALL, border=5)
            book_details_sizer.Add(self.batch_title_text, pos=(0, 1), flag=wx.ALL, border=5)
            # Editable Year
            year_label = wx.StaticText(book_details_panel, label="Year:")
            self.batch_year_text = wx.TextCtrl(book_details_panel, value=fileinfo.get("year", ""))
            book_details_sizer.Add(year_label, pos=(1, 0), flag=wx.ALL, border=5)
            book_details_sizer.Add(self.batch_year_text, pos=(1, 1), flag=wx.ALL, border=5)
            # Save button for batch file details
            save_btn = wx.Button(book_details_panel, label="💾 Save Details")
            def on_save_batch_details(event):
                # Save title/year to batch_files
                self.batch_files[selected_idx]["title"] = self.batch_title_text.GetValue()
                self.batch_files[selected_idx]["year"] = self.batch_year_text.GetValue()
                # Persist batch state to disk
                try:
                    with open("batch_state.json", "w", encoding="utf-8") as f:
                        json.dump(self.batch_files, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    wx.MessageBox(f"Failed to save batch state: {e}", "Error", style=wx.OK | wx.ICON_ERROR)
                else:
                    wx.MessageBox("Batch file details saved.", "Saved", style=wx.OK | wx.ICON_INFORMATION)
            save_btn.Bind(wx.EVT_BUTTON, on_save_batch_details)
            book_details_sizer.Add(save_btn, pos=(2, 1), flag=wx.ALL, border=5)
            # Show file path
            path_label = wx.StaticText(book_details_panel, label="File Path:")
            path_text = wx.StaticText(book_details_panel, label=fileinfo["path"])
            book_details_sizer.Add(path_label, pos=(3, 0), flag=wx.ALL, border=5)
            book_details_sizer.Add(path_text, pos=(3, 1), flag=wx.ALL, border=5)
        else:
            # Single-file mode (original)
            title_label = wx.StaticText(book_details_panel, label="Title:")
            title_text = wx.StaticText(book_details_panel, label=self.selected_book_title)
            book_details_sizer.Add(title_label, pos=(0, 0), flag=wx.ALL, border=5)
            book_details_sizer.Add(title_text, pos=(0, 1), flag=wx.ALL, border=5)

            author_label = wx.StaticText(book_details_panel, label="Author:")
            author_text = wx.StaticText(book_details_panel, label=self.selected_book_author)
            book_details_sizer.Add(author_label, pos=(1, 0), flag=wx.ALL, border=5)
            book_details_sizer.Add(author_text, pos=(1, 1), flag=wx.ALL, border=5)

            year_label = wx.StaticText(book_details_panel, label="Year:")
            self.year_text = wx.TextCtrl(book_details_panel, value=str(getattr(self, 'book_year', '')))
            book_details_sizer.Add(year_label, pos=(2, 0), flag=wx.ALL, border=5)
            book_details_sizer.Add(self.year_text, pos=(2, 1), flag=wx.ALL, border=5)

            length_label = wx.StaticText(book_details_panel, label="Total Length:")
            if not hasattr(self, 'document_chapters'):
                total_len = 0
            else:
                total_len = sum([len(c.extracted_text) for c in self.document_chapters])
            length_text = wx.StaticText(book_details_panel, label=f'{total_len:,} characters')
            book_details_sizer.Add(length_label, pos=(3, 0), flag=wx.ALL, border=5)
            book_details_sizer.Add(length_text, pos=(3, 1), flag=wx.ALL, border=5)

    def create_params_panel(self):
        panel_box = wx.Panel(self.right_panel, style=wx.SUNKEN_BORDER)
        panel_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, panel_box, "Audiobook Parameters")
        panel_box.SetSizer(panel_box_sizer)

        panel = self.params_panel = wx.Panel(panel_box)
        panel_box_sizer.Add(panel, 1, wx.ALL | wx.EXPAND, 5)
        self.right_sizer.Add(panel_box, 1, wx.ALL | wx.EXPAND, 5)
        sizer = wx.GridBagSizer(10, 10)
        panel.SetSizer(sizer)

        engine_label = wx.StaticText(panel, label="Engine:")
        engine_radio_panel = wx.Panel(panel)
        cpu_radio = wx.RadioButton(engine_radio_panel, label="CPU", style=wx.RB_GROUP)
        cuda_radio = wx.RadioButton(engine_radio_panel, label="CUDA")
        if torch.cuda.is_available():
            cuda_radio.SetValue(True)
        else:
            cpu_radio.SetValue(True)
            # cuda_radio.Disable()
        sizer.Add(engine_label, pos=(0, 0), flag=wx.ALL, border=border)
        sizer.Add(engine_radio_panel, pos=(0, 1), flag=wx.ALL, border=border)
        engine_radio_panel_sizer = wx.BoxSizer(wx.HORIZONTAL)
        engine_radio_panel.SetSizer(engine_radio_panel_sizer)
        engine_radio_panel_sizer.Add(cpu_radio, 0, wx.ALL, 5)
        engine_radio_panel_sizer.Add(cuda_radio, 0, wx.ALL, 5)
        cpu_radio.Bind(wx.EVT_RADIOBUTTON, lambda event: torch.set_default_device('cpu'))
        cuda_radio.Bind(wx.EVT_RADIOBUTTON, lambda event: torch.set_default_device('cuda'))

        # Create a list of voices with flags
        flag_and_voice_list = []
        for code, l in voices.items():
            for v in l:
                flag_and_voice_list.append(f'{flags[code]} {v}')

        voice_label = wx.StaticText(panel, label="Voice:")
        # Load saved voice or use default
        saved_voice = self.config.Read("selected_voice", flag_and_voice_list[0])
        self.selected_voice = saved_voice
        voice_dropdown = wx.ComboBox(panel, choices=flag_and_voice_list, value=saved_voice)
        voice_dropdown.Bind(wx.EVT_COMBOBOX, self.on_select_voice)
        sizer.Add(voice_label, pos=(1, 0), flag=wx.ALL, border=border)
        sizer.Add(voice_dropdown, pos=(1, 1), flag=wx.ALL, border=border)

        # Save output folder in config, load on startup
        saved_output_folder = self.config.Read("output_folder", os.path.abspath('.'))

        # Add dropdown for speed
        speed_label = wx.StaticText(panel, label="Speed:")
        speed_text_input = wx.TextCtrl(panel, value="1.0")
        self.selected_speed = '1.0'
        speed_text_input.Bind(wx.EVT_TEXT, self.on_select_speed)
        sizer.Add(speed_label, pos=(2, 0), flag=wx.ALL, border=border)
        sizer.Add(speed_text_input, pos=(2, 1), flag=wx.ALL, border=border)

        # Add file dialog selector to select output folder
        output_folder_label = wx.StaticText(panel, label="Output Folder:")
        self.output_folder_text_ctrl = wx.TextCtrl(panel, value=saved_output_folder)
        self.output_folder_text_ctrl.SetEditable(False)
        output_folder_button = wx.Button(panel, label="📂 Select")
        output_folder_button.Bind(wx.EVT_BUTTON, self.open_output_folder_dialog)
        sizer.Add(output_folder_label, pos=(3, 0), flag=wx.ALL, border=border)
        sizer.Add(self.output_folder_text_ctrl, pos=(3, 1), flag=wx.ALL | wx.EXPAND, border=border)
        sizer.Add(output_folder_button, pos=(4, 1), flag=wx.ALL, border=border)

    def create_synthesis_panel(self):
        # Think and identify layout issue with the folling code
        panel_box = wx.Panel(self.right_panel, style=wx.SUNKEN_BORDER)
        panel_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, panel_box, "Audiobook Generation Status")
        panel_box.SetSizer(panel_box_sizer)

        panel = self.synth_panel = wx.Panel(panel_box)
        panel_box_sizer.Add(panel, 1, wx.ALL | wx.EXPAND, 5)
        self.right_sizer.Add(panel_box, 1, wx.ALL | wx.EXPAND, 5)
        sizer = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(sizer)

        # Add Start button
        self.start_button = wx.Button(panel, label="🚀 Start Audiobook Synthesis")
        self.start_button.Bind(wx.EVT_BUTTON, self.on_start)
        sizer.Add(self.start_button, 0, wx.ALL, 5)

        # Add Stop button
        # self.stop_button = wx.Button(panel, label="⏹️ Stop Synthesis")
        # self.stop_button.Bind(wx.EVT_BUTTON, self.on_stop)
        # sizer.Add(self.stop_button, 0, wx.ALL, 5)
        # self.stop_button.Hide()

        # Add Progress Bar label:
        self.progress_bar_label = wx.StaticText(panel, label="Synthesis Progress:")
        sizer.Add(self.progress_bar_label, 0, wx.ALL, 5)
        self.progress_bar = wx.Gauge(panel, range=100, style=wx.GA_PROGRESS)
        self.progress_bar.SetMinSize((-1, 30))
        sizer.Add(self.progress_bar, 0, wx.ALL | wx.EXPAND, 5)
        self.progress_bar_label.Hide()
        self.progress_bar.Hide()

        # Add ETA Label
        self.eta_label = wx.StaticText(panel, label="Estimated Time Remaining: ")
        self.eta_label.Hide()
        sizer.Add(self.eta_label, 0, wx.ALL, 5)

    def open_output_folder_dialog(self, event):
        with wx.DirDialog(self, "Choose a directory:", style=wx.DD_DEFAULT_STYLE) as dialog:
            if dialog.ShowModal() == wx.ID_CANCEL:
                return
            output_folder = dialog.GetPath()
            print(f"Selected output folder: {output_folder}")
            self.output_folder_text_ctrl.SetValue(output_folder)
            self.config.Write("output_folder", output_folder)

    def on_select_voice(self, event):
        self.selected_voice = event.GetString()
        self.config.Write("selected_voice", self.selected_voice) # Save the selected voice

    def on_select_speed(self, event):
        speed = float(event.GetString())
        print('Selected speed', speed)
        self.selected_speed = speed

    def open_epub(self, file_path):
        # Cleanup previous layout
        if hasattr(self, 'selected_book'):
            self.splitter.DestroyChildren()

        self.selected_file_path = file_path
        print(f"Opening file: {file_path}")  # Do something with the filepath (e.g., parse the EPUB)

        from ebooklib import epub
        from audiblez.core import find_document_chapters_and_extract_texts, find_good_chapters, find_cover
        book = epub.read_epub(file_path)
        meta_title = book.get_metadata('DC', 'title')
        self.selected_book_title = meta_title[0][0] if meta_title else ''
        meta_creator = book.get_metadata('DC', 'creator')
        self.selected_book_author = meta_creator[0][0] if meta_creator else ''
        self.selected_book = book
        # try:
        #     summary = wikipedia.summary(self.selected_book_title, sentences=1)
        #     match = re.search(r'(\d{4})', summary)
        #     self.book_year = match.group(1) if match else ''
        #     print(f"Debug: Wikipedia year for '{self.selected_book_title}': {self.book_year}")
        # except Exception:
        #     self.book_year = ''
        #     print(f"Debug: Wikipedia lookup failed for '{self.selected_book_title}'")

        self.document_chapters = find_document_chapters_and_extract_texts(book)
        good_chapters = find_good_chapters(self.document_chapters)
        self.selected_chapter = good_chapters[0]
        for chapter in self.document_chapters:
            chapter.short_name = chapter.get_name().replace('.xhtml', '').replace('xhtml/', '').replace('.html', '').replace('Text/', '')
            chapter.is_selected = chapter in good_chapters

        self.create_layout_for_ebook(self.splitter)

        # Update Cover
        cover = find_cover(book)
        if cover is not None:
            pil_image = Image.open(io.BytesIO(cover.content))
            wx_img = wx.EmptyImage(pil_image.size[0], pil_image.size[1])
            wx_img.SetData(pil_image.convert("RGB").tobytes())
            cover_h = 200
            cover_w = int(cover_h * pil_image.size[0] / pil_image.size[1])
            wx_img.Rescale(cover_w, cover_h)
            self.cover_bitmap.SetBitmap(wx_img.ConvertToBitmap())
            self.cover_bitmap.SetMaxSize((200, cover_h))

        chapters_panel = self.create_chapters_table_panel(good_chapters)

        #   chapters_panel to left_sizer, or replace if it exists already
        if self.chapters_panel:
            self.left_sizer.Replace(self.chapters_panel, chapters_panel)
            self.chapters_panel.Destroy()
            self.chapters_panel = chapters_panel
        else:
            self.left_sizer.Add(chapters_panel, 1, wx.ALL | wx.EXPAND, 5)
            self.chapters_panel = chapters_panel

        # These two are very important:
        self.splitter_left.Layout()
        self.splitter_right.Layout()
        self.splitter.Layout()

    def open_pdf(self, file_path):
        # Cleanup previous layout
        if hasattr(self, 'selected_book'):
            self.splitter.DestroyChildren()

        self.selected_file_path = file_path
        print(f"Opening PDF file: {file_path}")

        import PyPDF2

        # Extract text from each page
        pdf_reader = PyPDF2.PdfReader(file_path)
        num_pages = len(pdf_reader.pages)
        all_text = []
        for i, page in enumerate(pdf_reader.pages):
            text = page.extract_text() or ""
            all_text.append(text)

        # Split into "chapters" by page (or group pages if short)
        class PDFChapter:
            def __init__(self, short_name, extracted_text, chapter_index):
                self.short_name = short_name
                self.extracted_text = extracted_text
                self.chapter_index = chapter_index
                self.is_selected = True

            def get_name(self):
                return self.short_name

        # Group pages into chapters of ~5000 chars
        chapters = []
        buffer = ""
        chapter_index = 0
        for i, text in enumerate(all_text):
            buffer += text + "\n"
            if len(buffer) >= 5000 or i == num_pages - 1:
                buffer_count_newlines = buffer.count('\n')
                short_name = f"Pages {i - buffer_count_newlines + 2}-{i + 1}" if buffer_count_newlines > 0 else f"Page {i + 1}"
                chapters.append(PDFChapter(short_name, buffer.strip(), chapter_index))
                buffer = ""
                chapter_index += 1

        self.document_chapters = chapters
        self.selected_book_title = os.path.splitext(os.path.basename(file_path))[0]
        self.selected_book_author = "Unknown"
        # try:
        #     summary = wikipedia.summary(self.selected_book_title, sentences=1)
        #     match = re.search(r'(\d{4})', summary)
        #     self.book_year = match.group(1) if match else ''
        # except Exception:
        #     self.book_year = ''
        self.book_year = ''            
        self.selected_book = None
        self.selected_chapter = chapters[0] if chapters else None

        self.create_layout_for_ebook(self.splitter)

        # No cover for PDF
        self.cover_bitmap.SetBitmap(wx.NullBitmap)
        self.cover_bitmap.SetMaxSize((200, 200))

        chapters_panel = self.create_chapters_table_panel(chapters)

        if self.chapters_panel:
            self.left_sizer.Replace(self.chapters_panel, chapters_panel)
            self.chapters_panel.Destroy()
            self.chapters_panel = chapters_panel
        else:
            self.left_sizer.Add(chapters_panel, 1, wx.ALL | wx.EXPAND, 5)
            self.chapters_panel = chapters_panel

        self.splitter_left.Layout()
        self.splitter_right.Layout()
        self.splitter.Layout()

    def on_table_checked(self, event):
        self.document_chapters[event.GetIndex()].is_selected = True

    def on_table_unchecked(self, event):
        self.document_chapters[event.GetIndex()].is_selected = False

    def on_table_selected(self, event):
        chapter = self.document_chapters[event.GetIndex()]
        print('Selected', event.GetIndex(), chapter.short_name)
        self.selected_chapter = chapter
        # Initialize saved_text if not present
        if not hasattr(chapter, "saved_text"):
            chapter.saved_text = chapter.extracted_text
        self.text_area.SetValue(chapter.extracted_text)
        # Restore year field in single-file mode
        if hasattr(self, 'year_text'):
            self.year_text.SetValue(str(self.book_year))
        self.chapter_label.SetLabel(f'Edit / Preview content for section "{chapter.short_name}":')

    def create_chapters_table_panel(self, good_chapters):
        panel = ScrolledPanel(self.splitter_left, -1, style=wx.TAB_TRAVERSAL | wx.SUNKEN_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(sizer)

        self.table = table = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        table.InsertColumn(0, "Included")
        table.InsertColumn(1, "Chapter Name")
        table.InsertColumn(2, "Chapter Length")
        table.InsertColumn(3, "Status")
        table.SetColumnWidth(0, 80)
        table.SetColumnWidth(1, 150)
        table.SetColumnWidth(2, 150)
        table.SetColumnWidth(3, 100)
        table.SetSize((250, -1))
        table.EnableCheckBoxes()
        table.Bind(wx.EVT_LIST_ITEM_CHECKED, self.on_table_checked)
        table.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self.on_table_unchecked)
        table.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_table_selected)

        for i, chapter in enumerate(self.document_chapters):
            auto_selected = chapter in good_chapters
            table.Append(['', chapter.short_name, f"{len(chapter.extracted_text):,}"])
            if auto_selected: table.CheckItem(i)

        title_text = wx.StaticText(panel, label=f"Select chapters to include in the audiobook:")
        sizer.Add(title_text, 0, wx.ALL, 5)

        # Add Select All / Unselect All buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        select_all_btn = wx.Button(panel, label="Select All")
        unselect_all_btn = wx.Button(panel, label="Unselect All")
        btn_sizer.Add(select_all_btn, 0, wx.ALL, 2)
        btn_sizer.Add(unselect_all_btn, 0, wx.ALL, 2)
        sizer.Add(btn_sizer, 0, wx.ALL, 5)

        select_all_btn.Bind(wx.EVT_BUTTON, self.on_select_all_chapters)
        unselect_all_btn.Bind(wx.EVT_BUTTON, self.on_unselect_all_chapters)

        sizer.Add(table, 1, wx.ALL | wx.EXPAND, 5)
        return panel

    def on_select_all_chapters(self, event):
        # Check all checkboxes and set is_selected = True for all chapters
        for i, chapter in enumerate(self.document_chapters):
            self.table.CheckItem(i, True)
            chapter.is_selected = True

    def on_unselect_all_chapters(self, event):
        # Uncheck all checkboxes and set is_selected = False for all chapters
        for i, chapter in enumerate(self.document_chapters):
            self.table.CheckItem(i, False)
            chapter.is_selected = False

    def get_selected_voice(self):
        return self.selected_voice.split(' ')[1]

    def on_apply_regex(self, event):
        regex = self.regex_text_ctrl.GetValue()
        replacement_string = self.replacement_text_ctrl.GetValue() # Get value from new text control

        if not regex:
            wx.MessageBox("Please enter a regex pattern.", "Input Error", style=wx.OK | wx.ICON_WARNING)
            return

        flags = 0
        if self.multiline_checkbox.GetValue():
            flags |= re.MULTILINE
        if self.dotall_checkbox.GetValue():
            flags |= re.DOTALL

        try:
            pattern = re.compile(regex, flags=flags)
        except Exception as e:
            wx.MessageBox(f"Invalid regex: {e}", "Regex Error", style=wx.OK | wx.ICON_ERROR)
            return

        chapters_processed_count = 0
        for chapter in self.document_chapters:
            if getattr(chapter, "is_selected", False):
                original_text = chapter.extracted_text

                # Apply the regex substitution using the user-provided replacement string
                chapter.extracted_text = pattern.sub(replacement_string, original_text)

                if original_text != chapter.extracted_text: # Check if any change occurred
                    chapters_processed_count += 1
                    print(f"Chapter text modified. New content (first 200 chars):\n{chapter.extracted_text[:200]}...")


        wx.MessageBox(f"Regex applied to {chapters_processed_count} selected chapters.",
                      "Regex Application Complete", style=wx.OK | wx.ICON_INFORMATION)

        # If the currently selected chapter was affected, update the text area
        if hasattr(self, 'selected_chapter') and self.selected_chapter and getattr(self.selected_chapter, "is_selected", False):
            self.text_area.SetValue(self.selected_chapter.extracted_text)

    def on_save_text(self, event):
        # Save current text in textarea as the saved_text for the selected chapter
        if self.selected_chapter:
            self.selected_chapter.saved_text = self.text_area.GetValue()
            self.selected_chapter.extracted_text = self.text_area.GetValue()
        # Save edited Year field in single‐file mode
        if hasattr(self, 'year_text'):
            self.book_year = self.year_text.GetValue()
            # wx.MessageBox("Text saved for this chapter.", "Saved", style=wx.OK | wx.ICON_INFORMATION)

    def on_undo_text(self, event):
        # Revert textarea to last saved_text for the selected chapter
        if self.selected_chapter and hasattr(self.selected_chapter, 'saved_text'):
            self.text_area.SetValue(self.selected_chapter.saved_text)
            self.selected_chapter.extracted_text = self.selected_chapter.saved_text
            wx.MessageBox("Text reverted to last saved state.", "Undo", style=wx.OK | wx.ICON_INFORMATION)

    def get_selected_speed(self):
        return float(self.selected_speed)

    def on_preview_chapter(self, event):
        lang_code = self.get_selected_voice()[0]
        button = event.GetEventObject()
        button.SetLabel("⏳")
        button.Disable()

        def generate_preview():
            import audiblez.core as core
            from kokoro import KPipeline
            pipeline = KPipeline(lang_code=lang_code)
            core.load_spacy()
            text = self.selected_chapter.extracted_text[:300]
            if len(text) == 0: return
            audio_segments = core.gen_audio_segments(
                pipeline,
                text,
                voice=self.get_selected_voice(),
                speed=self.get_selected_speed())
            final_audio = np.concatenate(audio_segments)
            tmp_preview_wav_file = NamedTemporaryFile(suffix='.wav', delete=False)
            soundfile.write(tmp_preview_wav_file, final_audio, core.sample_rate)
            cmd = ['ffplay', '-autoexit', '-nodisp', tmp_preview_wav_file.name]
            subprocess.run(cmd)
            button.SetLabel("🔊 Preview")
            button.Enable()

        if len(self.preview_threads) > 0:
            for thread in self.preview_threads:
                thread.join()
            self.preview_threads = []
        thread = threading.Thread(target=generate_preview)
        thread.start()
        self.preview_threads.append(thread)

    def on_start(self, event):
        self.synthesis_in_progress = True
        voice = self.selected_voice.split(' ')[1]  # Remove the flag
        speed = float(self.selected_speed)
        self.start_button.Disable()
        self.params_panel.Disable()

        # Batch mode
        if hasattr(self, "batch_files") and self.batch_files:
            selected_files = [f["path"] for f in self.batch_files if f["selected"]]
            if not selected_files:
                wx.MessageBox("No files selected for batch synthesis.", "Batch Mode", style=wx.OK | wx.ICON_WARNING)
                self.start_button.Enable()
                self.params_panel.Enable()
                return
            for file_path in selected_files:
                year = next((f["year"] for f in self.batch_files if f["path"] == file_path), '')
                print('Starting Audiobook Synthesis (batch)', dict(file_path=file_path, voice=voice, pick_manually=False, speed=speed, book_year=year))
                core_thread = CoreThread(params=dict(
                    file_path=file_path, voice=voice, pick_manually=False, speed=speed,
                    book_year=year,
                    output_folder=self.output_folder_text_ctrl.GetValue(),
                    selected_chapters=None
                ))
                core_thread.start()
                core_thread.join()
            return

        # Single file mode
        file_path = self.selected_file_path
        selected_chapters = [chapter for chapter in self.document_chapters if chapter.is_selected]

        self.table.EnableCheckBoxes(False)
        for chapter_index, chapter in enumerate(self.document_chapters):
            if chapter in selected_chapters:
                self.set_table_chapter_status(chapter_index, "Planned")
                self.table.SetItem(chapter_index, 0, '✔️')

        # Save output folder to config on start
        self.config.Write("output_folder", self.output_folder_text_ctrl.GetValue())

        regex_value = self.regex_text_ctrl.GetValue()
        print('Starting Audiobook Synthesis', dict(file_path=file_path, voice=voice, pick_manually=False, speed=speed, book_year=self.book_year))
        self.core_thread = CoreThread(params=dict(
            file_path=file_path, voice=voice, pick_manually=False, speed=speed,
            book_year=self.book_year,
            output_folder=self.output_folder_text_ctrl.GetValue(),
            selected_chapters=selected_chapters
        ))
        self.core_thread.start()

    def set_table_chapter_status(self, index, status_text):
        if self.table and index < self.table.GetItemCount():
            self.table.SetItem(index, 3, status_text) # Column 3 is for status
            self.table.RefreshItem(index)

    def open_folder_with_explorer(self, path):
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":  # macOS
            subprocess.Popen(["open", path])
        else:  # Linux
            subprocess.Popen(["xdg-open", path])

    def on_exit(self, event):
        self.Destroy()

    def on_open(self, event):
        with wx.FileDialog(self, "Open E-book File",
                           wildcard="EPUB files (*.epub)|*.epub|PDF files (*.pdf)|*.pdf|All files (*.*)|*.*",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fileDialog:
            if fileDialog.ShowModal() == wx.ID_CANCEL:
                return

            pathname = fileDialog.GetPath()
            file_extension = os.path.splitext(pathname)[1].lower()

            if file_extension == '.epub':
                self.open_epub(pathname)
            elif file_extension == '.pdf':
                self.open_pdf(pathname)
            else:
                wx.MessageBox("Unsupported file type.", "Error", style=wx.OK | wx.ICON_ERROR)

    def on_open_folder(self, event):
        with wx.DirDialog(self, "Select a folder containing e-books", style=wx.DD_DEFAULT_STYLE) as dirDialog:
            if dirDialog.ShowModal() == wx.ID_CANCEL:
                return
            folder_path = dirDialog.GetPath()
            # Scan for supported files
            supported_exts = [".epub", ".pdf"]
            files = [os.path.join(folder_path, f) for f in os.listdir(folder_path)
                     if os.path.isfile(os.path.join(folder_path, f)) and os.path.splitext(f)[1].lower() in supported_exts]
            if not files:
                wx.MessageBox("No supported files (.epub, .pdf) found in the selected folder.", "No Files", style=wx.OK | wx.ICON_INFORMATION)
                return
            self.batch_files = [{"path": f, "selected": True, "year": ""} for f in files]
            # Try to load batch state from disk and merge
            try:
                with open("batch_state.json", "r", encoding="utf-8") as f:
                    saved_batch = json.load(f)
                saved_map = {item["path"]: item for item in saved_batch}
                for fileinfo in self.batch_files:
                    if fileinfo["path"] in saved_map:
                        fileinfo.update({k: v for k, v in saved_map[fileinfo["path"]].items() if k in ("title", "year")})
            except Exception:
                pass
            for fileinfo in self.batch_files:
                fname = os.path.splitext(os.path.basename(fileinfo["path"]))[0]
                if " - " in fname:
                    title, author = fname.split(" - ", 1)
                else:
                    title, author = fname, ""
                try:
                    summary = wikipedia.summary(f"{title} {author}", sentences=1)
                    match = re.search(r'(\d{4})', summary)
                    fileinfo["year"] = match.group(1) if match else fileinfo.get("year", "")
                    print(f"Debug: Wikipedia year for '{fileinfo['path']}': {fileinfo['year']}")
                except Exception:
                    fileinfo["year"] = fileinfo.get("year", "")
                    print(f"Debug: Wikipedia lookup failed for '{fileinfo['path']}'")

            # Ensure left panel exists for batch mode
            # Fully reset splitter and both panels for batch mode (mirror create_layout_for_ebook)
            for child in self.splitter.GetChildren():
                child.Destroy()
            self.splitter_sizer.Clear(delete_windows=True)
            # Left panel
            self.splitter_left = wx.Panel(self.splitter, -1)
            self.left_sizer = wx.BoxSizer(wx.VERTICAL)
            self.splitter_left.SetSizer(self.left_sizer)
            self.splitter_sizer.Add(self.splitter_left, 1, wx.ALL | wx.EXPAND, 5)
            # Right panel
            self.splitter_right = wx.Panel(self.splitter)
            self.right_sizer = wx.BoxSizer(wx.VERTICAL)
            self.splitter_right.SetSizer(self.right_sizer)
            self.splitter_sizer.Add(self.splitter_right, 2, wx.ALL | wx.EXPAND, 5)
            self.selected_book_title = "Batch Mode"
            self.selected_book_author = "Multiple Files"
            self.create_right_panel(self.splitter_right)
            # Add horizontal sizer to splitter_right, add dummy center_panel and right_panel (mirror create_layout_for_ebook)
            splitter_right_sizer = wx.BoxSizer(wx.HORIZONTAL)
            # Dummy center_panel (hidden, just for layout)
            self.center_panel = wx.Panel(self.splitter_right)
            self.center_panel.Hide()
            splitter_right_sizer.Add(self.center_panel, 1, wx.ALL | wx.EXPAND, 5)
            splitter_right_sizer.Add(self.right_panel, 1, wx.ALL | wx.EXPAND, 5)
            self.splitter_right.SetSizer(splitter_right_sizer)
            # Add batch panel to left
            batch_panel = self.create_batch_files_panel(self.batch_files)
            self.left_sizer.Add(batch_panel, 1, wx.ALL | wx.EXPAND, 5)
            self.chapters_panel = batch_panel
            self.splitter.SetSizer(self.splitter_sizer)
            self.splitter_left.Layout()
            self.splitter_right.Layout()
            self.right_panel.Show()
            self.right_panel.Layout()
            self.splitter_sizer.Layout()
            self.splitter.Layout()

    def create_batch_files_panel(self, batch_files):
        panel = ScrolledPanel(self.splitter_left, -1, style=wx.TAB_TRAVERSAL | wx.SUNKEN_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(sizer)

        self.batch_table = table = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        table.InsertColumn(0, "Included")
        table.InsertColumn(1, "File Name")
        table.InsertColumn(2, "Year")
        table.InsertColumn(3, "File Path")
        table.SetColumnWidth(0, 80)
        table.SetColumnWidth(1, 200)
        table.SetColumnWidth(2, 80)
        table.SetColumnWidth(3, 400)
        table.SetSize((600, -1))
        table.EnableCheckBoxes()
        table.Bind(wx.EVT_LIST_ITEM_CHECKED, self.on_batch_table_checked)
        table.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self.on_batch_table_unchecked)
        table.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_batch_table_selected)

        for i, fileinfo in enumerate(batch_files):
            fname = os.path.basename(fileinfo["path"])
            table.Append(['', fname, fileinfo["year"], fileinfo["path"]])
            table.CheckItem(i)

        title_text = wx.StaticText(panel, label=f"Select files to include in batch synthesis:")
        sizer.Add(title_text, 0, wx.ALL, 5)

        # Add Select All / Unselect All buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        select_all_btn = wx.Button(panel, label="Select All")
        unselect_all_btn = wx.Button(panel, label="Unselect All")
        btn_sizer.Add(select_all_btn, 0, wx.ALL, 2)
        btn_sizer.Add(unselect_all_btn, 0, wx.ALL, 2)
        sizer.Add(btn_sizer, 0, wx.ALL, 5)

        select_all_btn.Bind(wx.EVT_BUTTON, self.on_select_all_batch_files)
        unselect_all_btn.Bind(wx.EVT_BUTTON, self.on_unselect_all_batch_files)

        sizer.Add(table, 1, wx.ALL | wx.EXPAND, 5)
        return panel

    def on_batch_table_checked(self, event):
        idx = event.GetIndex()
        self.batch_files[idx]["selected"] = True

    def on_batch_table_unchecked(self, event):
        idx = event.GetIndex()
        self.batch_files[idx]["selected"] = False

    def on_batch_table_selected(self, event):
        idx = event.GetIndex()
        self.selected_batch_file_idx = idx
        # Refresh the book details panel
        # Remove and recreate the book details panel
        for child in self.book_info_panel.GetChildren():
            child.Destroy()
        self.create_book_details_panel()
        self.book_info_panel.Layout()
        self.book_info_panel.Parent.Layout()
        self.right_panel.Layout()

    def on_select_all_batch_files(self, event):
        for i, fileinfo in enumerate(self.batch_files):
            self.batch_table.CheckItem(i, True)
            fileinfo["selected"] = True

    def on_unselect_all_batch_files(self, event):
        for i, fileinfo in enumerate(self.batch_files):
            self.batch_table.CheckItem(i, False)
            fileinfo["selected"] = False

# --- Add this at the end to make the app runnable ---
class CoreThread(threading.Thread):
    def __init__(self, params):
        threading.Thread.__init__(self)
        self.params = params

    def run(self):
        import core
        core.main(**self.params, post_event=self.post_event)

    def post_event(self, event_name, **kwargs):
        # eg. 'EVENT_CORE_PROGRESS' -> EventCoreProgress, EVENT_CORE_PROGRESS
        EventObject, EVENT_CODE = EVENTS[event_name]
        event_object = EventObject()
        for k, v in kwargs.items():
            setattr(event_object, k, v)
        wx.PostEvent(wx.GetApp().GetTopWindow(), event_object)


def main():
    print('Starting GUI...')
    app = wx.App(False)
    frame = MainWindow(None, "Audiblez - Generate Audiobooks from E-books")
    frame.Show(True)
    frame.Layout()
    app.SetTopWindow(frame)
    print('Done.')
    app.MainLoop()


if __name__ == '__main__':
    main()
