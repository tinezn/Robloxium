# Robloxium

![Robloxium Banner](assets/banners.png)

## 🚀 Overview
Robloxium is a powerful, open-source account and automation manager for Roblox, featuring a modern GUI, Discord integration, secure credential storage, and advanced automation tools. Built with Python 3.10+, it streamlines multi-account management, automates browser tasks, and provides robust error handling—all in a user-friendly package.

---

## ✨ Features
- **Modern UI**: CustomTkinter-based interface for a sleek, responsive experience
- **Account Management**: Securely store, import, and manage multiple Roblox accounts
- **Discord Integration**: Connect your Discord bot for notifications and remote control
- **Secure Storage**: Encrypted token and credential handling using `cryptography`
- **Automation**: Browser automation with Selenium for login, actions, and more
- **OCR Support**: Integrated Tesseract OCR for advanced tasks
- **Error Scanning**: Built-in error detection and reporting
- **Installer**: Easy Windows installation via Inno Setup
- **Open Source**: Licensed under GNU GPL v3

---

## 🖥️ Installation

### Requirements
- Windows 10/11
- Python 3.10+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (included in installer)
- Git (for source installation)

### Quick Start (Installer)
1. Download the latest release from [www.robloxium.xyz](https://www.robloxium.xyz)
2. Run `Robloxium Setup.exe` and follow the prompts
3. Launch Robloxium from your Start Menu or desktop shortcut

### From Source
```bash
git clone https://github.com/yourusername/robloxium.git
cd robloxium
pip install -r requirements.txt
python robloxium.py
```

---

## ⚙️ Usage
- **Import Accounts**: Use the Import Cookie dialog to add accounts (no cancel button, X to close)
- **Manage Accounts**: View, edit, and switch between accounts easily
- **Discord Bot**: Add your bot token (encrypted for security) to enable Discord features
- **Automation**: Use built-in tools for browser automation and OCR tasks

---

## 📁 Project Structure
```
Robloxium/
├── robloxium.py           # Main application
├── AccountManagerData/    # Encrypted account data (excluded from git)
├── assets/                # Images, icons, banners
├── tesseract/             # OCR binaries and configs
├── LICENSE.txt            # GNU GPL v3
├── README.md              # This file
```

---

## 🛡️ License
This project is licensed under the [GNU General Public License v3.0](LICENSE.txt).

---

## 🙏 Credits
- [customtkinter](https://github.com/TomSchimansky/CustomTkinter)
- [discord.py](https://github.com/Rapptz/discord.py)
- [cryptography](https://cryptography.io/)
- [selenium](https://www.selenium.dev/)
- [pytesseract](https://github.com/madmaze/pytesseract)
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)

---

## 💡 Contributing
Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.

---

## 📫 Contact
For support or questions, open an issue or contact the maintainer at [support@robloxium.xyz](mailto:support@robloxium.xyz).

---

> © 2025 Robloxium. All rights reserved.
