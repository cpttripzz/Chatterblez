# -*- coding: utf-8 -*-
import argparse
import sys
import os
from pathlib import Path

def cli_main():
    parser = argparse.ArgumentParser(
        description="Chatterblez  CLI - Convert EPUB/PDF to Audiobook",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--file', '-f', help='Path to a single EPUB or PDF file')
    group.add_argument('--batch', '-b', help='Path to a folder containing EPUB/PDF files for batch processing')

    parser.add_argument('-o', '--output', default='.', help='Output folder for the audiobook and temporary files', metavar='FOLDER')
    parser.add_argument('--filterlist', help='Comma-separated list of chapter names to ignore (case-insensitive substring match)')
    parser.add_argument('--wav', help='Path to a WAV file for voice conditioning (audio prompt)')
    parser.add_argument('--speed', type=float, default=1.0, help='Speech speed (default: 1.0)')
    parser.add_argument('--cuda', default=False, help='Use GPU via Cuda in Torch if available', action='store_true')

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
    args = parser.parse_args()

    if args.cuda:
        import torch.cuda
        if torch.cuda.is_available():
            print('CUDA GPU available')
            torch.set_default_device('cuda')
        else:
            print('CUDA GPU not available. Defaulting to CPU')

    from core import main

    # Prepare ignore_list
    ignore_list = [s.strip() for s in args.filterlist.split(',')] if args.filterlist else None

    # Prepare audio prompt
    audio_prompt_wav = args.wav if args.wav else None

    # Prepare output folder
    output_folder = args.output


    # Prepare speed
    speed = args.speed

    # Batch mode
    if args.batch:
        folder = Path(args.batch)
        if not folder.is_dir():
            print(f"Batch folder does not exist: {folder}", file=sys.stderr)
            sys.exit(1)
        supported_exts = [".epub", ".pdf"]
        batch_files = [
            str(folder / f)
            for f in os.listdir(folder)
            if os.path.isfile(str(folder / f)) and os.path.splitext(f)[1].lower() in supported_exts
        ]
        if not batch_files:
            print("No supported files (.epub, .pdf) found in the selected folder.", file=sys.stderr)
            sys.exit(1)
        main(
            file_path=None,
            pick_manually=False,
            speed=speed,
            output_folder=output_folder,
            batch_files=batch_files,
            ignore_list=ignore_list,
            audio_prompt_wav=audio_prompt_wav
        )
    # Single file mode
    elif args.file:
        file_path = args.file
        if not os.path.isfile(file_path):
            print(f"File does not exist: {file_path}", file=sys.stderr)
            sys.exit(1)
        main(
            file_path=file_path,
            pick_manually=False,
            speed=speed,
            output_folder=output_folder,
            batch_files=None,
            ignore_list=ignore_list,
            audio_prompt_wav=audio_prompt_wav
        )

if __name__ == '__main__':
    cli_main()
