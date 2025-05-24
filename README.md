# OpenMuscle Software

This repository contains software and firmware for the **OpenMuscle** system, which captures and interprets muscle signals from flexible pressure-sensing devices like LASK5 and FlexGrid. It supports both embedded firmware and PC-side applications for machine learning and real-time prediction.

---

## 🔗 Part of the OpenMuscle Ecosystem

This repo is part of the larger [OpenMuscle Hub](https://github.com/Open-Muscle/OpenMuscle-Hub), which provides:

- 🔧 Hardware design files and schematics  
- 📜 Documentation and guides  
- 🧪 Data pipelines and ML model training resources  
- 🎓 Educational content and community links

---

## 📦 Features

- 📡 **Wi-Fi Data Streaming** via UDP packets from ESP32-based sensors  
- 🧠 **Machine Learning Integration** for gesture and motion inference  
- 📊 **Real-Time Prediction and Visualization** on PC  
- ⚙️ **Cross-platform scripts** and libraries for research, prototyping, and evaluation

---

## 🗂 Repository Structure

```bash
OpenMuscle-Software/
├── embedded/          # MicroPython and C firmware
│   ├── LASK5/         # LASK5 hardware versions (V2, V3...)
│   ├── FlexGrid/      # FlexGrid embedded sensor band
│   └── SensorBand/    # Legacy OM12 and early band prototypes
├── pc/                # Python tools for data acquisition and ML
│   └── ...            # UDP receiver, model loader, data visualizer
├── LICENSE
└── README.md
```

---

## 🧰 Installation

```bash
git clone https://github.com/Open-Muscle/OpenMuscle-Software.git
cd OpenMuscle-Software
pip install -r requirements.txt
```

---

## 📚 Related Repositories

- [OpenMuscle-FlexGrid](https://github.com/Open-Muscle/OpenMuscle-FlexGrid): Flexible 60-sensor band PCB files
- [OpenMuscle-LASK5](https://github.com/Open-Muscle/OpenMuscle-LASK5): Labeling hardware schematics
- [OpenMuscle-Hub](https://github.com/Open-Muscle/OpenMuscle-Hub): Central documentation and roadmap
- [OpenMuscle-Band](https://github.com/Open-Muscle/OpenMuscle-Band): Early MVP prototypes

---

## 🤝 Contributing

Want to help build the future of prosthetic sensing? We welcome contributions in Python, MicroPython, C, hardware design, and documentation. A full contributor guide is coming soon!

---
