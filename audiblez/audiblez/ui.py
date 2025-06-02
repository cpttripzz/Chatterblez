        stage = getattr(event.stats, "stage", None)
        if stage in ("concat", "ffmpeg"):
            # Post-processing (concatenation / encoding) progress
            self.progress_bar_label.SetLabel(f"Audio Conversion Progress: {event.stats.progress}%")
        else:
            self.progress_bar_label.SetLabel(f"Synthesis Progress: {event.stats.progress}%")