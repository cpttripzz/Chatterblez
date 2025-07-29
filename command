in the settings dialog add another section underneath batch settings named Model settings. have sliders for the following:
with default setting
       repetition_penalty=1.2,
        min_p=0.05,
        top_p=1.0,
        audio_prompt_path=None,
        exaggeration=0.5,
        cfg_weight=0.5,
        temperature=0.8,


 these settings will be sent to the core.py

wav = cb_model.generate(sent.text)

