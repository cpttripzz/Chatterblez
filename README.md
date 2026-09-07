# Chatterblez 🗣️📖✨

## 🚀 Transform Your PDFs & EPUBS into Engaging Audiobooks! 🎧

Ever wished your favorite books could talk to you? 🤩 Chatterblez is here to make that dream a reality! 🪄 We leverage the cutting-edge **Next-gen AI Chatterbox-tts from Resemble-AI** ([check them out!](https://github.com/resemble-ai/chatterbox)) to generate high-quality audiobooks directly from your PDF or EPUB files. 📚➡️🔊

Inspired by the awesome work of [audiblez](https://github.com/santinic/audiblez), Chatterblez takes text-to-speech to the next level, offering a seamless and delightful listening experience. 💖

---

### 💻 Compatibility 🧑‍💻

Tested and running smoothly on:

* Windows 11 🪟
* Python 3.12 🐍
* **NVIDIA CUDA 12.4:** Required for GPU acceleration and optimal performance. Please ensure you have a compatible NVIDIA graphics card and the necessary CUDA drivers installed. 🚀

---

### 🛠️ Installation & Setup 🚀

Ready to dive in? Here's how to get Chatterblez up and running on your machine:

#### 1. Clone the Repository 📥

```bash
git clone https://github.com/cpttripzz/Chatterblez
```

#### 2. Install CUDA (NVIDIA Graphics Cards Only!) ⚡️

If you have an NVIDIA GPU, install CUDA for optimal performance. This significantly speeds up the AI processing!

* Download CUDA 12.4:
  [https://developer.nvidia.com/cuda-12-4-0-download-archive?target\_os=Windows\&target\_arch=x86\_64\&target\_version=11\&target\_type=exe\_local](https://developer.nvidia.com/cuda-12-4-0-download-archive?target_os=Windows&target_arch=x86_64&target_version=11&target_type=exe_local)
* Follow the installation instructions provided by NVIDIA. 🧑‍💻

#### 3. Install Python Dependencies 📦

Navigate into the cloned directory and install the required Python packages:

```bash
pyenv install 3.11.9
pyenv local 3.11.9

# Create a new one with Python 3.11
python -m venv .venv

# Activate it
.venv\Scripts\activate

# Now, install the requirements
pip install --upgrade setuptools wheel

pip install llvmlite==0.41.1 numba==0.58.1 numpy==1.25.2


# Reinstall PyQt6 (this will automatically install the correct sip version)
pip install PyQt6
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt

pip install git+https://github.com/resemble-ai/chatterbox.git

```

This might take a moment, so grab a coffee! ☕

#### Optional TTS engines

Chatterblez keeps Qwen3 TTS and PocketTTS in `.venv-qwen3-tts` and
`.venv-pockettts` respectively. This is intentional: their Transformers and
NumPy requirements conflict with Chatterbox. The application starts the chosen
engine in its matching environment automatically; do not activate either
environment to launch the GUI.

After cloning an existing checkout where these folders are absent, create them
with the project Python and install the engine packages (use the CUDA wheel if
you want the Settings GPU option):

```powershell
.venv\Scripts\python.exe -m venv .venv-qwen3-tts
.venv-qwen3-tts\Scripts\python.exe -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
.venv-qwen3-tts\Scripts\python.exe -m pip install -f req-nochatterbox.txt

.venv\Scripts\python.exe -m venv .venv-pockettts
.venv-pockettts\Scripts\python.exe -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
.venv-pockettts\Scripts\python.exe -m pip install pocket-tts==3.1.0 soundfile
```

#### 4. Install FFMPEG 🔊

FFmpeg is required for audio processing. Here's how to install it:

**🔵 Windows:**

1. Download a static build from [https://www.gyan.dev/ffmpeg/builds/](https://www.gyan.dev/ffmpeg/builds/)
2. Extract the `.zip` to a location like `C:\ffmpeg`
3. Add the `C:\ffmpeg\bin` path to your **System Environment Variables**:

   * Search *"Edit the system environment variables"* from the Start Menu
   * Click "Environment Variables..."
   * Under "System Variables", find `Path`, click **Edit...**, then **New**, and paste the `bin` folder path
4. Open a new Command Prompt and run:

   ```bash
   ffmpeg -version
   ```

   You should see FFmpeg version info.

**🟢 Linux (Ubuntu):**

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install ffmpeg -y
ffmpeg -version
```

**🟣 macOS (with Homebrew):**

```bash
brew install ffmpeg
ffmpeg -version
```

---

### 🚀 Usage (Coming Soon!)

Detailed usage instructions, including how to convert your first PDF or EPUB, will be added here shortly! Stay tuned! ⏳

---

### 🙏 Acknowledgements

* **Resemble-AI** for their incredible [Chatterbox-tts](https://github.com/resemble-ai/chatterbox) project. They're making AI voices sound truly human! 🗣️
* **santinic** for the inspiration from [audiblez](https://github.com/santinic/audiblez). Great minds think alike! 💡

---

### 💌 Contributing

Got ideas? Found a bug? Want to make Chatterblez even better? We'd love your contributions! Please feel free to open an issue or submit a pull request. Let's build something amazing together! 🤝

---

### 📜 License

\[Add your license information here, e.g., MIT License]

---

Made with ❤️ by cpttripzz ✨
Happy listening! 🎧📖💖

---

Let me know if you’d like to add demo commands, screenshots, or a `chatterblez.py` usage example next.
