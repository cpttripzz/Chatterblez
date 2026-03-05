#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# chatterblez - A program to convert e-books into audiobooks using
# chatterbox-tts
# by Zachary Erskine
# by Claudio Santini 2025 - https://claudio.uk
import logging
import os
import sys
import traceback
from glob import glob

import torch.cuda
import spacy
import ebooklib
import soundfile
import numpy as np
import time
import shutil
import subprocess
import platform
import re
from io import StringIO
from types import SimpleNamespace
from tabulate import tabulate
from pathlib import Path
from string import Formatter
from bs4 import BeautifulSoup
from ebooklib import epub
from pick import pick
import threading
import queue  # Import queue for concurrent reading
from pydub import AudioSegment
from pydub.silence import split_on_silence

from functools import lru_cache

sample_rate = 24000
import perth
if perth.PerthImplicitWatermarker is None:
    perth.PerthImplicitWatermarker = perth.DummyWatermarker

def remove_silence_from_audio(input_file, output_file, silence_thresh=-50, min_silence_len=1000, keep_silence=200):
    """
    Remove silences from an audio file using pydub with high quality.

    Args:
        input_file: Path to input audio file.
        output_file: Path to output audio file.
        silence_thresh: Silence threshold in dBFS (try -50 to -60).
        min_silence_len: Minimum silence length in milliseconds to remove.
        keep_silence: Amount of silence to keep in ms.
    """
    # Load audio file
    audio = AudioSegment.from_file(input_file)

    # Analyze audio loudness for debugging
    logging.info(f"Audio dBFS: {audio.dBFS:.2f}")
    logging.info(f"Max dBFS: {audio.max_dBFS:.2f}")
    logging.info(f"Sample rate: {audio.frame_rate}Hz")
    logging.info(f"Channels: {audio.channels}")
    logging.info(f"Sample width: {audio.sample_width} bytes")

    # Split audio on silence
    chunks = split_on_silence(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh,
        keep_silence=keep_silence
    )

    # Check if any chunks were found
    logging.info(f"Found {len(chunks)} audio chunks")

    if len(chunks) == 0:
        logging.warning("WARNING: No audio chunks found! Adjust silence_thresh or min_silence_len")
        logging.warning(f"Try setting silence_thresh to {audio.dBFS - 10:.1f} dBFS")
        return

    # Combine chunks
    combined = AudioSegment.empty()
    for chunk in chunks:
        combined += chunk

    # Export based on file extension
    output_format = Path(output_file).suffix[1:].lower()

    if output_format == 'wav':
        # Export as WAV with PCM (uncompressed)
        combined.export(
            output_file,
            format='wav',
            parameters=[
                '-ar', str(audio.frame_rate),  # Preserve sample rate
                '-ac', str(audio.channels)  # Preserve channels
            ]
        )
    elif output_format in ['m4a', 'm4b']:
        # Export as M4A/M4B with AAC
        export_params = {
            'format': 'ipod',
            'codec': 'aac',
            'bitrate': '128k',
            'parameters': [
                '-ar', str(audio.frame_rate),
                '-ac', str(audio.channels),
                '-q:a', '2'
            ]
        }

        if output_format == 'm4b':
            temp_output = Path(output_file).with_suffix('.m4a')
            combined.export(temp_output, **export_params)
            if os.path.exists(output_file):
                os.remove(output_file)
            os.rename(temp_output, output_file)
        else:
            combined.export(output_file, **export_params)
    else:
        # For other formats, let pydub handle it
        combined.export(output_file, format=output_format)

    logging.info(f"Processed audio saved to: {output_file}")
    original_duration = len(audio) / 1000
    new_duration = len(combined) / 1000
    removed_time = original_duration - new_duration

    logging.info(f"\nOriginal duration: {original_duration:.2f}s")
    logging.info(f"New duration: {new_duration:.2f}s")
    logging.info(f"Removed silence: {removed_time:.2f}s ({removed_time / original_duration * 100:.1f}%)")


import string

# Set of all punctuation characters to preserve (from `string.punctuation`)
PUNCTUATION = set(string.punctuation)

# Precompiled regex: sequences of 2 or more non-alphanumeric characters
non_alnum_seq_re = re.compile(r'[^a-zA-Z0-9]{2,}')

# Substitution function
def replace_non_alnum_sequence(match):
    first = match.group(0)[0]
    return first if first in PUNCTUATION else ''

allowed_chars_re = re.compile(r"[^’\"a-zA-Z0-9\s.,;:'\"-]")

@lru_cache(maxsize=1)
def get_nlp():
    """
    Lightweight, cached spacy pipeline used only for sentence segmentation.
    Falls back to full model if `spacy.blank` is not available for the
    requested language.
    """
    try:
        nlp = spacy.blank("xx")  # very small, language-agnostic
    except Exception:  # Fallback – should basically never happen
        load_spacy()
        nlp = spacy.load("en_core_web_trf")
    if "sentencizer" not in nlp.pipe_names:
        nlp.add_pipe("sentencizer")
    return nlp


# ---------------------------------------------------------------------------
# Helper for progress / ETA
# ---------------------------------------------------------------------------
def update_stats(stats, added_chars):
    """
    Update statistics (chars processed, speed, ETA) using an exponential
    moving average to smooth the instantaneous chars/sec measurement. This
    greatly improves the accuracy of the ETA that is reported to the user.
    """
    stats.processed_chars += added_chars
    elapsed = time.perf_counter() - stats.start_time
    if elapsed <= 0:
        return
    current_rate = stats.processed_chars / elapsed
    alpha = 0.3  # smoothing factor
    stats.chars_per_sec = alpha * current_rate + (1 - alpha) * stats.chars_per_sec
    remaining_chars = max(stats.total_chars - stats.processed_chars, 0)
    stats.eta = strfdelta(remaining_chars / stats.chars_per_sec) if stats.chars_per_sec else "?:??"
    stats.progress = stats.processed_chars * 100 // stats.total_chars


