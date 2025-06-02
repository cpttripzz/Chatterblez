                            if post_event:
                                elapsed = time.time() - start_time_eta
                                eta_seconds = elapsed * (100 / progress - 1) if progress > 0 else None
                                eta_str = strfdelta(eta_seconds) if eta_seconds is not None else "--"
                                stats_obj = SimpleNamespace(progress=progress, stage="ffmpeg", eta=eta_str)
                                post_event('CORE_PROGRESS', stats=stats_obj)
                                if post_event:
                                    # Dynamic ETA based on observed speed
                                    elapsed = time.time() - start_time_eta
                                    eta_seconds = elapsed * (100 / progress - 1) if progress > 0 else None
                                    eta_str = strfdelta(eta_seconds) if eta_seconds is not None else "--"
                                    stats_obj = SimpleNamespace(progress=progress, stage="ffmpeg", eta=eta_str)
                                    post_event('CORE_PROGRESS', stats=stats_obj)
                            if post_event:
                                elapsed = time.time() - start_time_eta
                                eta_seconds = elapsed * (100 / progress - 1) if progress > 0 else None
                                eta_str = strfdelta(eta_seconds) if eta_seconds is not None else "--"
                                stats_obj = SimpleNamespace(progress=progress, stage="concat", eta=eta_str)
                                post_event('CORE_PROGRESS', stats=stats_obj)
                                if post_event:
                                    # Calculate ETA based on observed speed, not on audio length
                                    elapsed = time.time() - start_time_eta
                                    eta_seconds = elapsed * (100 / progress - 1) if progress > 0 else None
                                    eta_str = strfdelta(eta_seconds) if eta_seconds is not None else "--"
                                    stats_obj = SimpleNamespace(progress=progress, stage="concat", eta=eta_str)
                                    post_event('CORE_PROGRESS', stats=stats_obj)

    # For dynamic ETA that reflects actual conversion speed
    start_time_eta = time.time()

    # For dynamic ETA that reflects actual encoding speed
    start_time_eta = time.time()
    process = subprocess.Popen(