def load_spacy():
    if not spacy.util.is_package("en_core_web_trf"):
        logging.info("Downloading Spacy model en_core_web_trf...")
        spacy.cli.download("en_core_web_trf")


import ctypes
import time
import threading

# Constants from WinBase.h
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


# Set execution state to prevent sleep
def prevent_sleep():
    if platform.system() == "Windows":
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        )


# Reset execution state to allow sleep
def allow_sleep():
    if platform.system() == "Windows":
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def set_espeak_library():
    """Find the espeak library path"""
    try:
        if os.environ.get('ESPEAK_LIBRARY'):
            library = os.environ['ESPEAK_LIBRARY']
        elif platform.system() == 'Darwin':
            from subprocess import check_output
            try:
                cellar = Path(check_output(["brew", "--cellar"], text=True).strip())
                pattern = cellar / "espeak-ng" / "*" / "lib" / "*.dylib"
                if not (library := next(iter(glob(str(pattern))), None)):
                    raise RuntimeError("No espeak-ng library found; please set the path manually")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                raise RuntimeError("Cannot locate Homebrew Cellar. Is 'brew' installed and in PATH?") from e
        elif platform.system() == 'Linux':
            library = glob('/usr/lib/*/libespeak-ng*')[0]
        elif platform.system() == 'Windows':
            paths = glob('C:\\Program Files\\eSpeak NG\\libespeak-ng.dll') + \
                    glob('C:\\Program Files (x86)\\eSpeak NG\\libespeak-ng.dll')
            if paths:
                library = paths[0]
            else:
                raise RuntimeError(
                    "eSpeak NG library not found in default paths. Please set ESPEAK_LIBRARY environment variable.")
        else:
            logging.warning('Unsupported OS, please set the espeak library path manually')
            return
        logging.info('Using espeak library: %s', library)
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_library(library)
    except Exception:
        logging.exception("Error finding espeak-ng library")
        logging.warning("Probably you haven't installed espeak-ng.")
        logging.warning("On Mac: brew install espeak-ng")
        logging.warning("On Linux: sudo apt install espeak-ng")
        logging.warning("On Windows: Download from https://github.com/espeak-ng/espeak-ng/releases")


def match_case(word, replacement):
    if word.isupper():
        return replacement.upper()
    elif word.islower():
        return replacement.lower()
    elif word[0].isupper():
        return replacement.capitalize()
    else:
        return replacement  # fallback (e.g., mixed case)


def replace_preserve_case(text, old, new):
    if len(old) != len(new):
        raise ValueError("Replacement arrays must be the same length.")

    for o, n in zip(old, new):
        pattern = re.compile(rf'\b{re.escape(o)}\b', re.IGNORECASE)

        def repl(match):
            return match_case(match.group(), n)

        text = pattern.sub(repl, text)

    return text

# Define only "speakable" punctuation - ones that affect how text is read aloud
SPEAKABLE_PUNCT = '.,-\'"'
ESCAPED_SPEAKABLE = re.escape(SPEAKABLE_PUNCT)

# All punctuation for removal purposes
ALL_PUNCT = re.escape(string.punctuation)

# Compiled regex patterns
remove_unwanted = re.compile(rf'[^\w\s{ALL_PUNCT}]+')
remove_unspeakable = re.compile(rf'[{re.escape("".join(set(string.punctuation) - set(SPEAKABLE_PUNCT)))}]+')
normalize_quotes = re.compile(r'[""''`]')  # Smart quotes and backticks to normalize
replace_em_dash = re.compile(r'—')  # Em dash to replace with space
collapse_punct = re.compile(rf'[{ESCAPED_SPEAKABLE}][\s{ESCAPED_SPEAKABLE}]*(?=[{ESCAPED_SPEAKABLE}])')

def clean_string(text):
    """
    Remove non-alphanumeric chars, keep only speakable punctuation,
    normalize quotes, replace em dashes with spaces, and collapse multiple punctuation to keep only the last one.
    """
    # Replace em dashes with spaces FIRST before any other processing
    step1 = replace_em_dash.sub(' ', text)
    
    # Remove all characters that aren't alphanumeric, whitespace, or punctuation
    step2 = remove_unwanted.sub('', step1)
    
    # Normalize smart quotes and backticks to standard quotes
    step3 = normalize_quotes.sub(lambda m: '"' if m.group() in '""' else "'", step2)
    
    # Remove unspeakable punctuation (symbols like @#$%^&*()[]{}|\ etc.)
    step4 = remove_unspeakable.sub('', step3)
    
    # Then collapse sequences of speakable punctuation (with optional whitespace) to keep only the last one
    step5 = collapse_punct.sub('', step4)
    
    # Clean up any remaining multiple whitespace
    result = re.sub(r'\s+', ' ', step5).strip()
    
    return result


# Step 1: Normalize curly quotes
def normalize_quotes(text: str) -> str:
    return (
        text.replace("“", '"')
            .replace("”", '"')
            .replace("‘", "'")
            .replace("’", "'")
    )

# Step 2: Replace disallowed characters (not letter/digit/space/period/comma/apos) with space
non_allowed_re = re.compile(r"[^a-zA-Z0-9\s.,']+")

# Step 3: Collapse multiple spaces
space_re = re.compile(r'\s+')

# Step 4: Remove space(s) before a period
space_before_period_re = re.compile(r'\s+\.')

# Step 5: Collapse consecutive periods
multiple_periods_re = re.compile(r'\.{2,}')

def clean_line(line: str) -> str:
    line = normalize_quotes(line)
    line = non_allowed_re.sub(' ', line)                      # Remove unwanted chars
    line = space_before_period_re.sub('.', line)              # Remove space before .
    line = multiple_periods_re.sub('.', line)                 # Remove repeated .
    line = space_re.sub(' ', line)                            # Collapse spaces
    return line.strip()
def main(file_path, pick_manually, speed, book_year='', output_folder='.',
         max_chapters=None, max_sentences=None, selected_chapters=None, post_event=None, audio_prompt_wav=None, batch_files=None, ignore_list=None, should_stop=None,
         repetition_penalty=1.1, min_p=0.02, top_p=0.95, exaggeration=0.4, cfg_weight=0.8, temperature=0.85,
         enable_silence_trimming=False, silence_thresh=-50, min_silence_len=500, keep_silence=100):
    """
    Main entry point for audiobook synthesis.
    - ignore_list: list of chapter names to ignore (case-insensitive substring match)
    - batch_files: if provided, a list of file paths to process sequentially
    - should_stop: optional callback, returns True if synthesis should be interrupted
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(module)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler("logs/app.log", encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    logging.getLogger('chatterbox').setLevel(logging.WARNING)
    params = {
        "repetition_penalty":repetition_penalty,
        "min_p":min_p,
        "top_p":top_p,
        "exaggeration":exaggeration,
        "cfg_weight":cfg_weight,
        "temperature":temperature,
        "enable_silence_trimming":enable_silence_trimming,
        "silence_thresh":silence_thresh,
        "min_silence_len":min_silence_len,
        "keep_silence":keep_silence,
    }

    # Log all parameters
    for key, value in params.items():
        logging.info(f"{key} = {value}")
    if should_stop is None:
        should_stop = lambda: False

    if batch_files is not None:
        # Sequentially process each file in batch_files
        for batch_file in batch_files:
            # Call main for each file, passing ignore_list and other params
            main(
                file_path=batch_file,
                pick_manually=pick_manually,
                speed=speed,
                book_year=book_year,
                output_folder=output_folder,
                max_chapters=max_chapters,
                max_sentences=max_sentences,
                selected_chapters=None,
                post_event=post_event,
                audio_prompt_wav=audio_prompt_wav,
                batch_files=None,  # Prevent infinite recursion
                ignore_list=ignore_list,
                should_stop=should_stop,
                repetition_penalty=repetition_penalty,
                min_p=min_p,
                top_p=top_p,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
                temperature=temperature,
                enable_silence_trimming=enable_silence_trimming,
                silence_thresh=silence_thresh,
                min_silence_len=min_silence_len,
                keep_silence=keep_silence,
            )
            if post_event:
                post_event('CORE_FILE_FINISHED', file_path=batch_file)
            if should_stop():
                break
        return

    if post_event: post_event('CORE_STARTED')
    IS_WINDOWS = sys.platform.startswith("win")

    prevent_sleep()

    load_spacy()
    if output_folder != '.':
        Path(output_folder).mkdir(parents=True, exist_ok=True)

    filename = Path(file_path).name
    filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    extension = os.path.splitext(file_path)[1].lower()
    logging.info(f"extension {extension}")
    if extension == '.pdf':
        title = os.path.splitext(os.path.basename(file_path))[0]
        creator = "Unknown"
        cover_image = b""
        document_chapters = selected_chapters
    else:
        extension = '.epub'
        book = epub.read_epub(file_path)
        meta_title = book.get_metadata('DC', 'title')
        title = meta_title[0][0] if meta_title else ''
        meta_creator = book.get_metadata('DC', 'creator')
        creator = meta_creator[0][0] if meta_creator else ''
        cover_maybe = find_cover(book)
        cover_image = cover_maybe.get_content() if cover_maybe else b""
        if cover_maybe:
            logging.info(f'Found cover image {cover_maybe.file_name} in {cover_maybe.media_type} format')
            if False:
                # Save cover image as "<book name>.<image extension>"
                media_type = cover_maybe.media_type  # e.g., "image/jpeg"
                ext_map = {
                    "image/jpeg": ".jpg",
                    "image/jpg": ".jpg",
                    "image/png": ".png",
                    "image/gif": ".gif",
                    "image/bmp": ".bmp",
                    "image/webp": ".webp"
                }
                ext = ext_map.get(media_type, ".img")
                # Clean title for filename
                safe_title = re.sub(r'[\\/:*?"<>|]', '_', title).strip() or "cover"
                cover_filename = f"{safe_title}{ext}"
                cover_path = Path(output_folder) / cover_filename
                with open(cover_path, "wb") as f:
                    f.write(cover_image)
                logging.info(f"Cover image saved as {cover_path}")
        document_chapters = find_document_chapters_and_extract_texts(book)

        if not selected_chapters:
            if pick_manually is True:
                selected_chapters = pick_chapters(document_chapters)
            else:
                selected_chapters = find_good_chapters(document_chapters)
    if selected_chapters is None:
        selected_chapters = document_chapters

    # Filter chapters based on ignore_list
    if ignore_list:
        def should_include(chapter):
            name = chapter.get_name().lower()
            for ignore in ignore_list:
                if ignore.lower() in name:
                    return False
            return True
        selected_chapters = [c for c in selected_chapters if should_include(c)]

    print_selected_chapters(document_chapters, selected_chapters)
    texts = [c.extracted_text for c in selected_chapters]

    has_ffmpeg = shutil.which('ffmpeg') is not None
    if not has_ffmpeg:
        logging.error('ffmpeg not found. Please install ffmpeg to create mp3 and m4b audiobook files.')
        if post_event:
            post_event('CORE_ERROR', message="FFmpeg not found. Please install it to create audiobooks.")
        allow_sleep()
        return

    stats = SimpleNamespace(
        total_chars=sum(map(len, texts)),
        processed_chars=0,
        chars_per_sec=500 if torch.cuda.is_available() else 50,  # initial guess
        start_time=time.perf_counter(),
        eta='–',
        progress=0
    )
    logging.info('Started at: %s', time.strftime('%H:%M:%S'))
    logging.info(f'Total characters: {stats.total_chars:,}')
    logging.info('Total words: %d', len(' '.join(texts).split()))
    eta = strfdelta((stats.total_chars - stats.processed_chars) / stats.chars_per_sec)
    logging.info(f'Estimated time remaining (assuming {stats.chars_per_sec} chars/sec): {eta}')
    chapter_wav_files = []

    import torchaudio as ta
    from chatterbox.tts import ChatterboxTTS
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logging.info(f'running on device: {device}')

    cb_model = ChatterboxTTS.from_pretrained(device=device)

    # If a custom audio prompt is provided, use it
    if audio_prompt_wav:
        AUDIO_PROMPT_PATH = audio_prompt_wav
        cb_model.prepare_conditionals(wav_fpath=AUDIO_PROMPT_PATH)
    # You must set AUDIO_PROMPT_PATH to the correct path for your audio prompt
    # AUDIO_PROMPT_PATH = "audio_prompt.wav"  # <-- Set this to your actual prompt file
    # cb_model.prepare_conditionals(wav_fpath=AUDIO_PROMPT_PATH)

    chapter_wav_files = []
    nlp = get_nlp()
    for i, chapter in enumerate(selected_chapters, start=1):
        if should_stop():
            logging.info("Synthesis interrupted by user (chapter loop).")
            break
        if max_chapters and i > max_chapters: break
        lines = chapter.extracted_text.splitlines()
        text = "\n".join(
            cleaned_line
            for line in lines
            if (
                cleaned_line :=  clean_line(line)
            ).strip() and re.search(r'\w', cleaned_line)
        )

        # Sanitize the chapter name to remove all non-alphanumeric characters for the filename
        xhtml_file_name = re.sub(r'[^a-zA-Z0-9-]', '', chapter.get_name()).replace('xhtml', '').replace('html', '')
        chapter_wav_path = Path(output_folder) / filename.replace(extension, f'_chapter_{xhtml_file_name}.wav')
        chapter_wav_files.append(chapter_wav_path)
        if Path(chapter_wav_path).exists():
            logging.info(f'File for chapter {i} already exists. Skipping')
            stats.processed_chars += len(text)
            if post_event and hasattr(chapter, "chapter_index"):
                post_event('CORE_CHAPTER_FINISHED', chapter_index=chapter.chapter_index)
            continue
        if len(text.strip()) < 10:
            logging.info(f'Skipping empty chapter {i}')
            chapter_wav_files.remove(chapter_wav_path)
            continue

        logging.info(f'Writing  {text}')
        start_time = time.time()
        if post_event and hasattr(chapter, "chapter_index"):
            post_event('CORE_CHAPTER_STARTED', chapter_index=chapter.chapter_index)
        audio_segments = gen_audio_segments(
            cb_model,
            nlp,
            text,
            speed,
            stats,
            post_event=post_event,
            max_sentences=max_sentences,
            should_stop=should_stop,
            repetition_penalty=repetition_penalty,
            min_p=min_p,
            top_p=top_p,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            temperature=temperature
        )
        if should_stop():
            logging.info("Synthesis interrupted by user (after audio_segments).")
            break
        if audio_segments:
            final_audio = np.concatenate(audio_segments)
            soundfile.write(chapter_wav_path, final_audio, sample_rate)

            if enable_silence_trimming:
                trimmed_path = chapter_wav_path.with_suffix('.trimmed.wav')
                remove_silence_from_audio(
                    chapter_wav_path,
                    trimmed_path,
                    silence_thresh=silence_thresh,
                    min_silence_len=min_silence_len,
                    keep_silence=keep_silence
                )
                # Replace original with trimmed
                os.remove(chapter_wav_path)
                os.rename(trimmed_path, chapter_wav_path)

            end_time = time.time()
            delta_seconds = end_time - start_time
            chars_per_sec = len(text) / delta_seconds
            logging.info('Chapter written to %s', chapter_wav_path)
            if post_event and hasattr(chapter, "chapter_index"):
                post_event('CORE_CHAPTER_FINISHED', chapter_index=chapter.chapter_index)
            logging.info(f'Chapter {i} read in {delta_seconds:.2f} seconds ({chars_per_sec:.0f} characters per second)')
        else:
            logging.warning(f'Warning: No audio generated for chapter {i}')
            chapter_wav_files.remove(chapter_wav_path)

    if not chapter_wav_files:
        logging.error("No audio chapters were generated. Cannot create audiobook.")
        if post_event:
            post_event('CORE_ERROR', message="No audio chapters were generated.")
        allow_sleep()
        return

    if not chapter_wav_files:
        logging.error("No audio chapters were generated. Cannot create audiobook.")
        if post_event:
            post_event('CORE_ERROR', message="No audio chapters were generated.")
        allow_sleep()
        return

    original_name = Path(filename).with_suffix('').name  # removes old suffix
    parts = original_name.split('--')
    if len(parts) > 2:
        new_dir_name = f"{parts[0]}--{parts[1]}".strip()
        output_folder = Path(output_folder) / new_dir_name
        output_folder.mkdir(parents=True, exist_ok=True)

    if has_ffmpeg:
        create_index_file(title, creator, chapter_wav_files, output_folder)
        try:
            concat_file_path = concat_wavs_with_ffmpeg(chapter_wav_files, output_folder, filename,
                                                       post_event=post_event, should_stop=should_stop)
            if should_stop() or concat_file_path is None:
                logging.info("Synthesis interrupted before or during FFmpeg concat.")
                allow_sleep()
                return
            create_m4b(concat_file_path, filename, cover_image, output_folder, post_event=post_event, should_stop=should_stop)
            if should_stop():
                logging.info("Synthesis interrupted before or during FFmpeg m4b creation.")
                allow_sleep()
                return
            if post_event: post_event('CORE_FINISHED')
        except RuntimeError as e:
            logging.error(f"Audiobook creation failed: {e}")
            if post_event:
                post_event('CORE_ERROR', message=str(e))
    logging.info('Ended at: %s', time.strftime('%H:%M:%S'))

    all_files = os.listdir(output_folder)
    wav_files = [os.path.join(output_folder, f) for f in all_files if f.lower().endswith('.wav')]

    for wav_file in wav_files:
        try:
            os.remove(wav_file)
            logging.debug(f"Deleted: {wav_file}")
        except Exception as e:
            logging.debug(f"Failed to delete {wav_file}: {e}")

    allow_sleep()



def batch_sentences_intelligently(sentences, min_chars=150, max_chars=800):
    """
    Batch sentences into reasonable chunks for TTS processing.

    OPTIMIZED FOR SPEED: Larger batches (150-800 chars) = fewer TTS calls

    Args:
        sentences: List of spacy sentence objects
        min_chars: Minimum characters per batch (default 150, increased for speed)
        max_chars: Maximum characters per batch (default 800, increased for speed)

    Returns:
        List of batched sentence texts
    """
    batches = []
    current_batch = []
    current_length = 0

    for sent in sentences:
        sent_text = sent.text.strip()
        sent_length = len(sent_text)

        # Skip empty sentences
        if not sent_text or sent_length < 2:
            continue

        # If this sentence alone exceeds max_chars, add it as its own batch
        if sent_length > max_chars:
            # First, flush current batch if it exists
            if current_batch:
                batches.append(' '.join(current_batch))
                current_batch = []
                current_length = 0

            # Add the long sentence as its own batch
            batches.append(sent_text)
            continue

        # If adding this sentence would exceed max_chars, start a new batch
        if current_length > 0 and (current_length + sent_length + 1) > max_chars:
            batches.append(' '.join(current_batch))
            current_batch = [sent_text]
            current_length = sent_length
        else:
            # Add to current batch
            current_batch.append(sent_text)
            current_length += sent_length + (1 if current_batch else 0)  # +1 for space

        # If we've reached a good minimum size and hit a natural break, flush
        if current_length >= min_chars and sent_text.endswith(('.', '!', '?', '"', "'")):
            batches.append(' '.join(current_batch))
            current_batch = []
            current_length = 0

    # Don't forget the last batch
    if current_batch:
        batches.append(' '.join(current_batch))

    return batches


def find_cover(book):
    def is_image(item):
        return item is not None and item.media_type.startswith('image/')

    for item in book.get_items_of_type(ebooklib.ITEM_COVER):
        if is_image(item):
            return item

    for meta in book.get_metadata('OPF', 'cover'):
        if is_image(item := book.get_item_with_id(meta[1]['content'])):
            return item

    if is_image(item := book.get_item_with_id('cover')):
        return item

    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        if 'cover' in item.get_name().lower() and is_image(item):
            return item

    return None


def print_selected_chapters(document_chapters, chapters):
    ok = 'X' if platform.system() == 'Windows' else '✅'
    logging.info("\n" + tabulate([
        [i, c.get_name(), len(c.extracted_text), ok if c in chapters else '', chapter_beginning_one_liner(c)]
        for i, c in enumerate(document_chapters, start=1)
    ], headers=['#', 'Chapter', 'Text Length', 'Selected', 'First words']))


def gen_audio_segments(cb_model, nlp, text, speed, stats=None, max_sentences=None,
                       post_event=None, should_stop=None, repetition_penalty=1.2, min_p=0.05, top_p=1.0, exaggeration=0.5, cfg_weight=0.5, temperature=0.8):  # Use spacy to split into sentences

    if should_stop is None:
        should_stop = lambda: False

    audio_segments = []
    doc = nlp(text)
    sentences = list(doc.sents)
    batch_min_chars=150
    batch_max_chars=800
    num_candidates=3
    # Then batch sentences intelligently
    batches = batch_sentences_intelligently(
        sentences,
        min_chars=batch_min_chars,
        max_chars=batch_max_chars
    )

    total_batches = len(batches)
    logging.info(f"Split {len(sentences)} sentences into {total_batches} batches")

    # Show some batch examples
    for i, batch in enumerate(batches[:3]):
        logging.info(f"  Batch {i + 1} ({len(batch)} chars): {batch[:80]}{'...' if len(batch) > 80 else ''}")
    if total_batches > 3:
        logging.info(f"  ... and {total_batches - 3} more batches")

    for i, batch_text in enumerate(batches):
        if should_stop():
            logging.info("Synthesis interrupted by user (batch loop).")
            return audio_segments
        if max_sentences and i >= max_sentences:
            break

        batch_text = batch_text.strip()
        if not batch_text:
            continue


        wav = cb_model.generate(batch_text, repetition_penalty=repetition_penalty, min_p=min_p, top_p=top_p,
                                exaggeration=exaggeration, cfg_weight=cfg_weight, temperature=temperature)
        audio_segments.append(wav.numpy().flatten())

        # Update statistics based on batch size
        if stats:
            update_stats(stats, len(batch_text))
            if post_event:
                post_event('CORE_PROGRESS', stats=stats)
    return audio_segments


def extract_chapter_number(chapter_name):
    """
    Extracts the chapter number from a chapter name like 'Text/Chapter_18.xhtml'.
    Returns the integer number, or a large number if not found, to sort non-matching
    chapters last.
    """
    match = re.search(r'(\d+)', chapter_name)
    if match:
        return int(match.group(1))
    return float('inf') # Return a large number for chapters that don't match

def find_document_chapters_and_extract_texts(book):
    """Returns every chapter that is an ITEM_DOCUMENT and enriches each chapter with extracted_text."""
    document_chapters = []
    for chapter in book.get_items():
        if chapter.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        xml = chapter.get_body_content()
        soup = BeautifulSoup(xml, features='lxml')
        chapter.extracted_text = ''
        html_content_tags = ['title', 'p', 'h1', 'h2', 'h3', 'h4', 'li']
        for text in [c.text.strip() for c in soup.find_all(html_content_tags) if c.text]:
            if not text.endswith('.'):
                text += '.'
            chapter.extracted_text += text + '\n'
        document_chapters.append(chapter)

    # Sort chapters numerically based on their names
    document_chapters.sort(key=lambda c: extract_chapter_number(c.get_name()))

    for i, c in enumerate(document_chapters):
        c.chapter_index = i
    return document_chapters


def is_chapter(c):
    name = c.get_name().lower()
    has_min_len = len(c.extracted_text) > 100
    title_looks_like_chapter = bool(
        'chapter' in name.lower()
        or re.search(r'part_?\d{1,3}', name)
        or re.search(r'split_?\d{1,3}', name)
        or re.search(r'ch_?\d{1,3}', name)
        or re.search(r'chap_?\d{1,3}', name)
    )
    return has_min_len and title_looks_like_chapter


def chapter_beginning_one_liner(c, chars=20):
    s = c.extracted_text[:chars].strip().replace('\n', ' ').replace('\r', ' ')
    return s + '…' if len(s) > 0 else ''


def find_good_chapters(document_chapters):
    chapters = [c for c in document_chapters if c.get_type() == ebooklib.ITEM_DOCUMENT and is_chapter(c)]
    if len(chapters) == 0:
        logging.info('Not easy to recognize the chapters, defaulting to all non-empty documents.')
        chapters = [c for c in document_chapters if
                    c.get_type() == ebooklib.ITEM_DOCUMENT and len(c.extracted_text) > 10]
    return chapters


def pick_chapters(chapters):
    chapters_by_names = {
        f'{c.get_name()}\t({len(c.extracted_text)} chars)\t[{chapter_beginning_one_liner(c, 50)}]': c
        for c in chapters}
    title = 'Select which chapters to read in the audiobook'
    ret = pick(list(chapters_by_names.keys()), title, multiselect=True, min_selection_count=1)
    selected_chapters_out_of_order = [chapters_by_names[r[0]] for r in ret]
    selected_chapters = [c for c in chapters if c in selected_chapters_out_of_order]
    return selected_chapters


def strfdelta(tdelta, fmt='{D:02}d {H:02}h {M:02}m {S:02}s'):
    remainder = int(tdelta)
    f = Formatter()
    desired_fields = [field_tuple[1] for field_tuple in f.parse(fmt)]
    possible_fields = ('W', 'D', 'H', 'M', 'S')
    constants = {'W': 604800, 'D': 86400, 'H': 3600, 'M': 60, 'S': 1}
    values = {}
    for field in possible_fields:
        if field in desired_fields and field in constants:
            values[field], remainder = divmod(remainder, constants[field])
    return f.format(fmt, **values)


def enqueue_output(stream, queue_obj):
    """Helper function to read from a stream and put lines into a queue."""
    for line in iter(stream.readline, ''):
        queue_obj.put(line)
    stream.close()

MAX_PATH_LEN = 240

def safe_concat_path(output_folder: str, filename: str) -> Path:
    folder_path = Path(output_folder)
    name_part = Path(filename).stem
    suffix = Path(filename).suffix

    candidate = folder_path / f"{name_part}{suffix}"

    # If too long, truncate the base name until it fits
    while len(str(candidate.resolve())) > MAX_PATH_LEN and len(name_part) > 1:
        name_part = name_part[:-1]  # progressively shorten the name
        candidate = folder_path / f"{name_part}{suffix}"


    return candidate

def concat_wavs_with_ffmpeg(chapter_files, output_folder, filename, post_event=None, should_stop=None):
    base_filename_stem = Path(filename).stem
    wav_list_txt = Path(output_folder) / f"{base_filename_stem}_wav_list.txt"
    with open(wav_list_txt, 'w') as f:
        for wav_file in chapter_files:
            f.write(f"file '{str(wav_file)}'\n")

    concat_file_path = Path(output_folder) / f"{base_filename_stem}.tmp.mp4"

    ffmpeg_concat_cmd = [
        'ffmpeg',
        '-y',
        '-nostdin',  # <--- ADD THIS LINE
        '-f', 'concat',
        '-safe', '0',
        '-i', str(wav_list_txt),
        '-c:a', 'aac',
        '-b:a', '64k',
        '-progress', 'pipe:1',
        '-nostats',
        str(concat_file_path)
    ]

    logging.info(f"Running FFmpeg concat command: {' '.join(ffmpeg_concat_cmd)}")

    total_duration_seconds = sum(probe_duration(wav_file) for wav_file in chapter_files if wav_file.exists())
    logging.info(f"Concatenation Total Duration: {total_duration_seconds:.2f} seconds")

    process = subprocess.Popen(
        ffmpeg_concat_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    if should_stop is None:
        should_stop = lambda: False

    q_stdout = queue.Queue()
    q_stderr = queue.Queue()

    t_stdout = threading.Thread(target=enqueue_output, args=(process.stdout, q_stdout))
    t_stderr = threading.Thread(target=enqueue_output, args=(process.stderr, q_stderr))
    t_stdout.daemon = True
    t_stderr.daemon = True
    t_stdout.start()
    t_stderr.start()

    initial_stderr_lines = []
    # Drain initial STDERR output for a limited time or until queue is empty
    # This prevents blocking on large initial stderr bursts
    timeout_start = time.time()
    while (t_stderr.is_alive() or not q_stderr.empty()) and (
            time.time() - timeout_start < 5):  # DRAIN for max 5 seconds
        try:
            line = q_stderr.get_nowait().strip()
            if line:
                initial_stderr_lines.append(line)
                logging.error(f"FFmpeg CONCAT Initial STDERR: {line}")
        except queue.Empty:
            time.sleep(0.01)  # Small pause to yield CPU

    current_time_seconds = 0.0
    concat_error_output = initial_stderr_lines  # Start collecting from here

    try:
        while process.poll() is None or not q_stdout.empty() or not q_stderr.empty():
            if should_stop():
                logging.info("Synthesis interrupted by user (ffmpeg concat). Terminating FFmpeg process.")
                process.terminate()
                process.wait()
                return None
            # Process stdout for progress
            try:
                line_stdout = q_stdout.get(timeout=0.05)
                line_stdout = line_stdout.strip()
                # print(f"FFmpeg CONCAT STDOUT: {line_stdout}") # Debugging stdout output
                if "=" in line_stdout:
                    key, value = line_stdout.split("=", 1)
                    if key == "out_time":
                        try:
                            h, m, s = map(float, value.split(':'))
                            current_time_seconds = h * 3600 + m * 60 + s
                            if total_duration_seconds > 0:
                                progress = int((current_time_seconds / total_duration_seconds) * 100)
                                if post_event:
                                    stats_obj = SimpleNamespace(progress=progress, stage="concat", eta=strfdelta(
                                        total_duration_seconds - current_time_seconds))
                                    post_event('CORE_PROGRESS', stats=stats_obj)
                                # print(f"CONCAT Progress: {progress}% (Time: {current_time_seconds:.2f})") # More debugging
                        except ValueError:
                            pass
                    elif key == "progress" and value == "end":
                        break
            except queue.Empty:
                pass

            # Process stderr for errors/warnings
            try:
                line_stderr = q_stderr.get(timeout=0.05)
                stripped_line = line_stderr.strip()
                if stripped_line:
                    logging.error(f"FFmpeg CONCAT STDERR: {stripped_line}")
                    concat_error_output.append(stripped_line)
            except queue.Empty:
                pass

            time.sleep(0.001)  # Small sleep to avoid busy-waiting

    finally:
        # Final drain of queues
        while not q_stdout.empty():
            line_stdout = q_stdout.get_nowait().strip()
            if "=" in line_stdout:  # Still try to process any last progress updates
                key, value = line_stdout.split("=", 1)
                if key == "out_time":
                    try:
                        h, m, s = map(float, value.split(':'))
                        current_time_seconds = h * 3600 + m * 60 + s
                        if total_duration_seconds > 0:
                            progress = int((current_time_seconds / total_duration_seconds) * 100)
                            if post_event:
                                stats_obj = SimpleNamespace(progress=progress, stage="concat", eta=strfdelta(
                                    total_duration_seconds - current_time_seconds))
                                post_event('CORE_PROGRESS', stats=stats_obj)
                    except ValueError:
                        pass
        while not q_stderr.empty():
            stripped_line = q_stderr.get_nowait().strip()
            if stripped_line:
                logging.error(f"FFmpeg CONCAT STDERR (Post-loop): {stripped_line}")
                concat_error_output.append(stripped_line)

        process.wait()

    Path(wav_list_txt).unlink()

    if process.returncode != 0:
        error_message = f"FFmpeg concatenation failed with error code {process.returncode}.\nDetails:\n" + "\n".join(
            concat_error_output[-50:])
        logging.error(error_message)
        raise RuntimeError(error_message)

    return concat_file_path


def create_m4b(concat_file_path, filename, cover_image, output_folder, post_event=None, should_stop=None):
    logging.info('Creating M4B file...')

    original_name = Path(filename).with_suffix('').name  # removes old suffix


    new_name = f"{original_name}.m4b"

    final_filename = safe_concat_path(output_folder,new_name)
    chapters_txt_path = Path(output_folder) / "chapters.txt"
    logging.info('Creating M4B file...')

    ffmpeg_command = [
        'ffmpeg',
        '-y',
        '-nostdin',  # <--- ADD THIS LINE
        '-i', str(concat_file_path),
        '-i', str(chapters_txt_path),
    ]

    if cover_image:
        cover_file_path = Path(output_folder) / 'cover'
        with open(cover_file_path, 'wb') as f:
            f.write(cover_image)
        ffmpeg_command.extend([
            '-i', str(cover_file_path),
        ])
        map_video_index = '2:v'
        map_metadata_index = '2'
        map_chapters_index = '2'
    else:
        map_video_index = None
        map_metadata_index = '1'
        map_chapters_index = '1'

    ffmpeg_command.extend([
        '-map', '0:a',
        '-c:a', 'aac',
        '-b:a', '64k',
    ])

    if map_video_index:
        ffmpeg_command.extend([
            '-map', map_video_index,
            '-metadata:s:v', 'title="Album cover"',
            '-metadata:s:v', 'comment="Cover (front)"',
            '-disposition:v:0', 'attached_pic',
            '-c:v', 'copy'
        ])

    ffmpeg_command.extend([
        '-map_metadata', map_metadata_index,
        '-map_chapters', map_chapters_index,
        '-f', 'mp4',
        '-progress', 'pipe:1',
        '-nostats',
        str(final_filename)
    ])

    logging.info(f"Running FFmpeg command:\n{' '.join(ffmpeg_command)}\n")

    total_duration_seconds = probe_duration(concat_file_path)  # Changed to use Path object directly
    logging.info(f"M4B Conversion Total Duration: {total_duration_seconds:.2f} seconds")

    process = subprocess.Popen(
        ffmpeg_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    if should_stop is None:
        should_stop = lambda: False

    q_stdout = queue.Queue()
    q_stderr = queue.Queue()

    t_stdout = threading.Thread(target=enqueue_output, args=(process.stdout, q_stdout))
    t_stderr = threading.Thread(target=enqueue_output, args=(process.stderr, q_stderr))
    t_stdout.daemon = True
    t_stderr.daemon = True
    t_stdout.start()
    t_stderr.start()

    initial_stderr_lines = []
    # Drain initial STDERR output for a limited time or until queue is empty
    timeout_start = time.time()
    while (t_stderr.is_alive() or not q_stderr.empty()) and (
            time.time() - timeout_start < 5):  # DRAIN for max 5 seconds
        try:
            line = q_stderr.get_nowait().strip()
            if line:
                initial_stderr_lines.append(line)
                logging.error(f"FFmpeg M4B Initial STDERR: {line}")
        except queue.Empty:
            time.sleep(0.01)

    current_time_seconds = 0.0
    ffmpeg_error_output = initial_stderr_lines

    try:
        while process.poll() is None or not q_stdout.empty() or not q_stderr.empty():
            if should_stop():
                logging.info("Synthesis interrupted by user (ffmpeg m4b). Terminating FFmpeg process.")
                process.terminate()
                process.wait()
                return
            # Process stdout for progress
            try:
                line_stdout = q_stdout.get(timeout=0.05)
                line_stdout = line_stdout.strip()
                # print(f"FFmpeg M4B STDOUT: {line_stdout}") # Debugging stdout output
                if "=" in line_stdout:
                    key, value = line_stdout.split("=", 1)
                    if key == "out_time":
                        try:
                            h, m, s = map(float, value.split(':'))
                            current_time_seconds = h * 3600 + m * 60 + s
                            if total_duration_seconds > 0:
                                progress = int((current_time_seconds / total_duration_seconds) * 100)
                                if post_event:
                                    stats_obj = SimpleNamespace(progress=progress, stage="ffmpeg", eta=strfdelta(
                                        total_duration_seconds - current_time_seconds))
                                    post_event('CORE_PROGRESS', stats=stats_obj)
                                # print(f"M4B Progress: {progress}% (Time: {current_time_seconds:.2f})") # More debugging
                        except ValueError:
                            pass
                    elif key == "progress" and value == "end":
                        break
            except queue.Empty:
                pass

            # Process stderr for errors/warnings
            try:
                line_stderr = q_stderr.get(timeout=0.05)
                stripped_line = line_stderr.strip()
                if stripped_line:
                    logging.error(f"FFmpeg M4B STDERR: {stripped_line}")
                    ffmpeg_error_output.append(stripped_line)
            except queue.Empty:
                pass

            time.sleep(0.001)

    finally:
        # Final drain of queues
        while not q_stdout.empty():
            line_stdout = q_stdout.get_nowait().strip()
            if "=" in line_stdout:
                key, value = line_stdout.split("=", 1)
                if key == "out_time":
                    try:
                        h, m, s = map(float, value.split(':'))
                        current_time_seconds = h * 3600 + m * 60 + s
                        if total_duration_seconds > 0:
                            progress = int((current_time_seconds / total_duration_seconds) * 100)
                            if post_event:
                                stats_obj = SimpleNamespace(progress=progress, stage="ffmpeg", eta=strfdelta(
                                    total_duration_seconds - current_time_seconds))
                                post_event('CORE_PROGRESS', stats=stats_obj)
                    except ValueError:
                        pass
        while not q_stderr.empty():
            stripped_line = q_stderr.get_nowait().strip()
            if stripped_line:
                logging.error(f"FFmpeg M4B STDERR (Post-loop): {stripped_line}")
                ffmpeg_error_output.append(stripped_line)

        process.wait()

    Path(concat_file_path).unlink()
    if process.returncode == 0:
        logging.info(f'{final_filename} created. Enjoy your audiobook.')
    else:
        error_message = f"FFmpeg process exited with error code {process.returncode}.\nDetails:\n" + "\n".join(
            ffmpeg_error_output[-50:])
        logging.error(error_message)
        raise RuntimeError(error_message)


def probe_duration(file_name):
    # Check if the file exists before probing, to prevent errors if file was not created
    if not Path(file_name).exists():
        logging.warning(f"Warning: File not found for ffprobe duration: {file_name}")
        return 0.0

    args = ['ffprobe', '-i', str(file_name), '-show_entries', 'format=duration', '-v', 'quiet', '-of',
            'default=noprint_wrappers=1:nokey=1']
    try:
        # Using CREATE_NO_WINDOW on Windows to prevent console flashing for ffprobe
        creation_flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        proc = subprocess.run(args, capture_output=True, text=True, check=True, creationflags=creation_flags)
        duration = float(proc.stdout.strip())
        return duration
    except subprocess.CalledProcessError as e:
        logging.error(f"Error probing duration for {file_name}: {e.stderr}")
        return 0.0
    except ValueError:  # Occurs if stdout is not a float (e.g., empty or error message)
        logging.error(f"Could not parse duration from ffprobe output for {file_name}: '{proc.stdout.strip()}'")
        return 0.0


def create_index_file(title, creator, chapter_mp3_files, output_folder):
    with open(Path(output_folder) / "chapters.txt", "w", encoding="ascii", newline="\n") as f:
        f.write(f";FFMETADATA1\ntitle={title}\nartist={creator}\n\n")
        start = 0
        i = 0
        for c in chapter_mp3_files:
            duration = probe_duration(c)
            end = start + (int)(duration * 1000)
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}\ntitle=Chapter {i}\n\n")
            i += 1
            start = end


def unmark_element(element, stream=None):
    if stream is None:
        stream = StringIO()
    if element.text:
        stream.write(element.text)
    for sub in element:
        unmark_element(sub, stream)
    if element.tail:
        stream.write(element.tail)
    return stream.getvalue()


def unmark(text):
    return text
